"""Файл-досье на игрока (.apexcase).

Внутри обычный ZIP: его можно переименовать в .zip и открыть любым архиватором.

    manifest.json   — данные игрока и список моментов (читает программа)
    timeline.txt    — таймкоды текстом
    report.html     — отчёт для браузера (после распаковки видео играют прямо в нём)
    ea_report.txt   — заготовка текста жалобы в EA (по моментам с оценкой 4–5)
    clips/*.mp4     — клипы моментов
    frames/*.jpg    — кадр в момент отметки
    portrait.png    — портрет легенды из списка отряда (вырез с экрана)

Главный ключ досье — идентификатор с экрана наблюдения; без него — ник.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import cv2
import numpy as np

from .i18n import t

CASE_EXT = ".apexcase"
FORMAT_VERSION = 2

# Твоя оценка момента.
SCALE = {
    1: "Мувмент, макрогейминг",
    2: "Нет чита",
    3: "Хорошая стрельба",
    4: "Подозрительно",
    5: "Чит",
}
SCALE_EN = {1: "movement / macro play", 2: "no cheat", 3: "good shooting", 4: "suspicious", 5: "cheating"}

PLATFORMS = ["PC", "PlayStation", "Xbox", "Switch", "Неизвестно"]

# Теги досье. OWNER — твоё собственное досье (моменты из своих игр).
TAGS = ["OWNER", "CHEATER", "PRO", "NOOB"]
OWNER = "OWNER"


def scale_text(n: int | None) -> str:
    return f"{n} · {t(SCALE[n])}" if n in SCALE else t("без оценки")


@dataclass
class Moment:
    id: str
    created_at: float
    note: str = ""
    legend: str = ""
    clip: str = ""                      # путь внутри архива
    frame: str = ""
    mark_offset: float = 0.0            # секунда клипа, где сам момент
    label: int | None = None            # твоя оценка 1–5 (см. SCALE)
    tag: str = "MANUAL"


@dataclass
class CaseInfo:
    case_id: str
    player_name: str
    platform: str = "PC"
    ea_id: str = ""                     # идентификатор с экрана наблюдения
    aliases: list[str] = field(default_factory=list)   # прежние ники с тем же ID
    clan_tag: str = ""
    teammates: list[str] = field(default_factory=list)
    legends: list[str] = field(default_factory=list)
    portrait: str = ""                  # portrait.png внутри архива
    first_seen: float = 0.0
    last_seen: float = 0.0
    notes: str = ""
    tags: list[str] = field(default_factory=list)       # см. TAGS
    pinned: bool = False                                # закреплено вверху списка
    moments: list[Moment] = field(default_factory=list)
    format_version: int = FORMAT_VERSION


def make_case_id(player_name: str, platform: str) -> str:
    key = f"{platform.strip().lower()}:{player_name.strip().casefold()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:60] or "player"


def fmt_ts(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fmt_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"


def identity_text(info: "CaseInfo") -> str:
    """Как называть игрока: ник, ID, легенда."""
    legend = info.legends[-1] if info.legends else t("легенда неизвестна")
    who = f"{info.player_name} · ID {info.ea_id}" if info.ea_id else f"{info.player_name} · {t('без ID')}"
    return f"{who} · {legend}"


class CaseFile:
    def __init__(self, path: Path, info: CaseInfo):
        self.path = Path(path)
        self.info = info
        self._extra: dict[str, Path] = {}

    # --- открытие/создание -----------------------------------------------

    @classmethod
    def open(cls, path: Path) -> "CaseFile":
        with zipfile.ZipFile(path) as zf:
            data = json.loads(zf.read("manifest.json").decode("utf-8"))
        known = {f.name for f in fields(Moment)}
        moments = [Moment(**{k: v for k, v in m.items() if k in known}) for m in data.pop("moments", [])]
        if data.get("format_version", 1) < 2:
            for m in moments:
                m.label = None      # в версии 1 была другая шкала оценок
        known = {f.name for f in fields(CaseInfo)}
        info = CaseInfo(**{k: v for k, v in data.items() if k in known})
        info.moments = moments
        info.format_version = FORMAT_VERSION
        return cls(Path(path), info)

    @staticmethod
    def list_cases(cases_dir: Path) -> list["CaseFile"]:
        result = []
        for p in Path(cases_dir).glob(f"*{CASE_EXT}"):
            try:
                result.append(CaseFile.open(p))
            except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                continue
        result.sort(key=lambda c: (c.info.pinned, c.info.last_seen), reverse=True)
        return result

    @classmethod
    def find_owner(cls, cases_dir: Path) -> "CaseFile | None":
        return next((c for c in cls.list_cases(cases_dir) if OWNER in c.info.tags), None)

    @classmethod
    def owner_case(cls, cases_dir: Path, name: str = "") -> "CaseFile":
        """Твоё досье: моменты из своих игр. Заводится один раз, дальше находится по тегу OWNER."""
        case = cls.find_owner(cases_dir)
        placeholder = t("Мои моменты")
        if case is None:
            case = cls.open_or_create(cases_dir, name or placeholder)
            case.info.tags.append(OWNER)
            case.info.pinned = True
            case.save()
        elif name and case.info.player_name == placeholder:
            case = case.set_identity(name, "", "")
        return case

    def set_tags(self, tags: list[str], pinned: bool) -> None:
        self.info.tags = [tg for tg in TAGS if tg in tags]
        self.info.pinned = pinned
        self.save()

    @classmethod
    def find(cls, cases_dir: Path, uid: str = "", name: str = "") -> "CaseFile | None":
        """Сначала по ID (он не меняется), потом по нику и прежним никам — но только среди досье без ID."""
        cases = cls.list_cases(cases_dir)
        if uid:
            hit = next((c for c in cases if c.info.ea_id == uid), None)
            if hit:
                return hit
        if name:
            key = name.strip().casefold()
            for c in cases:
                if uid and c.info.ea_id and c.info.ea_id != uid:
                    continue   # тот же ник, но другой ID — другой человек
                if any(n.casefold() == key for n in (c.info.player_name, *c.info.aliases)):
                    return c
        return None

    def expected_filename(self) -> str:
        i = self.info
        if i.ea_id:
            return f"{safe_filename(i.player_name)}_ID{safe_filename(i.ea_id)}{CASE_EXT}"
        return f"{safe_filename(i.player_name)}_{i.case_id}{CASE_EXT}"

    def rename_to_identity(self) -> None:
        target = self.path.with_name(self.expected_filename())
        if target != self.path and self.path.exists() and not target.exists():
            os.replace(self.path, target)
            self.path = target

    @classmethod
    def open_or_create(cls, cases_dir: Path, player_name: str, uid: str = "", legend: str = "",
                       platform: str = "PC") -> "CaseFile":
        cases_dir = Path(cases_dir)
        cases_dir.mkdir(parents=True, exist_ok=True)
        case = cls.find(cases_dir, uid=uid, name=player_name)
        now = time.time()
        if case is None:
            info = CaseInfo(uid or make_case_id(player_name, platform), player_name.strip(), platform,
                            ea_id=uid, first_seen=now, last_seen=now)
            case = cls(cases_dir / "new.apexcase", info)
            case.path = cases_dir / case.expected_filename()
        else:
            i = case.info
            i.last_seen = now
            if uid and not i.ea_id:
                i.ea_id = uid
            if player_name and player_name.strip() != i.player_name:
                if i.player_name not in i.aliases:
                    i.aliases.append(i.player_name)   # тот же ID, новый ник
                i.player_name = player_name.strip()
        if legend and legend not in case.info.legends:
            case.info.legends.append(legend)
        case.save()
        case.rename_to_identity()
        return case

    def set_identity(self, name: str, uid: str, legend: str) -> "CaseFile":
        """Ручная правка в досье. Если досье с таким ID уже есть — моменты переносятся туда."""
        i = self.info
        name, uid = name.strip() or i.player_name, uid.strip()
        if uid and uid != i.ea_id:
            other = CaseFile.find(self.path.parent, uid=uid)
            if other is not None and other.path != self.path:
                other.merge_from(self)
                if name not in (other.info.player_name, *other.info.aliases):
                    other.info.aliases.append(other.info.player_name)
                    other.info.player_name = name
                other.save()
                other.rename_to_identity()
                self.path.unlink(missing_ok=True)
                return other
            i.ea_id = uid
        if name != i.player_name:
            if i.player_name not in i.aliases:
                i.aliases.append(i.player_name)
            i.player_name = name
        if legend:
            i.legends = [lg for lg in i.legends if lg != legend] + [legend]
        self.save()
        self.rename_to_identity()
        return self

    def merge_from(self, src: "CaseFile") -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for m in src.info.moments:
                clip = src.extract(m.clip, Path(tmp)) if m.clip else None
                frame = src.extract(m.frame, Path(tmp)) if m.frame else None
                copy = Moment(**{k: v for k, v in asdict(m).items()})
                copy.clip, copy.frame = "", ""
                self.add_moment(copy, clip, frame)
        for lg in src.info.legends:
            if lg not in self.info.legends:
                self.info.legends.append(lg)
        self.info.first_seen = min(x for x in (self.info.first_seen, src.info.first_seen) if x) or time.time()

    # --- изменение -------------------------------------------------------

    def add_moment(self, moment: Moment, clip_src: Path | None, frame_src: Path | None) -> None:
        stem = f"{len(self.info.moments) + 1:03d}_{moment.id}"
        extra: dict[str, Path] = {}
        if clip_src and Path(clip_src).exists():
            moment.clip = f"clips/{stem}.mp4"
            extra[moment.clip] = Path(clip_src)
        if frame_src and Path(frame_src).exists():
            moment.frame = f"frames/{stem}.jpg"
            extra[moment.frame] = Path(frame_src)
        if moment.legend and moment.legend not in self.info.legends:
            self.info.legends.append(moment.legend)
        self.info.moments.append(moment)
        self.info.last_seen = max(self.info.last_seen, moment.created_at)
        self.save(extra)

    def update_moment(self, moment_id: str, label: int | None, note: str, legend: str | None = None) -> None:
        for m in self.info.moments:
            if m.id == moment_id:
                m.label, m.note = label, note
                if legend is not None:
                    m.legend = legend
                    if legend and legend not in self.info.legends:
                        self.info.legends.append(legend)
        self.save()

    def replace_clip(self, moment_id: str, src: Path, mark_offset: float) -> None:
        """Обрезанный клип вместо старого (старый из архива уходит — место освобождается)."""
        for m in self.info.moments:
            if m.id == moment_id and m.clip:
                m.mark_offset = mark_offset
                self.save({m.clip: Path(src)})

    def member_size(self, member: str) -> int:
        if not member or not self.path.exists():
            return 0
        with zipfile.ZipFile(self.path) as zf:
            try:
                return zf.getinfo(member).file_size
            except KeyError:
                return 0

    def remove_moment(self, moment_id: str) -> None:
        self.info.moments = [m for m in self.info.moments if m.id != moment_id]
        self.save()

    def set_portrait(self, portrait: np.ndarray) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "portrait.png"
            cv2.imencode(".png", portrait)[1].tofile(str(p))
            self.info.portrait = "portrait.png"
            self.save({"portrait.png": p})

    def portrait_image(self) -> np.ndarray | None:
        if not self.info.portrait or not self.path.exists():
            return None
        with zipfile.ZipFile(self.path) as zf:
            if self.info.portrait not in zf.namelist():
                return None
            data = np.frombuffer(zf.read(self.info.portrait), np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)

    def save(self, extra_files: dict[str, Path] | None = None) -> None:
        """ZIP нельзя править на месте, поэтому пересобираем архив и атомарно подменяем."""
        extra_files = extra_files or {}
        referenced = {p for m in self.info.moments for p in (m.clip, m.frame) if p}
        if self.info.portrait:
            referenced.add(self.info.portrait)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=self.path.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp_name, "w") as out:
                out.writestr("manifest.json", json.dumps(asdict(self.info), ensure_ascii=False, indent=2),
                             zipfile.ZIP_DEFLATED)
                out.writestr("timeline.txt", self.render_timeline(), zipfile.ZIP_DEFLATED)
                out.writestr("report.html", self.render_html(), zipfile.ZIP_DEFLATED)
                out.writestr("ea_report.txt", self.render_ea_report(), zipfile.ZIP_DEFLATED)
                if self.path.exists():
                    with zipfile.ZipFile(self.path) as old:
                        for item in old.infolist():
                            if item.filename in referenced and item.filename not in extra_files:
                                with old.open(item) as src, out.open(item.filename, "w") as dst:
                                    shutil.copyfileobj(src, dst, 1024 * 1024)
                for arcname, src in extra_files.items():
                    out.write(src, arcname, zipfile.ZIP_STORED)   # видео уже сжато
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # --- чтение содержимого ---------------------------------------------

    def extract(self, member: str, dest_dir: Path) -> Path | None:
        if not member:
            return None
        dest = Path(dest_dir) / self.info.case_id / member
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.path) as zf:
            if member not in zf.namelist():
                return None
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        return dest

    def export_zip(self, dest: Path) -> Path:
        dest = Path(dest)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        shutil.copyfile(self.path, dest)
        return dest

    # --- текстовые отчёты ------------------------------------------------

    def render_timeline(self) -> str:
        i = self.info
        lines = [
            f"{t('Игрок')}: {i.player_name}   ID: {i.ea_id or '—'}   {t('Прежние ники')}: {', '.join(i.aliases) or '—'}",
            f"{t('Легенды')}: {', '.join(i.legends) or '—'}   {t('Клан')}: {i.clan_tag or '—'}   "
            f"{t('Тиммейты')}: {', '.join(i.teammates) or '—'}",
            f"{t('Первая встреча')}: {fmt_date(i.first_seen)}   {t('Последняя')}: {fmt_date(i.last_seen)}",
            f"{t('Моментов')}: {len(i.moments)}",
            "",
        ]
        for n, m in enumerate(i.moments, 1):
            lines.append(f"#{n}  {fmt_date(m.created_at)}  {t('момент на')} {fmt_ts(m.mark_offset)}  "
                         f"{scale_text(m.label)}  {m.clip or t('нет видео')}")
            if m.note:
                lines.append(f"    {m.note}")
        return "\n".join(lines) + "\n"

    def render_ea_report(self) -> str:
        """Текст жалобы на английском — только моменты, которые ты оценил как 4 или 5."""
        i = self.info
        evidence = [m for m in i.moments if m.label in (4, 5)]
        lines = ["Subject: Cheating report — Apex Legends", ""]
        if i.ea_id:
            lines.append(f"Player ID (shown in spectator view): {i.ea_id}")
        lines += [f"Player name: {i.player_name}" + (f" (previously: {', '.join(i.aliases)})" if i.aliases else ""),
                  f"Legend(s): {', '.join(i.legends) or 'unknown'}", "",
                  "I spectated this player after being eliminated and recorded the following moments.", ""]
        if not evidence:
            lines.append("(No moments rated 4 — suspicious or 5 — cheating yet.)")
        for n, m in enumerate(evidence, 1):
            lines.append(f"{n}. {fmt_date(m.created_at)} — at {fmt_ts(m.mark_offset)} in {m.clip or 'screenshot'} — "
                         f"{SCALE_EN[m.label]}" + (f" ({m.note})" if m.note else ""))
        lines += ["", "Clips were captured by screen recording of the spectator view only.", ""]
        return "\n".join(lines)

    def render_html(self) -> str:
        i = self.info
        esc = html.escape
        rows = []
        for n, m in enumerate(i.moments, 1):
            media = ""
            if m.clip:
                media = (f'<video controls preload="metadata" src="{esc(m.clip)}#t={m.mark_offset:.1f}"'
                         f'{f" poster=\"{esc(m.frame)}\"" if m.frame else ""}></video>')
            elif m.frame:
                media = f'<img src="{esc(m.frame)}" alt="">'
            rows.append(f"""
