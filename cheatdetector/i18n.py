"""Языки интерфейса: русский (исходный) и английский.

Строки в коде пишутся по-русски и оборачиваются в t(); английский перевод — в i18n_en.py.
Язык выбирается в настройках и применяется после перезапуска.
"""
from __future__ import annotations

from .i18n_en import EN

_lang = "ru"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in ("ru", "en") else "ru"


def language() -> str:
    return _lang


def t(text: str) -> str:
    return EN.get(text, text) if _lang == "en" else text
