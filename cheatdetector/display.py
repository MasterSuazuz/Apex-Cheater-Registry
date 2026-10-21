"""Поиск окна Apex Legends и определение области захвата.

Окно ищем только по заголовку и классу через обычные функции user32 — к процессу
игры мы не подключаемся и его память не трогаем, чтобы античит (EA Javelin)
не видел в программе ничего, кроме того, что делает любая программа записи экрана.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32
# Без явных типов 64-битные HWND/HMONITOR могут не влезть в c_int.
for _name, _args in {
    "IsWindowVisible": [wintypes.HWND],
    "IsIconic": [wintypes.HWND],
    "GetClassNameW": [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int],
    "GetWindowTextW": [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int],
    "GetClientRect": [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
    "ClientToScreen": [wintypes.HWND, ctypes.POINTER(wintypes.POINT)],
    "GetWindowLongW": [wintypes.HWND, ctypes.c_int],
    "MonitorFromWindow": [wintypes.HWND, wintypes.DWORD],
}.items():
    getattr(user32, _name).argtypes = _args
user32.MonitorFromWindow.restype = wintypes.HMONITOR

APEX_WINDOW_CLASSES = {"Respawn001"}
APEX_TITLES = ("Apex Legends",)

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
MONITORINFOF_PRIMARY = 1


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfoEx)]


@dataclass(frozen=True)
class Monitor:
    index: int
    handle: int
    device: str
    left: int
    top: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        from .i18n import t
        main = ", " + t("основной") if self.primary else ""
        return f"{t('Монитор')} {self.index + 1}: {self.width}×{self.height}{main}"


@dataclass(frozen=True)
class CaptureTarget:
    monitor: Monitor
    left: int       # абсолютные координаты рабочего стола
    top: int
    width: int
    height: int
    game_found: bool
    mode: str       # "fullscreen" | "windowed" | "monitor"

    @property
    def region_on_monitor(self) -> tuple[int, int, int, int]:
        x = self.left - self.monitor.left
        y = self.top - self.monitor.top
        return x, y, x + self.width, y + self.height

    @property
    def description(self) -> str:
        if not self.game_found:
            return f"Игра не найдена — пишу весь экран: {self.width}×{self.height}"
        mode = {"fullscreen": "полный экран / без рамки", "windowed": "в окне"}.get(self.mode, self.mode)
        return f"Apex Legends: {self.width}×{self.height}, {mode}, монитор {self.monitor.index + 1}"


def enable_dpi_awareness() -> None:
    """Без этого Windows отдаёт «масштабированные» координаты при 125%/150%."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()


def list_monitors() -> list[Monitor]:
    raw: list[Monitor] = []
    proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def callback(hmon, _hdc, _rect, _lparam):
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            raw.append(Monitor(0, int(hmon or 0), info.szDevice, r.left, r.top,
                               r.right - r.left, r.bottom - r.top,
                               bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    # Основной монитор первым, остальные слева направо — как привык видеть пользователь.
    raw.sort(key=lambda m: (not m.primary, m.left, m.top))
    return [Monitor(i, m.handle, m.device, m.left, m.top, m.width, m.height, m.primary)
            for i, m in enumerate(raw)]


def find_game_window() -> int | None:
    found: list[int] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if cls.value in APEX_WINDOW_CLASSES or title.value.startswith(APEX_TITLES):
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return found[0] if found else None


def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 64 or h < 64:
        return None
    return origin.x, origin.y, w, h


def resolve_target(auto_detect: bool, monitor_index: int) -> CaptureTarget:
    monitors = list_monitors()
    if not monitors:
        from .i18n import t
        raise RuntimeError(t("Не найдено ни одного монитора"))
    fallback = monitors[monitor_index] if 0 <= monitor_index < len(monitors) else monitors[0]

    if auto_detect:
        hwnd = find_game_window()
        rect = _client_rect(hwnd) if hwnd else None
        if rect:
            x, y, w, h = rect
            hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            mon = next((m for m in monitors if m.handle == int(hmon or 0)), fallback)
            # Окно может вылезать за край монитора — захват возможен только внутри него.
            left, top = max(x, mon.left), max(y, mon.top)
            right = min(x + w, mon.left + mon.width)
            bottom = min(y + h, mon.top + mon.height)
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            mode = "windowed" if style & WS_CAPTION else "fullscreen"
            return CaptureTarget(mon, left, top, (right - left) & ~1, (bottom - top) & ~1, True, mode)

    m = fallback
    return CaptureTarget(m, m.left, m.top, m.width & ~1, m.height & ~1, False, "monitor")
