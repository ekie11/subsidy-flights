"""
Парсер ответа БилетДВ getFaresByFOP_Ex3 (FlightsSearchResponse).

Структура (по WSDL и живым ответам на тестовый PartnerID):

  FlightsSearchResponse
    isSuccess / ErrorText
    References            — справочники (авиакомпании, аэропорты, MiniRules)
    Offers/Proposal       — одно предложение = одна покупка
        @Total @Currency @Cache_Best_Before @TL_Utc
        Flights/Flight    — сегменты: @Code @Num @Origin @Destination
                            @Departure="15.10.2026T10:10" @FareCode @MRID
                            @AvailQty @Direction (0 — туда) ...
        BookURL           — глубокая ссылка с PartnerID

Маршрут предложения — тот, что запрашивали (города: KHV-MOW), а не аэропорты
из ответа (KHV-SVO): иначе рейсы в Шереметьево и Внуково разъехались бы по
разным «маршрутам».

Ответ на мусорный запрос — тоже isSuccess=true с пустым Offers, так что
«ошибку» от «мест нет» по ответу не отличить: валидность параметров — на нас.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import quote

import config

log = logging.getLogger("parser")


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
    flight_number: str = ""     # "SU 1719", с пересадкой "SU 1719 + SU 1402"
    airline: str = ""
    fare_code: str = ""
    mrid: str = ""
    avail_qty: int = 0
    price: float = 0.0
    currency: str = "RUB"
    book_url: str = ""
    psg: str = ""               # категория пассажира партнёра (mla/aaa), по которой пришёл тариф

    def key(self) -> str:
        """
        Ключ рейса+тарифа: по нему сравниваем снапшоты между циклами.

        Id из ответа (rguid/iguid в BookURL) меняются при каждом запросе,
        поэтому ключ собираем из стабильного: дата, рейсы, тариф, MRID —
        иначе молодёжный и «прописочный» тарифы одного рейса схлопнутся.
        """
        return "|".join([self.route, self.depart_date, self.flight_number,
                         self.fare_code, self.mrid, self.psg])

    def is_subsidized(self) -> bool:
        codes = {c for c in self.fare_code.upper().split("/") if c}
        if codes and codes <= config.SUBSIDY_FARE_CODES:
            return True
        if config.TREAT_MRID_AS_SUBSIDY and self.mrid.strip() not in ("", "0"):
            return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------

def _localname(tag: str) -> str:
    """Убирает namespace: '{urn:x}Proposal' -> 'proposal'."""
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _attrs_lower(el: ET.Element) -> dict[str, str]:
    return {_localname(k): (v or "").strip() for k, v in el.attrib.items()}


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _localname(c.tag) == name]


def _child_text(el: ET.Element, name: str) -> str:
    for c in _children(el, name):
        return (c.text or "").strip()
    return ""


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

def _find_response(root: ET.Element) -> ET.Element | None:
    """FlightsSearchResponse: корень при GET, <…Result> внутри SOAP-конверта."""
    for el in root.iter():
        name = _localname(el.tag)
        if name == "flightssearchresponse" or name.endswith("result"):
            return el
    return None


def _with_person_id(url: str) -> str:
    if not url or not config.PERSON_ID or "personid=" in url.lower():
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}PersonID={quote(config.PERSON_ID)}"


def _proposal_offer(prop: ET.Element, fb_origin: str, fb_dest: str,
                    fallback_date: str, fallback_url: str) -> FlightOffer | None:
    """Одно предложение → одна строка. Пересадки склеиваются: места — по самому
    загруженному сегменту, вылет — первого, прилёт — последнего."""
    attrs = _attrs_lower(prop)
    flights = [_attrs_lower(f) for fl in _children(prop, "flights")
               for f in _children(fl, "flight")]
    # Direction 0 — туда; обратные сегменты (при backDate) не наш случай.
    outbound = [f for f in flights if f.get("direction", "0") in ("", "0")] or flights
    if not outbound:
        return None
    first, last = outbound[0], outbound[-1]

    origin = fb_origin or first.get("origin", "").upper()
    dest = fb_dest or last.get("destination", "").upper()
    fare_codes = list(dict.fromkeys(f.get("farecode", "") for f in outbound
                                    if f.get("farecode")))
    mrids = [f.get("mrid", "") for f in outbound if f.get("mrid", "") not in ("", "0")]

    return FlightOffer(
        route=f"{origin}-{dest}",
        origin=origin,
        destination=dest,
        depart_date=_to_iso_date(first.get("departure", ""), default=fallback_date),
        depart_time=_to_time(first.get("departure", "")),
        arrive_time=_to_time(last.get("arrival", "")),
        flight_number=" + ".join(
            f"{f.get('code', '')} {f.get('num', '')}".strip() for f in outbound),
        airline=first.get("code", ""),
        fare_code="/".join(fare_codes),
        mrid=mrids[0] if mrids else "",
        avail_qty=min(_to_int(f.get("availqty", "")) for f in outbound),
        price=_to_float(_first(attrs, "total", "fare")),
        currency=_first(attrs, "currency", default="RUB"),
        book_url=_with_person_id(_child_text(prop, "bookurl") or fallback_url),
    )


def parse_offers(
    xml_text: str,
    route: str = "",
    depart_date: date | str | None = None,
    subsidized_only: bool = True,
) -> list[FlightOffer]:
    """
    Разбирает ответ getFaresByFOP_Ex3 в список предложений.

    route — запрошенный маршрут городами (KHV-MOW), он и станет маршрутом
    предложения; depart_date — fallback, если у сегмента нет даты вылета.
    """
    if not xml_text or not xml_text.strip():
        raise ParseError("пустой ответ")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ParseError(f"невалидный XML: {exc}") from exc

    _raise_if_api_error(root)

    resp = _find_response(root)
    if resp is None:
        raise ParseError(f"неизвестный формат ответа: <{_localname(root.tag)}>")
    if _child_text(resp, "issuccess").lower() == "false":
        raise ParseError(f"API: {_child_text(resp, 'errortext') or 'isSuccess=false'}")

    fallback_date = ""
    if isinstance(depart_date, date):
        fallback_date = depart_date.isoformat()
    elif isinstance(depart_date, str):
        fallback_date = _to_iso_date(depart_date)

    fb_origin, fb_dest = "", ""
    if route and "-" in route:
        fb_origin, fb_dest = (p.upper() for p in route.split("-", 1))

    fallback_url = _child_text(resp, "bookurl")
    offers: list[FlightOffer] = []
    seen: set[str] = set()

    for offers_el in _children(resp, "offers"):
        for prop in _children(offers_el, "proposal"):
            offer = _proposal_offer(prop, fb_origin, fb_dest, fallback_date,
                                    fallback_url)
            if offer is None:
                continue
            if subsidized_only and not offer.is_subsidized():
                log.warning("пропущен тариф без признаков субсидии: %s %s %s "
                            "FareCode=%s MRID=%s", offer.route, offer.depart_date,
                            offer.flight_number, offer.fare_code, offer.mrid or 0)
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
