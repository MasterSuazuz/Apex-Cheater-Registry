"""Портреты легенд (как в списке отряда в игре) — скачиваются с вики Apex Legends.

Готовые картинки лежат в assets/legends и идут вместе с программой. Когда выйдет новая легенда,
кнопка в настройках скачает недостающие (файлы Portrait_<Имя>_square.png).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .config import ASSETS_DIR

ICON_DIR = ASSETS_DIR / "legends"
ICON_SIZE = 160
API = "https://apexlegends.fandom.com/api.php"
SKIP = {"Fade", "Rhapsody", "Leigh"}      # легенды Apex Mobile, в обычной игре их нет
_UA = {"User-Agent": "ApexCheatDetector/1.0 (legend icons)"}


def _get(url: str, timeout: float = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def _api(**params) -> dict:
    params["format"] = "json"
    return json.loads(_get(API + "?" + urllib.parse.urlencode(params)))


def remote_list() -> dict[str, str]:
    """Имя легенды -> адрес картинки."""
    items = _api(action="query", list="allimages", aiprefix="Portrait_", ailimit=500)["query"]["allimages"]
    out = {}
    for it in items:
        name = it["name"]
        if not name.endswith("_square.png"):
            continue
        legend = name[len("Portrait_"):-len("_square.png")].replace("_", " ")
        if legend not in SKIP:
            out[legend] = it["url"]
    return out


def icon_path(legend: str) -> Path:
    return ICON_DIR / f"{legend}.png"


def installed() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.png")) if ICON_DIR.exists() else []


def download(force: bool = False, progress=None) -> list[str]:
    """Скачивает портреты, которых ещё нет. Возвращает список новых легенд."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    added = []
    for legend, url in sorted(remote_list().items()):
        dst = icon_path(legend)
        if dst.exists() and not force:
            continue
        img = cv2.imdecode(np.frombuffer(_get(url), np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.ndim == 3 and img.shape[2] == 4:      # прозрачный фон -> тёмный, как в интерфейсе игры
            a = img[:, :, 3:4].astype(np.float32) / 255
            img = (img[:, :, :3].astype(np.float32) * a + 20 * (1 - a)).astype(np.uint8)
        img = cv2.resize(img, (ICON_SIZE, ICON_SIZE), interpolation=cv2.INTER_AREA)
        cv2.imencode(".png", img)[1].tofile(str(dst))
        added.append(legend)
        if progress:
            progress(legend)
    return added


if __name__ == "__main__":
    print(download(force=True))
