"""The window's building blocks: the picture, keyboard keys you can click, and rows of choices."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

BACKGROUND = "#15171a"
PANEL = "#1d2024"
TEXT = "#e8e8e8"
DIM = "#9aa1a9"
ACCENT = "#6cb6ff"
URGENT = "#ff6369"
BORDER = "#3a3f46"

STYLE = f"""
QWidget {{ background: {PANEL}; color: {TEXT}; font-size: 13px; }}
QMainWindow, QWidget#root {{ background: {BACKGROUND}; }}
QLabel[role="dim"] {{ color: {DIM}; font-size: 12px; }}
QLabel[role="title"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="status"] {{ font-size: 15px; }}
QLabel[role="error"] {{ font-size: 15px; color: {URGENT}; }}
QPushButton {{ border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 11px; background: transparent; }}
QPushButton:hover {{ border-color: {TEXT}; }}
QPushButton:pressed {{ background: #2d3238; }}
QPushButton:checked {{ border-color: {ACCENT}; background: #24384d; color: #ffffff; }}
QPushButton:disabled {{ color: #6b7178; border-color: #2a2e33; }}
QPushButton:checked:disabled {{ border-color: #3d5a78; background: #1f2c3a; color: #9fb4c8; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {BORDER}; }}
QTabBar::tab {{ padding: 7px 14px; border: 0; border-bottom: 2px solid transparent; color: {DIM}; background: transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QScrollArea {{ border: 0; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def label(text: str = "", role: str = "", wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setProperty("role", role)
    return widget


def set_role(widget: QWidget, role: str) -> None:
    if widget.property("role") != role:
        widget.setProperty("role", role)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def choice_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCheckable(True)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys belong to the camera, not to whichever button was clicked last
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to the next line when the row is full."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, row_height = rect.x(), y + row_height + self._gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y()

    def clear(self) -> None:
        while self._items:
            widget = self._items.pop().widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class Preview(QWidget):
    """The live picture, scaled to fit, with a message when there is none."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image = QImage()
        self._message = ""
        self._recording = False
        self.source_size = QSize()
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def show_jpeg(self, jpeg: bytes) -> bool:
        """Decode a frame no larger than it will be drawn. False if it is not a readable JPEG."""
        buffer = QBuffer()
        buffer.setData(QByteArray(jpeg))
        buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"jpeg")
        full = reader.size()
        if full.isValid():
            self.source_size = full
            target = self.size() * self.devicePixelRatioF()
            if full.width() > target.width() and full.height() > target.height():
                reader.setScaledSize(full.scaled(target, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            return False
        self._image = image
        self.update()
        return True

    def clear(self, message: str = "") -> None:
        self._image = QImage()
        self._message = message
        self.update()

    def set_recording(self, recording: bool) -> None:
        if recording != self._recording:
            self._recording = recording
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._image.isNull():
            painter.setPen(QColor(DIM))
            font = QFont(self.font())
            font.setPixelSize(17)
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(24, 24, -24, -24), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)
        else:
            size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(QPoint((self.width() - size.width()) // 2, (self.height() - size.height()) // 2), size)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._image)
        if self._recording:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(URGENT))
            painter.drawEllipse(QRectF(16, 16, 14, 14))
            painter.setPen(QColor("#ffffff"))
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRect(38, 10, 80, 26), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "REC")


class KeyCap(QAbstractButton):
    """A keyboard key drawn where it sits on the keyboard, which is also a button.

    It shows the key's letter, a symbol and what it does, so the window teaches
    the shortcuts. ``pressed`` and ``released`` (from QAbstractButton) drive the
    hold-to-move keys; ``clicked`` drives the rest.
    """

    def __init__(self, key: str, symbol: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.symbol = symbol
        self.caption = text
        self.lit = False
        self.urgent = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_face(self, symbol: str | None = None, caption: str | None = None, lit: bool | None = None, urgent: bool | None = None) -> None:
        before = (self.symbol, self.caption, self.lit, self.urgent)
        self.symbol = self.symbol if symbol is None else symbol
        self.caption = self.caption if caption is None else caption
        self.lit = self.lit if lit is None else lit
        self.urgent = self.urgent if urgent is None else urgent
        if before != (self.symbol, self.caption, self.lit, self.urgent):
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(62, 56)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.4)
        tint = QColor(URGENT if self.urgent else TEXT)
        hot = self.isEnabled() and (self.underMouse() or self.lit)
        fill = QColor(tint)
        fill.setAlphaF(0.24 if self.isDown() else 0.11 if hot else 0.0)
        edge = QColor(ACCENT if self.lit and not self.urgent else tint)
        edge.setAlphaF(1.0 if hot else 0.35)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 7, 7)
        # A thicker bottom edge reads as a keycap.
        under = QColor(tint)
        under.setAlphaF(0.35)
        painter.setPen(QPen(under, 2))
        painter.drawLine(int(box.left() + 7), int(box.bottom() - 1), int(box.right() - 7), int(box.bottom() - 1))

        small = QFont(self.font())
        small.setPixelSize(11)
        bold = QFont(small)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(ACCENT))
        painter.drawText(self.rect().adjusted(6, 3, -4, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.key)
        if self.symbol:
            big = QFont(self.font())
            big.setPixelSize(19)
            painter.setFont(big)
            painter.setPen(tint)
            painter.drawText(self.rect().adjusted(0, 6, 0, -16), Qt.AlignmentFlag.AlignCenter, self.symbol)
        painter.setFont(small)
        painter.setPen(QColor(TEXT if not self.symbol else DIM))
        area = self.rect().adjusted(3, 16 if not self.symbol else 0, -3, -5)
        flags = Qt.AlignmentFlag.AlignHCenter | (
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap if not self.symbol else Qt.AlignmentFlag.AlignBottom
        )
        painter.drawText(area, flags, self.caption)


class OptionRow(QWidget):
    """A titled row of choices where exactly one is selected, with an optional note under it."""

    chosen = Signal(object)

    def __init__(self, title: str, options: list[tuple[object, str]], note: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(label(title))
        holder = QWidget()
        self._flow = FlowLayout(holder)
        layout.addWidget(holder)
        self._note = label(note, "dim")
        self._note.setVisible(bool(note))
        layout.addWidget(self._note)
        self.buttons: dict[object, QPushButton] = {}
        self.set_options(options)

    def set_options(self, options: list[tuple[object, str]]) -> None:
        if [(v, b.text()) for v, b in self.buttons.items()] == list(options):
            return
        self._flow.clear()
        self.buttons = {}
        for value, text in options:
            button = choice_button(text)
            button.clicked.connect(lambda _checked=False, value=value: self.chosen.emit(value))
            self._flow.addWidget(button)
            self.buttons[value] = button
        self._flow.invalidate()

    def show_state(self, selected: object, enabled: bool = True, locked: tuple = ()) -> None:
        for value, button in self.buttons.items():
            button.setChecked(value == selected)
            button.setEnabled(enabled and value not in locked)

    def set_note(self, text: str) -> None:
        self._note.setText(text)
        self._note.setVisible(bool(text))
