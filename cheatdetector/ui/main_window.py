from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QSizePolicy, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import display, legend_icons, legends
from ..autorecord import SpectateWatcher
from ..capture import CaptureEngine, MarkResult
from ..casefile import TAGS, CaseFile, Moment, fmt_date, move_to_recycle_bin, scale_text
from ..config import APP_TITLE, Settings
from ..hotkeys import GlobalHotkeys
from ..i18n import t
from ..identify import Player, PlayerTracker
from ..screenreader import ScanResult, same_name
from . import theme
from .case_viewer import CaseViewer
from .legend_ui import LegendStrip, NameCell, legend_icon, legend_pixmap
from .overlay import GREEN, GREY, RED, YELLOW, Overlay
from .settings_page import SettingsPage
from .theme import Card, button, chip, muted, page_header, set_chip


class _Signals(QObject):
    status = Signal(str)
    mark_done = Signal(object)
    hotkey = Signal(str)
    spectating = Signal(bool)
    scan = Signal(object)


def _item(text: str, color: str | None = None, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(color))
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _label_color(label: int | None) -> str | None:
    return {4: theme.WARN, 5: theme.ACCENT_HOVER, 1: theme.MUTED, 2: theme.OK, 3: theme.INFO}.get(label)


class LivePage(QWidget):
    """«Эфир»: только наблюдение за работой программы — запись, кто в эфире, игроки, отметки."""

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(page_header(t("Эфир"), t("Запись включаешь сам — F9 или кнопкой. F8 сохраняет момент: в режиме "
                                                "зрителя — в досье наблюдаемого, в своей игре — в твоё досье [OWNER].")), 1)
        self.rec_btn = button(t("●  Начать запись"), "primary")
        self.rec_btn.setMinimumWidth(200)
        top.addWidget(self.rec_btn, 0, Qt.AlignBottom)
        root.addLayout(top)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.rec_card = Card(t("Запись"))
        self.rec_chip = chip(t("ВЫКЛ"), "muted")
        self.rec_card.add_header_widget(self.rec_chip)
        self.rec_time = QLabel("00:00")
        self.rec_time.setObjectName("bigValue")
        self.rec_detail = muted("—")
        self.rec_buffer = muted("")
        self.rec_card.body.addWidget(self.rec_time)
        self.rec_card.body.addWidget(self.rec_buffer)
        self.rec_card.body.addWidget(self.rec_detail)

        self.player_card = Card(t("Игрок в эфире"))
        self.legend_chip = chip(t("легенда —"), "muted")
        self.legend_img = QLabel()
        self.player_card.add_header_widget(self.legend_img)
        self.player_card.add_header_widget(self.legend_chip)
        self.name = QLabel(t("Никого"))
        self.name.setObjectName("midValue")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pid = muted(t("Запись выключена — нажми F9"))
        self.pid.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.extra = muted("")
        self.player_card.body.addWidget(self.name)
        self.player_card.body.addWidget(self.pid)
        self.player_card.body.addWidget(self.extra)

        self.state_card = Card(t("Распознавание"))
        self.game_chip = chip(t("Apex не найдена"), "muted")
        self.spect_chip = chip(t("режим зрителя не виден"), "muted")
        self.read_chip = chip(t("ник и ID: ждём"), "muted")
        self.audio_chip = chip(t("звук выключен"), "muted")
        self.overlay_chip = chip(t("оверлей скрыт"), "muted")
        for c in (self.game_chip, self.spect_chip, self.read_chip, self.audio_chip, self.overlay_chip):
            self.state_card.body.addWidget(c)
        for c, k in ((self.rec_card, 3), (self.player_card, 4), (self.state_card, 3)):
            stats.addWidget(c, k)
        root.addLayout(stats)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        prev_card = Card(t("Экран"))
        self.preview = QLabel(t("Превью появится во время записи"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(420, 220)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(f"background:#000;border-radius:8px;color:{theme.MUTED}")
        prev_card.body.addWidget(self.preview)
        roster_card = Card(t("Игроки этой записи"), t("Двойной щелчок — открыть досье"))
        self.roster = QListWidget()
        roster_card.body.addWidget(self.roster)
        mid.addWidget(prev_card, 3)
        mid.addWidget(roster_card, 2)
        root.addLayout(mid, 3)

        feed = Card(t("Отметки"))
        self.marks = QTableWidget(0, 4)
        self.marks.setHorizontalHeaderLabels([t("Время"), t("Игрок"), t("Оценка"), t("Статус")])
        hh = self.marks.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self.marks.verticalHeader().hide()
        self.marks.setShowGrid(False)
        self.marks.setAlternatingRowColors(True)
        self.marks.setSelectionBehavior(QTableWidget.SelectRows)
        self.marks.setEditTriggers(QTableWidget.NoEditTriggers)
        self.marks.setMinimumHeight(140)
        feed.body.addWidget(self.marks)
        root.addWidget(feed, 2)

    def _legend(self, legend: str):
        set_chip(self.legend_chip, legend or t("легенда —"), "info" if legend else "muted")
        self.legend_img.setPixmap(legend_pixmap(legend, 28) if legend else QPixmap())

    def show_owner(self, name: str, legend: str, moments: int):
        self.player_card.set_title(t("Своя игра"))
        self.name.setText(name or t("Ты"))
        self.pid.setText(t("моменты уходят в твоё досье [OWNER]"))
        self.extra.setText(t("моментов в досье") + f": {moments}")
        self._legend(legend)

    def show_player(self, p: Player | None, moments: int = 0, recording: bool = True):
        self.player_card.set_title(t("Игрок в эфире"))
        if p is None:
            self.name.setText(t("Никого"))
            self.pid.setText(t("Жду режим зрителя или свою игру…") if recording else t("Запись выключена — нажми F9"))
            self.extra.setText("")
            self._legend("")
            return
        self.name.setText(p.name or "—")
        self.pid.setText(f"ID {p.player_id}" if p.player_id else t("ID не прочитан"))
        parts = []
        if p.clan_tag:
            parts.append(f"[{p.clan_tag}]")
        if p.teammates:
            parts.append(t("с ним") + ": " + ", ".join(p.teammates))
        parts.append(t("моментов в досье") + f": {moments}")
        self.extra.setText(" · ".join(parts))
        self._legend(p.legend)

    def add_mark_row(self, mark_id: str, who: str):
        self.marks.insertRow(0)
        self.marks.setItem(0, 0, _item(time.strftime("%H:%M:%S"), data=mark_id))
        self.marks.setItem(0, 1, _item(who))
        self.marks.setItem(0, 2, _item(scale_text(None), theme.MUTED))
        self.marks.setItem(0, 3, _item(t("сохраняю клип…")))

    def set_mark_status(self, mark_id: str, text: str):
        for r in range(self.marks.rowCount()):
            if self.marks.item(r, 0).data(Qt.UserRole) == mark_id:
                self.marks.setItem(r, 3, _item(text))


class CasesPage(QWidget):
    COLUMNS = ["", "Игрок", "ID", "Легенды", "Моменты (оценено)", "Макс. оценка", "Последняя встреча", ""]

    def __init__(self, settings: Settings):
        super().__init__()
        self.setObjectName("page")
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(page_header(t("Досье"), t("Досье заводятся сами на каждого, чей момент ты отметил F8. "
                                                 "★ — закрепить вверху. Файл .apexcase — это ZIP.")), 1)
        top.addWidget(button(t("Открыть папку"), "", self._open_folder), 0, Qt.AlignBottom)
        root.addLayout(top)
        card = Card()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Поиск по нику или ID…"))
        self.search.textChanged.connect(self._filter)
        self.tag_filter = QComboBox()
        self.tag_filter.addItem(t("Все досье"), "")
        self.tag_filter.addItem("★ " + t("Закреплённые"), "*pinned")
        for tg in TAGS:
            self.tag_filter.addItem(f"[{tg}]", tg)
        self.tag_filter.currentIndexChanged.connect(lambda: self._filter(self.search.text()))
        sr = QHBoxLayout()
        sr.addWidget(self.search, 1)
        sr.addWidget(self.tag_filter)
        card.body.addLayout(sr)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([t(c) for c in self.COLUMNS])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 44)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)     # иконки легенд — виджет, по тексту не растянется
        self.table.setColumnWidth(3, 160)
        hh.setSectionResizeMode(len(self.COLUMNS) - 1, QHeaderView.Fixed)
        self.table.setColumnWidth(len(self.COLUMNS) - 1, 52)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)
        card.body.addWidget(self.table)
        card.body.addWidget(muted(t("Двойной щелчок — открыть досье: видео, оценки, правка ника/ID/легенды, "
                                    "текст жалобы, экспорт.")))
        root.addWidget(card, 1)
        self.cases: list[CaseFile] = []

    def reload(self):
        self.cases = CaseFile.list_cases(Path(self.settings.paths.cases_dir))
        self.table.setRowCount(0)          # убирает старые кнопки и значки строк
        self.table.setRowCount(len(self.cases))
        for r, c in enumerate(self.cases):
            i = c.info
            rated = [m.label for m in i.moments if m.label]
            top = max(rated) if rated else None
            cells = {
                2: _item(i.ea_id or "—"),
                4: _item(f"{len(i.moments)} ({len(rated)})"),
                5: _item(scale_text(top) if top else "—", _label_color(top)),
                6: _item(fmt_date(i.last_seen)),
            }
            for col in (0, 1, 3, 7):
                cells[col] = _item("")
            for col, it in cells.items():
                self.table.setItem(r, col, it)
            star = button("★" if i.pinned else "☆", "ghost", lambda _c=False, p=c.path: self._toggle_pin(p))
            star.setToolTip(t("Закрепить вверху списка"))
            star.setStyleSheet(f"color:{theme.WARN if i.pinned else theme.MUTED};font-size:18px;padding:0;")
            self.table.setCellWidget(r, 0, star)
            self.table.setCellWidget(r, 1, NameCell(i.legends[-1] if i.legends else "", i.player_name,
                                                    (t("был") + ": " + ", ".join(i.aliases)) if i.aliases else "",
                                                    i.tags))
            self.table.setCellWidget(r, 3, LegendStrip(i.legends))
            # Удаление одной кнопкой: досье уходит в Корзину Windows, вернуть можно оттуда.
            trash = button("🗑", "ghost", lambda _c=False, p=c.path: self._delete(p))
            trash.setToolTip(t("Удалить досье (в Корзину)"))
            self.table.setCellWidget(r, len(self.COLUMNS) - 1, trash)
        self._filter(self.search.text())

    def _toggle_pin(self, path: Path):
        try:
            c = CaseFile.open(path)
        except (OSError, KeyError, ValueError):
            return
        c.set_tags(c.info.tags, not c.info.pinned)
        self.reload()

    def _delete(self, path: Path):
        name = path.stem
        if move_to_recycle_bin(path):
            self.window().statusBar().showMessage(t("Досье перемещено в Корзину") + f": {name}", 8000)
        else:
            self.window().statusBar().showMessage(t("Не удалось удалить досье") + f": {name}", 8000)
        self.reload()

    def _filter(self, text: str):
        text = text.strip().casefold()
        tag = self.tag_filter.currentData() or ""
        for r, c in enumerate(self.cases):
            hay = " ".join([c.info.ea_id, c.info.player_name, *c.info.aliases, *c.info.legends]).casefold()
            hide = bool(text) and text not in hay
            if tag == "*pinned":
                hide = hide or not c.info.pinned
            elif tag:
                hide = hide or tag not in c.info.tags
            self.table.setRowHidden(r, hide)

    def _open_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.cases):
            CaseViewer(CaseFile.open(self.cases[row].path), self).exec()
            self.reload()

    def open_path(self, path: str):
        if Path(path).exists():
            CaseViewer(CaseFile.open(Path(path)), self).exec()
            self.reload()

    def _open_folder(self):
        p = Path(self.settings.paths.cases_dir)
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(theme.app_icon())
        self.resize(1320, 880)
        self.setMinimumSize(1100, 720)

        self.signals = _Signals()
        self.engine = CaptureEngine(settings, self.signals.status.emit, self.signals.mark_done.emit)
        self.hotkeys = GlobalHotkeys(self.signals.hotkey.emit)
        self.signals.hotkey.connect(self._on_hotkey)
        self.watcher = SpectateWatcher(settings, lambda: (self.engine.running, self.engine.latest_frame),
                                       self.signals.spectating.emit, self.signals.scan.emit)
        self.signals.spectating.connect(self._on_spectating)
        self.signals.scan.connect(self._on_scan)
        self.signals.mark_done.connect(self._on_mark_done)
        self.signals.status.connect(lambda s: self.live.rec_detail.setText(s))
        self.tracker = PlayerTracker()
        self.case: CaseFile | None = None          # досье игрока, который сейчас в эфире
        self._session_cases: dict[str, str] = {}   # ключ игрока -> путь досье в этой записи
        self._last_read = 0.0
        self._own_name, self._own_legend = "", ""  # своя игра: ник и легенда пользователя
        self._owner_count: tuple[int, float] | None = None
        self.overlay = Overlay()

        self.live = LivePage()
        self.live.rec_btn.clicked.connect(self.toggle_recording)
        self.live.roster.itemDoubleClicked.connect(lambda it: self.cases.open_path(it.data(Qt.UserRole)))
        self.cases = CasesPage(settings)
        self.settings_page = SettingsPage(settings)
        self.settings_page.saved.connect(self._settings_saved)
        self.settings_page.overlay_test.connect(self._overlay_demo)
        self._demo_until = 0.0
        self.stack = QStackedWidget()
        for w in (self.live, self.cases, self.settings_page):
            self.stack.addWidget(w)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 22, 0, 16)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 16, 18)
        mark = QLabel()
        mark.setPixmap(theme.app_icon().pixmap(36, 36))
        names = QVBoxLayout()
        names.setSpacing(0)
        b1 = QLabel("APEX")
        b1.setObjectName("brand")
        b2 = QLabel("CHEATER REGISTRY")
        b2.setObjectName("brandSub")
        names.addWidget(b1)
        names.addWidget(b2)
        brand.addWidget(mark)
        brand.addSpacing(10)
        brand.addLayout(names)
        brand.addStretch(1)
        sl.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        for icon, text in ((theme.ICONS["live"], t("Эфир")), (theme.ICONS["cases"], t("Досье")),
                           (theme.ICONS["settings"], t("Настройки"))):
            self.nav.addItem(QListWidgetItem(f"{icon}   {text}"))
        self.nav.currentRowChanged.connect(self._switch_page)
        sl.addWidget(self.nav, 1)
        status = QGridLayout()
        status.setContentsMargins(22, 0, 16, 0)
        status.setVerticalSpacing(6)
        self.side_rec = chip(t("ВЫКЛ"), "muted")
        self.side_spect = chip(t("зритель —"), "muted")
        status.addWidget(self.side_rec, 0, 0, Qt.AlignLeft)
        status.addWidget(self.side_spect, 1, 0, Qt.AlignLeft)
        sl.addLayout(status)
        foot = muted(t("Только запись экрана —<br>к игре не подключается"))
        foot.setContentsMargins(22, 10, 16, 0)
        sl.addWidget(foot)

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(sidebar)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

        self._register_hotkeys()
        self.watcher.start()
        if not legend_icons.installed():
            # Портреты легенд не входят в репозиторий (арт игры) — при первом запуске скачиваем с вики.
            threading.Thread(target=self._fetch_legend_icons, daemon=True).start()
        self._target: display.CaptureTarget | None = None
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(300)

    @staticmethod
    def _fetch_legend_icons():
        try:
            legend_icons.download()
            legends.reload()
        except Exception:
            pass    # нет интернета — легенду можно указать в досье вручную, скачать позже в настройках

    # --- каркас ----------------------------------------------------------------------

    def _switch_page(self, row: int):
        self.stack.setCurrentIndex(row)
        if self.stack.currentWidget() is self.cases:
            self.cases.reload()

    def _register_hotkeys(self):
        hk = self.settings.hotkeys
        self.hotkeys.start({"mark": hk.mark, "toggle": hk.toggle})
        if self.hotkeys.failed:
            self.statusBar().showMessage(t("Не удалось назначить клавишу") + f": {', '.join(self.hotkeys.failed)}")

    def _settings_saved(self):
        self._register_hotkeys()
        self.statusBar().showMessage(t("Настройки сохранены."), 5000)

    # --- запись ----------------------------------------------------------------------

    def _start_recording(self) -> bool:
        try:
            self._target = display.resolve_target(self.settings.capture.auto_detect_game,
                                                  self.settings.capture.monitor_index)
        except RuntimeError as e:
            self.statusBar().showMessage(str(e), 8000)
            return False
        self.tracker.reset()
        self.case = None
        self._own_name, self._own_legend = "", ""
        self._session_cases.clear()
        self.live.roster.clear()
        self.engine.start(self._target)
        return True

    def _stop_recording(self):
        self.engine.stop()
        self.tracker.reset()
        self.live.show_player(None, recording=False)

    def toggle_recording(self):
        """Запись включается и выключается только вручную: F9 или кнопкой."""
        if self.engine.running:
            self._stop_recording()
            self.overlay.flash(t("Запись остановлена"), 3)
        elif self._start_recording():
            self.overlay.flash(t("Запись включена"), 3)

    def _on_spectating(self, on: bool):
        """Режим зрителя появился/пропал — запись не трогаем, только меняем, кому идут моменты."""
        if not on:
            self.tracker.reset()
            self.case = None

    def _on_scan(self, scan: ScanResult):
        """Скан кадра записи: кто на экране, не сменился ли игрок."""
        if not self.engine.running:
            return
        self._last_read = time.monotonic()
        if scan.own and not self.watcher.spectating:
            self._read_own(scan)
            self.live.show_owner(self._own_name, self._own_legend, self._owner_moments())
            return
        if not scan.spectating:
            return
        player = self.tracker.push(scan, self.engine.stream_time)
        if player is not None:
            self._switch_player(player)
        elif self.tracker.current is not None:
            self._refine_case(self.tracker.current)
        self.live.show_player(self.tracker.current, len(self.case.info.moments) if self.case else 0)

    def _read_own(self, scan: ScanResult) -> str:
        """Своя игра: ник и легенда пользователя. Возвращает легенду этого кадра."""
        if scan.name:
            self._own_name = scan.name
        legend = legends.match(scan.portrait)[0]
        if legend:
            self._own_legend = legend
        return legend

    def _owner_moments(self) -> int:
        if self._owner_count is None or time.monotonic() - self._owner_count[1] > 5:
            c = CaseFile.find_owner(Path(self.settings.paths.cases_dir))
            self._owner_count = (len(c.info.moments) if c else 0, time.monotonic())
        return self._owner_count[0]

    def _switch_player(self, p: Player):
        """Новый игрок в эфире — открываем (или заводим) его досье."""
        self.case = CaseFile.open_or_create(Path(self.settings.paths.cases_dir), p.name, uid=p.player_id,
                                            legend=p.legend)
        if p.portrait is not None and not self.case.info.portrait:
            self.case.set_portrait(p.portrait)
        self._refine_case(p)
        self._session_cases[p.key] = str(self.case.path)
        self._roster_update()
        who = p.name + (f" · {p.legend}" if p.legend else "")
        self.overlay.flash(t("В эфире") + f": {who}", 3)

    def _refine_case(self, p: Player):
        c = self.case
        if c is None:
            return
        i, changed = c.info, False
        if p.player_id and not i.ea_id:
            # ID дочитался позже ника: если досье с этим ID уже есть — переносим туда.
            self.case = c = c.set_identity(p.name, p.player_id, p.legend)
            i, changed = c.info, False
            self._session_cases[p.key] = str(c.path)
            self._roster_update()
        if p.legend and p.legend not in i.legends:
            i.legends.append(p.legend)
            changed = True
        if p.clan_tag and p.clan_tag != i.clan_tag:
            i.clan_tag, changed = p.clan_tag, True
        for m in p.teammates:
            if m not in i.teammates and not same_name(m, i.player_name):
                i.teammates.append(m)
                changed = True
        if changed:
            c.save()

    def _roster_update(self):
        self.live.roster.clear()
        for key, path in self._session_cases.items():
            try:
                c = CaseFile.open(Path(path))
            except (OSError, KeyError, ValueError):
                continue
            i = c.info
            current = self.case is not None and str(self.case.path) == path
            it = QListWidgetItem(legend_icon(i.legends[-1] if i.legends else "", 28),
                                 ("● " if current else "") + f"{i.player_name}"
                                 + (f" [{', '.join(i.tags)}]" if i.tags else "")
                                 + (f" · ID {i.ea_id}" if i.ea_id else "")
                                 + f"  —  {t('моментов')}: {len(i.moments)}")
            it.setData(Qt.UserRole, path)
            self.live.roster.addItem(it)

    # --- отметки --------------------------------------------------------------------

    def _on_hotkey(self, name: str):
        if name == "mark":
            self.mark()
        elif name == "toggle":
            self.toggle_recording()

    def mark(self):
        if not self.engine.running:
            self.overlay.flash(t("Запись выключена — нажми F9"))
            return
        frame = self.engine.latest_frame
        # Кто на экране именно в момент нажатия — перечитываем кадр, не ждём следующего скана.
        scan = self.watcher.read_now(frame) if frame is not None else None
        legend = ""
        if scan is not None and scan.spectating and (scan.player_id or scan.name):
            if not self.tracker.same_player(scan) and (scan.player_id or scan.confident):
                p = Player(scan.name, scan.player_id, legends.match(scan.portrait)[0], scan.clan_tag,
                           list(scan.teammates), scan.portrait, since=self.engine.stream_time)
                self.tracker.current = p
                self._switch_player(p)
            elif self.tracker.same_player(scan):
                legend = legends.match(scan.portrait)[0]
        spectated = self.tracker.current is not None and self.case is not None and (
            self.watcher.spectating or (scan is not None and scan.spectating))
        if spectated:
            p = self.tracker.current
            case, who, uid, since = self.case, p.name, p.player_id, p.since
            legend = legend or p.legend
        else:
            # Своя игра (или экран без вкладок наблюдения) — момент идёт в твоё досье [OWNER].
            if scan is not None and scan.own:
                legend = self._read_own(scan)
            legend = legend or self._own_legend
            case = CaseFile.owner_case(Path(self.settings.paths.cases_dir), self._own_name)
            self._owner_count = None
            self._session_cases["*owner"] = str(case.path)
            self._roster_update()
            who, uid, since = case.info.player_name, case.info.ea_id, None
        payload = {"case_path": str(case.path), "player": who, "uid": uid, "legend": legend, "since": since}
        req = self.engine.mark(payload)
        if req is not None:
            self.live.add_mark_row(req.id, who + (f" · {legend}" if legend else ""))
            self.overlay.flash(t("✓ Отмечено") + f": {who}" + (f" · {legend}" if legend else ""))

    def _on_mark_done(self, result: MarkResult):
        req = result.request
        p = req.payload
        path = Path(p.get("case_path", ""))
        try:
            case = CaseFile.open(path) if path.exists() else CaseFile.find(
                Path(self.settings.paths.cases_dir), uid=p.get("uid", ""), name=p.get("player", ""))
        except (OSError, KeyError, ValueError):
            case = None
        if case is None:
            self.live.set_mark_status(req.id, t("досье не найдено"))
            return
        case.add_moment(Moment(id=req.id, created_at=req.wall_time, legend=p.get("legend", ""),
                               mark_offset=result.mark_offset), result.clip_path, result.frame_path)
        if self.case is not None and self.case.path == case.path:
            self.case = case
        self._owner_count = None
        state = t("в досье") if result.clip_path else t("только кадр") + f" ({result.error})"
        self.live.set_mark_status(req.id, state)
        self._roster_update()
        self.overlay.flash(t("Клип сохранён в досье") + f": {case.info.player_name}")

    # --- обновление экрана ---------------------------------------------------------------

    def _tick(self):
        running = self.engine.running
        lv = self.live
        hk = self.settings.hotkeys
        lv.rec_btn.setText((t("■  Остановить") if running else t("●  Начать запись"))
                           + (f"   {hk.toggle}" if hk.toggle else ""))
        rec = self.settings.recording
        lv.rec_buffer.setText(t("Повтор") + f": {rec.seconds_before} {t('с')} · {t('F8 сохранит')} "
                              f"{rec.seconds_before + rec.seconds_after} {t('с')}")
        if running:
            tt = int(self.engine.stream_time)
            lv.rec_time.setText(f"{tt // 60:02d}:{tt % 60:02d}")
            set_chip(lv.rec_chip, "● REC", "rec")
            frame = self.engine.latest_frame
            if frame is not None and lv.isVisible():
                h = max(1, lv.preview.height())
                w = int(frame.shape[1] * h / frame.shape[0])
                small = cv2.cvtColor(cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
                img = QImage(small.data, w, h, w * 3, QImage.Format_RGB888)
                lv.preview.setPixmap(QPixmap.fromImage(img.copy()).scaled(
                    lv.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            set_chip(lv.rec_chip, t("ВЫКЛ"), "muted")
        w = self.watcher
        set_chip(lv.game_chip, t("Apex запущена") if w.game_found else t("Apex не найдена"),
                 "ok" if w.game_found else "muted")
        set_chip(lv.spect_chip, t("режим зрителя") if w.spectating else t("режим зрителя не виден"),
                 "ok" if w.spectating else "muted")
        fresh = running and time.monotonic() - self._last_read < 2
        set_chip(lv.read_chip, t("ник и легенда читаются") if fresh else t("ник и ID: ждём"),
                 "ok" if fresh else "muted")
        a = self.settings.audio
        audio = self.engine.audio if running else None
        if not (a.system or a.microphone):
            set_chip(lv.audio_chip, t("звук выключен"), "muted")
        elif audio is not None:
            errs = [tr.name for tr in audio.tracks if tr.error]
            parts = [t("игра") if a.system else "", t("микрофон") if a.microphone else ""]
            set_chip(lv.audio_chip, ("🔊 " + " + ".join(p for p in parts if p)) if not errs
                     else t("звук: ошибка") + f" ({', '.join(errs)})", "warn" if errs else "ok")
        else:
            set_chip(lv.audio_chip, t("звук пишется с записью"), "muted")
        set_chip(self.side_rec, "● REC" if running else t("ВЫКЛ"), "rec" if running else "muted")
        set_chip(self.side_spect, t("зритель") if w.spectating else t("зритель —"), "ok" if w.spectating else "muted")
        self._update_overlay()

    def _overlay_demo(self):
        """Показать оверлей сейчас, даже без игры, — проверить, что он есть и где стоит."""
        self._demo_until = time.monotonic() + 6
        o = self.settings.overlay
        try:
            target = display.resolve_target(self.settings.capture.auto_detect_game, self.settings.capture.monitor_index)
        except RuntimeError:
            return
        self.overlay.set_state([(RED, "REC 00:42"), (GREY, "Warlock1301 · Fuse"), (GREY, "ID 0160456899…"),
                                (GREY, f"{self.settings.hotkeys.mark} — " + t("отметить момент"))],
                               target, o.corner, o.opacity)
        self.overlay.show()
        self.overlay.flash(t("Так выглядит оверлей"), 6)

    def _update_overlay(self):
        if time.monotonic() < self._demo_until:
            return   # идёт показ из настроек
        o = self.settings.overlay
        running = self.engine.running
        visible = o.mode != "off" and (running or (o.mode == "always" and self.watcher.game_found))
        if o.mode == "off":
            set_chip(self.live.overlay_chip, t("оверлей выключен в настройках"), "muted")
        elif visible:
            set_chip(self.live.overlay_chip, t("оверлей показан"), "ok")
        else:
            set_chip(self.live.overlay_chip, t("оверлей появится с игрой"), "muted")
        if not visible:
            self.overlay.hide()
            return
        try:
            target = self._target or display.resolve_target(True, self.settings.capture.monitor_index)
        except RuntimeError:
            return
        hk = self.settings.hotkeys
        if running:
            tt = int(self.engine.stream_time)
            lines = [(RED, f"REC {tt // 60:02d}:{tt % 60:02d}")]
            p = self.tracker.current
            if self.watcher.spectating and p is not None:
                lines.append((GREY, p.name + (f" · {p.legend}" if p.legend else "")))
                if p.player_id:
                    lines.append((GREY, f"ID {p.player_id}"))
            elif self.watcher.spectating:
                lines.append((YELLOW, t("Распознаю игрока…")))
            else:
                lines.append((GREY, t("Своя игра") + (f" · {self._own_legend}" if self._own_legend else "")))
            if hk.mark:
                lines.append((GREY, f"{hk.mark} — " + t("отметить момент")))
        else:
            lines = [(GREEN, (f"{hk.toggle} — " if hk.toggle else "") + t("начать запись"))]
        self.overlay.set_state(lines, target, o.corner, o.opacity)
        if not self.overlay.isVisible():
            self.overlay.show()

    def closeEvent(self, event):
        self._tick_timer.stop()
        self.overlay.close()
        self.watcher.stop()
        self.hotkeys.stop()
        if self.engine.running:
            self.engine.stop()
        super().closeEvent(event)
