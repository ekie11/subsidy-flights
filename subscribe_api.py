#!/usr/bin/env python3
"""
Приём подписок «сообщить, когда появятся места» — /api/subscribe.

Витрина (webapp.py) и будущий редизайн (design/) — статика без бэкенда,
поэтому у формы подписки был только прототип, который ничего не отправлял
(см. README → «Известные ограничения»). Это минимальный бэкенд для нужного
одного эндпоинта: только stdlib, без новых зависимостей — тот же принцип
простоты, что и у остального проекта.

    python subscribe_api.py                 # слушает 127.0.0.1:8787

Наружу отдаёт nginx (см. README → «Деплой на VPS», location /api/subscribe/),
сам процесс наружу лучше не светить. Держать под systemd или supervisor —
это долгоживущий процесс, а не разовый скрипт вроде collector.py.

Запрос:
    POST /api/subscribe
    {"contact": "user@example.com", "origin": "KHV", "destination": "MOW"}

contact — e-mail или @telegram-ник, обязателен. origin/destination —
необязательны (общая подписка «на любое направление», если не переданы).
"""
from __future__ import annotations

import json
import logging
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from db import Database

log = logging.getLogger("subscribe_api")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TELEGRAM_RE = re.compile(r"^@[a-zA-Z0-9_]{5,32}$")
AIRPORT_RE = re.compile(r"^[A-Z]{3}$")

MAX_BODY_BYTES = 4096

# Анти-спам: не более RATE_LIMIT запросов с одного IP за RATE_WINDOW секунд.
# Хранилище в памяти процесса — на рестарте сбрасывается, это ок для формы
# подписки, а не платёжного API.
RATE_LIMIT = 5
RATE_WINDOW = 60.0
_hits: dict[str, list[float]] = {}


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
    hits.append(now)
    _hits[ip] = hits
    return len(hits) > RATE_LIMIT


def valid_contact(contact: str) -> bool:
    contact = contact.strip()
    return bool(EMAIL_RE.match(contact) or TELEGRAM_RE.match(contact))


def parse_subscription(raw: bytes) -> dict:
    """Разбирает и валидирует тело запроса. Бросает ValueError с текстом
    для пользователя, если что-то не так."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("битый JSON")
    if not isinstance(data, dict):
        raise ValueError("тело запроса должно быть объектом")

    contact = str(data.get("contact", "")).strip()
    if not contact:
        raise ValueError("укажите e-mail или @telegram")
    if not valid_contact(contact):
        raise ValueError("не похоже на e-mail или @telegram")

    origin = str(data.get("origin", "")).strip().upper()
    destination = str(data.get("destination", "")).strip().upper()
    for label, code in (("origin", origin), ("destination", destination)):
        if code and not AIRPORT_RE.match(code):
            raise ValueError(f"{label}: ожидается код аэропорта из 3 букв")

    return {"contact": contact, "origin": origin, "destination": destination}


class Handler(BaseHTTPRequestHandler):
    server_version = "subsidy-subscribe/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 — сигнатура stdlib
        log.info("%s %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Форма может открываться не с того же origin, что и API (например,
        # design/ на GitHub Pages, а API — на VPS); отдаём без ограничения,
        # т.к. эндпоинт не читает и не использует куки/авторизацию.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/subscribe":
            self._send_json(404, {"ok": False, "error": "неизвестный путь"})
            return

        if _rate_limited(self.client_address[0]):
            self._send_json(429, {"ok": False, "error": "слишком часто, попробуйте позже"})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"ok": False, "error": "пустое или слишком большое тело"})
            return
        raw = self.rfile.read(length)

        try:
            sub = parse_subscription(raw)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        sub_id = self.server.db.add_subscription(  # type: ignore[attr-defined]
            sub["contact"], sub["origin"], sub["destination"])
        log.info("новая подписка #%s: %s %s-%s", sub_id, sub["contact"],
                 sub["origin"] or "*", sub["destination"] or "*")
        self._send_json(200, {"ok": True})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, db: Database, **kwargs):
        self.db = db
        super().__init__(*args, **kwargs)


def run(host: str | None = None, port: int | None = None,
        db: Database | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    host = host or config.SUBSCRIBE_HOST
    port = port or config.SUBSCRIBE_PORT
    server = Server((host, port), Handler, db=db or Database())
    log.info("слушаю http://%s:%s — POST /api/subscribe", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