<section class="moment">
  <div class="meta"><b>#{n} · {esc(scale_text(m.label))}</b>
  <span>{fmt_date(m.created_at)} · {esc(t('момент на'))} {fmt_ts(m.mark_offset)}</span></div>
  {f'<p>{esc(m.note)}</p>' if m.note else ''}
  {media}
</section>""")
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(identity_text(i))}</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#121417;color:#e6e6e6;margin:0;padding:24px;max-width:1100px;margin:auto}}
h1{{margin:0 0 4px}} .sub{{color:#9aa0a6;margin-bottom:24px}}
.moment{{background:#1c1f24;border-radius:10px;padding:16px;margin:16px 0}}
.meta{{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between}} .meta span{{color:#9aa0a6}}
video,img{{width:100%;border-radius:6px;margin-top:10px;background:#000}}
.note{{color:#9aa0a6;font-size:13px}}
</style></head><body>
<h1>{esc(identity_text(i))}</h1>
<div class="sub">{esc(t('Прежние ники'))}: {esc(', '.join(i.aliases) or '—')} · {esc(t('Тиммейты'))}:
{esc(', '.join(i.teammates) or '—')}<br>{esc(t('Первая встреча'))}: {fmt_date(i.first_seen)} ·
{esc(t('Последняя'))}: {fmt_date(i.last_seen)} · {esc(t('Моментов'))}: {len(i.moments)}</div>
{''.join(rows) or '<p>—</p>'}
<p class="note">{esc(t('Распакуйте архив целиком, чтобы видео воспроизводились в этом отчёте.'))}</p>
</body></html>
"""


def move_to_recycle_bin(path: Path) -> bool:
    """Удаление в Корзину Windows (не навсегда) — поэтому можно без подтверждения."""
    import ctypes
    from ctypes import wintypes

    class _ShFileOp(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = _ShFileOp(None, FO_DELETE, str(Path(path).resolve()) + "\0", None,
                   FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not Path(path).exists()
