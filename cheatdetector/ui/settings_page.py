from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit,
                               QLineEdit, QMessageBox, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import audio, capture, display, legend_icons, legends
from ..config import (CAPTURE_BACKENDS, ENCODERS, LANGUAGES, OVERLAY_CORNERS, OVERLAY_MODES, RECORD_RESOLUTIONS,
                      Settings)
from ..i18n import t
from .theme import Card, button, muted, page_header


def _combo(options: dict[str, str], current: str) -> QComboBox:
    box = QComboBox()
    for key, label in options.items():
        box.addItem(t(label), key)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    return f


class _PathRow(QWidget):
    def __init__(self, path: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(path)
        lay.addWidget(self.edit, 1)
        lay.addWidget(button(t("Выбрать…"), "", self._browse))
        lay.addWidget(button(t("Открыть"), "ghost", self._open))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, t("Выбор папки"), self.edit.text())
        if path:
            self.edit.setText(os.path.normpath(path))

    def _open(self):
        p = Path(self.edit.text())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)


def _device_combo(names: list[str], current: str) -> QComboBox:
    box = QComboBox()
    box.addItem(t("По умолчанию (как в Windows)"), "")
    for n in names:
        box.addItem(n, n)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


class SettingsPage(QWidget):
    saved = Signal()
    overlay_test = Signal()
    icons_done = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.setObjectName("page")
        self.settings = settings
        s = settings

        replay = Card(t("Повтор (как в NVIDIA)"))
        self.before = QSpinBox()
        self.before.setRange(5, 600)
        self.before.setSuffix(" " + t("с"))
        self.before.setValue(s.recording.seconds_before)
        self.before.valueChanged.connect(self._replay_info)
        presets = QHBoxLayout()
        presets.setSpacing(6)
        presets.addWidget(self.before)
        for sec in (15, 30, 60, 120, 300):
            presets.addWidget(button(f"{sec // 60} " + t("мин") if sec >= 60 else f"{sec} " + t("с"), "ghost",
                                     lambda _c=False, v=sec: self.before.setValue(v)))
        presets.addStretch(1)
        self.after = QSpinBox()
        self.after.setRange(0, 60)
        self.after.setSuffix(" " + t("с"))
        self.after.setValue(s.recording.seconds_after)
        self.after.valueChanged.connect(self._replay_info)
        f = _form()
        f.addRow(t("Длина повтора"), presets)
        f.addRow(t("Дописать после F8"), self.after)
        replay.body.addLayout(f)
        self.replay_text = muted("")
        replay.body.addWidget(self.replay_text)
        self._replay_info()

        general = Card(t("Общие"))
        self.language = _combo(LANGUAGES, s.language)
        self.hk_mark = QKeySequenceEdit(QKeySequence(s.hotkeys.mark))
        self.hk_mark.setMaximumSequenceLength(1)
        self.hk_toggle = QKeySequenceEdit(QKeySequence(s.hotkeys.toggle))
        self.hk_toggle.setMaximumSequenceLength(1)
        f = _form()
        f.addRow(t("Язык"), self.language)
        f.addRow(t("Отметить момент"), self.hk_mark)
        f.addRow(t("Старт/стоп записи вручную"), self.hk_toggle)
        general.body.addLayout(f)

        recog = Card(t("Распознавание экрана"),
                     t("Пока идёт запись, программа читает экран. Режим зрителя — вкладки «В эфире» и «Режим "
                       "зрителя»: ник с красной плашки, ID из строки «Идентификатор». Своя игра — твой ник внизу "
                       "списка отряда. Легенда — по портрету в списке отряда."))
        self.live_words = QLineEdit(", ".join(s.recognition.live_words))
        self.spect_words = QLineEdit(", ".join(s.recognition.spectate_words))
        f = _form()
        f.addRow(t("Вкладка «В эфире»"), self.live_words)
        f.addRow(t("Вкладка «Режим зрителя»"), self.spect_words)
        recog.body.addLayout(f)
        recog.body.addWidget(muted(t("Если в твоей версии игры надписи другие — допиши их через запятую.")))
        ib = QHBoxLayout()
        self.icons_info = muted(t("Портретов легенд") + f": {len(legend_icons.installed())}")
        ib.addWidget(self.icons_info, 1)
        self.icons_btn = button(t("Скачать новых легенд"), "", self._update_icons)
        ib.addWidget(self.icons_btn)
        recog.body.addLayout(ib)
        self.icons_done.connect(self._icons_done)

        snd = Card(t("Звук"), t("Пишется вместе с видео и попадает в клипы. Игра пишется так же, как в OBS: "
                                "через звук Windows, к игре программа не подключается."))
        a = s.audio
        self.snd_system = QCheckBox(t("Звук игры (всё, что слышно в наушниках/колонках)"))
        self.snd_system.setChecked(a.system)
        self.snd_speaker = _device_combo(audio.speakers(), a.speaker_device)
        self.snd_mic = QCheckBox(t("Микрофон (твой голос)"))
        self.snd_mic.setChecked(a.microphone)
        self.snd_mic_dev = _device_combo(audio.microphones(), a.mic_device)
        self.snd_mic_vol = QSlider(Qt.Horizontal)
        self.snd_mic_vol.setRange(0, 200)
        self.snd_mic_vol.setValue(a.mic_volume)
        f = _form()
        f.addRow(self.snd_system)
        f.addRow(t("Устройство вывода"), self.snd_speaker)
        f.addRow(self.snd_mic)
        f.addRow(t("Микрофон"), self.snd_mic_dev)
        f.addRow(t("Громкость микрофона"), self.snd_mic_vol)
        snd.body.addLayout(f)

        cap = Card(t("Захват и запись"))
        self.auto_detect = QCheckBox(t("Находить окно Apex автоматически"))
        self.auto_detect.setChecked(s.capture.auto_detect_game)
        self.monitor = QComboBox()
        for m in display.list_monitors():
            self.monitor.addItem(m.label, m.index)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(s.capture.monitor_index)))
        self.backend = _combo(CAPTURE_BACKENDS, s.capture.backend)
        self.fps = QComboBox()
        for v in (30, 60, 120):
            self.fps.addItem(f"{v} " + t("к/с"), v)
        self.fps.setCurrentIndex(max(0, self.fps.findData(s.capture.fps)))
        self.resolution = _combo(RECORD_RESOLUTIONS, s.recording.resolution)
        self.encoder = _combo(ENCODERS, s.recording.encoder)
        self.quality = QSpinBox()
        self.quality.setRange(15, 35)
        self.quality.setValue(s.recording.quality)
        f = _form()
        f.addRow(self.auto_detect)
        f.addRow(t("Монитор без игры"), self.monitor)
        f.addRow(t("Способ захвата"), self.backend)
        f.addRow(t("Частота кадров"), self.fps)
        f.addRow(t("Разрешение клипов"), self.resolution)
        f.addRow(t("Кодировщик"), self.encoder)
        f.addRow(t("Качество (CQ/CRF)"), self.quality)
        cap.body.addLayout(f)
        cap.body.addWidget(muted(t("NVENC доступен — запись на видеокарте") if capture.nvenc_available()
                                 else t("NVENC недоступен — запись на процессоре (x264)")))

        overlay = Card(t("Оверлей поверх игры"), t("Не попадает в клипы. Виден в режимах экрана «Без рамки» и "
                                                   "«В окне»."))
        self.ov_mode = _combo(OVERLAY_MODES, s.overlay.mode)
        self.ov_corner = _combo(OVERLAY_CORNERS, s.overlay.corner)
        self.ov_opacity = QSlider(Qt.Horizontal)
        self.ov_opacity.setRange(30, 100)
        self.ov_opacity.setValue(s.overlay.opacity)
        f = _form()
        f.addRow(t("Показывать"), self.ov_mode)
        f.addRow(t("Угол экрана"), self.ov_corner)
        f.addRow(t("Непрозрачность"), self.ov_opacity)
        overlay.body.addLayout(f)
        ob = QHBoxLayout()
        ob.addWidget(muted(t("Поверх эксклюзивного полноэкранного режима Windows окна не показывает — поставь в игре "
                             "«Без рамки».")), 1)
        ob.addWidget(button(t("Показать оверлей на 6 секунд"), "", self.overlay_test.emit))
        overlay.body.addLayout(ob)

        paths = Card(t("Папки"))
        self.cases_dir = _PathRow(s.paths.cases_dir)
        self.temp_dir = _PathRow(s.paths.temp_dir)
        f = _form()
        f.addRow(t("Досье (.apexcase)"), self.cases_dir)
        f.addRow(t("Временный буфер"), self.temp_dir)
        paths.body.addLayout(f)

        body = QWidget()
        body.setObjectName("page")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 6, 0)
        column = QWidget()
        column.setMaximumWidth(1000)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(14)
        for card in (replay, general, cap, snd, recog, overlay, paths):
            cl.addWidget(card)
        cl.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)

        top = QHBoxLayout()
        top.addWidget(page_header(t("Настройки"), t("Изменения применяются кнопкой «Сохранить»")), 1)
        top.addWidget(button(t("Сохранить"), "primary", self.save), 0, Qt.AlignBottom)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 22, 22)
        root.setSpacing(14)
        root.addLayout(top)
        root.addWidget(scroll, 1)

    def _replay_info(self):
        b, a = self.before.value(), self.after.value()
        self.replay_text.setText(
            t("Пока идёт запись, на диске лежат только последние {b} с — всё, что старше, стирается само. "
              "F8 сохраняет в досье последние {b} с + {a} с после нажатия ({n} с), эти клипы не стираются.")
            .format(b=b, a=a, n=b + a))

    def _update_icons(self):
        self.icons_btn.setEnabled(False)
        self.icons_info.setText(t("Скачиваю портреты с вики Apex Legends…"))

        def work():
            try:
                added = legend_icons.download()
                msg = (t("Новые легенды") + ": " + ", ".join(added)) if added else t("Новых легенд нет")
            except Exception as exc:
                msg = t("Не удалось скачать") + f": {exc}"
            self.icons_done.emit(msg)
        threading.Thread(target=work, daemon=True).start()

    def _icons_done(self, msg: str):
        legends.reload()
        self.icons_btn.setEnabled(True)
        self.icons_info.setText(msg + f" · {t('Портретов легенд')}: {len(legend_icons.installed())}")

    @staticmethod
    def _words(edit: QLineEdit) -> list[str]:
        return [w.strip() for w in edit.text().split(",") if w.strip()]

    def save(self):
        s = self.settings
        lang_changed = self.language.currentData() != s.language
        s.language = self.language.currentData()
        mark, toggle = self.hk_mark.keySequence().toString(), self.hk_toggle.keySequence().toString()
        if mark and mark == toggle:
            QMessageBox.warning(self, t("Горячие клавиши"), t("Клавиши отметки и записи должны отличаться."))
            return
        s.hotkeys.mark, s.hotkeys.toggle = mark, toggle
        s.recognition.live_words = self._words(self.live_words) or ["в эфире", "live"]
        s.recognition.spectate_words = self._words(self.spect_words) or ["режим зрителя", "spectat"]
        s.audio.system = self.snd_system.isChecked()
        s.audio.speaker_device = self.snd_speaker.currentData() or ""
        s.audio.microphone = self.snd_mic.isChecked()
        s.audio.mic_device = self.snd_mic_dev.currentData() or ""
        s.audio.mic_volume = self.snd_mic_vol.value()
        s.capture.auto_detect_game = self.auto_detect.isChecked()
        s.capture.monitor_index = self.monitor.currentData() or 0
        s.capture.backend = self.backend.currentData()
        s.capture.fps = self.fps.currentData()
        s.recording.resolution = self.resolution.currentData()
        s.recording.encoder = self.encoder.currentData()
        s.recording.quality = self.quality.value()
        s.recording.seconds_before = self.before.value()
        s.recording.seconds_after = self.after.value()
        s.overlay.mode = self.ov_mode.currentData()
        s.overlay.corner = self.ov_corner.currentData()
        s.overlay.opacity = self.ov_opacity.value()
        s.paths.cases_dir = self.cases_dir.edit.text().strip()
        s.paths.temp_dir = self.temp_dir.edit.text().strip()
        s.save()
        self.saved.emit()
        if lang_changed:
            QMessageBox.information(self, t("Язык"), t("Язык сменится после перезапуска программы."))
