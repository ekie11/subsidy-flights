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
import json
from datetime import datetime, timezone
from pathlib import Path

import cities
import config
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


# ==========================================================================
# Сборка страницы
# ==========================================================================

def _rows_to_data(rows) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "o": r["origin"] or "",
            "d": r["destination"] or "",
            "dt": r["depart_date"] or "",
            "tm": r["depart_time"] or "",
            "ar": r["arrive_time"] or "",
            "fn": r["flight_number"] or "",
            "al": r["airline"] or "",
            "fc": r["fare_code"] or "",
            "q": int(r["avail_qty"] or 0),
            "p": float(r["price"] or 0),
            "url": r["book_url"] or "",
        })
    return out


def _json(obj) -> str:
    # </script> внутри данных сломал бы страницу — экранируем слэш.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


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
тарифам на рейсах с Дальнего Востока. Обновляется автоматически каждые 15 минут.">
{robots}<style>{CSS}</style></head><body>"""

    demo_bar = ("<div class='demo-bar'><div class='wrap'><b>Демо-режим.</b> "
                "Данные из тестовой фикстуры, а не от партнёрского API: рейсы, места "
                "и цены вымышлены, переход к покупке отключён.</div></div>"
                ) if is_demo else ""

    cats_html = "".join(
        f"<button class='cat{' on' if c['id'] == 'dfo' else ''}' data-id='{c['id']}'>"
        f"{c['short']}</button>"
        for c in cities.CATEGORIES
    )

    pax_rows = "".join(
        f"""<div class="pax-row"><div class="t"><b>{title}</b><span>{hint}</span></div>
        <button class="step" data-k="{key}" data-d="-1">−</button>
        <span class="step-val" id="v-{key}">0</span>
        <button class="step" data-k="{key}" data-d="1">+</button></div>"""
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
      <div class="logo"><span class="mark">✈</span>субсидия</div>
      <nav class="topnav">
        <a href="#results">Рейсы</a><a href="#cal">Календарь мест</a>
        <a href="#how">Как это работает</a>
      </nav>
    </div>

    <div class="hero">
      <h1>Тут видно, есть ли места по субсидии</h1>
      <div class="cats">{cats_html}</div>
    </div>

    <div class="search">
      <div class="field">
        <span class="lab">Откуда</span>
        <select class="control" id="from"></select>
        <span class="code" id="fromCode"></span>
        <button class="swap" id="swap" title="Поменять местами">⇄</button>
      </div>
      <div class="field">
        <span class="lab">Куда</span>
        <select class="control" id="to"></select>
        <span class="code" id="toCode"></span>
      </div>
      <div class="field">
        <span class="lab">Когда</span>
        <input class="control" type="date" id="date">
      </div>
      <div class="field">
        <span class="lab">Пассажиры</span>
        <button class="control pax-btn" id="paxBtn"><span id="paxLabel">1 пассажир</span></button>
        <span class="pax-sub">субсидированный тариф</span>
        <div class="pax-pop" id="paxPop">{pax_rows}
          <div class="pax-hint">Младенцу на руках отдельное место не нужно —
            в поиске мест он не учитывается.</div>
        </div>
      </div>
      <button class="btn-find" id="find">Найти места</button>
    </div>

    <div class="underbar">
      <span>Квота тает за часы — мы проверяем наличие каждые 15 минут</span>
      <span class="right">обновлено {meta['generated'].replace('T', ' ')} UTC</span>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="strip">
    <span class="ic">◎</span>
    <span>Показываем живое наличие мест, а не тариф, которого уже нет</span>
    <a href="#how">Узнать больше</a>
  </div>
</div>

<div class="wrap page">
  <div class="sec" id="results">
    <div class="sec-h">
      <h2 id="routeTitle">—</h2><div class="sub" id="routeSub"></div>
      <div class="right"><select class="sortsel" id="sort">
        <option value="time">по времени вылета</option>
        <option value="seats">где больше мест</option>
        <option value="price">сначала дешевле</option>
      </select></div>
    </div>
    <div id="board"></div>
  </div>

  <div class="sec">
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
      <div class="sub">сколько свободно по дням на выбранном направлении</div></div>
    <div class="cal">
      <div class="cal-top">
        <h3 id="calTitle">—</h3>
        <div class="nav"><button id="calPrev">‹</button><button id="calNext">›</button></div>
      </div>
      <div class="cal-grid" id="calGrid"></div>
      <div class="legend">
        <span><i style="background:#e7f3ec"></i>места есть</span>
        <span><i style="background:#fdf3e2"></i>мест мало</span>
        <span><i style="background:#fff;box-shadow:inset 0 0 0 1px #e5e8f0"></i>мест нет</span>
        <span><i style="background:#f5f7fb"></i>вне мониторинга</span>
      </div>
    </div>
  </div>

  <div class="sec how" id="how">
    <b>Как это работает.</b> Мы не продаём билеты и не берём с вас денег.
    Каждые 15 минут опрашиваем систему бронирования, храним историю наличия мест
    и показываем её здесь. Покупка — на сайте авиакассы-партнёра по нашей ссылке,
    цена для вас ровно та же.
  </div>
</div>

<div class="modal" id="watch"><div class="modal-box">
  <h3>Сообщить, когда появятся места</h3>
  <p>Пришлём уведомление, как только субсидированные места вернутся в продажу
     на выбранном направлении.</p>
  <input id="watchInput" placeholder="E-mail или @telegram">
  <div id="watchMsg" style="font-size:13px;color:#e03131;min-height:19px"></div>
  <div class="modal-actions">
    <button id="watchClose">Отмена</button>
    <button class="primary" id="watchSave">Подписаться</button>
  </div>
</div></div>

<footer><div class="wrap">
  <div class="fcols">
    <div><h4>Субсидии</h4><p>Жителям ДФО</p><p>Молодёжи до 23 лет</p>
      <p>Пенсионерам</p><p>Многодетным семьям</p></div>
    <div><h4>Направления</h4><p>Хабаровск — Москва</p><p>Хабаровск — Петербург</p>
      <p>Владивосток — Москва</p></div>
    <div><h4>Помощь</h4><p>Как купить по субсидии</p><p>Какие нужны документы</p>
      <p>Что если мест нет</p></div>
    <div><h4>О сервисе</h4><p>Партнёр: БилетДВ</p><p>ИП Харханов К.</p>
      <p>harhanovk@gmail.com</p></div>
  </div>
  Сервис информационный: наличие мест показываем мы, билеты продаёт партнёр.
</div></footer>

<script>
const DATA={_json(data)};
const ROUTES={_json(routes)};
const META={_json(meta)};
const AIRPORTS={_json(cities.as_json_dict())};
const CATEGORIES={_json(cities.CATEGORIES)};
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
