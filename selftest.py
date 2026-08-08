#!/usr/bin/env python3
"""
Автотест всего пайплайна на фикстуре, без сети и без внешних зависимостей.

    python selftest.py

Проверяет:
  1. парсер — 3 субсидированных тарифа, поля разобраны верно;
  2. несубсидированный тариф (YFLEX) отфильтрован и не «протёк» в соседний;
  3. ключи рейсов уникальны и стабильны между прогонами;
  4. запись/чтение SQLite;
  5. алерты: restock (0 → есть места), soldout, low, drop;
  6. кулдаун подавляет повторный алерт;
  7. HTML-отчёт генерируется и содержит данные.

Работает в отдельной временной папке — боевую БД не трогает.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="subsidy-selftest-"))

# Изолируем окружение ДО импорта config.
os.environ["SUBSIDY_DATA_DIR"] = str(TMP)
os.environ["SUBSIDY_DB_PATH"] = str(TMP / "test.sqlite3")
os.environ["SUBSIDY_REPORT_PATH"] = str(TMP / "report.html")
os.environ["SUBSIDY_LOG_PATH"] = str(TMP / "test.log")
os.environ["SUBSIDY_DRY_RUN"] = "1"
os.environ["SUBSIDY_ALERT_COOLDOWN"] = "0"
sys.path.insert(0, str(BASE))

import config            # noqa: E402
import parser            # noqa: E402
import report            # noqa: E402
from alerts import AlertManager, evaluate  # noqa: E402
from db import Database  # noqa: E402
from fetcher import Fetcher  # noqa: E402

PASSED, FAILED = 0, 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# --------------------------------------------------------------------------
section("1. Парсер")

xml = Path(config.FIXTURE_PATH).read_text(encoding="utf-8")
offers = parser.parse_offers(xml, route="KHV-MOW", depart_date=date(2026, 10, 1))
offers.sort(key=lambda o: o.flight_number)

check("найдено 3 субсидированных тарифа", len(offers) == 3, f"получено {len(offers)}")

if len(offers) == 3:
    by_flight = {o.flight_number: o for o in offers}
    check("номера рейсов разобраны",
          set(by_flight) == {"SU 1710", "S7 5209", "HZ 5601"}, str(set(by_flight)))

    su = by_flight.get("SU 1710")
    if su:
        check("AvailQty SU 1710 = 9", su.avail_qty == 9, str(su.avail_qty))
        check("FareCode = PZZSOC", su.fare_code.upper() == "PZZSOC", su.fare_code)
        check("MRID разобран", su.mrid == "DV-2026-0431", su.mrid)
        check("цена 10800", abs(su.price - 10800) < 0.01, str(su.price))
        check("маршрут KHV-MOW", su.route == "KHV-MOW", su.route)
        check("дата вылета", su.depart_date == "2026-10-01", su.depart_date)
        check("время вылета 07:35", su.depart_time == "07:35", su.depart_time)
        check("BookURL с PartnerID", "KirillTest" in su.book_url, su.book_url)

    s7 = by_flight.get("S7 5209")
    check("AvailQty S7 5209 = 2 (не протёк соседний тариф)",
          s7 is not None and s7.avail_qty == 2, str(s7.avail_qty if s7 else None))
    hz = by_flight.get("HZ 5601")
    check("AvailQty HZ 5601 = 0", hz is not None and hz.avail_qty == 0,
          str(hz.avail_qty if hz else None))

all_offers = parser.parse_offers(xml, route="KHV-MOW",
                                 depart_date=date(2026, 10, 1), subsidized_only=False)
check("без фильтра тарифов больше (YFLEX виден)", len(all_offers) > len(offers),
      f"{len(all_offers)} vs {len(offers)}")

keys = [o.key() for o in offers]
check("ключи уникальны", len(set(keys)) == len(keys), str(keys))
check("ключи стабильны при повторном парсинге",
      [o.key() for o in parser.parse_offers(xml, "KHV-MOW", date(2026, 10, 1))]
      == [o.key() for o in parser.parse_offers(xml, "KHV-MOW", date(2026, 10, 1))])

# Дату парсер берёт из ответа, а не из запроса (аргумент — только fallback),
# поэтому берём ответ на другую дату через fetcher, как в реальном прогоне.
other_xml = Fetcher(dry_run=True, save_raw=False).fetch("KHV", "MOW", date(2026, 10, 2))
other_day = parser.parse_offers(other_xml, "KHV-MOW", date(2026, 10, 2))
check("дата берётся из ответа, а не из запроса",
      all(o.depart_date == "2026-10-02" for o in other_day),
      str({o.depart_date for o in other_day}))
check("ключи разных дат не совпадают",
      not (set(keys) & {o.key() for o in other_day}))

section("2. Fetcher (dry-run)")
f = Fetcher(dry_run=True, save_raw=False)
led = f.fetch("KHV", "LED", date(2026, 10, 5))
led_offers = parser.parse_offers(led, route="KHV-LED", depart_date=date(2026, 10, 5))
check("фикстура подставляет запрошенный маршрут",
      all(o.route == "KHV-LED" for o in led_offers),
      str({o.route for o in led_offers}))
check("фикстура подставляет запрошенную дату",
      all(o.depart_date == "2026-10-05" for o in led_offers),
      str({o.depart_date for o in led_offers}))

section("3. База данных")
db = Database()
saved = db.save_observations(offers)
check("сохранено 3 наблюдения", saved == 3, str(saved))
latest = db.latest_by_key([o.key() for o in offers])
check("прочитано 3 последних наблюдения", len(latest) == 3, str(len(latest)))
check("пустой фильтр возвращает пусто", db.latest_by_key([]) == {})
check("current_state видит будущие рейсы", len(db.current_state()) == 3)

section("4. Алерты")
manager = AlertManager(db, dry_run=True)

# Первый прогон уже записан выше; строим второй с изменёнными местами.
prev = db.latest_by_key([o.key() for o in offers])
mutated = []
for o in offers:
    clone = parser.FlightOffer(**o.as_dict())
    if clone.flight_number == "SU 1710":
        clone.avail_qty = 2          # 9 -> 2 : падение на 7 + мало мест
    elif clone.flight_number == "HZ 5601":
        clone.avail_qty = 4          # 0 -> 4 : появились места
    elif clone.flight_number == "S7 5209":
        clone.avail_qty = 0          # 2 -> 0 : всё раскупили
    mutated.append(clone)

produced = []
for clone in mutated:
    produced.extend(evaluate(clone, prev.get(clone.key())))

types = {a.alert_type for a in produced}
check("сработал restock (0 → 4)", "restock" in types, str(types))
check("сработал soldout (2 → 0)", "soldout" in types, str(types))
check("сработал drop (9 → 2)", "drop" in types, str(types))
check("restock помечен как critical",
      all(a.severity == "critical" for a in produced if a.alert_type == "restock"))

restock_msg = next((a.message for a in produced if a.alert_type == "restock"), "")
check("текст алерта не покалечен разделителем тысяч",
      "Было 0, стало 4" in restock_msg, restock_msg)
check("цена в алерте отформатирована", "10 800 RUB" in restock_msg, restock_msg)

sent = manager.process(produced)
check("алерты записаны в БД", len(db.recent_alerts()) == len(sent) and len(sent) > 0,
      f"sent={len(sent)}, в БД={len(db.recent_alerts())}")

db.save_observations(mutated)

# Кулдаун
config.ALERTS.cooldown_minutes = 120
repeat = manager.process(produced)
check("кулдаун подавил повтор", repeat == [], f"повторно прошло {len(repeat)}")
config.ALERTS.cooldown_minutes = 0

# Стабильное состояние не должно генерировать алерты
prev2 = db.latest_by_key([o.key() for o in mutated])
quiet = []
for clone in mutated:
    quiet.extend(evaluate(clone, prev2.get(clone.key())))
check("без изменений алертов нет", quiet == [], str([a.alert_type for a in quiet]))

section("5. Отчёт")
path = report.build()
html_text = Path(path).read_text(encoding="utf-8")
check("файл отчёта создан", Path(path).exists())
check("в отчёте есть маршрут", "KHV-MOW" in html_text)
check("в отчёте есть номер рейса", "SU 1710" in html_text)
check("в отчёте есть блок событий", "restock" in html_text)
check("в отчёте есть ссылка на бронирование", "biletdv.ru/book" in html_text)

# Кнопка «купить»: показываем только рабочую ссылку и только когда есть места.
check("ссылка рендерится при живых данных и наличии мест",
      "купить" in report._book_cell("https://biletdv.ru/book?x=1", 5, False))
check("ссылки нет, если мест нет",
      "купить" not in report._book_cell("https://biletdv.ru/book?x=1", 0, False))
check("ссылки нет, если партнёр её не прислал",
      report._book_cell("", 5, False) == "")
check("мусорный URL не превращается в ссылку",
      "<a" not in report._book_cell("javascript:alert(1)", 5, False))
check("в демо-режиме ссылка отключена",
      "<a" not in report._book_cell("https://biletdv.ru/book?x=1", 5, True))

# Демо-режим: прогон помечен dry-run → баннер и никаких кликабельных ссылок.
db.finish_run(db.start_run(dry_run=True), 1, 3, 0)
demo_html = Path(report.build()).read_text(encoding="utf-8")
check("в демо-отчёте есть предупреждение", "Демонстрационные данные" in demo_html)
check("в демо-отчёте нет кликабельных ссылок на бронирование",
      "href='https://biletdv.ru/book" not in demo_html)

section("6. Защита демо-режима на витрине")
import webapp  # noqa: E402  — импорт здесь, чтобы не тянуть его в разделы выше

demo_site = Path(webapp.build(TMP / "site_demo.html")).read_text(encoding="utf-8")
check("демо-витрина закрыта от индексации",
      'name="robots" content="noindex' in demo_site)
check("на демо-витрине есть баннер", "Демо-режим" in demo_site)

# Помечаем прогон как боевой — защита должна снять себя сама.
db.finish_run(db.start_run(dry_run=False), 60, 180, 0)
live_site = Path(webapp.build(TMP / "site_live.html")).read_text(encoding="utf-8")
check("в боевом режиме noindex снят", "noindex" not in live_site)
check("в боевом режиме баннера нет", "Демо-режим" not in live_site)
check("данные на витрину попали", "KHV" in live_site and "SU 1710" in live_site)

section("7. Ошибки API")
try:
    parser.parse_offers("<Response><Error Code='403' Message='bad partner'/></Response>",
                        route="KHV-MOW", depart_date=date(2026, 10, 1))
    check("ошибка API распознана", False, "исключение не выброшено")
except parser.ParseError as exc:
    check("ошибка API распознана", "403" in str(exc), str(exc))

try:
    parser.parse_offers("не xml", route="KHV-MOW")
    check("битый XML отловлен", False, "исключение не выброшено")
except parser.ParseError:
    check("битый XML отловлен", True)

# --------------------------------------------------------------------------
print(f"\n{'=' * 52}")
print(f"пройдено: {PASSED}   провалено: {FAILED}")
print(f"отчёт: {path}")
print(f"временные файлы: {TMP}")
sys.exit(1 if FAILED else 0)
