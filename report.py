#!/usr/bin/env python3
"""
HTML-дашборд по данным из БД. Один самодостаточный файл, без внешних зависимостей.

Оформление — тёмное «аэропортовое табло» с янтарным акцентом: тот же язык,
что и в лендинге, чтобы внутренний монитор и публичная витрина не расходились.

Запуск:
    python report.py               # data/report.html
    python report.py --out /var/www/html/index.html
"""
from __future__ import annotations

import argparse
import html
from datetime import datetime, timezone
from pathlib import Path

import config
from db import Database


CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#232b36;--txt:#e6edf3;--dim:#8b949e;
      --amber:#f0b429;--green:#3fb950;--red:#f85149;--orange:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
     font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:14px;
       border-bottom:2px solid var(--amber);padding-bottom:14px;margin-bottom:24px}
h1{font-size:22px;margin:0;letter-spacing:.06em;text-transform:uppercase}
h1 .dot{color:var(--amber)}
.meta{color:var(--dim);font-size:13px;margin-left:auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
       gap:12px;margin-bottom:28px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.09em}
.card .v{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:4px}
.card .v.amber{color:var(--amber)}.card .v.green{color:var(--green)}
.card .v.red{color:var(--red)}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
   margin:30px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--panel);
      border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
   color:var(--dim);padding:10px 12px;background:#11161d;border-bottom:1px solid var(--line)}
td{padding:10px 12px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1b222c}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.seats{font-weight:700}
.seats.ok{color:var(--green)}.seats.low{color:var(--orange)}.seats.none{color:var(--red)}
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;
       letter-spacing:.05em;text-transform:uppercase}
.badge.critical{background:rgba(63,185,80,.15);color:var(--green)}
.badge.warning{background:rgba(210,153,34,.15);color:var(--orange)}
.badge.info{background:rgba(139,148,158,.15);color:var(--dim)}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
.demo{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em;
      border:1px dashed var(--line);border-radius:4px;padding:2px 6px}
.banner{background:rgba(210,153,34,.10);border:1px solid var(--orange);
        border-radius:8px;padding:12px 16px;margin-bottom:22px;font-size:13px}
.banner b{color:var(--orange)}
.empty{color:var(--dim);padding:22px;background:var(--panel);
       border:1px solid var(--line);border-radius:8px}
footer{margin-top:36px;color:var(--dim);font-size:12px;
       border-top:1px solid var(--line);padding-top:14px}
