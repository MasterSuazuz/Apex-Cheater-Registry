# Qt обязательно загружаем раньше OCR Windows (пакеты winrt): в обратном порядке
# процесс падает с access violation из-за конфликта общих DLL.
import PySide6.QtCore  # noqa: F401
