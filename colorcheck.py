#!/usr/bin/env python3
"""
Проверка палитры витрины на контраст по WCAG 2.1.

Читает переменные из :root сгенерированной страницы, поэтому проверяет ровно
те цвета, которые уедут на прод, а не то, что записано в задумке.

    python colorcheck.py                 # data/index.html
    python colorcheck.py path/to/index.html

Пороги: 4.5:1 — обычный текст, 3:1 — крупный (от 18.66px жирного / 24px обычного)
и границы элементов управления.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import config


AA_NORMAL = 4.5
AA_LARGE = 3.0


def _srgb_to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * _srgb_to_lin(r)
            + 0.7152 * _srgb_to_lin(g)
            + 0.0722 * _srgb_to_lin(b))


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def read_palette(html: str) -> dict[str, str]:
    """Достаёт --переменные из блока :root{...}."""
    m = re.search(r":root\{(.*?)\}", html, re.S)
    if not m:
        raise SystemExit("не нашёл :root в странице — палитру проверить нечем")
    return {name: value for name, value in
            re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", m.group(1))}


# Пары «что на чём». level: 'normal' — мелкий текст, 'large' — крупный.
PAIRS = [
    ("текст страницы",          "ink",     "bg",         "normal"),
    ("второстепенный текст",    "dim",     "bg",         "normal"),
    ("текст на серой плашке",   "ink-2",   "grey",       "normal"),
    ("текст на кнопке действия", "cta-ink", "cta",       "normal"),
    ("мест много",              "green",   "bg",         "normal"),
    ("мест мало",               "amber",   "bg",         "normal"),
    ("мест нет",                "red",     "bg",         "normal"),
    ("мест много на плашке",    "green",   "#e7f3ec",    "normal"),
    ("мест мало на плашке",     "amber",   "#fdf3e2",    "normal"),
    ("ссылка бренда на белом",  "brand",   "bg",         "normal"),
    ("белый текст на бренде",   "#ffffff", "brand",      "normal"),
    ("белый на тёмном бренде",  "#ffffff", "brand-dk",   "normal"),
    # Подписи-приглушённые: допускаем крупный порог только там, где кегль >= 24px.
    ("крупные цифры мест",      "green",   "bg",         "large"),
]


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else (config.DATA_DIR / "index.html")
    if not path.exists():
        raise SystemExit(f"страница не найдена: {path}\nсначала: python webapp.py")

    html = path.read_text(encoding="utf-8")
    pal = read_palette(html)

    def resolve(token: str) -> str:
        return token if token.startswith("#") else pal.get(token, "")

    print(f"палитра из {path.name}: {len(pal)} переменных\n")
    failures = 0
    for label, fg_t, bg_t, level in PAIRS:
        fg, bg = resolve(fg_t), resolve(bg_t)
        if not fg or not bg:
            print(f"  ??    переменная не найдена: {fg_t} / {bg_t}")
            failures += 1
            continue
        need = AA_LARGE if level == "large" else AA_NORMAL
        r = contrast(fg, bg)
        ok = r >= need
        failures += not ok
        mark = "ok   " if ok else "МАЛО "
        print(f"  {mark}{r:5.2f} (нужно {need}) — {label}  {fg} на {bg}")

    print("\n" + "=" * 52)
    if failures:
        print(f"не проходят WCAG AA: {failures}")
    else:
        print("вся палитра проходит WCAG AA")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
