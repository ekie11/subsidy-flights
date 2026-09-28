#!/usr/bin/env python3
"""
Витрина поиска субсидированных билетов — статический сайт из данных сборщика.

Генерирует один самодостаточный HTML: данные из SQLite вшиваются в страницу
как JSON, поиск и фильтрация работают на клиенте. Бэкенда нет — страницу
раздаёт любой nginx, хостинг стоит копейки, а падать в рантайме нечему.

Раскладка повторяет привычную схему большого метапоиска: сплошной синий
экран сверху, крупный заголовок по центру, ряд пилюль-переключателей,
белая строка поиска из сегментов и контрастная кнопка действия. Ниже —
белая страница с серыми карточками. Пользователь такую страницу читает
без обучения, и это экономит ему усилия.

    python webapp.py                      # data/index.html
    python webapp.py --out /var/www/html/index.html

Пересобирать после каждого прогона collector.py:
    python collector.py --live --site

Стили и клиентская логика — в assets/webapp.css и assets/webapp.js, здесь
только сборка HTML вокруг них. Фирменные цвета — переменные --brand и --cta
в начале assets/webapp.css.
"""
from __future__ import annotations

import argparse
import html
from datetime import datetime, timezone
from pathlib import Path

import cities
import config
from datajson import rows_to_data as _rows_to_data, to_json as _json
from db import Database


ASSETS_DIR = Path(__file__).parent / "assets"


def _read_asset(name: str) -> str:
    return (ASSETS_DIR / name).read_text(encoding="utf-8").rstrip("\n")


# ==========================================================================
# Стили и клиентская логика вынесены в assets/webapp.css и assets/webapp.js —
# так их можно редактировать с подсветкой синтаксиса и линтерами вместо
# правки многострочных Python-констант. Здесь только сборка страницы.
# ==========================================================================

CSS = _read_asset("webapp.css")
JS = _read_asset("webapp.js")

# Иконки — инлайновые SVG с одной толщиной штриха (класс .ico в webapp.css),
# а не символы шрифта: ✈ и ⇄ в разных системах рисуются по-разному.
def _icon(paths: str, cls: str = "ico") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" '
            f'focusable="false">{paths}</svg>')


ICON_PLANE = _icon('<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8'
                   ' 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2'
                   ' 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>')
ICON_SWAP = _icon('<path d="M7 4 3 8l4 4"/><path d="M3 8h14"/><path d="m17 20 4-4-4-4"/>'
                  '<path d="M21 16H7"/>')
ICON_PREV = _icon('<path d="m15 18-6-6 6-6"/>')
ICON_NEXT = _icon('<path d="m9 18 6-6-6-6"/>')
ICON_PULSE = _icon('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>', "ico ic")


# ==========================================================================
# Сборка страницы
# ==========================================================================

def build(out_path: Path | str | None = None, db: Database | None = None) -> Path:
    db = db or Database()
    rows = db.current_state()
    runs = db.last_runs(limit=1)
    last_run = runs[0] if runs else None
    is_demo = bool(last_run and last_run["dry_run"])

    data = _rows_to_data(rows)
    routes = [{"origin": o, "destination": d}
              for o, d in sorted({(x["o"], x["d"]) for x in data})]

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": datetime.now(timezone.utc).date().isoformat(),
        "demo": is_demo,
        "partner": config.PARTNER_ID,
        "subscribeApi": config.SUBSCRIBE_ENABLED,
    }

    # В демо-режиме страница закрыта от индексации: публичная выдача с
    # вымышленными рейсами и ценами под именем ИП — прямой репутационный риск.
    robots = ('<meta name="robots" content="noindex,nofollow">\n'
              if is_demo else "")

    head = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Субсидированные авиабилеты с Дальнего Востока — есть ли места</title>
