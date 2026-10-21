"""Звук для клипов: то, что слышно в наушниках/колонках (игра, голосовой чат), и микрофон.

Оба потока пишутся в память (кольцевой буфер на последние N секунд) с отметками времени
time.perf_counter(). При сохранении момента нужный отрезок вырезается, микшируется и
подклеивается к видео. Через WASAPI, как OBS: игра ничего не замечает.
"""
from __future__ import annotations

import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

RATE = 48000
BLOCK = 960            # 20 мс
RESYNC = 0.15          # если поток «уплыл» от часов больше чем на 150 мс — привязываемся заново


def _sc():
    import soundcard
    return soundcard


def speakers() -> list[str]:
    try:
        return [s.name for s in _sc().all_speakers()]
    except Exception:
        return []


def microphones() -> list[str]:
    try:
        return [m.name for m in _sc().all_microphones()]
    except Exception:
        return []


class _Track:
    """Один источник: непрерывная лента сэмплов, привязанная к часам perf_counter."""

    def __init__(self, name: str, keep_seconds: float):
        self.name = name
        self.keep = keep_seconds
        self.chunks: deque[tuple[float, np.ndarray]] = deque()   # (время первого сэмпла, стерео float32)
        self.lock = threading.Lock()
        self.error = ""
        self._next_t: float | None = None

    def push(self, data: np.ndarray, now: float) -> None:
        if not len(data):
            return
        if data.ndim == 1:
            data = data[:, None]
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        data = data[:, :2].astype(np.float32)
        measured = now - len(data) / RATE
        # Идём по счётчику сэмплов (без дрожания), сверяясь с часами.
        t = measured if self._next_t is None or abs(self._next_t - measured) > RESYNC else self._next_t
        self._next_t = t + len(data) / RATE
        with self.lock:
            self.chunks.append((t, data))
            while self.chunks and self.chunks[0][0] < now - self.keep:
                self.chunks.popleft()

    def slice(self, t0: float, t1: float) -> np.ndarray:
        n = max(0, int(round((t1 - t0) * RATE)))
        out = np.zeros((n, 2), np.float32)
        with self.lock:
            chunks = list(self.chunks)
        for ct, data in chunks:
            start = int(round((ct - t0) * RATE))
            a, b = max(0, start), min(n, start + len(data))
            if a < b:
                out[a:b] = data[a - start:b - start]
        return out


class AudioRecorder:
    def __init__(self, system: bool, mic: bool, speaker_name: str = "", mic_name: str = "",
                 mic_volume: int = 100, keep_seconds: float = 120):
        self.mic_gain = mic_volume / 100
        self.tracks: list[_Track] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._plan = []
        if system:
            self._plan.append(("system", speaker_name))
        if mic:
            self._plan.append(("mic", mic_name))
        self.keep = keep_seconds

    @property
    def active(self) -> bool:
        return any(not tr.error for tr in self.tracks)

    def start(self) -> None:
        self._stop.clear()
        for kind, dev_name in self._plan:
            tr = _Track(kind, self.keep)
            self.tracks.append(tr)
            th = threading.Thread(target=self._run, args=(tr, kind, dev_name), name=f"audio-{kind}", daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._stop.set()
        for th in self._threads:
            th.join(2)
        self._threads.clear()

    def _device(self, kind: str, dev_name: str):
        sc = _sc()
        if kind == "system":
            spk = next((s for s in sc.all_speakers() if s.name == dev_name), None) or sc.default_speaker()
            return sc.get_microphone(id=str(spk.name), include_loopback=True), 2
        mic = next((m for m in sc.all_microphones() if m.name == dev_name), None) or sc.default_microphone()
        return mic, 1

    def _run(self, track: _Track, kind: str, dev_name: str) -> None:
        import warnings
        try:
            dev, channels = self._device(kind, dev_name)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")    # «data discontinuity» — пропуски закрываются по часам
                with dev.recorder(samplerate=RATE, channels=channels, blocksize=BLOCK) as rec:
                    while not self._stop.is_set():
                        data = rec.record(numframes=None)
                        track.push(data, time.perf_counter())
        except Exception as exc:
            track.error = str(exc) or type(exc).__name__

    def write_wav(self, t0: float, t1: float, path: Path) -> bool:
        """Сводит все источники за отрезок часов [t0, t1] в WAV. False — если звука нет."""
        mix = None
        for tr in self.tracks:
            part = tr.slice(t0, t1)
            if tr.name == "mic":
                part *= self.mic_gain
            mix = part if mix is None else mix + part
        if mix is None or not len(mix):
            return False
        pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
        return True
