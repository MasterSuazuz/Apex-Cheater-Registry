"""Индикатор поверх игры: идёт ли запись, автозапись, отметки.

Это обычное отдельное окно «поверх всех», а не внедрение в игру:
- клики проходят сквозь него в игру, фокус оно не забирает;
- исключено из захвата экрана (WDA_EXCLUDEFROMCAPTURE), поэтому не попадает в клипы.
Поверх эксклюзивного полноэкранного режима Windows такие окна не показывает —
нужен режим «Без рамки» или «В окне».
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..display import CaptureTarget

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x10, 0x40
WDA_EXCLUDEFROMCAPTURE = 0x11
MARGIN = 16

RED, GREEN, YELLOW, GREY = QColor(235, 64, 52), QColor(76, 217, 123), QColor(250, 200, 70), QColor(150, 155, 165)


class Overlay(QWidget):
    def __init__(self):
        # Не Qt.Tool: такие окна Windows/Qt прячут, когда главное окно программы свёрнуто — а во время игры
        # оно почти всегда свёрнуто. Кнопку на панели задач убирает WS_EX_TOOLWINDOW (см. showEvent).
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.font_main = QFont("Segoe UI", 11, QFont.DemiBold)
        self.font_small = QFont("Segoe UI", 9)
        self.lines: list[tuple[QColor, str]] = []
        self.flash_text = ""
        self.flash_until = 0.0
        self.blink = True
        self._target: CaptureTarget | None = None
        self._corner = "top-left"
        self._native_ready = False
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._on_blink)
        self._blink_timer.start(500)

    # --- данные --------------------------------------------------------------

    def flash(self, text: str, seconds: float = 2.5) -> None:
        self.flash_text, self.flash_until = text, time.monotonic() + seconds
        self._relayout()

    def set_state(self, lines: list[tuple[QColor, str]], target: CaptureTarget | None,
                  corner: str, opacity: int) -> None:
        self.lines = lines
        self._target, self._corner = target, corner
        self.setWindowOpacity(max(0.2, min(1.0, opacity / 100)))
        self._relayout()

    # --- окно ----------------------------------------------------------------

    def _on_blink(self):
        self.blink = not self.blink
        if self.flash_text and time.monotonic() > self.flash_until:
            self.flash_text = ""
            self._relayout()
        self.update()

    def _all_lines(self):
        lines = list(self.lines)
        if self.flash_text:
            lines.append((YELLOW, self.flash_text))
        return lines

    def _relayout(self):
        lines = self._all_lines()
        fm_main, fm_small = QFontMetrics(self.font_main), QFontMetrics(self.font_small)
        width = 0
        height = 12
        for i, (_, text) in enumerate(lines):
            fm = fm_main if i == 0 else fm_small
            width = max(width, fm.horizontalAdvance(text) + (26 if i == 0 else 0))
            height += fm.height() + 2
        self.resize(width + 24, height + 6)
        self._place()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._native_ready:
            hwnd = int(self.winId())
            # WS_EX_LAYERED Qt ставит сам для прозрачного окна; добавляем только «сквозь клики» и «без фокуса».
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            if not os.environ.get("ACD_OVERLAY_VISIBLE_IN_CAPTURE"):  # для отладки: увидеть оверлей на скриншоте
                user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            self._native_ready = True
        self._place()

    def _place(self):
        """Позиция в физических пикселях через WinAPI: при масштабе 125–150% координаты Qt другие."""
        if not self.isVisible() or self._target is None:
            return
        hwnd = int(self.winId())
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        t = self._target
        x = t.left + MARGIN if "left" in self._corner else t.left + t.width - w - MARGIN
        y = t.top + MARGIN if "top" in self._corner else t.top + t.height - h - MARGIN
        # Заодно поднимаем окно наверх: игра при переключении может его перекрыть.
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(12, 14, 18, 200))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        y = 8
        for i, (color, text) in enumerate(self._all_lines()):
            font = self.font_main if i == 0 else self.font_small
            fm = QFontMetrics(font)
            x = 12
            if i == 0:
                if color != RED or self.blink:
                    p.setBrush(color)
                    p.drawEllipse(QRectF(x, y + (fm.height() - 12) / 2, 12, 12))
                x += 22
            p.setFont(font)
            p.setPen(QColor(235, 235, 235) if i == 0 else color)
            p.drawText(x, y + fm.ascent(), text)
            p.setPen(Qt.NoPen)
            y += fm.height() + 2
