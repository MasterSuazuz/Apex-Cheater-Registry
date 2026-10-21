"""Наблюдатель за экраном во время записи: кто сейчас на экране.

Запись сама не включается — только клавишей F9 (или кнопкой). Пока она идёт, два раза в секунду
читаем кадр записи:
- режим зрителя («В ЭФИРЕ» + «РЕЖИМ ЗРИТЕЛЯ») — ник, ID, отряд и легенда наблюдаемого;
- своя игра — ник и легенда самого пользователя из списка отряда.
С игрой программа никак не взаимодействует: только снимает экран.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from . import display
from .config import Settings
from .screenreader import ScanResult, ScreenReader

TICK = 0.5
CONFIRM = 2
SPECTATE_GRACE = 3.0     # сек без вкладок, после которых считаем, что режим зрителя закончился


class SpectateWatcher:
    """События (вызываются из рабочего потока):
        on_spectating(bool)   — режим зрителя на экране появился/пропал (запись не трогает)
        on_scan(ScanResult)   — результат чтения кадра записи
    """

    def __init__(self, settings: Settings, engine_state: Callable[[], tuple[bool, np.ndarray | None]],
                 on_spectating: Callable[[bool], None], on_scan: Callable[[ScanResult], None]):
        self.settings = settings
        self._engine_state = engine_state
        self._on_spectating = on_spectating
        self._on_scan = on_scan
        r = settings.recognition
        self.reader = ScreenReader(r.live_words, r.spectate_words)
        self._stop = threading.Event()
        self._scan_lock = threading.Lock()     # OCR из двух потоков (наблюдатель и отметка F8) — по очереди
        self._thread: threading.Thread | None = None
        self.spectating = False
        self.game_found = False
        self.last_scan: ScanResult | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="screen-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)

    def release_camera(self) -> None:
        """Совместимость: свой захват экрана наблюдатель больше не держит."""

    def _set(self, on: bool) -> None:
        if on != self.spectating:
            self.spectating = on
            self._on_spectating(on)

    def _loop(self) -> None:
        hits, last_seen, tick = 0, 0.0, 0
        while not self._stop.wait(TICK):
            tick += 1
            r = self.settings.recognition
            self.reader.live_words, self.reader.spectate_words = r.live_words, r.spectate_words
            if tick % 4 == 1:        # поиск окна игры раз в 2 с — для оверлея и подсказок
                self.game_found = display.find_game_window() is not None
            recording, frame = self._engine_state()
            if not recording or frame is None:
                hits = 0
                self._set(False)
                continue
            try:
                with self._scan_lock:
                    scan = self.reader.scan(frame, full=True)
            except Exception:
                continue
            self.last_scan = scan
            if scan.spectating:
                hits += 1
                last_seen = time.monotonic()
                if hits >= CONFIRM:
                    self._set(True)
            else:
                hits = 0
                if self.spectating and time.monotonic() - last_seen > SPECTATE_GRACE:
                    self._set(False)
            try:
                self._on_scan(scan)
            except Exception:
                pass  # сбой разбора не должен останавливать наблюдение

    def read_now(self, frame: np.ndarray) -> ScanResult:
        """Немедленное чтение кадра (для отметки: кто на экране именно сейчас)."""
        with self._scan_lock:
            return self.reader.scan(frame, full=True)
