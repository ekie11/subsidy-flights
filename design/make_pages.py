"""Многостраничный макет витрины «Есть места» из design/index-v2.html.

    python design/make_pages.py          # → design/site/*.html

Одностраничный макет режется на страницы по секциям; шапка, футер, стили
и скрипты общие. Основной скрипт макета написан под одну страницу и ищет
элементы по id — на страницах, где блока нет, `$()` отдаёт отвязанный
пустой элемент, чтобы код не падал. Поиск с любой страницы ведёт на
reisy.html с параметрами (?from=&to=&date=&pax=).
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "index-v2.html"
OUT = HERE / "site"

# страница → (файл, заголовок, секции, шапка: "search" | (h1, lead))
PAGES = [
    ("index.html", "Есть места — места по субсидии на рейсах ДФО",
     ["seychas", "podpiska"], "search"),
    ("reisy.html", "Рейсы и календарь мест — Есть места",
     ["rejsy", "calendar"], "search"),
    ("napravleniya.html", "Направления — Есть места",
     ["napravleniya"],
     ("Направления", "История льготных мест по каждому маршруту: сколько дней места были и когда обычно возвращаются.")),
    ("kak.html", "Как это работает — Есть места",
     ["kak", "zachem", "otzyv"],
     ("Как это работает", "Три шага от поиска до билета — и чем мы отличаемся от агрегаторов и сайтов авиакомпаний.")),
    ("uvedomleniya.html", "Уведомления о местах — Есть места",
     ["podpiska"],
     ("Уведомления о местах", "Подпишитесь на маршрут и дату — напишем в Telegram или на почту, как только места вернутся.")),
]

# id секции → страница, где она живёт (для переписывания якорей)
HOME = {"seychas": "index.html", "rejsy": "reisy.html", "calendar": "reisy.html",
        "napravleniya": "napravleniya.html", "kak": "kak.html", "zachem": "kak.html",
        "podpiska": "uvedomleniya.html"}

NAV = [("reisy.html", "Рейсы"), ("napravleniya.html", "Направления"), ("kak.html", "Как это работает")]
_SVG = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
# в вендорном наборе Phosphor только иконки, что уже есть на сайте, — для меню свои SVG
TABS = [("index.html", _SVG.format('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>'), "Главная"),
        ("reisy.html", _SVG.format('<path d="M2 16l20-6-3-3-6 2-6-5-2 1 3 6-4 1-2-1-1 1z"/><path d="M3 21h18"/>'), "Рейсы"),
        ("napravleniya.html", _SVG.format('<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>'), "Направления"),
        ("uvedomleniya.html", _SVG.format('<path d="M6 9a6 6 0 1 1 12 0c0 6 3 8 3 8H3s3-2 3-8"/><path d="M10 21h4"/>'), "Уведомления")]

EXTRA_CSS = """
.phead{padding:18px 0 34px}
.phead h1{margin:14px 0 10px}
.phead .lead{max-width:60ch;margin:0}
.topnav a[aria-current],.menu a[aria-current]{color:#fff;background:rgba(255,255,255,.12)}
.crumbs{font-size:14px;color:#aab5d6;margin:0}
.crumbs a{color:#c9d3f0}
.sec-lead{margin:0 0 22px;color:var(--dim);max-width:58ch}
.more{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}
"""

NAV_JS = """
<script>
/* Многостраничность: поиск ведёт на reisy.html, страница рейсов подхватывает параметры. */
(function(){
  var form=document.getElementById('search'); if(!form) return;
  var onResults=!!document.getElementById('rejsy');
  var ids=['from','to','date','pax'];
  if(!onResults){
    form.addEventListener('submit',function(e){
      e.preventDefault(); e.stopImmediatePropagation();
      var q=new URLSearchParams(); ids.forEach(function(k){q.set(k,document.getElementById(k).value)});
      location.href='reisy.html?'+q.toString()+'#rejsy';
    },true);
  } else {
    var p=new URLSearchParams(location.search); if(!p.get('from')) return;
    // после инициализации основного скрипта (он тоже ждёт DOMContentLoaded и выставляет поля сам)
    document.addEventListener('DOMContentLoaded',function(){
      ids.forEach(function(k){var el=document.getElementById(k); if(p.get(k)){el.value=p.get(k); el.dispatchEvent(new Event('change'));}});
      form.requestSubmit();
    });
  }
})();
</script>
"""


def section(html: str, sid: str) -> str:
    """Вырезает <section id=sid ...>...</section> верхнего уровня."""
    m = re.search(rf'\n  <section id="{sid}"[^>]*>.*?\n  </section>\n', html, re.S)
    if not m:
        raise SystemExit(f"секция {sid} не найдена")
    return m.group(0)


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    # отзыв без id — дадим, чтобы вырезать как остальные
    src = src.replace('  <section aria-label="Отзыв">', '  <section id="otzyv" aria-label="Отзыв">', 1)
    # null-safe $ для общего скрипта
    src = src.replace(
        "var $ = function (s, r) { return (r || document).querySelector(s); };",
        "var $ = function (s, r) { return (r || document).querySelector(s) || document.createElement('div'); };", 1)
    src = src.replace("</style>\n</head>", EXTRA_CSS + "</style>\n</head>", 1)
    src = src.replace('href="assets/', 'href="../assets/').replace('src="assets/', 'src="../assets/').replace("'checkout.html?", "'../checkout.html?")

    main_start = src.index('<main id="main" tabindex="-1">\n') + len('<main id="main" tabindex="-1">\n')
    main_end = src.index("</main>")
    hero = re.search(r'\n    <div class="hero">.*?\n    </div>\n  </div>\n</header>', src, re.S).group(0)

    hero_div = hero[: -len("\n  </div>\n</header>")]
    OUT.mkdir(exist_ok=True)
    for fname, title, secs, head in PAGES:
        page = src
        body = "".join(section(src, s) for s in secs)
        if fname == "index.html":
            body += ('\n  <section aria-label="Разделы"><div class="wrap"><div class="more">'
                     '<a class="btn s" href="napravleniya.html">Все направления</a>'
                     '<a class="btn s" href="kak.html">Как это работает</a></div></div></section>\n')
        page = page[:main_start] + body + page[main_end:]
        page = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", page, count=1, flags=re.S)
        if head != "search":
            h1, lead = head
            # заголовок секции, повторяющий h1 страницы, не нужен
            page = re.sub(rf'\n      <div class="sec-h">\n        <div>\n          <h2 id="[\w-]+"[^>]*>{re.escape(h1)}</h2>\n          <p>(.*?)</p>\n        </div>\n      </div>',
                          r'\n      <p class="sec-lead">\1</p>', page, count=1, flags=re.S)
            page = page.replace(hero, (
                f'\n    <div class="phead">\n      <p class="crumbs"><a href="index.html">Главная</a> / {h1}</p>\n'
                f'      <h1>{h1}</h1>\n      <p class="lead">{lead}</p>\n    </div>\n'
                # поиск остаётся в разметке скрытым: общий скрипт рассчитывает на его поля
                f'    <div hidden>{hero_div}</div>\n  </div>\n</header>'), 1)

        # якоря → страницы
        def fix(m: re.Match) -> str:
            sid = m.group(1)
            target = HOME.get(sid)
            if sid == "main":
                return 'href="index.html"'
            if not target or target == fname:
                return m.group(0)
            return f'href="{target}#{sid}"' if sid not in ("rejsy",) else f'href="{target}"'
        page = re.sub(r'href="#([\w-]+)"', fix, page)

        # шапка: пункты меню и активный раздел
        def cur(h: str) -> str:
            return ' aria-current="page"' if h == fname else ""
        links = "".join(
            f'\n        <a href="{h}"{cur(h)}>{t}</a>' for h, t in NAV)
        page = re.sub(r'(<nav class="topnav" aria-label="Разделы">).*?(\n      </nav>)', rf"\1{links}\2", page, count=1, flags=re.S)
        items = "".join(
            f'\n          <li><a href="{h}"{cur(h)}>{t}</a></li>' for h, t in NAV)
        items += '\n          <li><a class="tg-link" href="uvedomleniya.html">Бот в Telegram</a></li>'
        page = re.sub(r'(<nav class="menu" id="menu" aria-label="Разделы" hidden>\s*<ul>).*?(\n        </ul>)', rf"\1{items}\2", page, count=1, flags=re.S)
        tabs = "".join(
            f'\n  <li><a href="{h}"{cur(h)}>{i}{t}</a></li>'
            for h, i, t in TABS)
        page = re.sub(r'(<nav class="tabbar" aria-label="Основные разделы"><ul>).*?(\n</ul></nav>)', rf"\1{tabs}\2", page, count=1, flags=re.S)
        # футер: ссылки «Сервис»
        f0 = page.index('<footer class="foot">')
        page = page[:f0] + page[f0:].replace('<li><a href="reisy.html">Рейсы</a></li>',
                            '<li><a href="reisy.html">Рейсы</a></li>\n          <li><a href="napravleniya.html">Направления</a></li>', 1)

        i = page.rfind("</body>")
        page = page[:i] + NAV_JS + page[i:]
        (OUT / fname).write_text(page, encoding="utf-8")
        print("→", OUT / fname)


if __name__ == "__main__":
    main()
