#!/usr/bin/env bash
#
# Публикация проекта на GitHub. Один запуск — от пустого репозитория до пуша.
#
#   bash publish.sh ВАШ_ЛОГИН [имя-репозитория]
#
# Скрипт НЕ создаёт репозиторий на github.com и не спрашивает пароль:
# создание — один клик в браузере, авторизация — ваш обычный git-credential
# helper или SSH-ключ. Пароли и токены здесь не запрашиваются и не хранятся.

set -euo pipefail

USER_LOGIN="${1:-}"
REPO="${2:-subsidy-flights}"

if [ -z "$USER_LOGIN" ]; then
  echo "Укажите логин GitHub:  bash publish.sh ВАШ_ЛОГИН [имя-репозитория]" >&2
  exit 1
fi

cd "$(dirname "$0")"
echo "проект:      $(pwd)"
echo "репозиторий: https://github.com/$USER_LOGIN/$REPO"
echo

# ---------------------------------------------------------------- проверки
echo "→ прогоняю тесты, чтобы не публиковать сломанное"
python3 selftest.py > /tmp/selftest.log 2>&1 && echo "  пайплайн: ок" || {
  echo "  ПАЙПЛАЙН УПАЛ — смотрите /tmp/selftest.log"; exit 1; }

python3 webapp.py --out /tmp/index.html > /dev/null
python3 colorcheck.py /tmp/index.html > /tmp/colorcheck.log 2>&1 \
  && echo "  контраст палитры: ок" || {
  echo "  ПАЛИТРА НЕ ПРОХОДИТ WCAG — смотрите /tmp/colorcheck.log"; exit 1; }

if command -v node > /dev/null 2>&1; then
  if node -e "require('jsdom')" > /dev/null 2>&1; then
    node uitest.js /tmp/index.html > /tmp/uitest.log 2>&1 \
      && echo "  интерфейс: ок" || {
      echo "  ИНТЕРФЕЙС УПАЛ — смотрите /tmp/uitest.log"; exit 1; }
  else
    echo "  интерфейс: пропущен (нет jsdom, поставьте: npm install jsdom)"
  fi
else
  echo "  интерфейс: пропущен (нет Node)"
fi

# ------------------------------------------------------------------- git
if [ ! -d .git ]; then
  echo "→ инициализирую репозиторий"
  git init -q
  git branch -M main
fi

echo "→ собираю коммит"
git add -A

# Секреты не должны уехать наружу ни при каких обстоятельствах.
if git diff --cached --name-only | grep -qE '(^|/)\.env$'; then
  echo "  СТОП: в коммит попал .env с секретами. Проверьте .gitignore." >&2
  git reset -q
  exit 1
fi
if git diff --cached --name-only | grep -qE '(^|/)data/'; then
  echo "  СТОП: в коммит попала папка data (база и логи). Проверьте .gitignore." >&2
  git reset -q
  exit 1
fi

echo "  файлов в коммите: $(git diff --cached --name-only | wc -l | tr -d ' ')"

if git diff --cached --quiet; then
  echo "  изменений нет, коммит не нужен"
else
  git commit -q -m "Мониторинг субсидированных билетов: сборщик, витрина, тесты"
  echo "  коммит создан"
fi

if ! git remote | grep -qx origin; then
  git remote add origin "https://github.com/$USER_LOGIN/$REPO.git"
  echo "→ remote origin добавлен"
fi

echo
echo "→ отправляю на GitHub"
echo "  (если репозиторий ещё не создан — создайте его сейчас:"
echo "   https://github.com/new  →  имя: $REPO  →  Public  →  без README)"
echo
read -r -p "  репозиторий создан? Enter — продолжить, Ctrl+C — выйти: " _

git push -u origin main

# ---------------------------------------------------------------- финиш
cat <<EOF

Готово. Осталось два клика в браузере:

  1. Settings → Pages → Source: GitHub Actions
     https://github.com/$USER_LOGIN/$REPO/settings/pages

  2. Actions → «Публикация витрины на GitHub Pages» → Run workflow
     https://github.com/$USER_LOGIN/$REPO/actions

Через пару минут витрина будет здесь:

  https://$USER_LOGIN.github.io/$REPO/

Пока в Secrets нет боевого PartnerID, страница публикуется в демо-режиме:
с баннером, отключёнными кнопками покупки и noindex. Это защита от того,
чтобы вымышленные рейсы попали в поиск под вашим именем.
EOF
