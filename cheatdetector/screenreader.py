"""Чтение экрана наблюдения Apex по его устройству, а не по координатам, которые размечает человек.

Что ищем (проверено на реальном скриншоте русской версии игры):
- Режим зрителя: вверху вкладки «В ЭФИРЕ» и «РЕЖИМ ЗРИТЕЛЯ» (в английской — LIVE и SPECTATE).
  Запись разрешена, только когда видны обе.
- ID наблюдаемого: внизу справа «Идентификатор: 016045689983748751123» — длинная цепочка цифр.
- Ник наблюдаемого: красная плашка внизу по центру. Сверяем с нижней строкой списка отряда слева —
  у Apex это строка того, чей экран сейчас показан.
- Тиммейты: строки списка отряда выше.
- Портрет легенды: квадрат слева от ника в списке отряда — сравнивается с официальными портретами
  легенд (см. legends.py).
- Своя игра (вкладок нет): нижняя строка списка отряда — сам пользователь, его ник и легенда.

Читаем не весь кадр, а полосы: верх (вкладки) и низ (ник, ID, отряд) — это быстрее и точнее.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import ocr

# Надписи вкладок (начало слов, без регистра). Можно дополнить в настройках.
LIVE_WORDS = ["в эфире", "live"]
SPECTATE_WORDS = ["режим зрителя", "spectat"]
ID_WORDS = ["идентификатор", "identifier", "id:"]
# Надписи подсказок внизу экрана — не ники.
HINT_WORDS = ("продолжить", "пожаловаться", "лобби", "меню", "союзник", "баннер", "space", "continue", "report",
              "lobby", "menu", "teammate", "banner", "удерж", "hold", "esc", "tab", "lshift")
FEED_WORDS = ("spotted", "eliminated", "kill leader", "bleed out", "knocked", "killed", "заметил", "устранен",
              "устранён", "лидер", "нокаут", "убит", "вижу", "врага", "enemy")
LEVEL_WORDS = ("lvl", "level", "evo", "уровень", "ур.")
CLAN_RE = re.compile(r"^\s*[\[\(]([^\]\)]{1,6})[\]\)]\s*")
DIGITS_RE = re.compile(r"\d{12,24}")

TOP_BAND = (0.25, 0.0, 0.75, 0.14)      # вкладки «В ЭФИРЕ / СВОДКА» и «РЕЖИМ ЗРИТЕЛЯ / ОТРЯД»
BOTTOM_BAND = (0.30, 0.86, 1.0, 0.97)   # «Идентификатор: …» справа внизу
BANNER_ZONE = (0.38, 0.885, 0.62, 0.96) # красная плашка с ником по центру (читается только отдельно)
SQUAD_ZONE = (0.0, 0.6, 0.23, 0.92)     # список отряда слева внизу; правее начинаются субтитры
PORTRAIT_H = 96


@dataclass
class Word:
    text: str
    x: float        # центр, доли кадра
    y: float
    h: float        # высота, доли кадра
    line: int
    w: float = 0.0  # ширина, доли кадра


@dataclass
class Line:
    text: str
    x0: float
    x1: float
    y: float
    h: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class ScanResult:
    live: bool = False          # видна вкладка «В ЭФИРЕ»
    spectating: bool = False    # видна вкладка «РЕЖИМ ЗРИТЕЛЯ» (вместе с «В ЭФИРЕ»)
    name: str = ""              # ник наблюдаемого
    player_id: str = ""         # его идентификатор
    clan_tag: str = ""
    teammates: list[str] = field(default_factory=list)
    portrait: np.ndarray | None = None   # портрет легенды (цветной вырез, высота PORTRAIT_H)
    confident: bool = False     # ник с плашки совпал со строкой отряда
    own: bool = False           # своя игра: name/portrait — сам пользователь


async def _ocr_words(img: np.ndarray, lang: str, line_offset: int):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    engine = ocr._engine(lang)
    if engine is None:
        return []
    bgra = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2BGRA))
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                  bgra.shape[1], bgra.shape[0])
    result = await engine.recognize_async(bmp)
    out = []
    for li, line in enumerate(result.lines):
        for w in line.words:
            r = w.bounding_rect
            out.append((w.text, r.x, r.y, r.width, r.height, line_offset + li))
    return out


def read_zone(frame: np.ndarray, zone: tuple[float, float, float, float], scale: float = 2.0,
              lang: str = "en-US") -> list[Word]:
    """Один проход OCR по области целиком."""
    if not ocr.AVAILABLE or frame is None:
        return []
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = int(zone[0] * w), int(zone[1] * h), int(zone[2] * w), int(zone[3] * h)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    try:
        raw = asyncio.run(_ocr_words(crop, lang, 0))
    except Exception:
        return []
    return [Word(t, (x0 + (x + ww / 2) / scale) / w, (y0 + (y + hh / 2) / scale) / h, hh / scale / h, line,
                 ww / scale / w) for t, x, y, ww, hh, line in raw]


def group_lines(words: list[Word]) -> list[Line]:
    by_line: dict[int, list[Word]] = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    out = []
    for ws in by_line.values():
        ws.sort(key=lambda w: w.x)
        text = " ".join(w.text for w in ws)
        out.append(Line(text, ws[0].x - ws[0].w / 2, ws[-1].x + ws[-1].w / 2, sum(w.y for w in ws) / len(ws),
                        max(w.h for w in ws)))
    return out


def has_phrase(lines: list[Line], phrases: list[str]) -> bool:
    for ln in lines:
        words = ln.text.casefold().split()
        for phrase in phrases:
            parts = phrase.casefold().split()
            for i in range(len(words) - len(parts) + 1):
                if all(words[i + j].startswith(p) for j, p in enumerate(parts)):
                    return True
    return False


def clean_name(text: str) -> tuple[str, str]:
    """'[WAYA]AURORA ajrein 4' -> ('AURORA ajrein', 'WAYA'): убираем клан-тег и следы значков."""
    text = text.replace("—", "_").replace("–", "_").replace("Ø", "0").replace("ø", "0")
    tag = ""
    m = CLAN_RE.match(text)
    if m:
        tag, text = m.group(1).strip(), text[m.end():]
    tokens = [t.strip(".,:;'\"«»|„“”") for t in text.split()]
    # Хвосты от значков: одиночные символы и номера уровня («BLenDO 14.»).
    while tokens and (len(tokens[-1]) <= 1 or tokens[-1].strip(".,").isdigit()):
        tokens.pop()
    while tokens and len(tokens[0]) <= 1:
        tokens.pop(0)
    return " ".join(t for t in tokens if t), tag


def plausible_name(name: str) -> bool:
    if not 3 <= len(name) <= 32 or len(name.split()) > 2:
        return False
    letters = sum(c.isalpha() for c in name)
    if letters < 3 or any(ord(c) > 127 for c in name) or ":" in name:
        return False     # «Валькирия: …» — субтитры, а не ник
    if len(set(name.casefold().replace(" ", ""))) < 3 or not re.search(r"[A-Za-z0-9]{3,}", name):
        return False     # «iii», «g.ti» — следы значков
    low = name.casefold()
    return not any(h in low for h in HINT_WORDS + FEED_WORDS + LEVEL_WORDS)


def same_name(a: str, b: str) -> bool:
    """Ники совпадают с точностью до типичных ошибок OCR (l/I/1, O/0, регистр, _ и пробел)."""
    norm = lambda s: s.casefold().translate(str.maketrans("il|10-—– ", "llllo____")).replace("_", "")
    return bool(a and b) and norm(a) == norm(b)


class ScreenReader:
    def __init__(self, live_words=None, spectate_words=None, id_words=None):
        self.live_words = live_words or LIVE_WORDS
        self.spectate_words = spectate_words or SPECTATE_WORDS
        self.id_words = id_words or ID_WORDS

    def tabs(self, frame: np.ndarray) -> tuple[bool, bool]:
        """Вкладки наверху. Русские надписи читает русский OCR, английские — английский."""
        for lang in ("ru", "en-US"):
            lines = group_lines(read_zone(frame, TOP_BAND, 1.5, lang))
            live, spect = has_phrase(lines, self.live_words), has_phrase(lines, self.spectate_words)
            if live or spect:
                return live, spect
        return False, False

    def bottom(self, frame: np.ndarray) -> tuple[str, str]:
        """Ник с красной плашки и ID из нижней полосы."""
        lines = group_lines(read_zone(frame, BOTTOM_BAND, 2.0, "en-US"))
        banner = group_lines(read_zone(frame, BANNER_ZONE, 2.0, "en-US"))
        player_id = ""
        best = []
        for ln in lines:
            for d in DIGITS_RE.findall(ln.text.replace(" ", "")):
                best.append((ln.x0 > 0.55, len(d), d))   # ID справа; слева внизу бывает служебный код
        if best:
            player_id = max(best)[2]
        name = ""
        cands = []
        for ln in banner:
            if abs(ln.cx - 0.5) > 0.12:
                continue   # плашка с ником — по центру
            n, _ = clean_name(ln.text)
            if plausible_name(n):
                cands.append((abs(ln.cx - 0.5), n))
        if cands:
            name = min(cands)[1]
        return name, player_id

    def squad(self, frame: np.ndarray, banner: str = "",
              need_level: bool = False) -> tuple[str, str, list[str], np.ndarray | None]:
        """Список отряда: владелец экрана — под полосой уровня или нижний; тиммейты — выше.
        need_level — без полосы уровня (EVO) это не интерфейс игры: ничего не берём."""
        all_lines = group_lines(read_zone(frame, SQUAD_ZONE, 2.0, "en-US"))
        level = [ln for ln in all_lines if any(w in ln.text.casefold() for w in LEVEL_WORDS)]
        if need_level and not level:
            return "", "", [], None
        entries = []
        for ln in all_lines:
            if ln in level or ln.x0 > 0.12:
                continue   # строки отряда начинаются у левого края
            n, tag = clean_name(ln.text)
            if plausible_name(n):
                entries.append((ln, n, tag))
        if not entries:
            return "", "", [], None
        # Обрывки значков мельче шрифта ников.
        med = float(np.median([e[0].h for e in entries]))
        entries = [e for e in entries if e[0].h >= 0.7 * med]
        entries.sort(key=lambda e: e[0].y)
        # Строка, совпавшая с ником на плашке, — точно владелец экрана.
        own = next((e for e in entries if banner and same_name(e[1], banner)), None)
        for lv in ([] if own else level):
            below = [e for e in entries if 0 < e[0].y - lv.y < 0.08]
            if below:
                own = min(below, key=lambda e: e[0].y - lv.y)
        if own is None and level:
            # Под полосой уровня — строка владельца, даже если ник не прочитался (его портрет всё равно нужен).
            lv = level[-1]
            raw = [ln for ln in all_lines if ln not in level and ln.x0 <= 0.12 and 0 < ln.y - lv.y < 0.08]
            if raw:
                ln = min(raw, key=lambda x: x.y)
                mates = [n for e, n, _ in entries if e.y < ln.y and ln.y - e.y < 0.3]
                return "", "", mates, self._portrait(frame, ln)
        own = own or entries[-1]
        mates = [n for ln, n, _ in entries if ln.y < own[0].y and own[0].y - ln.y < 0.3]
        return own[1], own[2], mates, self._portrait(frame, own[0])

    @staticmethod
    def _portrait(frame: np.ndarray, ln: Line) -> np.ndarray | None:
        """Портрет легенды — квадрат слева от ника в строке отряда."""
        # С запасом: портрет у владельца крупнее, чем у тиммейтов; legends.match ищет лицо внутри.
        h, w = frame.shape[:2]
        x1 = int(ln.x0 * w)
        x0 = max(0, x1 - int(0.075 * h))
        y0 = max(0, int(ln.y * h - 0.035 * h))
        y1 = min(h, int(ln.y * h + 0.045 * h))
        crop = frame[y0:y1, x0:x1]
        if crop.shape[0] < 20 or crop.shape[1] < 20:
            return None
        width = round(crop.shape[1] * PORTRAIT_H / crop.shape[0])
        return cv2.resize(crop, (width, PORTRAIT_H), interpolation=cv2.INTER_AREA)

    def scan(self, frame: np.ndarray, full: bool = True) -> ScanResult:
        res = ScanResult()
        res.live, spect = self.tabs(frame)
        res.spectating = res.live and spect
        if not full or (res.live and not res.spectating):
            return res      # другие вкладки экрана смерти — там никого не отмечаем
        if not res.spectating:
            # Своя игра: ник и легенда самого пользователя из списка отряда.
            res.own = True
            res.name, res.clan_tag, res.teammates, res.portrait = self.squad(frame, need_level=True)
            return res
        banner, res.player_id = self.bottom(frame)
        own, res.clan_tag, res.teammates, res.portrait = self.squad(frame, banner)
        # Плашка надёжнее (крупный шрифт); строка отряда — проверка и запасной вариант.
        res.name = banner or own
        res.confident = bool(banner and own and same_name(banner, own))
        return res
