#!/usr/bin/env python3
"""
Общие хелперы сериализации данных сборщика в JSON для витрин.

Используются и webapp.py (витрина в /), и design_app.py (витрина в /design/) —
вынесены сюда, чтобы витрины не зависели друг от друга.
"""
from __future__ import annotations

import json


def rows_to_data(rows) -> list[dict]:
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


def to_json(obj) -> str:
    # </script> внутри данных сломал бы страницу — экранируем слэш.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
