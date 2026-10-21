from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QSizePolicy,
                               QSlider, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from .. import capture, legends
from ..casefile import SCALE, CaseFile, fmt_date, fmt_ts, move_to_recycle_bin, scale_text
from ..config import CACHE_DIR
from ..i18n import t
from .legend_ui import TagButtons, legend_icon, legend_pixmap
from . import theme
from .theme import Card, button, muted


class ScaleButtons(QWidget):
    """Оценка момента 1–5."""
    picked = Signal(int)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for n, text in SCALE.items():
            b = button(f"{n} · {t(text)}", "")
            b.setCheckable(True)
            b.setProperty("scale", n)
            b.clicked.connect(lambda _c, n=n: self.picked.emit(n))
            self.group.addButton(b, n)
            self.buttons[n] = b
            lay.addWidget(b)

    def set_value(self, n: int | None):
        self.group.setExclusive(False)
        for k, b in self.buttons.items():
            b.setChecked(k == n)
        self.group.setExclusive(True)

    def value(self) -> int | None:
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)


def legend_combo(current: str = "", empty: str = "") -> QComboBox:
    box = QComboBox()
    box.setEditable(True)
    box.setIconSize(QSize(22, 22))
    box.addItem(empty)
    for lg in legends.all_legends():
        box.addItem(legend_icon(lg, 22), lg)
    box.setCurrentText(current)
    return box


