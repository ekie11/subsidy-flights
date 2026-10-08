#!/usr/bin/env bash
# Статус-строка Claude Code: модель, заполненность контекста, ветка.
input=$(cat)
python3 -c '
import json, sys
d = json.loads(sys.argv[1] or "{}")
model = (d.get("model") or {}).get("display_name", "?")
cw = d.get("context_window") or {}
pct = cw.get("used_percentage")
size = cw.get("context_window_size") or 200000
if pct is None:
    u = cw.get("current_usage") or {}
    used = sum(u.get(k, 0) or 0 for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens"))
    pct = used * 100 / size if size else 0
else:
    used = pct * size / 100
pct = max(0, min(100, float(pct)))
n = 20; f = round(pct * n / 100)
color = "\033[32m" if pct < 50 else "\033[33m" if pct < 80 else "\033[31m"
bar = color + "█" * f + "\033[2m" + "░" * (n - f) + "\033[0m"
print(f"{model} │ ctx {bar} {pct:.0f}% ({used/1000:.0f}k/{size/1000:.0f}k)", end="")
' "$input"
branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null)
[ -n "$branch" ] && printf ' │ ⎇ %s' "$branch"
