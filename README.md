# Мониторинг субсидированных авиабилетов

Сервис следит за наличием мест по субсидированным тарифам (дальневосточная
субсидия и молодёжная до 23 лет), сохраняет историю, шлёт алерты при появлении
и исчезновении мест и отдаёт HTML-табло. Переход на покупку — по партнёрской
ссылке с вшитым `PartnerID`.

Партнёр: **БилетДВ** (biletdv.ru), метод `getFaresByFOP_Ex3`
(`http://fares.biletdv.ru/SASSirenaFares.asmx/getFaresByFOP_Ex3`, HTTP GET).
Один запрос — маршрут, дата и одна категория пассажира (`mla` — молодёжь,
`aaa` — взрослый с пропиской ДФО). Партнёр отдаёт только субсидированные
тарифы; признаки субсидии — код тарифа (`PZZSOC`, `PSOCDO`, `UBDOWZZ`,
`RBDOWD`) и ненулевой `MRID`. Количество мест — `AvailQty`, ссылка на
бронирование — `BookURL` предложения.

---

## Файлы

| Файл | Назначение |
|---|---|
| `config.py` | Все настройки; переопределяются через переменные окружения |
| `fetcher.py` | HTTP-клиент: ретраи, throttling, dry-run на фикстуре, дамп сырых ответов |
| `parser.py` | Разбор XML в `FlightOffer`; фильтр субсидированных тарифов |
| `db.py` | SQLite: наблюдения (append-only), алерты, прогоны |
| `alerts.py` | Правила переходов состояния + доставка в Telegram/e-mail |
| `collector.py` | Оркестратор: маршруты × даты → парсинг → БД → алерты |
| `report.py` | Служебное HTML-табло для себя: что собрано, какие были события |
| `webapp.py` | **Публичная витрина поиска** — статический сайт из данных БД |
| `assets/webapp.css`, `assets/webapp.js` | Стили и клиентский код витрины (правятся отдельно от Python, `webapp.py` вшивает их в страницу при сборке) |
| `subscribe_api.py` | Приём формы «сообщить, когда появятся места» — `/api/subscribe`, отдельный процесс (см. «Подписка на уведомления» ниже) |
| `cities.py` | Справочник аэропортов ДФО и категорий субсидий |
| `selftest.py` | Автотест пайплайна без сети (78 проверок) |
| `uitest.js` | UI-тест витрины в jsdom (44 проверки) |
| `fixtures/biletdv_mla_KHV-MOW_1510.xml` | Настоящий ответ API (mla, KHV-MOW, 15.10.2026) |
| `fixtures/biletdv_empty.xml` | Настоящий пустой ответ (aaa на ту же дату) |

---

## Быстрый старт

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python selftest.py                   # проверка пайплайна, ~1 сек, без сети
python collector.py --report --site  # прогон на фикстуре + отчёт + витрина
open data/index.html                 # публичная витрина поиска
open data/report.html                # служебное табло для себя
```

Тест интерфейса витрины (нужен Node):

```bash
npm install jsdom
node uitest.js data/index.html
```

## Боевой запуск

```bash
cp .env.example .env        # вписать боевой PartnerID и доступы
set -a; source .env; set +a

python collector.py --live --report
```

Ограничить объём на первый прогон:

```bash
python collector.py --live --routes KHV-MOW --date-from 2026-10-01 --date-to 2026-10-03 -v
```

### Флаги `collector.py`

| Флаг | Что делает |
|---|---|
| `--live` | реальные запросы вместо фикстуры |
| `--dry-run` | принудительно фикстура (сильнее `--live`) |
| `--routes KHV-MOW KHV-LED` | ограничить маршруты |
| `--date-from` / `--date-to` | ограничить окно дат |
| `--report` | собрать HTML после сбора |
| `--no-alerts` | не отправлять уведомления |
| `-v` | подробный лог |

---

## Деплой на VPS

Из песочницы с фильтрующим прокси запрос к `biletdv.ru` возвращает **403** —
это блокировка окружения, а не API. Сбор запускать с VPS или локальной машины
с открытым исходящим доступом.

```bash
sudo apt update && sudo apt install -y python3-venv sqlite3
sudo mkdir -p /opt/subsidy && sudo chown $USER /opt/subsidy
# скопировать файлы проекта в /opt/subsidy
cd /opt/subsidy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # SUBSIDY_DRY_RUN=0, боевой PartnerID
chmod 600 .env
```

Проверка доступа к API до всего остального:

```bash
curl -sS -o /tmp/probe.xml -w '%{http_code}\n' \
  "http://fares.biletdv.ru/SASSirenaFares.asmx/getFaresByFOP_Ex3?depCity=KHV&destCity=MOW&depDate=1510&mla=1&PartnerID=KirillTest"
