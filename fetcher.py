"""
HTTP-клиент к партнёрскому API: метод getFaresByFOP_Ex3 по HTTP GET.

Особенности:
  * DRY_RUN — вместо сети читает fixtures/biletdv_*.xml (и позволяет
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

    def fetch(self, origin: str, destination: str, depart_date: date,
              category: str = "mla") -> str:
        """Текст XML-ответа по одному направлению, дате и категории пассажира."""
        if self.dry_run:
            return self._fetch_fixture(origin, destination, depart_date, category)
        return self._fetch_http(origin, destination, depart_date, category)

    # ---------------------------------------------------------------- private

    def _fetch_fixture(self, origin: str, destination: str, depart_date: date,
                       category: str) -> str:
        # Фикстуры — настоящие ответы партнёра на KHV-MOW 15.10: по mla три
        # рейса, по aaa пусто. Подставляем запрошенные город и дату, чтобы
        # одна фикстура обслуживала все маршруты и даты в dry-run.
        path = Path(config.FIXTURE_PATH if category == "mla"
                    else config.FIXTURE_AAA_PATH)
        if not path.exists():
            raise FetchError(f"фикстура не найдена: {path}")
        text = path.read_text(encoding="utf-8")
        text = (text
                .replace('Origin="KHV"', f'Origin="{origin}"')
                .replace("15.10.2026", depart_date.strftime("%d.%m.%Y")))
        if destination != "MOW":  # SVO — аэропорт Москвы, для других городов
            text = text.replace('Destination="SVO"', f'Destination="{destination}"')
        log.debug("dry-run: фикстура для %s-%s %s %s",
                  origin, destination, depart_date, category)
        return text

    @staticmethod
    def _params(origin: str, destination: str, depart_date: date,
                category: str) -> dict[str, str]:
        # depDate — ДДММ без года, как в примере партнёра и в проверенном
        # запросе (1510 → рейсы 15.10.2026).
        return {
            "depCity": origin,
            "destCity": destination,
            "depDate": depart_date.strftime("%d%m"),
            category: "1",
            "PartnerID": config.PARTNER_ID,
        }

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_ts
        wait = config.REQUEST_DELAY_SEC - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.time()

    def _fetch_http(self, origin: str, destination: str, depart_date: date,
                    category: str) -> str:
        last_error: Exception | None = None

        for attempt in range(1, config.HTTP_RETRIES + 1):
            self._throttle()
            try:
                resp = self._session.get(
                    config.API_URL,
                    params=self._params(origin, destination, depart_date, category),
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

            # Не resp.text: без charset в заголовке requests декодирует
            # text/xml как latin-1, и кириллица в References ломается.
            text = resp.content.decode("utf-8", errors="replace")
            if self.save_raw:
                self._dump(origin, destination, depart_date, category, text)
            return text

        raise FetchError(
            f"не удалось получить {origin}-{destination} {depart_date} {category} "
            f"за {config.HTTP_RETRIES} попыток: {last_error}"
        )

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(config.HTTP_BACKOFF ** attempt)

    @staticmethod
    def _dump(origin: str, destination: str, depart_date: date, category: str,
              text: str) -> None:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        name = f"{origin}-{destination}_{depart_date.isoformat()}_{category}_{stamp}.xml"
        try:
            (RAW_DIR / name).write_text(text, encoding="utf-8")
        except OSError as exc:
            log.warning("не удалось сохранить сырой ответ: %s", exc)
