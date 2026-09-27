"""
Правила алертов и доставка.

Алерт = переход состояния между двумя соседними наблюдениями одного рейса:
  restock   — места появились там, где было 0   (главный коммерческий сигнал)
  soldout   — места кончились
  low       — мест стало <= порога
  drop      — резкое падение количества мест
  price     — заметное изменение цены

Дедупликация — по (flight_key, alert_type) с кулдауном из config.ALERTS.

Кроме служебных каналов (Telegram/e-mail владельца), события restock и new
рассылаются подписчикам с витрины — см. AlertManager.notify_subscribers.
"""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Iterable, Sequence

import config
from db import Database
from parser import FlightOffer

log = logging.getLogger("alerts")


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

# События, о которых сообщаем подписчикам: оба значат «места есть, можно
# покупать». soldout/low/drop подписчику не нужны — он ждёт появления мест.
SUBSCRIBER_ALERT_TYPES = {"restock", "new"}

# Больше рейсов в одном письме подписчику не перечисляем — остальное на сайте.
SUBSCRIBER_MAX_ITEMS = 10


@dataclass
class Alert:
    flight_key: str
    alert_type: str
    severity: str
    title: str
    message: str
    prev_qty: int | None = None
    new_qty: int | None = None
    book_url: str = ""
    origin: str = ""
    destination: str = ""

    def as_text(self) -> str:
        parts = [f"[{self.severity.upper()}] {self.title}", self.message]
        if self.book_url:
            parts.append(self.book_url)
        return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Правила
# --------------------------------------------------------------------------

def _money(value: float, currency: str = "RUB") -> str:
    """Форматирует сумму. Отдельной функцией — чтобы замена разделителя
    тысяч не задевала обычные запятые в тексте сообщения."""
    return f"{value:,.0f}".replace(",", " ") + f" {currency or 'RUB'}"


def _describe(offer: FlightOffer) -> str:
    when = " ".join(x for x in (offer.depart_date, offer.depart_time) if x)
    flight = offer.flight_number or offer.airline or "рейс"
    return f"{offer.route} {when} · {flight}"


def evaluate(offer: FlightOffer, prev_row) -> list[Alert]:
    """Сравнивает текущее предложение с предыдущим наблюдением."""
    cfg = config.ALERTS
    out: list[Alert] = []
    desc = _describe(offer)
    new_qty = offer.avail_qty

    if prev_row is None:
        # Первое наблюдение: сигналим, только если места уже есть.
        if new_qty > 0:
            out.append(Alert(
                flight_key=offer.key(), alert_type="new", severity="info",
                title=f"Новый рейс с местами: {desc}",
                message=f"Мест: {new_qty}. Цена: {_money(offer.price, offer.currency)}",
                prev_qty=None, new_qty=new_qty, book_url=offer.book_url,
                origin=offer.origin, destination=offer.destination,
            ))
        return out

    prev_qty = int(prev_row["avail_qty"])
    prev_price = float(prev_row["price"] or 0)

    if prev_qty == 0 and new_qty > 0 and cfg.notify_on_restock:
        out.append(Alert(
            flight_key=offer.key(), alert_type="restock", severity="critical",
            title=f"Появились места: {desc}",
            message=f"Было 0, стало {new_qty}. "
                    f"Цена: {_money(offer.price, offer.currency)}",
            prev_qty=prev_qty, new_qty=new_qty, book_url=offer.book_url,
            origin=offer.origin, destination=offer.destination,
        ))

    elif prev_qty > 0 and new_qty == 0 and cfg.notify_on_soldout:
        out.append(Alert(
            flight_key=offer.key(), alert_type="soldout", severity="warning",
            title=f"Мест не осталось: {desc}",
            message=f"Было {prev_qty}, стало 0.",
            prev_qty=prev_qty, new_qty=new_qty, book_url=offer.book_url,
            origin=offer.origin, destination=offer.destination,
        ))

    else:
        drop = prev_qty - new_qty
        if drop >= cfg.seat_drop_threshold and new_qty > 0:
            out.append(Alert(
                flight_key=offer.key(), alert_type="drop", severity="warning",
                title=f"Резко убыло мест: {desc}",
                message=f"{prev_qty} → {new_qty} (−{drop}).",
                prev_qty=prev_qty, new_qty=new_qty, book_url=offer.book_url,
                origin=offer.origin, destination=offer.destination,
            ))
        elif new_qty > 0 and new_qty <= cfg.low_seats_threshold < prev_qty:
            out.append(Alert(
                flight_key=offer.key(), alert_type="low", severity="warning",
                title=f"Мест почти нет: {desc}",
                message=f"Осталось {new_qty} (было {prev_qty}).",
                prev_qty=prev_qty, new_qty=new_qty, book_url=offer.book_url,
                origin=offer.origin, destination=offer.destination,
            ))

    if cfg.price_change_threshold > 0 and prev_price > 0:
        delta = offer.price - prev_price
        if abs(delta) >= cfg.price_change_threshold:
            sign = "+" if delta > 0 else "−"
            out.append(Alert(
                flight_key=offer.key(), alert_type="price", severity="info",
                title=f"Изменилась цена: {desc}",
                message=f"{_money(prev_price, offer.currency)} → "
                        f"{_money(offer.price, offer.currency)} "
                        f"({sign}{_money(abs(delta), offer.currency)})",
                prev_qty=prev_qty, new_qty=new_qty, book_url=offer.book_url,
                origin=offer.origin, destination=offer.destination,
            ))

    return out


# --------------------------------------------------------------------------
# Дедупликация и доставка
# --------------------------------------------------------------------------