<meta name="description" content="Показываем, есть ли свободные места по субсидированным
тарифам на рейсах с Дальнего Востока. Обновляется автоматически каждый час.">
{robots}<style>{CSS}</style></head><body>"""

    demo_bar = ("<div class='demo-bar'><div class='wrap'><b>Демо-режим.</b> "
                "Данные из тестовой фикстуры, а не от партнёрского API: рейсы, места "
                "и цены вымышлены, переход к покупке отключён.</div></div>"
                ) if is_demo else ""

    cats_html = "".join(
        f"<button class='cat{' on' if c['id'] == 'dfo' else ''}' data-id='{c['id']}' "
        f"aria-pressed='{'true' if c['id'] == 'dfo' else 'false'}'>"
        f"{html.escape(c['short'])}</button>"
        for c in cities.CATEGORIES
    )

    # Подвал собирается из тех же справочников, что и страница: раньше список
    # направлений был вписан руками и показывал маршрут, который не отслеживается.
    foot_cats = "".join(f"<p>{html.escape(c['title'])}</p>" for c in cities.CATEGORIES)
    foot_routes = "".join(
        f"<p><a href='#results' data-o='{r.origin}' data-d='{r.destination}'>"
        f"{html.escape(r.label or r.origin + ' — ' + r.destination)}</a></p>"
        for r in config.ROUTES
    )

    pax_rows = "".join(
        f"""<div class="pax-row"><div class="t"><b id="t-{key}">{title}</b><span>{hint}</span></div>
        <button class="step" data-k="{key}" data-d="-1" aria-label="Меньше: {title.lower()}">−</button>
        <span class="step-val" id="v-{key}" aria-live="polite" aria-labelledby="t-{key} v-{key}">0</span>
        <button class="step" data-k="{key}" data-d="1" aria-label="Больше: {title.lower()}">+</button></div>"""
        for key, title, hint in (
            ("adults", "Взрослые", "от 12 лет"),
            ("children", "Дети", "2–11 лет, отдельное место"),
            ("infants", "Младенцы", "до 2 лет, на руках"),
        )
    )

    body = f"""
{demo_bar}
<div class="blue">
  <div class="wrap">
    <div class="topbar">
      <div class="logo"><span class="mark">{ICON_PLANE}</span>субсидия</div>
      <nav class="topnav" aria-label="Разделы">
        <a href="#results">Рейсы</a><a href="#cal">Календарь мест</a>
        <a href="#how">Как это работает</a>
      </nav>
    </div>

    <div class="hero">
      <h1>Тут видно, есть ли места по субсидии</h1>
      <div class="cats" role="group" aria-label="Льготы — можно выбрать несколько">{cats_html}</div>
      <p class="cat-hint"><a href="#docs" id="catHint">Что взять с собой по льготе «Житель ДФО»</a></p>
    </div>

    <div class="search" role="search">
      <div class="field">
        <label class="lab" for="from">Откуда</label>
        <select class="control" id="from"></select>
        <span class="code" id="fromCode" aria-hidden="true"></span>
        <button class="swap" id="swap" type="button" aria-label="Поменять города местами"
          title="Поменять местами">{ICON_SWAP}</button>
      </div>
      <div class="field">
        <label class="lab" for="to">Куда</label>
        <select class="control" id="to"></select>
        <span class="code" id="toCode" aria-hidden="true"></span>
      </div>
      <div class="field">
        <label class="lab" for="date">Когда</label>
        <input class="control" type="date" id="date">
      </div>
      <div class="field">
        <label class="lab" for="paxBtn">Пассажиры</label>
        <button class="control pax-btn" id="paxBtn" type="button" aria-expanded="false"
          aria-controls="paxPop"><span id="paxLabel">1 пассажир</span></button>
        <span class="pax-sub">субсидированный тариф</span>
        <div class="pax-pop" id="paxPop" role="group" aria-label="Состав пассажиров">{pax_rows}
          <div class="pax-hint">Младенцу на руках отдельное место не нужно —
            в поиске мест он не учитывается.</div>
        </div>
      </div>
      <button class="btn-find" id="find">Найти места</button>
    </div>

    <div class="underbar">
      <span>Квота тает за часы — мы проверяем наличие каждый час</span>
      <span class="right">обновлено <time id="updated" datetime="{meta['generated']}">{meta['generated'][:16].replace('T', ' ')} UTC</time></span>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="strip">
    {ICON_PULSE}
    <span>Показываем живое наличие мест, а не тариф, которого уже нет</span>
    <a href="#how">Узнать больше</a>
  </div>
</div>

