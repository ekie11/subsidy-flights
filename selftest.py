#!/usr/bin/env python3
"""
Автотест всего пайплайна на фикстуре, без сети и без внешних зависимостей.

    python selftest.py

Проверяет:
  1. парсер на настоящем ответе партнёра — 3 тарифа, поля разобраны верно;
  2. пересадки склеиваются, MRID=0 не считается субсидией, fetcher;
  3. ключи рейсов уникальны и стабильны между прогонами;
  4. запись/чтение SQLite;
  5. алерты: restock (0 → есть места), soldout, low, drop;
  6. кулдаун подавляет повторный алерт;
  7. HTML-отчёт генерируется и содержит данные;
  8. subscribe_api.py — валидация, приём подписки, анти-спам;
  9. рассылка подписчикам при появлении мест.

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
os.environ["BILETDV_PERSON_ID"] = ""
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
section("1. Парсер (настоящий ответ getFaresByFOP_Ex3, mla, KHV-MOW 15.10)")

xml = Path(config.FIXTURE_PATH).read_text(encoding="utf-8")
offers = parser.parse_offers(xml, route="KHV-MOW", depart_date=date(2026, 10, 15))
offers.sort(key=lambda o: o.flight_number)

check("найдено 3 субсидированных тарифа", len(offers) == 3, f"получено {len(offers)}")

if len(offers) == 3:
    by_flight = {o.flight_number: o for o in offers}
    check("номера рейсов разобраны",
          set(by_flight) == {"SU 1719", "SU 6298", "SU 5807"}, str(set(by_flight)))

    su = by_flight.get("SU 1719")
    if su:
        check("AvailQty SU 1719 = 9", su.avail_qty == 9, str(su.avail_qty))
        check("FareCode = PZZSOC", su.fare_code == "PZZSOC", su.fare_code)
        check("MRID разобран", su.mrid == "43834", su.mrid)
        check("цена 8099 (Total предложения)", abs(su.price - 8099) < 0.01, str(su.price))
        check("маршрут городами KHV-MOW, а не аэропортом SVO",
              su.route == "KHV-MOW", su.route)
        check("дата вылета из 15.10.2026T10:10", su.depart_date == "2026-10-15",
              su.depart_date)
        check("время вылета 10:10", su.depart_time == "10:10", su.depart_time)
        check("время прилёта 11:10", su.arrive_time == "11:10", su.arrive_time)
        check("BookURL своего предложения с PartnerID",
              "iguid=b3643b2f" in su.book_url and "PartnerID=KirillTest" in su.book_url,
              su.book_url)
        check("справочник MiniRules не подмешан в рейс", su.airline == "SU", su.airline)

keys = [o.key() for o in offers]
check("ключи уникальны", len(set(keys)) == len(keys), str(keys))
check("ключи стабильны при повторном парсинге",
      [o.key() for o in parser.parse_offers(xml, "KHV-MOW", date(2026, 10, 15))]
      == [o.key() for o in parser.parse_offers(xml, "KHV-MOW", date(2026, 10, 15))])

empty = Path(config.FIXTURE_EMPTY_PATH).read_text(encoding="utf-8")
check("пустой Offers — пустой список, не ошибка",
      parser.parse_offers(empty, "KHV-MOW", date(2026, 10, 15)) == [])


def _resp(proposals: str) -> str:
    return ("<FlightsSearchResponse><isSuccess>true</isSuccess>"
            f"<Offers>{proposals}</Offers></FlightsSearchResponse>")


connecting = _resp(
    '<Proposal Total="12000" Currency="RUB"><Flights>'
    '<Flight Code="SU" Num="5601" Origin="KHV" Destination="OVB" '
    'Departure="16.10.2026T08:00" Arrival="16.10.2026T09:00" '
    'FareCode="PZZSOC" MRID="1" AvailQty="7" Direction="0"/>'
    '<Flight Code="SU" Num="1402" Origin="OVB" Destination="SVO" '
    'Departure="16.10.2026T11:00" Arrival="16.10.2026T12:30" '
    'FareCode="PZZSOC" MRID="1" AvailQty="3" Direction="0"/>'
    '</Flights><BookURL>https://avia.biletdv.ru/Order.aspx?x=1</BookURL></Proposal>')
conn = parser.parse_offers(connecting, "KHV-MOW", date(2026, 10, 16))
check("пересадка — одно предложение", len(conn) == 1, str(len(conn)))
if conn:
    c = conn[0]
    check("пересадка: маршрут KHV-MOW, рейсы склеены",
          c.route == "KHV-MOW" and c.flight_number == "SU 5601 + SU 1402",
          f"{c.route} {c.flight_number}")
    check("пересадка: мест по самому загруженному сегменту", c.avail_qty == 3,
          str(c.avail_qty))
    check("пересадка: вылет первого, прилёт последнего",
          (c.depart_time, c.arrive_time) == ("08:00", "12:30"),
          f"{c.depart_time}-{c.arrive_time}")

not_subsidy = _resp(
    '<Proposal Total="30000"><Flights><Flight Code="SU" Num="1" '
    'Departure="16.10.2026T08:00" FareCode="YFLEX" MRID="0" AvailQty="9"/>'
    '</Flights></Proposal>')
check("MRID=0 и чужой FareCode — не субсидия",
      parser.parse_offers(not_subsidy, "KHV-MOW", date(2026, 10, 16)) == [])
check("…но виден без фильтра",
      len(parser.parse_offers(not_subsidy, "KHV-MOW", date(2026, 10, 16),
                              subsidized_only=False)) == 1)

config.PERSON_ID = "sub 42"
tagged = parser.parse_offers(xml, "KHV-MOW", date(2026, 10, 15))
config.PERSON_ID = ""
check("PersonID дописывается в глубокую ссылку",
      all(o.book_url.endswith("&PersonID=sub%2042") for o in tagged),
      tagged[0].book_url if tagged else "")

# Дату парсер берёт из ответа, а не из запроса (аргумент — только fallback),
# поэтому берём ответ на другую дату через fetcher, как в реальном прогоне.
other_xml = Fetcher(dry_run=True, save_raw=False).fetch("KHV", "MOW", date(2026, 10, 2))
other_day = parser.parse_offers(other_xml, "KHV-MOW", date(2026, 10, 2))
check("дата берётся из ответа, а не из запроса",
      other_day and all(o.depart_date == "2026-10-02" for o in other_day),
      str({o.depart_date for o in other_day}))
check("ключи разных дат не совпадают",
      not (set(keys) & {o.key() for o in other_day}))

section("2. Fetcher")
f = Fetcher(dry_run=True, save_raw=False)
led = f.fetch("KHV", "LED", date(2026, 10, 5))
led_offers = parser.parse_offers(led, route="KHV-LED", depart_date=date(2026, 10, 5))
check("фикстура подставляет запрошенный маршрут",
      led_offers and all(o.route == "KHV-LED" for o in led_offers),
      str({o.route for o in led_offers}))
check("фикстура подставляет запрошенную дату",
      led_offers and all(o.depart_date == "2026-10-05" for o in led_offers),
      str({o.depart_date for o in led_offers}))
check("категория aaa в dry-run — пустой ответ, как у партнёра",
      parser.parse_offers(f.fetch("KHV", "MOW", date(2026, 10, 5), "aaa"),
                          "KHV-MOW", date(2026, 10, 5)) == [])
params = Fetcher._params("KHV", "MOW", date(2026, 10, 15), "mla")
check("параметры запроса как в рабочем curl",
      params == {"depCity": "KHV", "destCity": "MOW", "depDate": "1510",
                 "mla": "1", "PartnerID": config.PARTNER_ID}, str(params))
check("адрес метода с SASSirenaFares (регистр важен)",
      "/SASSirenaFares.asmx/getFaresByFOP_Ex3" in config.API_URL, config.API_URL)

section("3. База данных")
# В живом ответе у всех трёх рейсов по 9 мест; для проверки алертов делаем
# исходное состояние разнообразнее: SU 6298 — 2 места, SU 5807 — 0.
for o in offers:
    o.avail_qty = {"SU 6298": 2, "SU 5807": 0}.get(o.flight_number, o.avail_qty)
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
    if clone.flight_number == "SU 1719":
        clone.avail_qty = 2          # 9 -> 2 : падение на 7 + мало мест
    elif clone.flight_number == "SU 5807":
        clone.avail_qty = 4          # 0 -> 4 : появились места
    elif clone.flight_number == "SU 6298":
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
check("цена в алерте отформатирована", "8 099 RUB" in restock_msg, restock_msg)

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
check("в отчёте есть номер рейса", "SU 1719" in html_text)
check("в отчёте есть блок событий", "restock" in html_text)
check("в отчёте есть ссылка на бронирование", "avia.biletdv.ru/Order.aspx" in html_text)

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
      "href='https://avia.biletdv.ru/Order.aspx" not in demo_html)

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
check("данные на витрину попали", "KHV" in live_site and "SU 1719" in live_site)

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

section("8. API подписки (subscribe_api.py)")
import json as _json            # noqa: E402
import threading as _threading  # noqa: E402
import urllib.error as _urlerr  # noqa: E402
import urllib.request as _urlreq  # noqa: E402
import subscribe_api            # noqa: E402

check("валидный e-mail проходит", subscribe_api.valid_contact("a@b.ru"))
check("валидный @telegram проходит", subscribe_api.valid_contact("@kirill_test"))
check("мусорный контакт отклонён", not subscribe_api.valid_contact("не контакт"))

try:
    subscribe_api.parse_subscription(b'{"contact":""}')
    check("пустой contact отклонён", False, "исключение не выброшено")
except ValueError:
    check("пустой contact отклонён", True)

sub_db = Database(TMP / "subs.sqlite3")
sub_server = subscribe_api.Server(("127.0.0.1", 0), subscribe_api.Handler, db=sub_db)
sub_port = sub_server.server_address[1]
_threading.Thread(target=sub_server.serve_forever, daemon=True).start()


def _post(payload, path="/api/subscribe"):
    req = _urlreq.Request(f"http://127.0.0.1:{sub_port}{path}",
                          data=_json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _urlreq.urlopen(req, timeout=2) as r:
            return r.status, _json.loads(r.read())
    except _urlerr.HTTPError as e:
        return e.code, _json.loads(e.read())


status, body = _post({"contact": "test@example.com", "origin": "khv", "destination": "mow"})
check("сервер принимает подписку", status == 200 and body.get("ok") is True, str((status, body)))

status, _ = _post({"contact": "мусор"})
check("сервер отклоняет невалидный контакт", status == 400, str(status))

status, _ = _post({"contact": "x@y.ru"}, path="/api/nowhere")
check("неизвестный путь — 404", status == 404, str(status))

stored = sub_db.subscriptions_for_route("KHV", "MOW")
check("подписка сохранена в БД с нужным маршрутом",
      len(stored) == 1 and stored[0]["contact"] == "test@example.com", str(stored))

statuses = [_post({"contact": f"flood{i}@b.ru"})[0] for i in range(10)]
check("анти-спам режет частые запросы", 429 in statuses, str(statuses))

sub_server.shutdown()

section("9. Рассылка подписчикам")
from alerts import Alert  # noqa: E402

notify_db = Database(TMP / "notify.sqlite3")
sub_khv_mow = notify_db.add_subscription("route@example.com", "KHV", "MOW")
sub_any = notify_db.add_subscription("any@example.com")
sub_other = notify_db.add_subscription("other@example.com", "VVO", "MOW")
sub_tg = notify_db.add_subscription("@kirill_test", "KHV", "MOW")

restock = Alert(flight_key="KHV-MOW|2026-10-05|SU1|PZZSOC|", alert_type="restock",
                severity="critical", title="Появились места: KHV-MOW 2026-10-05 · SU 1",
                message="Было 0, стало 4.", prev_qty=0, new_qty=4,
                book_url="https://biletdv.ru/book/1", origin="KHV", destination="MOW")
soldout = Alert(flight_key="KHV-LED|2026-10-06|SU2|PZZSOC|", alert_type="soldout",
                severity="warning", title="Мест не осталось", message="Было 2, стало 0.",
                origin="KHV", destination="LED")

sent_mail: list[tuple[str, str, str]] = []


def _fake_email(subject, body, to=None):
    sent_mail.append((to, subject, body))
    return True


dry_mgr = AlertManager(notify_db, dry_run=True)
check("dry-run: писем нет и подписки не гасятся",
      dry_mgr.notify_subscribers([restock]) == 0
      and len(notify_db.pending_subscriptions_matching("KHV", "MOW")) == 3)

live_mgr = AlertManager(notify_db, dry_run=False)
live_mgr._send_email = _fake_email  # type: ignore[method-assign]
_smtp_host, config.SMTP_HOST = config.SMTP_HOST, "smtp.test"
try:
    n = live_mgr.notify_subscribers([restock, soldout])
finally:
    config.SMTP_HOST = _smtp_host

recipients = sorted(m[0] for m in sent_mail)
check("уведомлены подписчики маршрута и «любого направления»",
      n == 2 and recipients == ["any@example.com", "route@example.com"], str(recipients))
check("письмо содержит ссылку на покупку и нужный маршрут",
      all("biletdv.ru/book/1" in m[2] and "KHV—MOW" in m[1] for m in sent_mail))
check("soldout подписчикам не рассылается",
      not any("Мест не осталось" in m[2] for m in sent_mail))
pending_ids = {r["id"] for r in notify_db.pending_subscriptions_matching("KHV", "MOW")}
check("уведомлённые подписки погашены, Telegram-ник ждёт",
      pending_ids == {sub_tg}, str(pending_ids))
check("чужой маршрут не задет",
      [r["id"] for r in notify_db.pending_subscriptions_matching("VVO", "MOW")] == [sub_other])

sent_mail.clear()
config.SMTP_HOST = "smtp.test"
try:
    live_mgr.notify_subscribers([restock])
finally:
    config.SMTP_HOST = _smtp_host
check("повторное событие не шлёт второе письмо", sent_mail == [], str(sent_mail))

offer = parser.FlightOffer(route="KHV-MOW", origin="KHV", destination="MOW",
                           depart_date="2026-10-05", avail_qty=3)
check("evaluate проставляет маршрут в алерт",
      all(a.origin == "KHV" and a.destination == "MOW"
          for a in evaluate(offer, {"avail_qty": 0, "price": 0})))

# --------------------------------------------------------------------------
print(f"\n{'=' * 52}")
print(f"пройдено: {PASSED}   провалено: {FAILED}")
print(f"отчёт: {path}")
print(f"временные файлы: {TMP}")
sys.exit(1 if FAILED else 0)