class AlertManager:
    def __init__(self, db: Database, dry_run: bool | None = None):
        self.db = db
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run

    def _in_cooldown(self, alert: Alert) -> bool:
        minutes = config.ALERTS.cooldown_minutes
        if minutes <= 0:
            return False
        last = self.db.last_alert_at(alert.flight_key, alert.alert_type)
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return False
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_dt < timedelta(minutes=minutes)

    def process(self, alerts: Iterable[Alert]) -> list[Alert]:
        """Фильтрует по кулдауну, пишет в БД, отправляет. Возвращает отправленные."""
        fresh: list[Alert] = []
        for a in alerts:
            if self._in_cooldown(a):
                log.debug("кулдаун: %s / %s", a.flight_key, a.alert_type)
                continue
            fresh.append(a)

        if not fresh:
            return []

        fresh.sort(key=lambda a: -SEVERITY_ORDER.get(a.severity, 0))
        delivered = self.deliver(fresh)
        for a in fresh:
            self.db.save_alert(a.flight_key, a.alert_type, a.severity,
                               f"{a.title} — {a.message}", a.prev_qty,
                               a.new_qty, delivered)
        return fresh

    # ---------------------------------------------------------- подписчики

    def notify_subscribers(self, alerts: Iterable[Alert]) -> int:
        """Шлёт подписчикам письмо о появившихся местах. Возвращает число
        уведомлённых подписок.

        Подписка разовая: после успешной отправки гасится
        (db.mark_subscription_notified), повторно письмо не придёт. Если
        отправить не вышло — подписка остаётся ждать следующего события.
        """
        by_route: dict[tuple[str, str], list[Alert]] = {}
        for a in alerts:
            if a.alert_type in SUBSCRIBER_ALERT_TYPES and a.origin and a.destination:
                by_route.setdefault((a.origin, a.destination), []).append(a)
        if not by_route:
            return 0

        # Одна подписка может подойти под несколько направлений (общая,
        # «на любое направление») — собираем ей все события в одно письмо.
        by_sub: dict[int, tuple] = {}
        for (origin, destination), items in by_route.items():
            for sub in self.db.pending_subscriptions_matching(origin, destination):
                by_sub.setdefault(sub["id"], (sub, []))[1].extend(items)

        notified = 0
        for sub, items in by_sub.values():
            contact = sub["contact"]
            if contact.startswith("@"):
                # Бот не может написать первым по нику: нужен chat_id, а его
                # даёт только /start в боте. Пока такие подписки ждут.
                log.info("подписка #%s (%s): Telegram-рассылка по нику "
                         "не поддерживается, пропускаю", sub["id"], contact)
                continue
            subject, body = _subscriber_letter(items)
            if self.dry_run:
                log.info("dry-run: письмо подписчику #%s (%s) не отправлено: %s",
                         sub["id"], contact, subject)
                continue
            if not config.SMTP_HOST:
                log.warning("SMTP не настроен — подписчик #%s ждёт", sub["id"])
                continue
            if self._send_email(subject, body, to=contact):
                self.db.mark_subscription_notified(sub["id"])
                notified += 1
                log.info("подписчик #%s уведомлён: %d рейсов", sub["id"], len(items))
        return notified

    # -------------------------------------------------------------- каналы

    def deliver(self, alerts: Sequence[Alert]) -> bool:
        body = "\n\n".join(a.as_text() for a in alerts)
        for a in alerts:
            log.info("ALERT %s | %s — %s", a.alert_type, a.title, a.message)

        if self.dry_run:
            log.info("dry-run: доставка отключена (%d алертов)", len(alerts))
            return False

        ok = False
        if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
            ok = self._send_telegram(body) or ok
        if config.SMTP_HOST and config.ALERT_EMAIL_TO:
            ok = self._send_email(f"Субсидии: {len(alerts)} событий", body) or ok
        if not ok:
            log.warning("каналы доставки не настроены — алерты только в БД и логе")
        return ok

    @staticmethod
    def _send_telegram(text: str) -> bool:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=20) as resp:
                payload = json.loads(resp.read().decode())
            if not payload.get("ok"):
                log.error("telegram отказал: %s", payload)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("telegram недоступен: %s", exc)
            return False

    @staticmethod
    def _send_email(subject: str, body: str, to: str | None = None) -> bool:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.ALERT_EMAIL_FROM or config.SMTP_USER
        msg["To"] = to or config.ALERT_EMAIL_TO
        msg.set_content(body)
        try:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("SMTP недоступен: %s", exc)
            return False


def _subscriber_letter(items: Sequence[Alert]) -> tuple[str, str]:
    """Тема и текст письма подписчику — без служебных меток серьёзности."""
    routes = sorted({f"{a.origin}—{a.destination}" for a in items})
    subject = "Появились субсидированные места: " + ", ".join(routes)
    lines = ["Здравствуйте! По вашей подписке появились места по субсидированному тарифу.", ""]
    for a in items[:SUBSCRIBER_MAX_ITEMS]:
        lines.append(a.title.split(": ", 1)[-1])
        lines.append(a.message)
        if a.book_url:
            lines.append(a.book_url)
        lines.append("")
    if len(items) > SUBSCRIBER_MAX_ITEMS:
        lines += [f"И ещё рейсов: {len(items) - SUBSCRIBER_MAX_ITEMS} — смотрите на сайте.", ""]
    lines += [
        "Субсидированных мест мало, и расходятся они быстро.",
        "Это разовое уведомление: подписка выполнена, больше писем не будет.",
        "Чтобы следить снова, оформите подписку на сайте ещё раз.",
    ]
    return subject, "\n".join(lines)
