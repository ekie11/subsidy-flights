"""
Конфигурация мониторинга субсидированных авиабилетов.

Все значения можно переопределить через переменные окружения (см. .env.example),
чтобы не хранить секреты в репозитории.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SUBSIDY_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("SUBSIDY_DB_PATH", DATA_DIR / "subsidy.sqlite3"))
REPORT_PATH = Path(os.getenv("SUBSIDY_REPORT_PATH", DATA_DIR / "report.html"))
LOG_PATH = Path(os.getenv("SUBSIDY_LOG_PATH", DATA_DIR / "collector.log"))
FIXTURE_PATH = Path(os.getenv("SUBSIDY_FIXTURE", BASE_DIR / "fixtures" / "sample_response.xml"))


# --------------------------------------------------------------------------
# Партнёрский API (БилетДВ)
# --------------------------------------------------------------------------

API_URL = os.getenv("BILETDV_API_URL", "https://api.biletdv.ru/xml/search")
PARTNER_ID = os.getenv("BILETDV_PARTNER_ID", "KirillTest")
API_LOGIN = os.getenv("BILETDV_LOGIN", "")
API_PASSWORD = os.getenv("BILETDV_PASSWORD", "")

# Метод запроса: "GET" (параметры в query) или "POST" (XML-конверт в теле).
# Уточняется по документации партнёра; парсер ответа от этого не зависит.
API_METHOD = os.getenv("BILETDV_API_METHOD", "GET").upper()

HTTP_TIMEOUT = float(os.getenv("SUBSIDY_HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.getenv("SUBSIDY_HTTP_RETRIES", "3"))
HTTP_BACKOFF = float(os.getenv("SUBSIDY_HTTP_BACKOFF", "2.0"))
USER_AGENT = os.getenv("SUBSIDY_UA", f"subsidy-monitor/1.0 (partner={PARTNER_ID})")

# Пауза между запросами, чтобы не упереться в rate limit партнёра.
REQUEST_DELAY_SEC = float(os.getenv("SUBSIDY_REQUEST_DELAY", "1.5"))

# DRY_RUN=1 — читаем фикстуру вместо реального HTTP. По умолчанию включён,
# чтобы случайный запуск без боевого PartnerID не долбил партнёрский API.
DRY_RUN = os.getenv("SUBSIDY_DRY_RUN", "1") not in ("0", "false", "False", "")


# --------------------------------------------------------------------------
# Признаки субсидированного тарифа
# --------------------------------------------------------------------------

# Коды субсидированных тарифов. PZZSOC подтверждён на реальном ответе БилетДВ.
SUBSIDY_FARE_CODES = {
    code.strip().upper()
    for code in os.getenv("SUBSIDY_FARE_CODES", "PZZSOC").split(",")
    if code.strip()
}

# Наличие непустого MRID у тарифа — второй независимый признак субсидии.
# Если True, тариф с MRID считается субсидированным даже при неизвестном FareCode.
TREAT_MRID_AS_SUBSIDY = os.getenv("SUBSIDY_MRID_IS_SUBSIDY", "1") not in ("0", "false", "")


# --------------------------------------------------------------------------
# Что мониторим
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    label: str = ""

    def __str__(self) -> str:
        return f"{self.origin}-{self.destination}"


ROUTES: list[Route] = [
    Route("KHV", "MOW", "Хабаровск — Москва"),
    Route("KHV", "LED", "Хабаровск — Санкт-Петербург"),
]

# Окно дат вылета. Тестовый доступ выдан на 01.10–30.10.2026.
DATE_FROM = date.fromisoformat(os.getenv("SUBSIDY_DATE_FROM", "2026-10-01"))
DATE_TO = date.fromisoformat(os.getenv("SUBSIDY_DATE_TO", "2026-10-30"))


def date_range() -> list[date]:
    """Все даты вылета из окна мониторинга."""
    days = (DATE_TO - DATE_FROM).days
    return [DATE_FROM + timedelta(days=i) for i in range(days + 1)]


# --------------------------------------------------------------------------
# Правила алертов
# --------------------------------------------------------------------------

@dataclass
class AlertConfig:
    # Мест стало <= этого числа (а было больше) — «заканчиваются».
    low_seats_threshold: int = int(os.getenv("SUBSIDY_LOW_SEATS", "3"))
    # Места появились там, где было 0.
    notify_on_restock: bool = True
    # Мест стало 0 (а было больше).
    notify_on_soldout: bool = True
    # Абсолютное падение мест за один цикл, при котором шлём алерт.
    seat_drop_threshold: int = int(os.getenv("SUBSIDY_SEAT_DROP", "5"))
    # Изменение цены в рублях, при котором шлём алерт (0 — не следим за ценой).
    price_change_threshold: float = float(os.getenv("SUBSIDY_PRICE_DELTA", "0"))
    # Не слать один и тот же тип алерта по одному рейсу чаще, чем раз в N минут.
    cooldown_minutes: int = int(os.getenv("SUBSIDY_ALERT_COOLDOWN", "120"))


ALERTS = AlertConfig()

# Каналы доставки. Пустой токен = канал выключен, алерты только в лог и БД.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "harhanovk@gmail.com")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", SMTP_USER)


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------

REPORT_TITLE = os.getenv("SUBSIDY_REPORT_TITLE", "Субсидированные билеты — наличие мест")
REPORT_TIMEZONE = os.getenv("SUBSIDY_TZ", "Asia/Vladivostok")
