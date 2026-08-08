#!/usr/bin/env python3
"""
Оркестратор сбора: обходит маршруты × даты, парсит, сохраняет, шлёт алерты.

Запуск:
    python collector.py                      # режим из config (по умолчанию dry-run)
    python collector.py --live               # реальные запросы к API
    python collector.py --routes KHV-MOW     # только один маршрут
    python collector.py --date-from 2026-10-01 --date-to 2026-10-07
    python collector.py --report             # после сбора собрать HTML-отчёт

Cron (раз в 15 минут):
    */15 * * * * cd /opt/subsidy && /opt/subsidy/.venv/bin/python collector.py --live --report >> data/cron.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

import config
from alerts import AlertManager, evaluate
from db import Database
from fetcher import FetchError, Fetcher
from parser import ParseError, parse_offers


def setup_logging(verbose: bool) -> None:
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        ],
    )


log = logging.getLogger("collector")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Сбор наличия субсидированных мест")
    p.add_argument("--live", action="store_true",
                   help="реальные HTTP-запросы вместо фикстуры")
    p.add_argument("--dry-run", action="store_true",
                   help="принудительно фикстура (перекрывает --live)")
    p.add_argument("--routes", nargs="*", default=None,
                   help="маршруты вида KHV-MOW; по умолчанию — все из config")
    p.add_argument("--date-from", default=None)
    p.add_argument("--date-to", default=None)
    p.add_argument("--report", action="store_true",
                   help="сгенерировать служебный HTML-отчёт после сбора")
    p.add_argument("--site", action="store_true",
                   help="пересобрать публичную витрину поиска (index.html)")
    p.add_argument("--no-alerts", action="store_true", help="не отправлять алерты")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def resolve_routes(arg: list[str] | None):
    if not arg:
        return config.ROUTES
    out = []
    for item in arg:
        origin, _, dest = item.upper().partition("-")
        if not origin or not dest:
            raise SystemExit(f"неверный формат маршрута: {item} (нужно KHV-MOW)")
        out.append(config.Route(origin, dest))
    return out


def resolve_dates(date_from: str | None, date_to: str | None) -> list[date]:
    if not date_from and not date_to:
        return config.date_range()
    start = date.fromisoformat(date_from) if date_from else config.DATE_FROM
    end = date.fromisoformat(date_to) if date_to else start
    if end < start:
        raise SystemExit("--date-to раньше, чем --date-from")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run(args: argparse.Namespace) -> int:
    # --dry-run сильнее --live; без флагов берём значение из config.
    if args.dry_run:
        dry_run = True
    elif args.live:
        dry_run = False
    else:
        dry_run = config.DRY_RUN

    routes = resolve_routes(args.routes)
    dates = resolve_dates(args.date_from, args.date_to)

    log.info("режим: %s | маршрутов: %d | дат: %d | всего запросов: %d",
             "DRY-RUN (фикстура)" if dry_run else "LIVE",
             len(routes), len(dates), len(routes) * len(dates))

    db = Database()
    fetcher = Fetcher(dry_run=dry_run)
    manager = AlertManager(db, dry_run=dry_run or args.no_alerts)
    run_id = db.start_run(dry_run)

    n_requests = n_offers = n_errors = 0
    pending_alerts = []

    for route in routes:
        for day in dates:
            n_requests += 1
            try:
                xml_text = fetcher.fetch(route.origin, route.destination, day)
                offers = parse_offers(xml_text, route=str(route), depart_date=day)
            except (FetchError, ParseError) as exc:
                n_errors += 1
                log.error("%s %s — %s", route, day, exc)
                continue

            if not offers:
                log.debug("%s %s — субсидированных тарифов нет", route, day)
                continue

            prev = db.latest_by_key([o.key() for o in offers])
            for offer in offers:
                pending_alerts.extend(evaluate(offer, prev.get(offer.key())))

            db.save_observations(offers)
            n_offers += len(offers)
            seats = sum(o.avail_qty for o in offers)
            log.info("%s %s — тарифов: %d, мест суммарно: %d",
                     route, day, len(offers), seats)

    sent = [] if args.no_alerts else manager.process(pending_alerts)

    db.finish_run(run_id, n_requests, n_offers, n_errors,
                  note=f"alerts={len(sent)}")
    log.info("итого: запросов %d, тарифов %d, ошибок %d, алертов %d",
             n_requests, n_offers, n_errors, len(sent))

    if args.report:
        import report
        log.info("отчёт: %s", report.build())

    if args.site:
        import webapp
        log.info("витрина: %s", webapp.build())

    return 1 if n_errors and not n_offers else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except KeyboardInterrupt:
        log.warning("прервано пользователем")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
