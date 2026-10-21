"""Apex Cheater Registry — запуск."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


def purge_stale_buffers(temp_dir: str) -> None:
    """Сессии, оставшиеся после аварийного закрытия, и распакованные для просмотра клипы досье
    занимают место — чистим при старте (сами досье лежат отдельно и не трогаются)."""
    from cheatdetector.config import CACHE_DIR

    for d in Path(temp_dir).glob("session_*"):
        shutil.rmtree(d, ignore_errors=True)
    if CACHE_DIR.exists():
        for d in CACHE_DIR.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    # Импорты здесь, чтобы и ошибку загрузки библиотек поймал report_crash.
    from PySide6.QtWidgets import QApplication

    import ctypes

    from cheatdetector.config import APP_TITLE, Settings
    from cheatdetector.display import enable_dpi_awareness
    from cheatdetector.i18n import set_language

    settings = Settings.load()
    set_language(settings.language)   # до импорта окон: подписи создаются при их построении

    from cheatdetector.ui import theme
    from cheatdetector.ui.main_window import MainWindow

    # Свой идентификатор приложения: тогда на панели задач — наша иконка, а не иконка Python.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ApexCheatDetector.App")
    enable_dpi_awareness()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(theme.app_icon())
    theme.apply(app)
    purge_stale_buffers(settings.paths.temp_dir)
    window = MainWindow(settings)
    window.show()
    return app.exec()


def report_crash(exc_type, exc, tb) -> None:
    """pythonw не показывает консоль, поэтому ошибку пишем в журнал и показываем окном."""
    import ctypes
    import traceback

    import os

    text = "".join(traceback.format_exception(exc_type, exc, tb))
    # Путь без импорта config: при сбое загрузки пакета он тоже может не импортироваться.
    log = Path(os.environ.get("APPDATA") or Path.home()) / "ApexCheatDetector" / "crash.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{text}")
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(None, f"{exc}\n\nПодробности: {log}", "Apex Cheater Registry — ошибка", 0x10)


if __name__ == "__main__":
    sys.excepthook = report_crash
    try:
        sys.exit(main())
    except Exception:
        report_crash(*sys.exc_info())
        sys.exit(1)