head -c 500 /tmp/probe.xml
```

Регистр в пути важен: `SasSirenaFares.asmx` отвечает 404. Дата — `ДДММ`
без года. На неверные параметры API отвечает не ошибкой, а
`isSuccess=true` с пустым `Offers`.

### cron

```cron
17 * * * * cd /opt/subsidy && set -a && . ./.env && set +a && \
  .venv/bin/python collector.py --live --report >> data/cron.log 2>&1
```

Витрина — статический HTML без бэкенда: весь поиск, фильтры и календарь
работают на клиенте, данные вшиты в страницу при сборке. Раздавать любым nginx:

```nginx
server {
  listen 80;
  root /opt/subsidy/data;
  index index.html;
  location / { try_files $uri $uri/ =404; add_header Cache-Control "max-age=300"; }
  location /report.html { auth_basic "admin"; auth_basic_user_file /etc/nginx/.htpasswd; }
  location /api/subscribe { proxy_pass http://127.0.0.1:8787; }
}
```

`report.html` — служебный, наружу его лучше закрыть паролем: он показывает
внутреннюю кухню (частоту сбора, историю алертов, PartnerID).

В cron добавьте `--site`, чтобы витрина пересобиралась вместе с отчётом.
`location /api/subscribe` нужен, только если включена подписка — см. ниже.

---

## Подписка на уведомления

Форма «сообщить, когда появятся места» на витрине по умолчанию честно
говорит, что не подключена — так и должно быть везде, где `subscribe_api.py`
не запущен (в первую очередь GitHub Pages: это статика, бэкенду там неоткуда
взяться). На VPS, где есть постоянно работающий процесс, включить можно:

```bash
# .env
SUBSIDY_SUBSCRIBE_ENABLED=1
SUBSIDY_SUBSCRIBE_HOST=127.0.0.1
SUBSIDY_SUBSCRIBE_PORT=8787
```

`subscribe_api.py` — долгоживущий процесс (не разовый скрипт вроде
`collector.py`), держите его под systemd:

```ini
# /etc/systemd/system/subsidy-subscribe.service
[Unit]
Description=Приём подписок subsidy-flights
After=network.target

[Service]
WorkingDirectory=/opt/subsidy
EnvironmentFile=/opt/subsidy/.env
ExecStart=/opt/subsidy/.venv/bin/python subscribe_api.py
Restart=on-failure
User=subsidy

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now subsidy-subscribe
```

Добавьте `location /api/subscribe` в nginx (пример выше) и пересоберите
витрину (`collector.py --site` или `webapp.py`) — флаг `SUBSIDY_SUBSCRIBE_ENABLED`
читается при сборке страницы, а не в рантайме браузера.

Подписки лежат в таблице `subscriptions` той же SQLite, что и наблюдения —
отдельной БД под них нет. Поэтому `collector.py` и `subscribe_api.py` должны
смотреть в один и тот же файл (`SUBSIDY_DB_PATH`), то есть работать на одной
машине: сборщик в GitHub Actions подписок с VPS не видит.

**Рассылка.** Когда сборщик замечает, что места появились (`restock`) или
нашёлся новый рейс с местами (`new`), он пишет подписчикам этого направления
и подписчикам «на любое направление». Письмо уходит через тот же SMTP, что и
служебные алерты (`SMTP_HOST` и т.д.). В одном письме собраны все подходящие
рейсы, у каждого ссылка на покупку. Подписка разовая: после успешной отправки
она гасится (`notified_at`). Если отправка не удалась или SMTP не настроен,
подписка ждёт следующего события. В dry-run письма не отправляются, а только
пишутся в лог.

Подписки с `@telegram`-ником пока не рассылаются: бот не может написать
первым по нику, ему нужен `chat_id`, который появляется только после `/start`
в боте. Такие подписки хранятся и ждут, пока будет сделан бот.

Приём e-mail — это обработка персональных данных (152-ФЗ): уведомление
Роскомнадзора, политика обработки, хранение в РФ. Включайте
`SUBSIDY_SUBSCRIBE_ENABLED` осознанно, а не как техническую настройку.

---

## Алерты

| Тип | Условие | Важность |
|---|---|---|
| `restock` | было 0 мест, стало больше | critical |
| `soldout` | были места, стало 0 | warning |
| `drop` | падение на `SUBSIDY_SEAT_DROP` мест и больше | warning |
| `low` | осталось `SUBSIDY_LOW_SEATS` мест и меньше | warning |
| `price` | цена изменилась на `SUBSIDY_PRICE_DELTA` и больше | info |
| `new` | рейс увиден впервые и места есть | info |

Дедупликация: один и тот же тип по одному рейсу не чаще, чем раз в
`SUBSIDY_ALERT_COOLDOWN` минут. Все алерты пишутся в БД независимо от того,
настроены каналы доставки или нет.

Telegram включается парой `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`,
почта — заполненным `SMTP_HOST`. Без них алерты остаются в логе и БД.

---

## Что уточнить у партнёра

Уже известно: лимит — не больше 1000 запросов на одну продажу за
календарный месяц; договор — оказание услуг по привлечению клиентов
(«Веб-модуль (white label)» на uniagent.ru), только ИП или ООО; white-label
в iframe сразу, на своём домене — после 100 продаж за месяц.

Открыто:

1. Что происходит при превышении лимита и как он считается, пока продаж нет.
2. Примут ли ИП на НПД (чеки «Мой налог» вместо актов).
3. Какой параметр глубокой ссылки несёт `PersonID` (сейчас дописываем
   `&PersonID=`, если задан `BILETDV_PERSON_ID`).
4. Сколько живёт `BookURL`: в нём `rguid`/`iguid` поиска и `TL_Min=29`,
   ссылка в письме подписчику через час может не открыться.

---

## Результат прогона

`selftest.py` — 78 проверок, все зелёные. End-to-end на фикстуре
(2 маршрута × 30 дат × 2 категории): 120 запросов, 180 тарифов, 0 ошибок;
при подмене наличия корректно срабатывают `restock`, `soldout` и `drop`,
кулдаун подавляет повторы.

## Известные ограничения

* Маршрут предложения — запрошенные города (`KHV-MOW`), а не аэропорты из
  ответа (`SVO`). Рейс с пересадкой — одна строка: номера через « + »,
  мест — по самому загруженному сегменту.
* `AvailQty` в живом ответе у всех рейсов 9 — похоже на потолок «9 и больше»,
  как в системах бронирования; точное число выше 9 не узнать.
* `Cache_Best_Before` в живом ответе — практически момент запроса, так что
  кешировать по нему нечего; экономить лимит можно только частотой опроса.
* Тариф с незнакомым кодом и `MRID=0` отбрасывается с предупреждением в логе
  («пропущен тариф без признаков субсидии») — если такое появится, добавить
  код в `SUBSIDY_FARE_CODES`.
* **БД должна лежать на локальном диске.** SQLite в WAL-режиме падает с
  `disk I/O error` на сетевых и FUSE-томах (NFS, SMB, Dropbox, смонтированные
  папки). На VPS это не проблема; если всё же нужен сетевой том — вынесите
  БД на локальный диск через `SUBSIDY_DB_PATH` или уберите
  `PRAGMA journal_mode=WAL` в `db.py`.
* **Подписка работает только на VPS.** Бэкенд (`subscribe_api.py` и таблица
  `subscriptions`, включается `SUBSIDY_SUBSCRIBE_ENABLED`, см. «Подписка на
  уведомления») выключен по умолчанию. На GitHub Pages он не подключён: там
  статика, проксировать `/api/subscribe` некуда. Письма подписчикам уходят
  только на e-mail; `@telegram`-подписки ждут бота.
* **Категория льготы не фильтрует выдачу.** Коды известны со слов партнёра
  (SU: `PZZSOC` — молодёжь, `PSOCDO` — прописка ДФО; U6: `UBDOWZZ`,
  `RBDOWD`), но в `cities.CATEGORIES[*].fare_codes` ещё не вписаны, так что
  категория меняет только памятку о документах. Фильтр уже написан и
  включится сам. Льгот можно выбрать несколько сразу: рейс
  показывается, если подходит хотя бы под одну из выбранных.
* История в SQLite не чистится. При годовом горизонте и опросе раз в час
  таблица вырастет до сотен тысяч строк — это нормально для SQLite, но
  ротацию (`DELETE FROM observations WHERE depart_date < date('now','-30 day')`)
  стоит поставить в cron раз в неделю.
