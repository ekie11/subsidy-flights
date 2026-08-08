"""
Парсер XML-ответа партнёрского API.

Схема ответа партнёра описана неполно, поэтому парсер сознательно сделан
толерантным: он не завязан на конкретный путь в дереве. Он находит все узлы,
у которых есть признак тарифа (FareCode / MRID / AvailQty), и собирает
недостающие поля, поднимаясь по предкам и спускаясь в потомков.

Ключевые поля (подтверждены на реальном ответе БилетДВ):
  AvailQty  — живое количество мест по тарифу
  FareCode  — код тарифа, PZZSOC = субсидированный
  MRID      — идентификатор субсидии
  BookURL   — ссылка на бронирование с вшитым PartnerID
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any, Iterable

import config


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------

@dataclass
class FlightOffer:
    """Один субсидированный тариф на одном рейсе."""
    route: str                  # "KHV-MOW"
    origin: str
    destination: str
    depart_date: str            # ISO, YYYY-MM-DD
    depart_time: str = ""       # HH:MM, если есть
    arrive_time: str = ""
    flight_number: str = ""     # "SU 1710"
    airline: str = ""
    fare_code: str = ""
    mrid: str = ""
    avail_qty: int = 0
    price: float = 0.0
    currency: str = "RUB"
    book_url: str = ""
    raw_id: str = ""            # стабильный ключ для сопоставления между циклами

    def key(self) -> str:
        """
        Ключ рейса+тарифа: по нему сравниваем снапшоты между циклами.

        Дата входит в ключ всегда: id предложения у партнёра уникален внутри
        одного ответа, но повторяется между датами. Тариф и MRID — тоже,
        иначе два субсидированных тарифа на одном рейсе схлопнутся в один.
        """
        ident = self.raw_id or self.flight_number
        return "|".join([self.route, self.depart_date, ident,
                         self.fare_code, self.mrid])

    def is_subsidized(self) -> bool:
        if self.fare_code.upper() in config.SUBSIDY_FARE_CODES:
            return True
        if config.TREAT_MRID_AS_SUBSIDY and self.mrid.strip():
            return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# Утилиты обхода дерева
# --------------------------------------------------------------------------

def _localname(tag: str) -> str:
    """Убирает namespace: '{urn:x}Fare' -> 'fare'."""
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _attrs_lower(el: ET.Element) -> dict[str, str]:
    return {_localname(k): (v or "").strip() for k, v in el.attrib.items()}


def _build_parents(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent
    return parents


def _collect_context(el: ET.Element, parents: dict) -> dict[str, str]:
    """
    Собирает плоский словарь поле->значение из:
      1) атрибутов всех предков и их поддеревьев (ближний предок перекрывает
         дальнего — так данные «своего» рейса вытесняют данные соседних),
      2) атрибутов самого узла и его потомков (высший приоритет).

    Узлы-тарифы в поддеревьях игнорируются: иначе AvailQty соседнего тарифа
    протёк бы в текущее предложение.
    """
    ctx: dict[str, str] = {}

    # Предки — от дальнего к ближнему.
    chain: list[ET.Element] = []
    node = el
    while node in parents:
        node = parents[node]
        chain.append(node)
    on_path = set(chain)
    on_path.add(el)

    for anc in reversed(chain):
        ctx.update(_attrs_lower(anc))
        for child in anc:
            if child in on_path:
                continue  # ветку с самим тарифом обработаем ниже
            for sub in child.iter():
                if _is_fare_node(sub):
                    continue  # чужой тариф — не смешиваем
                ctx.update(_attrs_lower(sub))
                if len(sub) == 0 and (sub.text or "").strip():
                    ctx[_localname(sub.tag)] = sub.text.strip()

    # Сам узел и его поддерево (глубина 2) — приоритетнее предков.
    ctx.update(_attrs_lower(el))
    for child in el.iter():
        if child is el:
            continue
        ctx.update(_attrs_lower(child))
        if len(child) == 0 and (child.text or "").strip():
            ctx[_localname(child.tag)] = child.text.strip()

    return ctx


def _first(ctx: dict[str, str], *names: str, default: str = "") -> str:
    for n in names:
        v = ctx.get(n.lower())
        if v:
            return v
    return default


def _to_int(value: str, default: int = 0) -> int:
    m = re.search(r"-?\d+", value or "")
    return int(m.group()) if m else default


def _to_float(value: str, default: float = 0.0) -> float:
    if not value:
        return default
    cleaned = value.replace(" ", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(m.group()) if m else default


_DATE_PATTERNS = ("%Y-%m-%d", "%d.%m.%Y", "%Y%m%d", "%d/%m/%Y")


def _to_iso_date(value: str, default: str = "") -> str:
    if not value:
        return default
    v = value.strip()
    # ISO datetime: 2026-10-01T07:35:00
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ]", v)
    if m:
        return m.group(1)
    for pat in _DATE_PATTERNS:
        try:
            return datetime.strptime(v[:10], pat).date().isoformat()
        except ValueError:
            continue
    return default


def _to_time(value: str, default: str = "") -> str:
    if not value:
        return default
    m = re.search(r"(\d{1,2}):(\d{2})", value)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return default


# --------------------------------------------------------------------------
# Основной разбор
# --------------------------------------------------------------------------

_FARE_MARKERS = ("farecode", "mrid", "availqty")


def _is_fare_node(el: ET.Element) -> bool:
    """Узел похож на тариф, если несёт хотя бы один тарифный атрибут."""
    attrs = _attrs_lower(el)
    return any(m in attrs for m in _FARE_MARKERS)


def parse_offers(
    xml_text: str,
    route: str = "",
    depart_date: date | str | None = None,
    subsidized_only: bool = True,
) -> list[FlightOffer]:
    """
    Разбирает XML-ответ в список предложений.

    route / depart_date — то, что мы запрашивали; используются как fallback,
    если в ответе поля не нашлись.
    """
    if not xml_text or not xml_text.strip():
        raise ParseError("пустой ответ")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ParseError(f"невалидный XML: {exc}") from exc

    _raise_if_api_error(root)

    parents = _build_parents(root)
    fallback_date = ""
    if isinstance(depart_date, date):
        fallback_date = depart_date.isoformat()
    elif isinstance(depart_date, str):
        fallback_date = _to_iso_date(depart_date)

    fb_origin, fb_dest = "", ""
    if route and "-" in route:
        fb_origin, fb_dest = route.split("-", 1)

    offers: list[FlightOffer] = []
    seen: set[str] = set()

    for el in root.iter():
        if not _is_fare_node(el):
            continue
        ctx = _collect_context(el, parents)

        origin = _first(ctx, "origin", "from", "departureairport", "depairport",
                        "departurecode", "origincode", default=fb_origin).upper()
        dest = _first(ctx, "destination", "to", "arrivalairport", "arrairport",
                      "arrivalcode", "destinationcode", default=fb_dest).upper()

        raw_dep = _first(ctx, "departuredate", "depdate", "departure",
                         "departuredatetime", "date", "flightdate")
        dep_date = _to_iso_date(raw_dep, default=fallback_date)

        offer = FlightOffer(
            route=f"{origin}-{dest}" if origin and dest else (route or ""),
            origin=origin,
            destination=dest,
            depart_date=dep_date,
            depart_time=_to_time(_first(ctx, "departuretime", "deptime",
                                        "departuredatetime", "departure")),
            arrive_time=_to_time(_first(ctx, "arrivaltime", "arrtime",
                                        "arrivaldatetime", "arrival")),
            flight_number=_first(ctx, "flightnumber", "flightno", "flight",
                                 "number", "flightcode"),
            airline=_first(ctx, "airline", "carrier", "marketingcarrier",
                           "operatingcarrier", "airlinecode"),
            fare_code=_first(ctx, "farecode", "fareclass", "faretype"),
            mrid=_first(ctx, "mrid"),
            avail_qty=_to_int(_first(ctx, "availqty", "seats", "availableseats",
                                     "seatcount", "quantity")),
            price=_to_float(_first(ctx, "price", "totalprice", "amount",
                                   "fareamount", "total")),
            currency=_first(ctx, "currency", "currencycode", default="RUB"),
            book_url=_first(ctx, "bookurl", "booklink", "deeplink", "url"),
            raw_id=_first(ctx, "offerid", "recommendationid", "id", "uid"),
        )

        if subsidized_only and not offer.is_subsidized():
            continue

        k = offer.key()
        if k in seen:
            continue
        seen.add(k)
        offers.append(offer)

    return offers


def _raise_if_api_error(root: ET.Element) -> None:
    """Партнёр может вернуть 200 OK с телом-ошибкой — ловим это явно."""
    for el in root.iter():
        name = _localname(el.tag)
        if name in ("error", "fault", "errors"):
            attrs = _attrs_lower(el)
            msg = (el.text or "").strip() or _first(
                attrs, "message", "description", "text", default="")
            code = _first(attrs, "code", "errorcode", default="")
            for child in el:
                if not msg and (child.text or "").strip():
                    msg = child.text.strip()
            raise ParseError(f"API вернул ошибку {code}: {msg or 'без описания'}".strip())


def summarize(offers: Iterable[FlightOffer]) -> dict[str, Any]:
    offers = list(offers)
    return {
        "offers": len(offers),
        "with_seats": sum(1 for o in offers if o.avail_qty > 0),
        "total_seats": sum(o.avail_qty for o in offers),
        "routes": sorted({o.route for o in offers if o.route}),
    }
