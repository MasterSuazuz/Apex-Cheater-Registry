"""Распознавание текста встроенным OCR Windows (Windows.Media.Ocr).

Работает без интернета и без установки моделей; языки — те, что стоят в Windows
(обычно английский и русский). Ники в Apex латиницей, поэтому основной — английский.
"""
from __future__ import annotations

import asyncio
import difflib
import re

import cv2
import numpy as np

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    AVAILABLE = True
except ImportError:  # старая Windows или пакеты не установлены
    AVAILABLE = False

_engines: dict[str, object] = {}


def _engine(lang: str):
    if lang not in _engines:
        _engines[lang] = OcrEngine.try_create_from_language(Language(lang)) if AVAILABLE else None
    return _engines[lang]


def _prepare(img: np.ndarray) -> np.ndarray:
    """OCR Windows плохо читает мелкий текст — увеличиваем до ~40 px высоты строки."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = max(2.0, min(4.0, 80 / max(1, gray.shape[0])))
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # Без поля вокруг текст у самого края OCR часто не видит вообще.
    gray = cv2.copyMakeBorder(gray, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=int(np.median(gray)))
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA)


async def _recognize(img: np.ndarray, lang: str) -> list[str]:
    engine = _engine(lang)
    if engine is None:
        return []
    bgra = np.ascontiguousarray(_prepare(img))
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                     bgra.shape[1], bgra.shape[0])
    result = await engine.recognize_async(bitmap)
    return [line.text for line in result.lines]


def read_lines(img: np.ndarray, lang: str = "en-US") -> list[str]:
    if not AVAILABLE or img is None or img.size == 0:
        return []
    try:
        return asyncio.run(_recognize(img, lang))
    except Exception:
        return []


def crop(frame: np.ndarray, region: list[float]) -> np.ndarray | None:
    if frame is None or len(region) != 4:
        return None
    h, w = frame.shape[:2]
    x, y, rw, rh = region
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + rw) * w)), min(h, int((y + rh) * h))
    return frame[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else None


# Слова интерфейса, которые могут попасть в область ника.
_UI_WORDS = {"spectating", "наблюдение", "наблюдаете", "you", "are", "squad", "отряд", "champion", "чемпион",
             "kill", "leader", "killer", "убийца", "lvl", "level"}


def clean_name(lines: list[str]) -> str:
    """Из строк OCR выбираем то, что похоже на ник: самое длинное слово без мусора интерфейса."""
    candidates = []
    for line in lines:
        # Подчёркивание OCR читает как тире, а ноль — как перечёркнутую «Ø».
        line = line.replace("—", "_").replace("–", "_").replace("Ø", "0").replace("ø", "0")
        for word in re.split(r"\s+", line.strip()):
            w = word.strip("[]()<>|:;,.'\"«»")
            if len(w) < 2 or w.casefold() in _UI_WORDS or w.isdigit():
                continue
            candidates.append(w)
    return max(candidates, key=len) if candidates else ""


def best_match(name: str, known: list[str], cutoff: float = 0.82) -> str | None:
    """OCR путает l/I/1 и O/0 — сверяем с уже известными никами из досье."""
    # Склеиваем только по типичным ошибкам OCR. Простое «похоже» опасно: PlayerA и PlayerB —
    # разные люди, и их моменты нельзя складывать в одно досье.
    def norm(s: str) -> str:
        return s.casefold().translate(str.maketrans("il|10-—–", "llllo___"))
    table = {norm(k): k for k in known}
    key = norm(name)
    if key in table:
        return table[key]
    # OCR срезает начало/конец длинного ника под значками («utube.com/@Name» вместо «youtube.com/@Name»).
    if len(key) >= 8:
        for k in table:
            if 0 < abs(len(k) - len(key)) <= 3 and min(len(k), len(key)) >= 8 and (
                    k.endswith(key) or key.endswith(k) or k.startswith(key) or key.startswith(k)):
                return table[k]
    # Для длинных ников допускаем одну пропущенную/лишнюю букву (OCR иногда теряет символ на краю).
    if len(key) >= 10:
        hit = difflib.get_close_matches(key, [k for k in table if abs(len(k) - len(key)) == 1], n=1,
                                        cutoff=max(cutoff, 0.94))
        if hit:
            return table[hit[0]]
    return None
