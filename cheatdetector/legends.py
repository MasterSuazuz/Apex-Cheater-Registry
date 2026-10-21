"""Узнавание легенды по портрету в списке отряда.

Эталоны — официальные портреты легенд (assets/legends, скачаны с вики, см. legend_icons.py):
в игре в списке отряда показан тот же рисунок, только меньше и в рамке. Центр эталона ищется
внутри выреза с экрана на нескольких масштабах (нормированная корреляция).
Если пользователь исправит легенду в досье, вырез с экрана тоже запоминается и дальше помогает.
"""
from __future__ import annotations

import hashlib

import cv2
import numpy as np

from .config import APP_DIR
from .legend_icons import ICON_DIR, icon_path, installed

LIB_DIR = APP_DIR / "legends"
MIN_SCORE = 0.62
SURE_SCORE = 0.8
MIN_MARGIN = 0.08
SCALES = (0.55, 0.65, 0.75, 0.85, 0.95)   # размер лица относительно высоты выреза

LEGENDS = ["Alter", "Ash", "Axle", "Ballistic", "Bangalore", "Bloodhound", "Catalyst", "Caustic", "Conduit",
           "Crypto", "Fuse", "Gibraltar", "Horizon", "Lifeline", "Loba", "Mad Maggie", "Mirage", "Newcastle",
           "Octane", "Pathfinder", "Rampart", "Revenant", "Seer", "Sparrow", "Valkyrie", "Vantage", "Wattson",
           "Wraith"]

_icons: dict[str, np.ndarray] | None = None
_learned: dict[str, list[np.ndarray]] | None = None


def all_legends() -> list[str]:
    return sorted(set(LEGENDS) | set(installed()))


def _prep(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.equalizeHist(g)


def _read(path) -> np.ndarray | None:
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def _load_icons() -> dict[str, np.ndarray]:
    global _icons
    if _icons is None:
        _icons = {}
        if ICON_DIR.exists():
            for p in ICON_DIR.glob("*.png"):
                img = _read(p)
                if img is not None:
                    _icons[p.stem] = img
    return _icons


def _load_learned() -> dict[str, list[np.ndarray]]:
    global _learned
    if _learned is None:
        _learned = {}
        if LIB_DIR.exists():
            for d in LIB_DIR.iterdir():
                if d.is_dir():
                    imgs = [img for p in d.glob("*.png") if (img := _read(p)) is not None and img.shape[0] >= 64]
                    if imgs:
                        _learned[d.name] = imgs
    return _learned


def reload() -> None:
    global _icons, _learned
    _icons = _learned = None


def _icon_score(crop_g: np.ndarray, icon: np.ndarray) -> float:
    hh = crop_g.shape[0]
    best = -1.0
    for frac in SCALES:
        s = int(hh * frac)
        m = int(s * 0.2)
        tpl = _prep(cv2.resize(icon, (s, s), interpolation=cv2.INTER_AREA))[m:s - m, m:s - m]
        if tpl.shape[0] >= crop_g.shape[0] or tpl.shape[1] >= crop_g.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(crop_g, tpl, cv2.TM_CCOEFF_NORMED).max()))
    return best


def _learned_score(crop_g: np.ndarray, ref: np.ndarray) -> float:
    r = _prep(cv2.resize(ref, (round(ref.shape[1] * crop_g.shape[0] / ref.shape[0]), crop_g.shape[0])))
    m = crop_g.shape[0] // 6
    core = crop_g[m:-m, m:-m]
    if core.shape[0] >= r.shape[0] or core.shape[1] >= r.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(r, core, cv2.TM_CCOEFF_NORMED).max())


def scores(crop: np.ndarray | None) -> list[tuple[str, float]]:
    if crop is None or crop.ndim != 3 or crop.shape[0] < 40:
        return []
    g = _prep(crop)
    res: dict[str, float] = {}
    for legend, icon in _load_icons().items():
        res[legend] = _icon_score(g, icon)
    for legend, refs in _load_learned().items():
        for ref in refs:
            res[legend] = max(res.get(legend, -1.0), _learned_score(g, ref))
    return sorted(res.items(), key=lambda kv: -kv[1])


def match(crop: np.ndarray | None) -> tuple[str, float]:
    ranked = scores(crop)
    if not ranked:
        return "", 0.0
    (best, top), second = ranked[0], (ranked[1][1] if len(ranked) > 1 else -1.0)
    if top >= SURE_SCORE or (top >= MIN_SCORE and top - second >= MIN_MARGIN):
        return best, top
    return "", top


def learn(legend: str, crop: np.ndarray | None) -> bool:
    """Запомнить вырез с экрана (пользователь указал легенду в досье)."""
    if crop is None or not legend or crop.ndim != 3 or crop.shape[0] < 64:
        return False
    if match(crop)[0] == legend:
        return False        # и так узнаётся — не засоряем библиотеку
    d = LIB_DIR / legend
    d.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(crop.tobytes()).hexdigest()[:12]
    cv2.imencode(".png", crop)[1].tofile(str(d / f"{name}.png"))
    _load_learned().setdefault(legend, []).append(crop)
    return True


def known() -> list[str]:
    return sorted(set(_load_icons()) | set(_load_learned()))


__all__ = ["all_legends", "icon_path", "learn", "match", "known", "scores", "reload"]
