"""Глобальные горячие клавиши через RegisterHotKey.

Это штатный механизм Windows (им пользуются Discord, OBS), без перехвата клавиатуры,
поэтому работает, когда игра в фокусе, и не выглядит подозрительно для античита.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "meta": MOD_WIN}
_KEYS = {f"f{i}": 0x6F + i for i in range(1, 25)}
_KEYS.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update({"insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
              "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22, "pause": 0x13,
              "scrolllock": 0x91, "print": 0x2C, "space": 0x20})
_KEYS.update({f"num{d}": 0x60 + d for d in range(10)})


def parse(combo: str) -> tuple[int, int] | None:
    """'Ctrl+Shift+F8' -> (модификаторы, код клавиши)."""
    if not combo:
        return None
    mods, vk = 0, None
    for part in combo.replace(" ", "").split("+"):
        p = part.lower()
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _KEYS:
            vk = _KEYS[p]
        else:
            return None
    return (mods, vk) if vk is not None else None


class GlobalHotkeys:
    def __init__(self, on_hotkey: Callable[[str], None]):
        self._on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.failed: list[str] = []

    def start(self, bindings: dict[str, str]) -> None:
        self.stop()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(bindings, ready), daemon=True)
        self._thread.start()
        ready.wait(2)

    def stop(self) -> None:
        if self._thread and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread, self._thread_id = None, 0

    def _loop(self, bindings: dict[str, str], ready: threading.Event) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        names: dict[int, str] = {}
        self.failed = []
        for i, (name, combo) in enumerate(bindings.items(), start=1):
            if not combo:
                continue  # клавиша не назначена — это не ошибка
            parsed = parse(combo)
            if parsed and user32.RegisterHotKey(None, i, parsed[0] | MOD_NOREPEAT, parsed[1]):
                names[i] = name
            else:
                self.failed.append(combo)
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in names:
                self._on_hotkey(names[msg.wParam])
        for i in names:
            user32.UnregisterHotKey(None, i)