class TrimState(QObject):
    """Где обрезать: [ начало и ] конец в мс (-1 — ещё не поставлено). Общее для окна и полного экрана."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.start = self.end = -1

    def reset(self):
        self.start = self.end = -1
        self.changed.emit()

    @property
    def stage(self) -> int:
        """0 — ничего, 1 — стоит [, 2 — стоят [ и ]."""
        return 0 if self.start < 0 else (1 if self.end < 0 else 2)

    def put(self, pos: int):
        """Следующий щелчок ножниц: сначала [, потом ]."""
        if self.stage == 0:
            self.start = pos
        elif self.stage == 1:
            if pos < self.start:
                self.start, pos = pos, self.start
            self.end = max(pos, self.start + 500)
        self.changed.emit()

    def move(self, which: str, pos: int, duration: int):
        if which == "start":
            limit = self.end - 500 if self.end >= 0 else duration
            self.start = max(0, min(pos, limit))
        else:
            self.end = min(duration, max(pos, self.start + 500))
        self.changed.emit()


class Timeline(QWidget):
    """Полоса видео: щелчок/перетаскивание — перемотка; скобки [ ] обрезки тоже перетаскиваются."""
    PAD = 12
    CY = 18          # линия полосы; ниже — подписи времени скобок

    def __init__(self, player: QMediaPlayer, trim: TrimState):
        super().__init__()
        self.player, self.trim = player, trim
        self.setFixedHeight(48)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._drag = ""            # "pos", "start", "end"
        player.positionChanged.connect(lambda _p: self.update())
        player.durationChanged.connect(lambda _d: self.update())
        trim.changed.connect(self.update)

    def _x(self, ms: int) -> float:
        d = max(1, self.player.duration())
        return self.PAD + (self.width() - 2 * self.PAD) * min(max(ms, 0), d) / d

    def _ms(self, x: float) -> int:
        d = self.player.duration()
        frac = (x - self.PAD) / max(1, self.width() - 2 * self.PAD)
        return int(min(max(frac, 0.0), 1.0) * d)

    def _handle_at(self, x: float) -> str:
        for which, ms in (("end", self.trim.end), ("start", self.trim.start)):
            if ms >= 0 and abs(self._x(ms) - x) <= 9:
                return which
        return ""

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cy = self.CY
        x0, x1 = self.PAD, self.width() - self.PAD
        pos_x = self._x(self.player.position())
        tr = self.trim
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(QRectF(x0, cy - 4, x1 - x0, 8), 4, 4)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(x0, cy - 4, pos_x - x0, 8), 4, 4)
        if tr.stage:
            a = self._x(tr.start)
            b = self._x(tr.end) if tr.stage == 2 else pos_x
            a, b = min(a, b), max(a, b)
            if tr.stage == 2:                  # то, что будет вырезано, — затемнено
                p.setBrush(QColor(0, 0, 0, 150))
                p.drawRect(QRectF(x0 - 2, cy - 7, a - x0 + 2, 14))
                p.drawRect(QRectF(b, cy - 7, x1 - b + 2, 14))
            p.setBrush(QColor(245, 165, 36, 70 if tr.stage == 2 else 35))       # кусок, который останется
            p.drawRect(QRectF(a, cy - 11, b - a, 22))
        pen = QPen(QColor(theme.WARN), 3)
        pen.setCapStyle(Qt.FlatCap)
        f = p.font()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        for ms, sign in ((tr.start, 1), (tr.end, -1)):
            if ms < 0:
                continue
            x = self._x(ms)
            p.setPen(pen)                      # скобка [ или ]: вертикаль и два «уса» внутрь
            p.drawLine(QPointF(x, cy - 14), QPointF(x, cy + 14))
            p.drawLine(QPointF(x, cy - 14), QPointF(x + 7 * sign, cy - 14))
            p.drawLine(QPointF(x, cy + 14), QPointF(x + 7 * sign, cy + 14))
            p.setPen(QColor(theme.WARN))       # время под скобкой
            label = ("[ " if sign > 0 else "") + fmt_ts(ms / 1000) + (" ]" if sign < 0 else "")
            w = p.fontMetrics().horizontalAdvance(label)
            tx = x if sign > 0 else x - w
            p.drawText(QPointF(min(max(tx, 0), self.width() - w), self.height() - 3), label)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(pos_x, cy), 8, 8)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.player.duration() <= 0:
            return
        x = e.position().x()
        self._drag = self._handle_at(x) or "pos"
        self._apply(x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag:
            self._apply(x)
        else:
            h = self._handle_at(x)
            self.setCursor(Qt.SizeHorCursor if h else Qt.PointingHandCursor)
            self.setToolTip(fmt_ts(self._ms(x) / 1000))

    def mouseReleaseEvent(self, _e):
        self._drag = ""

    def _apply(self, x: float):
        ms = self._ms(x)
        if self._drag in ("start", "end"):
            self.trim.move(self._drag, ms, self.player.duration())
            ms = self.trim.start if self._drag == "start" else self.trim.end
        self.player.setPosition(ms)        # и при перетаскивании скобки видно кадр, где режем


class PlayerControls(QWidget):
    """Кнопки плеера. Один и тот же плеер — в окне досье и на весь экран.
    Ножницы: 1-й щелчок ставит [, 2-й — ], 3-й обрезает. ✕ — убрать скобки."""

    def __init__(self, player: QMediaPlayer, audio: QAudioOutput, trim: TrimState, on_moment, on_fullscreen,
                 fullscreen_text: str, on_trim):
        super().__init__()
        self.player, self.trim, self._on_trim = player, trim, on_trim
        self.play = button("▶", "primary", self.toggle)
        self.play.setFixedSize(52, 34)
        self.play.setStyleSheet("font-size:16px;padding:0;")
        self.slow = QComboBox()
        for rate in (0.25, 0.5, 1.0):
            self.slow.addItem(f"{rate}×", rate)
        self.slow.setCurrentIndex(2)
        self.slow.currentIndexChanged.connect(lambda: player.setPlaybackRate(self.slow.currentData()))
        self.timeline = Timeline(player, trim)
        self.time = muted("00:00", wrap=False)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(80)
        self.volume.setValue(int(audio.volume() * 100))
        self.volume.valueChanged.connect(lambda v: audio.setVolume(v / 100))
        self.volume.setToolTip(t("Громкость"))
        self.scissors = button("✂", "", self._scissors)
        self.scissors.setMinimumWidth(52)
        self.cancel = button("✕", "", trim.reset)
        self.cancel.setFixedSize(38, 34)
        self.cancel.setStyleSheet("font-size:15px;padding:0;")
        self.cancel.setToolTip(t("Убрать скобки обрезки"))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(self.timeline)           # полоса во всю ширину — скобки видно и удобно тащить
        lay = QHBoxLayout()
        col.addLayout(lay)
        lay.addWidget(self.play)
        lay.addSpacing(6)
        lay.addWidget(self.time)
        lay.addWidget(button(t("К моменту"), "ghost", on_moment))
        lay.addWidget(self.slow)
        lay.addStretch(1)
        lay.addWidget(self.scissors)
        lay.addWidget(self.cancel)
        lay.addWidget(QLabel("🔊"))
        lay.addWidget(self.volume)
        lay.addWidget(button(fullscreen_text, "", on_fullscreen))
        player.positionChanged.connect(self._pos)
        player.playbackStateChanged.connect(lambda _s: self._pos(player.position()))
        trim.changed.connect(self._trim_ui)
        self._trim_ui()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _scissors(self):
        if self.player.duration() <= 0:
            return
        if self.trim.stage < 2:
            self.trim.put(self.player.position())
        else:
            self._on_trim()

    def _trim_ui(self):
        tr = self.trim
        self.cancel.setVisible(tr.stage > 0)
        if tr.stage == 0:
            self.scissors.setText("✂")
            self.scissors.setProperty("kind", "")
            self.scissors.setToolTip(t("Обрезка: 1-й щелчок — начало [, 2-й — конец ], потом — обрезать. "
                                       "Скобки на полосе можно двигать."))
        elif tr.stage == 1:
            self.scissors.setText("✂  ]")
            self.scissors.setToolTip(t("Поставь видео на конец нужного куска и нажми — поставится ]"))
        else:
            self.scissors.setText("✂  " + t("Обрезать") + f" · {(tr.end - tr.start) / 1000:.0f} " + t("с"))
            self.scissors.setProperty("kind", "primary")
            self.scissors.setToolTip(t("Оставить только кусок между [ и ], остальное удалить из досье"))
        self.scissors.style().unpolish(self.scissors)
        self.scissors.style().polish(self.scissors)

    def _pos(self, pos: int):
        self.time.setText(f"{fmt_ts(pos / 1000)} / {fmt_ts(self.player.duration() / 1000)}")
        self.play.setText("⏸" if self.player.playbackState() == QMediaPlayer.PlayingState else "▶")


class FullscreenPlayer(QWidget):
    """Видео на весь экран. Esc / двойной щелчок — выход, пробел — пауза, ← → — 5 секунд."""

    def __init__(self, viewer: "CaseViewer"):
        # Дочернее окно досье: иначе модальное досье блокирует ему клавиши и мышь.
        super().__init__(viewer, Qt.Window | Qt.FramelessWindowHint)
        self.viewer = viewer
        self.setStyleSheet("background:#000;")
        self.video = QVideoWidget()
        self.video.mouseDoubleClickEvent = lambda _e: self.close()
        self.controls = PlayerControls(viewer.player, viewer.audio, viewer.trimstate, viewer._seek_moment, self.close,
                                       "✕  " + t("Выйти") + " (Esc)", self._trim)
        self.title = muted("", wrap=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.video, 1)
        bar = QWidget()
        bar.setStyleSheet("background:#0d0f13;")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(18, 8, 18, 10)
        bl.addWidget(self.title)
        bl.addWidget(self.controls)
        lay.addWidget(bar)

    def open(self, title: str):
        self.title.setText(title + "   ·   " + t("Esc — выйти, пробел — пауза, ← → — 5 секунд"))
        self.viewer.player.setVideoOutput(self.video)
        self.setGeometry(self.viewer.screen().geometry())     # тот же монитор, где досье
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _trim(self):
        self.close()
        self.viewer._trim()

    def keyPressEvent(self, e):
        p = self.viewer.player
        if e.key() in (Qt.Key_Escape, Qt.Key_F):
            self.close()
        elif e.key() == Qt.Key_Space:
            self.controls.toggle()
        elif e.key() == Qt.Key_Left:
            p.setPosition(max(0, p.position() - 5000))
        elif e.key() == Qt.Key_Right:
            p.setPosition(min(p.duration(), p.position() + 5000))
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.viewer.player.setVideoOutput(self.viewer.video)
        super().closeEvent(e)


class CaseViewer(QDialog):
    """Досье: кто это (ник, ID, легенда, теги), моменты с видео и оценками, жалоба, экспорт."""
    trim_done = Signal(str, str, float, float)    # id момента, файл или "", начало, конец (сек)

    def __init__(self, case: CaseFile, parent=None):
        super().__init__(parent)
        self.case = case
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)

        # --- кто это (компактно: две строки)
        who = Card(t("Игрок"))
        self.portrait = QLabel()
        self.portrait.setFixedSize(64, 64)
        self.portrait.setAlignment(Qt.AlignCenter)
        self.portrait.setStyleSheet("background:#000;border-radius:10px;")
        self.name = QLineEdit()
        self.uid = QLineEdit()
        self.uid.setPlaceholderText(t("идентификатор с экрана наблюдения"))
        self.legend = legend_combo()
        self.legend.setToolTip(t("Легенду программа узнаёт по портрету сама; если ошиблась — исправь, она запомнит."))
        self.legend.setMinimumWidth(170)
        self.tags = TagButtons(self._save_tags)
        self.pin = QCheckBox(t("Закрепить вверху списка"))
        self.pin.toggled.connect(lambda _on: self._save_tags())
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(self.portrait, 0, 0, 2, 1, Qt.AlignTop)
        grid.addWidget(QLabel(t("Ник")), 0, 1)
        grid.addWidget(self.name, 0, 2)
        grid.addWidget(QLabel("ID"), 0, 3)
        grid.addWidget(self.uid, 0, 4)
        grid.addWidget(button(t("Сохранить"), "primary", self._save_identity), 0, 5)
        grid.addWidget(QLabel(t("Легенда")), 1, 1)
        grid.addWidget(self.legend, 1, 2)
        grid.addWidget(QLabel(t("Теги")), 1, 3)
        tag_row = QHBoxLayout()
        tag_row.addWidget(self.tags, 1)
        tag_row.addWidget(self.pin)
        grid.addLayout(tag_row, 1, 4, 1, 2)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(4, 3)
        who.body.addLayout(grid)
        self.who_info = muted("")
        who.body.addWidget(self.who_info)

        # --- моменты
        self.filter = QComboBox()
        self.filter.setIconSize(QSize(20, 20))
        self.filter.currentIndexChanged.connect(lambda: self._fill_moments(self._current_id()))
        self.moments = QListWidget()
        self.moments.setIconSize(QSize(40, 40))
        self.moments.setMinimumWidth(240)
        self.moments.currentItemChanged.connect(self._select)
        left = Card(t("Моменты"))
        left.body.addWidget(self.filter)
        left.body.addWidget(self.moments, 1)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(150)
        self.video.mouseDoubleClickEvent = lambda _e: self._fullscreen()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.trimstate = TrimState()
        self.player.durationChanged.connect(lambda _d: self.trimstate.reset())   # другой клип — скобки заново
        self.fs = FullscreenPlayer(self)
        self.controls = PlayerControls(self.player, self.audio, self.trimstate, self._seek_moment,
                                       self._fullscreen, "⛶  " + t("На весь экран"), self._trim)
        self.trim_done.connect(self._trim_finished)
        video_card = Card()
        video_card.body.addWidget(self.video, 1)
        video_card.body.addWidget(self.controls)

        rate = Card(t("Оценка момента"))
        self.scale = ScaleButtons()
        self.moment_legend = legend_combo(empty="")
        self.moment_legend.setMinimumWidth(170)
        self.moment_legend.setToolTip(t("Легенда в этом моменте"))
        self.note = QLineEdit()
        self.note.setPlaceholderText(t("Заметка: что видно в этом моменте"))
        rate.body.addWidget(self.scale)
        lr = QHBoxLayout()
        lr.addWidget(self.moment_legend)
        lr.addWidget(self.note, 1)
        lr.addWidget(button(t("Удалить момент"), "ghost", self._delete_moment))
        lr.addWidget(button(t("Сохранить оценку"), "primary", self._save_moment))
        rate.body.addLayout(lr)
        rate.body.addWidget(muted(t("4 и 5 попадают в текст жалобы EA.")))

        # Видео и оценка делят высоту разделителем — ни одна панель не схлопнется и не уедет.
        right = QSplitter(Qt.Vertical)
        right.addWidget(video_card)
        right.addWidget(rate)
        right.setChildrenCollapsible(False)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 1000])

        bottom = QHBoxLayout()
        bottom.addWidget(button(t("Отчёт в браузере"), "", self._open_report))
        bottom.addWidget(button(t("Текст жалобы EA"), "", self._show_ea))
        bottom.addWidget(button(t("Показать файл"), "ghost", lambda: os.startfile(self.case.path.parent)))
        bottom.addWidget(button("🗑  " + t("Удалить досье"), "ghost", self._delete_case))
        bottom.addStretch(1)
        bottom.addWidget(button(t("Экспорт в .zip…"), "primary", self._export))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(who)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        # На маленьком экране (или с масштабом 125–150 %) — прокрутка, а не наложение панелей друг на друга.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._fit_to_screen()
        self._report_opened = False
        self.finished.connect(self._cleanup)
        self._refresh()

    def _fit_to_screen(self):
        """Окно не больше экрана; на небольшом экране — сразу развёрнуто."""
        screen = (self.parent().screen() if self.parent() is not None else None) or self.screen()
        avail = screen.availableGeometry()
        self.resize(min(1380, avail.width() - 40), min(900, avail.height() - 40))
        if avail.width() < 1400 or avail.height() < 880:
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    # --- данные ----------------------------------------------------------

    def _refresh(self, select_id: str | None = None):
        i = self.case.info
        self.setWindowTitle(t("Досье") + f" — {i.player_name}")
        self.name.setText(i.player_name)
        self.uid.setText(i.ea_id)
        self.legend.setCurrentText(i.legends[-1] if i.legends else "")
        self.tags.set_tags(i.tags)
        self.pin.blockSignals(True)
        self.pin.setChecked(i.pinned)
        self.pin.blockSignals(False)
        parts = []
        if i.aliases:
            parts.append(t("прежние ники") + ": " + ", ".join(i.aliases))
        if i.clan_tag:
            parts.append(f"[{i.clan_tag}]")
        if i.teammates:
            parts.append(t("тиммейты") + ": " + ", ".join(i.teammates[-6:]))
        if i.legends:
            parts.append(t("Легенды") + ": " + ", ".join(i.legends))
        parts.append(t("встречи") + f": {fmt_date(i.first_seen)} — {fmt_date(i.last_seen)}")
        self.who_info.setText(" · ".join(parts))
        self._show_portrait()
        # Фильтр по легендам, которые есть в моментах.
        used = []
        for m in i.moments:
            if m.legend and m.legend not in used:
                used.append(m.legend)
        keep = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem(t("Все легенды") + f" ({len(i.moments)})", "")
        for lg in used:
            n = sum(m.legend == lg for m in i.moments)
            self.filter.addItem(legend_icon(lg, 20), f"{lg} ({n})", lg)
        idx = self.filter.findData(keep) if keep else 0
        self.filter.setCurrentIndex(max(0, idx))
        self.filter.blockSignals(False)
        self._fill_moments(select_id)

    def _show_portrait(self):
        i = self.case.info
        legend = i.legends[-1] if i.legends else ""
        if legend and legends.icon_path(legend).exists():
            self.portrait.setPixmap(legend_pixmap(legend, 80))
            return
        img = self.case.portrait_image()
        if img is not None and img.ndim == 3:
            rgb = img[:, :, ::-1].copy()
            q = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QImage.Format_RGB888).copy()
            self.portrait.setPixmap(QPixmap.fromImage(q).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.portrait.setPixmap(legend_pixmap("", 80))

    def _fill_moments(self, select_id: str | None = None):
        i = self.case.info
        only = self.filter.currentData() or ""
        self.moments.blockSignals(True)
        self.moments.clear()
        for n, m in enumerate(i.moments, 1):
            if only and m.legend != only:
                continue
            size = self.case.member_size(m.clip) / 1024 / 1024
            item = QListWidgetItem(legend_icon(m.legend, 44),
                                   f"#{n}  {m.legend or t('легенда —')}\n{scale_text(m.label)}\n"
                                   f"{fmt_date(m.created_at)}" + (f" · {size:.1f} " + t("МБ") if m.clip
                                                                  else " · " + t("без видео")))
            item.setData(Qt.UserRole, m.id)
            item.setToolTip(m.note or "")
            self.moments.addItem(item)
        self.moments.blockSignals(False)
        ids = [self.moments.item(r).data(Qt.UserRole) for r in range(self.moments.count())]
        row = ids.index(select_id) if select_id in ids else (0 if ids else -1)
        self.moments.setCurrentRow(row)
        if row < 0:
            self._select(None, None)

    def _current_id(self) -> str | None:
        item = self.moments.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _moment(self):
        mid = self._current_id()
        return next((m for m in self.case.info.moments if m.id == mid), None) if mid else None

    def _select(self, current, _previous):
        self.player.stop()
        m = self._moment() if current else None
        self.scale.set_value(m.label if m else None)
        self.note.setText(m.note if m else "")
        self.moment_legend.setCurrentText(m.legend if m else "")
        if not m or not m.clip:
            self.player.setSource(QUrl())
            return
        clip = self.case.extract(m.clip, CACHE_DIR)
        if clip:
            self.player.setSource(QUrl.fromLocalFile(str(clip)))
            self._seek_moment()

    # --- действия ----------------------------------------------------------

    def _fullscreen(self):
        if self._moment() is None:
            return
        i = self.case.info
        m = self._moment()
        self.fs.open(f"{i.player_name}" + (f" · {m.legend}" if m.legend else "") + f" · {fmt_date(m.created_at)}")
        self.player.play()

    def _trim(self):
        m = self._moment()
        tr = self.trimstate
        if not m or not m.clip or tr.stage < 2:
            return
        src = self.case.extract(m.clip, CACHE_DIR)
        if not src:
            return
        start, end = tr.start / 1000, tr.end / 1000
        self.player.pause()
        self.controls.scissors.setEnabled(False)
        self.controls.scissors.setText("✂  " + t("Обрезаю…"))
        dst = Path(tempfile.mkdtemp(prefix="acd_trim_")) / "clip.mp4"

        def work():
            ok = capture.trim_clip(src, dst, start, end)
            self.trim_done.emit(m.id, str(dst) if ok else "", start, end)
        threading.Thread(target=work, daemon=True).start()

    def _trim_finished(self, moment_id: str, path: str, start: float, end: float):
        m = next((x for x in self.case.info.moments if x.id == moment_id), None)
        if not path or m is None:
            QMessageBox.warning(self, t("Обрезка"), t("Не удалось обрезать видео."))
            self.controls.scissors.setEnabled(True)
            self.trimstate.reset()
            return
        self.player.stop()
        self.player.setSource(QUrl())          # отпустить старый файл
        cached = Path(CACHE_DIR) / self.case.info.case_id / m.clip
        self.case.replace_clip(moment_id, Path(path), min(max(0.0, m.mark_offset - start), end - start))
        cached.unlink(missing_ok=True)
        shutil.rmtree(Path(path).parent, ignore_errors=True)
        self.controls.scissors.setEnabled(True)
        self.trimstate.reset()
        self._refresh(moment_id)

    def _save_tags(self):
        self.case.set_tags(self.tags.tags(), self.pin.isChecked())

    def _save_identity(self):
        self.player.stop()
        self.player.setSource(QUrl())
        legend = self.legend.currentText().strip()
        old_path = self.case.path
        portrait = self.case.portrait_image()
        self.case = self.case.set_identity(self.name.text(), self.uid.text(), legend)
        if legend and portrait is not None:
            legends.learn(legend, portrait)   # если по портрету не узнал — запомнит
        if self.case.path != old_path and not old_path.exists():
            QMessageBox.information(self, t("Досье"), t("Досье с таким ID уже было — моменты перенесены в него."))
        self._refresh()

    def _save_moment(self):
        m = self._moment()
        if m:
            self.player.stop()
            self.case.update_moment(m.id, self.scale.value(), self.note.text().strip(),
                                    self.moment_legend.currentText().strip())
            self._refresh(m.id)

    def _delete_moment(self):
        m = self._moment()
        if m and QMessageBox.question(self, t("Удалить"), t("Удалить этот момент вместе с видео?")) == QMessageBox.Yes:
            self.player.stop()
            self.player.setSource(QUrl())
            self.case.remove_moment(m.id)
            self._refresh()

    def _delete_case(self):
        """Одна кнопка: досье уходит в Корзину Windows (вернуть можно оттуда)."""
        self.player.stop()
        self.player.setSource(QUrl())
        if move_to_recycle_bin(self.case.path):
            self.accept()
        else:
            QMessageBox.warning(self, t("Удалить досье"), t("Не удалось удалить досье") + f": {self.case.path.name}")

    def _seek_moment(self):
        m = self._moment()
        if m:
            self.player.setPosition(int(max(0.0, m.mark_offset - 2) * 1000))

    def _open_report(self):
        self._report_opened = True     # браузеру нужны распакованные клипы — не удаляем их при закрытии
        for m in self.case.info.moments:
            self.case.extract(m.clip, CACHE_DIR)
            self.case.extract(m.frame, CACHE_DIR)
        path = Path(CACHE_DIR) / self.case.info.case_id / "report.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.case.render_html(), encoding="utf-8")
        os.startfile(path)

    def _show_ea(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("Текст жалобы для EA"))
        dlg.resize(760, 520)
        text = QTextEdit()
        text.setPlainText(self.case.render_ea_report())
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(muted(t("Жалобу в игре подай через «Пожаловаться» в режиме зрителя, а этот текст с клипами "
                              "из экспортированного .zip отправь в поддержку EA (help.ea.com).")))
        lay.addWidget(text, 1)
        lay.addWidget(button(t("Копировать"), "primary", lambda: (text.selectAll(), text.copy())), 0, Qt.AlignRight)
        dlg.exec()

    def _export(self):
        default = str(Path.home() / "Desktop" / self.case.path.with_suffix(".zip").name)
        path, _ = QFileDialog.getSaveFileName(self, t("Экспорт досье"), default, "ZIP (*.zip)")
        if path:
            out = self.case.export_zip(Path(path))
            QMessageBox.information(self, t("Экспорт"), t("Сохранено") + f": {out}")

    def _cleanup(self, *_):
        self.fs.close()
        self.player.stop()
        self.player.setSource(QUrl())
        # Клипы распаковывались только для просмотра — сами они хранятся в досье.
        if not self._report_opened:
            shutil.rmtree(Path(CACHE_DIR) / self.case.info.case_id, ignore_errors=True)
