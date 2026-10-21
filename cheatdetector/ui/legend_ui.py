"""Иконки легенд и теги досье для интерфейса."""
from __future__ import annotations

import html
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..casefile import TAGS
from ..legend_icons import icon_path
from . import theme

TAG_STYLE = {
    "OWNER": ("info", theme.INFO),
    "CHEATER": ("rec", theme.ACCENT_HOVER),
    "PRO": ("warn", theme.WARN),
    "NOOB": ("muted", theme.MUTED),
}


@lru_cache(maxsize=256)
def legend_pixmap(legend: str, size: int = 32) -> QPixmap:
    """Круглая иконка легенды; без картинки — первая буква на тёмном круге."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    p.setClipPath(clip)
    src = QPixmap(str(icon_path(legend))) if legend else QPixmap()
    if not src.isNull():
        p.drawPixmap(0, 0, src.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
    else:
        p.fillRect(0, 0, size, size, QColor(theme.BORDER))
        p.setPen(QColor(theme.MUTED))
        f = p.font()
        f.setPixelSize(int(size * 0.45))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, (legend[:1] or "?").upper())
    p.end()
    return pm


def legend_icon(legend: str, size: int = 32) -> QIcon:
    return QIcon(legend_pixmap(legend, size))


class LegendStrip(QWidget):
    """Несколько иконок легенд в ряд (с подсказкой — имя)."""

    def __init__(self, legends: list[str], size: int = 26, limit: int = 6):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(3)
        for lg in legends[-limit:]:
            lbl = QLabel()
            lbl.setPixmap(legend_pixmap(lg, size))
            lbl.setToolTip(lg)
            lay.addWidget(lbl)
        if not legends:
            lay.addWidget(QLabel("—"))
        lay.addStretch(1)


class NameCell(QWidget):
    """Ячейка «Игрок»: иконка легенды, ник, теги. Щелчки проходят в таблицу (двойной — открыть досье)."""

    def __init__(self, legend: str, name: str, extra: str, tags: list[str]):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 4, 0)
        lay.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(legend_pixmap(legend, 28))
        lay.addWidget(icon)
        lbl = QLabel(html.escape(name) + (f"  <span style='color:{theme.MUTED}'>({html.escape(extra)})</span>"
                                           if extra else ""))
        lay.addWidget(lbl)
        for tg in tags:
            lay.addWidget(theme.chip(tg, TAG_STYLE.get(tg, ("muted", ""))[0]))
        lay.addStretch(1)


class TagButtons(QWidget):
    """Переключатели тегов в досье."""

    def __init__(self, on_change):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for tg in TAGS:
            b = QPushButton(tg)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            color = TAG_STYLE[tg][1]
            b.setStyleSheet(f"QPushButton {{ padding: 4px 12px; border-radius: 12px; font-weight: 600; }}"
                            f"QPushButton:checked {{ background: {color}; color: #0d0f13; border-color: {color}; }}")
            b.toggled.connect(lambda _on: on_change())
            self.buttons[tg] = b
            lay.addWidget(b)
        lay.addStretch(1)

    def set_tags(self, tags: list[str]):
        for tg, b in self.buttons.items():
            b.blockSignals(True)
            b.setChecked(tg in tags)
            b.blockSignals(False)

    def tags(self) -> list[str]:
        return [tg for tg, b in self.buttons.items() if b.isChecked()]
