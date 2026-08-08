"""
HTTP-клиент к партнёрскому API.

Особенности:
  * DRY_RUN — вместо сети читает fixtures/sample_response.xml (и позволяет
    прогнать весь пайплайн без боевого доступа);
  * ретраи с экспоненциальным backoff на 429/5xx и сетевых ошибках;
  * сохранение сырых ответов на диск (data/raw/) — критично для разбора
    расхождений с партнёром;
  * пауза между запросами, чтобы не поймать rate limit.

Замечание про 403: если запрос уходит из окружения с фильтрующим прокси
(например, из песочницы), партнёрский домен вернёт 403 ещё до API. Это не
ошибка кода — запускать сбор нужно с VPS или локальной машины.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import config

log = logging.getLogger("fetcher")

try:
    import requests
except ImportError:  # requests нужен только для боевого режима
    requests = None  # type: ignore


RAW_DIR = config.DATA_DIR / "raw"


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, dry_run: bool | None = None, save_raw: bool = True):
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run
        self.save_raw = save_raw
        self._last_request_ts = 0.0
        self._session = None
        if not self.dry_run:
            if requests is None:
                raise FetchError("нужен пакет requests: pip install -r requirements.txt")
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": config.USER_AGENT,
                "Accept": "application/xml, text/xml;q=0.9, */*;q=0.5",
            })

    # ----------------------------------------------------------------- public

    def fetch(self, origin: str, destination: str, depart_date: date) -> str:
        """Возвращает текст XML-ответа по одному направлению и дате."""
        if self.dry_run:
            return self._fetch_fixture(origin, destination, depart_date)
        return self._fetch_http(origin, destination, depart_date)

    # ---------------------------------------------------------------- private

    def _fetch_fixture(self, origin: str, destination: str, depart_date: date) -> str:
        path = Path(config.FIXTURE_PATH)
        if not path.exists():
            raise FetchError(f"фикстура не найдена: {path}")
        text = path.read_text(encoding="utf-8")
        # Подставляем запрошенные параметры, чтобы одна фикстура
        # обслуживала все маршруты и даты в dry-run.
        text = (text
                .replace('Origin="KHV"', f'Origin="{origin}"')
                .replace('Destination="MOW"', f'Destination="{destination}"')
                .replace("2026-10-01", depart_date.isoformat()))
        log.debug("dry-run: фикстура для %s-%s %s", origin, destination, depart_date)
        return text

    def _params(self, origin: str, destination: str, depart_date: date) -> dict[str, str]:
        params = {
            "PartnerID": config.PARTNER_ID,
            "Origin": origin,
            "Destination": destination,
            "DepartureDate": depart_date.isoformat(),
            "AdultCount": "1",
            "Currency": "RUB",
        }
        if config.API_LOGIN:
            params["Login"] = config.API_LOGIN
        if config.API_PASSWORD:
            params["Password"] = config.API_PASSWORD
        return params

    def _body(self, origin: str, destination: str, depart_date: date) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<AirSearchRequest>"
            f"<PartnerID>{config.PARTNER_ID}</PartnerID>"
            f"<Origin>{origin}</Origin>"
            f"<Destination>{destination}</Destination>"
            f"<DepartureDate>{depart_date.isoformat()}</DepartureDate>"
            "<AdultCount>1</AdultCount>"
            "<Currency>RUB</Currency>"
            "</AirSearchRequest>"
        )

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_ts
        wait = config.REQUEST_DELAY_SEC - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.time()

    def _fetch_http(self, origin: str, destination: str, depart_date: date) -> str:
        last_error: Exception | None = None

        for attempt in range(1, config.HTTP_RETRIES + 1):
            self._throttle()
            try:
                if config.API_METHOD == "POST":
                    resp = self._session.post(
                        config.API_URL,
                        data=self._body(origin, destination, depart_date).encode("utf-8"),
                        headers={"Content-Type": "application/xml; charset=utf-8"},
                        timeout=config.HTTP_TIMEOUT,
                    )
                else:
                    resp = self._session.get(
                        config.API_URL,
                        params=self._params(origin, destination, depart_date),
                        timeout=config.HTTP_TIMEOUT,
                    )
            except Exception as exc:  # noqa: BLE001 — сетевые ошибки любого рода
                last_error = exc
                log.warning("попытка %s/%s — сетевая ошибка: %s",
                            attempt, config.HTTP_RETRIES, exc)
                self._backoff(attempt)
                continue

            if resp.status_code == 403:
                raise FetchError(
                    "403 Forbidden. Проверьте: (1) активен ли PartnerID, "
                    "(2) не блокирует ли исходящий трафик прокси/файрвол окружения. "
                    "Из песочницы с белым списком доменов запрос не пройдёт — "
                    "запускайте с VPS."
                )
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = FetchError(f"HTTP {resp.status_code}")
                log.warning("попытка %s/%s — HTTP %s",
                            attempt, config.HTTP_RETRIES, resp.status_code)
                self._backoff(attempt)
                continue
            if resp.status_code != 200:
                raise FetchError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            text = resp.text
            if self.save_raw:
                self._dump(origin, destination, depart_date, text)
            return text

        raise FetchError(
            f"не удалось получить {origin}-{destination} {depart_date} "
            f"за {config.HTTP_RETRIES} попыток: {last_error}"
        )

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(config.HTTP_BACKOFF ** attempt)

    @staticmethod
    def _dump(origin: str, destination: str, depart_date: date, text: str) -> None:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        name = f"{origin}-{destination}_{depart_date.isoformat()}_{stamp}.xml"
        try:
            (RAW_DIR / name).write_text(text, encoding="utf-8")
        except OSError as exc:
            log.warning("не удалось сохранить сырой ответ: %s", exc)
