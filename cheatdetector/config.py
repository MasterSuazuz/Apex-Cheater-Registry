"""Настройки приложения. Хранятся в %APPDATA%\\ApexCheatDetector\\settings.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

APP_NAME = "ApexCheatDetector"          # папки и настройки — не менять, иначе потеряются досье
APP_TITLE = "Apex Cheater Registry"       # название в окне
APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
SETTINGS_PATH = APP_DIR / "settings.json"
CACHE_DIR = APP_DIR / "cache"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# Ключ -> подпись в интерфейсе (подписи переводятся через i18n.t при показе).
RECORD_RESOLUTIONS = {
    "native": "Как в игре (без сжатия)",
    "1440": "1440p",
    "1080": "1080p",
    "900": "900p",
    "720": "720p",
    "540": "540p",
}

ENCODERS = {
    "auto": "Автоматически",
    "nvenc": "NVIDIA NVENC (видеокарта)",
    "x264": "x264 (процессор)",
}

OVERLAY_MODES = {
    "always": "Когда игра запущена",
    "recording": "Только во время записи",
    "off": "Выключен",
}

OVERLAY_CORNERS = {
    "top-left": "Слева сверху",
    "top-right": "Справа сверху",
    "bottom-left": "Слева снизу",
    "bottom-right": "Справа снизу",
}

CAPTURE_BACKENDS = {
    "auto": "Автоматически",
    "dxcam": "DXGI (dxcam), быстрый",
    "mss": "GDI (mss), совместимый",
}

LANGUAGES = {"ru": "Русский", "en": "English"}


@dataclass
class CaptureSettings:
    auto_detect_game: bool = True
    monitor_index: int = 0          # если окно игры не найдено
    backend: str = "auto"
    fps: int = 60


@dataclass
class RecordingSettings:
    resolution: str = "native"
    encoder: str = "auto"
    quality: int = 23               # CQ/CRF: меньше число — выше качество и больше файл
    seconds_before: int = 30        # длина повтора: столько секунд до F8 хранится и сохраняется
    seconds_after: int = 5          # сколько дописать после F8

    @property
    def buffer_seconds(self) -> int:
        """Сколько записи держим на диске; всё старше стирается (как повтор в NVIDIA)."""
        return self.seconds_before + 4


@dataclass
class AudioSettings:
    system: bool = True             # звук игры и всего, что слышно в наушниках
    microphone: bool = True         # свой голос
    speaker_device: str = ""        # "" — устройство по умолчанию
    mic_device: str = ""
    mic_volume: int = 100           # %


@dataclass
class PathSettings:
    cases_dir: str = str(Path.home() / "Documents" / APP_NAME / "cases")
    temp_dir: str = str(APP_DIR / "buffer")


@dataclass
class HotkeySettings:
    mark: str = "F8"
    toggle: str = "F9"              # включить/выключить запись вручную


@dataclass
class OverlaySettings:
    mode: str = "always"
    corner: str = "top-left"
    opacity: int = 85               # %


@dataclass
class RecognitionSettings:
    """Как программа узнаёт экран наблюдения (начала слов, без регистра)."""
    live_words: list = field(default_factory=lambda: ["в эфире", "live"])
    spectate_words: list = field(default_factory=lambda: ["режим зрителя", "spectat"])


@dataclass
class Settings:
    language: str = "en"           # при первом запуске — английский
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    recognition: RecognitionSettings = field(default_factory=RecognitionSettings)

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return _from_dict(cls, data)

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)


def _from_dict(cls, data):
    """Собирает dataclass из словаря, пропуская неизвестные и битые поля (старые версии настроек)."""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        value = data[f.name]
        if is_dataclass(current):
            setattr(obj, f.name, _from_dict(type(current), value))
        elif isinstance(current, bool):
            setattr(obj, f.name, bool(value))
        elif isinstance(current, float) and isinstance(value, (int, float)):
            setattr(obj, f.name, float(value))
        elif isinstance(current, (int, str, dict, list)) and isinstance(value, type(current)):
            setattr(obj, f.name, value)
    return obj