"""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _seat_class(qty: int) -> str:
    if qty <= 0:
        return "none"
    return "low" if qty <= config.ALERTS.low_seats_threshold else "ok"


def _fmt_price(value, currency: str = "RUB") -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    if v <= 0:
        return "—"
    sym = "₽" if (currency or "RUB").upper() == "RUB" else f" {currency}"
    return f"{v:,.0f}".replace(",", " ") + sym


def _fmt_ts(value: str) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return _esc(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%d.%m %H:%M UTC")


def _book_cell(book_url: str | None, qty: int, is_demo: bool) -> str:
    """
    Кнопка перехода к покупке.

    Ссылку показываем только если она пришла от партнёра, выглядит как http(s)
    и места действительно есть. В демо-режиме ссылка заведомо ведёт в никуда —
    вместо неё метка «демо».
    """
    url = (book_url or "").strip()
    if is_demo:
        return "<span class='demo'>демо</span>" if url else ""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    if qty <= 0:
        return "<span class='demo'>мест нет</span>"
    return (f"<a href='{_esc(url)}' target='_blank' rel='noopener nofollow'>"
            f"купить</a>")


def build(out_path: Path | str | None = None, db: Database | None = None) -> Path:
    db = db or Database()
    rows = db.current_state()
    alerts = db.recent_alerts(limit=25)
    runs = db.last_runs(limit=1)

    total_seats = sum(int(r["avail_qty"] or 0) for r in rows)
    with_seats = sum(1 for r in rows if int(r["avail_qty"] or 0) > 0)
    routes = sorted({r["route"] for r in rows if r["route"]})
    last_run = runs[0] if runs else None
    # В dry-run данные взяты из фикстуры: ссылки на бронирование ведут
    # в никуда, кликать их нельзя — иначе табло вводит в заблуждение.
    is_demo = bool(last_run and last_run["dry_run"])

    parts: list[str] = [
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_esc(config.REPORT_TITLE)}</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<header><h1>Табло субсидий<span class='dot'>.</span></h1>",
        f"<div class='meta'>обновлено {_fmt_ts(datetime.now(timezone.utc).isoformat())}"
        + (f" · режим {'DRY-RUN' if last_run and last_run['dry_run'] else 'LIVE'}"
           if last_run else "")
        + "</div></header>",

        "<div class='cards'>",
        f"<div class='card'><div class='k'>Рейсов в мониторинге</div>"
        f"<div class='v'>{len(rows)}</div></div>",
        f"<div class='card'><div class='k'>С местами сейчас</div>"
        f"<div class='v green'>{with_seats}</div></div>",
        f"<div class='card'><div class='k'>Мест суммарно</div>"
        f"<div class='v amber'>{total_seats}</div></div>",
        f"<div class='card'><div class='k'>Направлений</div>"
        f"<div class='v'>{len(routes)}</div></div>",
        "</div>",
    ]

    if is_demo:
        parts.append(
            "<div class='banner'><b>Демонстрационные данные.</b> Сбор шёл в режиме "
            "dry-run на тестовой фикстуре, а не по живому API партнёра. "
            "Номера рейсов, наличие мест и цены вымышлены, ссылки на бронирование "
            "нерабочие и поэтому отключены. Для боевых данных: "
            "<code>collector.py --live</code>.</div>"
        )

    # ---- таблица наличия
    parts.append("<h2>Наличие мест по рейсам</h2>")
    if not rows:
        parts.append("<div class='empty'>Данных пока нет — запустите collector.py</div>")
    else:
        parts.append(
            "<table><thead><tr>"
            "<th>Направление</th><th>Дата</th><th>Вылет</th><th>Рейс</th>"
            "<th>Тариф</th><th>Мест</th><th>Цена</th><th>Обновлено</th><th></th>"
            "</tr></thead><tbody>"
        )
        for r in rows:
            qty = int(r["avail_qty"] or 0)
            book = _book_cell(r["book_url"], qty, is_demo)
            parts.append(
                "<tr>"
                f"<td class='mono'>{_esc(r['route'])}</td>"
                f"<td>{_esc(r['depart_date'])}</td>"
                f"<td class='mono'>{_esc(r['depart_time'] or '—')}</td>"
                f"<td class='mono'>{_esc(r['flight_number'] or '—')}</td>"
                f"<td class='mono'>{_esc(r['fare_code'] or '—')}</td>"
                f"<td class='seats {_seat_class(qty)}'>{qty}</td>"
                f"<td>{_fmt_price(r['price'], r['currency'])}</td>"
                f"<td>{_fmt_ts(r['observed_at'])}</td>"
                f"<td>{book}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    # ---- последние события
    parts.append("<h2>Последние события</h2>")
    if not alerts:
        parts.append("<div class='empty'>Событий не было</div>")
    else:
        parts.append(
            "<table><thead><tr><th>Время</th><th>Тип</th>"
            "<th>Было → стало</th><th>Событие</th></tr></thead><tbody>"
        )
        for a in alerts:
            prev, new = a["prev_qty"], a["new_qty"]
            change = f"{prev if prev is not None else '—'} → {new if new is not None else '—'}"
            parts.append(
                "<tr>"
                f"<td>{_fmt_ts(a['created_at'])}</td>"
                f"<td><span class='badge {_esc(a['severity'])}'>{_esc(a['alert_type'])}</span></td>"
                f"<td class='mono'>{_esc(change)}</td>"
                f"<td>{_esc(a['message'])}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    note = ""
    if last_run:
        note = (f"Последний прогон: запросов {last_run['requests']}, "
                f"тарифов {last_run['offers']}, ошибок {last_run['errors']}.")
    parts.append(
        f"<footer>{_esc(note)} Партнёр: БилетДВ, PartnerID {_esc(config.PARTNER_ID)}. "
        f"Порог «мест почти нет»: {config.ALERTS.low_seats_threshold}.</footer>"
    )
    parts.append("</div></body></html>")

    out = Path(out_path or config.REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="HTML-отчёт по наличию мест")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = build(args.out)
    print(f"отчёт готов: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
