# CLAUDE.md — Мониторинг субсидированных авиабилетов

Сервис показывает живое наличие мест по субсидированным авиабилетам с Дальнего
Востока (партнёр — БилетДВ). Модель: показываем наличие → переход на покупку по
партнёрской ссылке → комиссия 1,5%. Билеты сами не продаём.

Полный бизнес-контекст, ответы партнёра и план: `docs/PROJECT_CONTEXT.md`.
Отчёт по начислениям партнёра: `docs/sample-accrual-report.md`.
Стратегия роста: `docs/growth-strategy.md`.

## Стек
- Python 3.10+, только stdlib + `requests`. SQLite (WAL).
- Витрина — один статический HTML, собирается `webapp.py`, данные вшиты как JSON,
  вся логика на клиенте (ванильный JS, без фреймворков).
- Хостинг: GitHub Pages через Actions (`.github/workflows/pages.yml`).
- `design/` — концепт нового дизайна витрины (`index.html`, `checkout.html`).

## Пайплайн
API БилетДВ (XML) → `fetcher.py` → `parser.py` → `db.py` (SQLite) → `alerts.py`
→ `webapp.py` (index.html) / `report.py` (служебный report.html).
Оркестратор — `collector.py`.

## Команды
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python selftest.py                     # тесты пайплайна, без сети
python collector.py --report --site    # прогон на фикстуре → data/index.html
python webapp.py --out /tmp/index.html # только пересобрать витрину
python colorcheck.py data/index.html   # контраст палитры WCAG AA
npm install jsdom && node uitest.js data/index.html   # UI-тесты
```
Боевой режим: `--live` + переменные из `.env` (см. `.env.example`).

## Правила
- По умолчанию DRY_RUN=1: без явного `--live` в API партнёра не ходим.
- Лимит партнёра: не более 1000 запросов к API на одну продажу. Не добавлять
  массовый опрос без учёта этого.
- Секреты (PartnerID боевой, токены) — только в `.env` / GitHub Secrets. `.env`
  и `data/` никогда не коммитить.
- Пока нет боевого PartnerID — витрина в демо-режиме (баннер, noindex,
  кнопки покупки выключены). Не ломать это поведение.
- Цвета витрины — только через CSS-переменные в начале `CSS` в `webapp.py`.
- После изменений: `selftest.py`, `colorcheck.py`, `uitest.js` должны быть зелёными.
- Язык интерфейса, комментариев и коммитов — русский.

## Известные проблемы
- В строке поиска название города («Хабаровск») может быть невидимым — белый
  текст на белом поле (цвет наследуется от синего блока).
- Подписка на уведомления не отправляется (нужен бэкенд).
- Категория льготы не фильтрует выдачу (нужен эндпоинт `getFaresByFOP_Ex3`).
- Не генерируется `PersonID` для `BookURL`.
- Квадратик «вне мониторинга» в легенде календаря сливается с фоном.
