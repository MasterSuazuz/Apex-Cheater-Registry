"""Оформление: палитра, стили Qt (QSS) и общие виджеты — карточки, «пилюли» статусов."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..config import ASSETS_DIR, CACHE_DIR

BG = "#0d0f13"
SURFACE = "#14171d"
SURFACE_2 = "#1a1e26"
SURFACE_3 = "#222733"
BORDER = "#262b36"
TEXT = "#e7e9ee"
MUTED = "#8b93a3"
ACCENT = "#e5484d"
ACCENT_HOVER = "#f2565b"
ACCENT_DIM = "#3a1d21"
OK = "#3dd68c"
WARN = "#f5a524"
INFO = "#4c9aff"

ICONS = {"live": "◉", "cases": "▤", "settings": "⚙"}


def draw_icon(size: int = 256) -> QImage:
    """Иконка программы: тёмный скруглённый квадрат, красное кольцо прицела и перекрестие."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    p.fillPath(bg, QColor("#14171d"))
    p.setPen(QPen(QColor("#262b36"), 6 * s))
    p.drawPath(bg)
    c = QPointF(128 * s, 128 * s)
    p.setPen(QPen(QColor(ACCENT), 18 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawEllipse(c, 70 * s, 70 * s)
    p.setPen(QPen(QColor("#ffffff"), 14 * s, Qt.SolidLine, Qt.RoundCap))
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        p.drawLine(QPointF(c.x() + dx * 34 * s, c.y() + dy * 34 * s), QPointF(c.x() + dx * 96 * s, c.y() + dy * 96 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawEllipse(c, 13 * s, 13 * s)
    p.end()
    return img


def app_icon() -> QIcon:
    png = ASSETS_DIR / "icon.png"
    icon = QIcon(str(png)) if png.exists() else QIcon()
    if icon.isNull():
        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(QPixmap.fromImage(draw_icon(size)))
    return icon


def _icon_files() -> dict[str, str]:
    """Галочка и стрелка для QSS: стили Qt принимают картинки только файлами."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "check": '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                 '<path d="M3 7.2l2.6 2.6L11 4.4" fill="none" stroke="white" stroke-width="2" '
                 'stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "chevron": f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                   f'<path d="M3 4.5l3 3 3-3" fill="none" stroke="{MUTED}" stroke-width="1.6" '
                   f'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    }
    out = {}
    for name, svg in files.items():
        p = CACHE_DIR / f"ui_{name}.svg"
        p.write_text(svg, encoding="utf-8")
        out[name] = p.as_posix()
    return out


def stylesheet() -> str:
    icons = _icon_files()
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}

/* Боковая панель */
QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#brand {{ font-size: 13pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 8.5pt; letter-spacing: 2px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 10px 14px; margin: 2px 10px; border-radius: 8px; color: {MUTED}; }}
QListWidget#nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}

/* Заголовок страницы */
QLabel#pageTitle {{ font-size: 17pt; font-weight: 700; }}
QLabel#pageSub {{ color: {MUTED}; }}

/* Карточки */
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#cardSub, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#bigValue {{ font-size: 20pt; font-weight: 700; }}
QLabel#midValue {{ font-size: 13pt; font-weight: 600; }}

/* «Пилюли» статусов */
QLabel[chip="rec"] {{ background: {ACCENT_DIM}; color: {ACCENT_HOVER}; border-radius: 10px; padding: 2px 10px; font-weight: 600; }}
QLabel[chip="ok"] {{ background: #12301f; color: {OK}; border-radius: 10px; padding: 2px 10px; font-weight: 600; }}
QLabel[chip="warn"] {{ background: #3a2a0c; color: {WARN}; border-radius: 10px; padding: 2px 10px; font-weight: 600; }}
QLabel[chip="info"] {{ background: #13263f; color: {INFO}; border-radius: 10px; padding: 2px 10px; font-weight: 600; }}
QLabel[chip="muted"] {{ background: {SURFACE_3}; color: {MUTED}; border-radius: 10px; padding: 2px 10px; }}

/* Кнопки */
QPushButton {{ background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: #2a3040; }}
QPushButton:pressed {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: #5a6170; background: {SURFACE_2}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton[kind="star"] {{ background: transparent; border: none; font-size: 16pt; color: #4a505e; padding: 0 2px; }}
QPushButton[kind="star"]:checked {{ color: {WARN}; }}
QPushButton[kind="star"]:hover {{ color: #c9a24a; }}

/* Шкала «есть ли чит»: 1–2 зелёные, 3 жёлтая, 4–5 красные */
QPushButton[scale]:checked {{ color: white; font-weight: 600; }}
QPushButton[scale="0"]:checked {{ background: #3a4150; border-color: {MUTED}; }}
QPushButton[scale="1"]:checked, QPushButton[scale="2"]:checked {{ background: #1f7a4d; border-color: {OK}; }}
QPushButton[scale="3"]:checked {{ background: #7a5a14; border-color: {WARN}; }}
QPushButton[scale="4"]:checked, QPushButton[scale="5"]:checked {{ background: #8f2a2e; border-color: {ACCENT}; }}

/* Поля ввода */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QKeySequenceEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 9px;
    selection-background-color: {ACCENT}; min-height: 20px;
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{icons['chevron']}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a4150; background: {SURFACE_2}; }}
QCheckBox::indicator:hover {{ border: 1px solid {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; image: url("{icons['check']}"); }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

/* Таблицы и списки */
QTableWidget, QListWidget, QTextBrowser {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: transparent;
    alternate-background-color: #171a21; outline: none;
}}
QTableWidget::item, QListWidget::item {{ padding: 6px; border: none; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3240; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4254; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2c3240; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; width: 8px; }}
QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QMessageBox {{ background: {SURFACE}; }}
QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE_2, QPalette.AlternateBase: SURFACE,
        QPalette.Text: TEXT, QPalette.Button: SURFACE_3, QPalette.ButtonText: TEXT, QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: SURFACE_3, QPalette.ToolTipText: TEXT,
        QPalette.Link: ACCENT_HOVER, QPalette.PlaceholderText: "#5f6676",
    }.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(stylesheet())


# --- виджеты ---------------------------------------------------------------------

class Card(QFrame):
    """Карточка с заголовком: body — вертикальная раскладка для содержимого."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title = None
        if title:
            self.title = QLabel(title)
            self.title.setObjectName("cardTitle")
            self.header.addWidget(self.title)
        self.header.addStretch(1)
        lay.addLayout(self.header)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("cardSub")
            s.setWordWrap(True)
            lay.addWidget(s)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        lay.addLayout(self.body)

    def add_header_widget(self, w: QWidget) -> None:
        self.header.addWidget(w)

    def set_title(self, text: str) -> None:
        if self.title is not None and self.title.text() != text:
            self.title.setText(text)


def chip(text: str = "", kind: str = "muted") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("chip", kind)
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


def set_chip(lbl: QLabel, text: str, kind: str) -> None:
    lbl.setText(text)
    if lbl.property("chip") != kind:
        lbl.setProperty("chip", kind)
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)


def muted(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("muted", True)
    lbl.setWordWrap(wrap)
    lbl.setTextFormat(Qt.RichText)
    return lbl


def button(text: str, kind: str = "", slot=None):
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot is not None:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    s = QLabel(subtitle)
    s.setObjectName("pageSub")
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w