<div class="wrap page">
  <div class="sec" id="results">
    <div class="sec-h">
      <h2 id="routeTitle">—</h2><div class="sub" id="routeSub"></div>
      <div class="right"><select class="sortsel" id="sort" aria-label="Сортировка рейсов">
        <option value="time">по времени вылета</option>
        <option value="seats">где больше мест</option>
        <option value="price">сначала дешевле</option>
      </select></div>
    </div>
    <p class="sr-only" id="status" role="status"></p>
    <div id="board"></div>
  </div>

  <div class="sec" id="docs">
    <div class="sec-h"><h2>Кому положена субсидия</h2>
      <div class="sub">выберите категорию в синем блоке выше</div></div>
    <div class="note" id="note"></div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>Направления под мониторингом</h2>
      <div class="sub">нажмите, чтобы открыть ближайшую дату с местами</div></div>
    <div class="pops" id="pops"></div>
  </div>

  <div class="sec" id="cal">
    <div class="sec-h"><h2>Календарь мест</h2>
      <div class="sub">сколько мест и почём по дням на выбранном направлении</div></div>
    <div class="cal">
      <div class="cal-top">
        <h3 id="calTitle">—</h3>
        <div class="nav"><button id="calPrev" type="button" aria-label="Предыдущий месяц">{ICON_PREV}</button><button
          id="calNext" type="button" aria-label="Следующий месяц">{ICON_NEXT}</button></div>
      </div>
      <div class="cal-grid" id="calGrid"></div>
      <div class="legend" aria-hidden="true">
        <span><i class="l-has"></i>места есть</span>
        <span><i class="l-low"></i>мест мало</span>
        <span><i class="l-none"></i>мест нет</span>
        <span><i class="l-unknown"></i>вне мониторинга</span>
      </div>
    </div>
  </div>

  <div class="sec how" id="how">
    <b>Как это работает.</b> Мы не продаём билеты и не берём с вас денег.
    Каждый час опрашиваем систему бронирования, храним историю наличия мест
    и показываем её здесь. Покупка — на сайте авиакассы-партнёра по нашей ссылке.
  </div>
</div>

<div class="modal" id="watch"><div class="modal-box" role="dialog" aria-modal="true"
  aria-labelledby="watchTitle" aria-describedby="watchDesc">
  <h3 id="watchTitle">Сообщить, когда появятся места</h3>
  <p id="watchDesc">Пришлём уведомление, как только субсидированные места вернутся в продажу
     на выбранном направлении.</p>
  <label for="watchInput">E-mail или ник в Telegram</label>
  <input id="watchInput" autocomplete="email" placeholder="name@mail.ru или @nick">
  <div id="watchMsg" class="watch-msg" role="status"></div>
  <div class="modal-actions">
    <button id="watchClose" type="button">Отмена</button>
    <button class="primary" id="watchSave" type="button">Подписаться</button>
  </div>
</div></div>

<footer><div class="wrap">
  <div class="fcols">
    <div><h4>Субсидии</h4>{foot_cats}</div>
    <div><h4>Направления</h4>{foot_routes}</div>
    <div><h4>Помощь</h4><p><a href="#how">Как это работает</a></p>
      <p><a href="#docs">Какие нужны документы</a></p><p><a href="#cal">Календарь мест</a></p></div>
    <div><h4>О сервисе</h4><p>Партнёр: БилетДВ</p><p>ИП Харханов К.</p>
      <p><a href="mailto:harhanovk@gmail.com">harhanovk@gmail.com</a></p></div>
  </div>
  Сервис информационный: наличие мест показываем мы, билеты продаёт партнёр.
</div></footer>

<script>
const DATA={_json(data)};
const ROUTES={_json(routes)};
const META={_json(meta)};
const AIRPORTS={_json(cities.as_json_dict())};
const CATEGORIES={_json(cities.CATEGORIES)};
const AIRLINES={_json(cities.AIRLINES)};
const LOW={config.ALERTS.low_seats_threshold};
{JS}
</script></body></html>"""

    out = Path(out_path or (config.DATA_DIR / "index.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(head + body, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка витрины поиска")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = build(args.out)
    print(f"витрина готова: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
