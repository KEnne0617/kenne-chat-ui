# A WeChat-like chat window built with PySide6.

from __future__ import annotations

import math
import re
import sys
import time

from PySide6.QtCore import (
    QAbstractAnimation, QCoreApplication, QEasingCurve, QEvent, QPointF, QRect,
    QRectF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPalette, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QScrollBar,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

# Windows API used to change always-on-top without recreating the window.
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    )
    _user32.SetWindowPos.restype = wintypes.BOOL
    HWND_TOPMOST = wintypes.HWND(-1)
    HWND_NOTOPMOST = wintypes.HWND(-2)
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_NOOWNERZORDER = 0x0200

# App name shown in the title bar and the chat header.
APP_NAME = "KEnne"

# Colors.
BG = "#fafafa"
TITLEBAR = "#e3e3e8"
DIVIDER = "#f0f0f0"
CARD_BORDER = "#eaeaea"
TEXT = "#19191a"
MUTED = "#676769"
PLACEHOLDER = "#cacace"
PLACEHOLDER_TEXT = "Please enter content..."
PLACEHOLDER_FADE_MS = 150
SEND_OFF_BG = "#ededed"
SEND_OFF_FG = "#a5a5a5"
SEND_ON_BG = "#222222"
SEND_ACTIVE_BG = "#222222"
SEND_ACTIVE_FG = "#ffffff"
BUBBLE = "#222222"
BUBBLE_TEXT = "#ffffff"

# Window and main layout sizes.
W, H = 508, 784
TITLEBAR_H = 34
HEADER_H = 47
HEADER_BOTTOM = TITLEBAR_H + HEADER_H
INPUT_CARD = QRectF(7, 643, 493, 134)
FEED_TOP = HEADER_BOTTOM + 4
FEED_BOTTOM = int(INPUT_CARD.top()) - 4

# Input card sizes (resizable by dragging its top edge).
CARD_BOTTOM = int(INPUT_CARD.bottom())
EDITOR_TOP_PAD = 4
EDITOR_TEXT_INSET = 9
EDITOR_BOTTOM = 732
FEED_CARD_GAP = 4
FEED_RIGHT_GAP = 6
MAX_INPUT_H = int(H * 0.45)
MIN_EDITOR_LINES = 1.5
RESIZE_HANDLE_H = 8
EDITOR_W = 472

# Bubble text wrapping. A zero-width space lets QLabel break words that are
# longer than the bubble, without altering what the message says. Only ASCII
# runs need it: CJK text can already break between any two characters, and
# adding a hint there would change where lines end.
# How long a send keeps the view pinned to the newest message.
FOLLOW_BOTTOM_S = 0.3

# Wheel scrolling: 60 px per notch, eased over 160 ms.
WHEEL_SCROLL_STEP = 60
WHEEL_SCROLL_MS = 160

SOFT_BREAK = "\u200b"
LONG_TOKEN = 24
LONG_TOKEN_RE = re.compile(r"([\x21-\x7e]{%d})(?=[\x21-\x7e])" % LONG_TOKEN)

# Outer frame and drop shadow sizes.
FRAME = 5
SHADOW = 20
OUTER_RADIUS = 8
PAGE_RADIUS = 6
OUTER_CARD = QRectF(SHADOW, SHADOW, W + 2 * FRAME, H + FRAME)
CONTENT_X = SHADOW + FRAME
CONTENT_Y = SHADOW
WINDOW_W = int(W + 2 * FRAME + 2 * SHADOW)
WINDOW_H = int(H + FRAME + 2 * SHADOW)


# Build the app font.
def font(size: int = 14) -> QFont:
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(size)
    return f


# Insert invisible break hints into ASCII runs that are too long to wrap.
def soft_wrap(text: str) -> str:
    return LONG_TOKEN_RE.sub(lambda m: m.group(1) + SOFT_BREAK, text)


# Strip the break hints again, so copied text is byte-identical to the message.
def hard_text(text: str) -> str:
    return text.replace(SOFT_BREAK, "")


# Text box: shows the placeholder on hover, sends on Enter.
class Editor(QTextEdit):
    def __init__(self, parent: QWidget, on_send, on_input_state) -> None:
        super().__init__(parent)
        self.on_send = on_send
        self.on_input_state = on_input_state
        self._composing = False
        self._hovered = False
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def is_composing(self) -> bool:
        return self._composing

    def is_hovered(self) -> bool:
        return self._hovered

    # Track IME typing so the placeholder can hide.
    def inputMethodEvent(self, event) -> None:
        super().inputMethodEvent(event)
        self._composing = bool(event.preeditString())
        self.on_input_state()

    # Track mouse hover inside the text box.
    def eventFilter(self, obj, event) -> bool:
        if obj is self.viewport():
            if event.type() == QEvent.Enter:
                self._set_hovered(True)
            elif event.type() == QEvent.Leave:
                self._set_hovered(False)
        return super().eventFilter(obj, event)

    def _set_hovered(self, hovered: bool) -> None:
        if self._hovered != hovered:
            self._hovered = hovered
            self.on_input_state()

    # Enter sends; Shift+Enter and Ctrl+Enter add a new line.
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # While an IME is composing, Enter belongs to the IME, not to us.
            if self._composing:
                super().keyPressEvent(event)
                return
            if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.insertPlainText("\n")
                event.accept()
                return
            self.on_send()
            event.accept()
            return
        super().keyPressEvent(event)


# Message bubble: three rounded corners, square bottom-right corner.
class Bubble(QLabel):
    RADIUS = 8

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hard_text = text
        # Never let a message be parsed as markup: show it exactly as typed.
        self.setTextFormat(Qt.PlainText)
        self.setText(soft_wrap(text))

    # The message as the user typed it.
    def message_text(self) -> str:
        return self._hard_text

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(BUBBLE))
        painter.drawPath(self._shape())
        painter.end()
        super().paintEvent(event)

    # Draw the bubble outline.
    def _shape(self) -> QPainterPath:
        radius = self.RADIUS
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.moveTo(rect.left() + radius, rect.top())
        path.lineTo(rect.right() - radius, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left() + radius, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - radius)
        path.lineTo(rect.left(), rect.top() + radius)
        path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
        path.closeSubpath()
        return path

    # Copy the message without the invisible break hints.
    def _copy_selection(self) -> None:
        selected = hard_text(self.selectedText())
        if selected:
            QApplication.clipboard().setText(selected)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Copy):
            self._copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        copy_action = menu.addAction(
            QCoreApplication.translate("QWidgetTextControl", "&Copy")
        )
        copy_action.setEnabled(bool(self.selectedText()))
        copy_action.triggered.connect(self._copy_selection)
        menu.exec(event.globalPos())


# Invisible drag bar on the input top edge, used to resize the input.
class ResizeHandle(QWidget):
    def __init__(self, parent: QWidget, on_start, on_drag) -> None:
        super().__init__(parent)
        self._on_start = on_start
        self._on_drag = on_drag
        self._press_y = None
        self.setCursor(Qt.SizeVerCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_y = event.globalPosition().y()
            self._on_start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_y is not None and event.buttons() & Qt.LeftButton:
            self._on_drag(event.globalPosition().y() - self._press_y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._press_y is not None:
            self._press_y = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


# Main window: holds the message list, the input and the custom title bar.
class FeedScrollBar(QScrollBar):
    """Chat-only scrollbar: always reserves width, paints only while hovered."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Vertical, parent)
        self._hovered = False

    def set_hovered(self, hovered: bool) -> None:
        if self._hovered != hovered:
            self._hovered = hovered
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        painter.end()
        if self.isSliderDown() or (self._hovered and self.maximum() > self.minimum()):
            super().paintEvent(event)


class WeChatWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self.setStyleSheet("QWidget { font-family: 'Microsoft YaHei UI'; }")

        self._card_path = QPainterPath()
        self._card_path.addRoundedRect(OUTER_CARD, OUTER_RADIUS, OUTER_RADIUS)
        self._drag_offset = None
        self._pinned = False
        self._grow_passes = 0
        self._follow_bottom_until = 0.0

        self.feed = self._make_feed()
        # The send button exists before the editor, because the editor calls back
        # into _update_inputs which touches it.
        self.send_button = self._make_send_button()
        self.editor = self._make_editor()

        # Work out the input min/max height for auto grow and dragging.
        self._min_editor_h = math.ceil(
            self.editor.fontMetrics().lineSpacing() * MIN_EDITOR_LINES
        )
        self._min_card_h = (
            EDITOR_TOP_PAD + EDITOR_TEXT_INSET + self._min_editor_h
            + (CARD_BOTTOM - EDITOR_BOTTOM)
        )
        self._max_card_h = max(MAX_INPUT_H, self._min_card_h)
        self._card_top = CARD_BOTTOM - self._min_card_h
        self._resize_origin_top = self._card_top
        self.resize_handle = ResizeHandle(
            self, self._start_input_resize, self._drag_input_resize
        )
        self._set_input_card_top(self._card_top)

        # Fade animation for the placeholder text.
        self._placeholder_wanted = False
        self._placeholder_opacity = 0.0
        self._placeholder_anim = QVariantAnimation(self)
        self._placeholder_anim.setDuration(PLACEHOLDER_FADE_MS)
        self._placeholder_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._placeholder_anim.valueChanged.connect(self._apply_placeholder_opacity)

        self._auto_grow_pending = False
        self._wheel_anims = {}
        self._feed_hover_timer = QTimer(self)
        self._feed_hover_timer.setInterval(40)
        self._feed_hover_timer.timeout.connect(self._update_feed_scrollbar_hover)
        self._feed_hover_timer.start()
        self.editor.textChanged.connect(self._on_text_changed)
        self.feed.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self._update_inputs()
        QApplication.instance().installEventFilter(self)

    # Message list (scroll area).
    def _make_feed(self) -> QScrollArea:
        area = QScrollArea(self)
        area.setGeometry(CONTENT_X + 16, CONTENT_Y + FEED_TOP,
                         W - 16 - FEED_RIGHT_GAP, FEED_BOTTOM - FEED_TOP)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setFocusPolicy(Qt.NoFocus)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBar(FeedScrollBar(area))
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        area.setStyleSheet(f"""
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}
            QScrollBar:vertical {{
                width: 8px; margin: 2px 0; background: transparent; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #c6c6c6; border-radius: 4px; min-height: 22px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #a8a8a8; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        content = QWidget()
        content.setStyleSheet(f"background: {BG};")
        self.feed_layout = QVBoxLayout(content)
        self.feed_layout.setContentsMargins(0, 16, 14, 8)
        self.feed_layout.setSpacing(12)
        self.feed_layout.addStretch(1)
        area.setWidget(content)
        return area

    # Show the chat bar only while the pointer is inside the message area.
    def _update_feed_scrollbar_hover(self) -> None:
        local = self.feed.mapFromGlobal(QCursor.pos())
        self.feed.verticalScrollBar().set_hovered(self.feed.rect().contains(local))

    # Input text box.
    def _make_editor(self) -> Editor:
        editor = Editor(self, self.send_message, self._update_inputs)
        editor_top = int(INPUT_CARD.top()) + EDITOR_TOP_PAD
        editor.setGeometry(
            CONTENT_X + 22,
            CONTENT_Y + editor_top,
            EDITOR_W,
            EDITOR_BOTTOM - editor_top,
        )
        editor.setFont(font())
        editor.setCursorWidth(1)
        editor.setFocusPolicy(Qt.ClickFocus)
        editor.setAcceptRichText(False)
        editor.setFrameShape(QTextEdit.NoFrame)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.document().setDocumentMargin(0)
        editor.setViewportMargins(0, EDITOR_TEXT_INSET, 0, 0)
        editor.setStyleSheet(f"""
            QTextEdit {{
                background: {BG}; color: {TEXT}; border: none; padding: 0;
                selection-background-color: #07c160; selection-color: white;
            }}
            QScrollBar:vertical {{
                width: 8px; margin: 2px 0; background: transparent; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #c6c6c6; border-radius: 4px; min-height: 22px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #a8a8a8; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        color = QColor(PLACEHOLDER)
        color.setAlphaF(0.0)
        editor.ensurePolished()
        palette = editor.palette()
        palette.setColor(QPalette.PlaceholderText, color)
        editor.setPalette(palette)
        return editor

    # Send button.
    def _make_send_button(self) -> QPushButton:
        button = QPushButton("SEND", self)
        button.setGeometry(CONTENT_X + 432, CONTENT_Y + 736, 56, 29)
        button.setFont(font())
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self.send_message)
        button.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 5px; background: {SEND_OFF_BG};
                color: {SEND_OFF_FG}; font-size: 12px; padding: 0;
            }}
            QPushButton:enabled {{ background: {SEND_ACTIVE_BG}; color: {SEND_ACTIVE_FG}; }}
        """)
        return button

    # Refresh the send button and the placeholder state.
    def _update_inputs(self) -> None:
        text = self.editor.toPlainText()
        composing = self.editor.is_composing()
        self.send_button.setEnabled(bool(text.strip()))
        self._update_placeholder(bool(text.strip()), composing)

    # React to typing.
    def _on_text_changed(self) -> None:
        self._update_inputs()
        self._grow_passes = 0
        self._schedule_auto_grow()

    def _schedule_auto_grow(self) -> None:
        if not self._auto_grow_pending:
            self._auto_grow_pending = True
            QTimer.singleShot(0, self._auto_grow_input)

    # Fit the input to its content: grow on new lines, shrink when text goes away.
    def _auto_grow_input(self) -> None:
        self._auto_grow_pending = False
        changed = self._apply_input_height()
        self.editor.ensureCursorVisible()
        # A scroll bar appearing or vanishing re-wraps the text, so re-check.
        if changed and self._grow_passes < 3:
            self._grow_passes += 1
            self._schedule_auto_grow()

    # Move the input card to the height the current text needs.
    def _apply_input_height(self) -> bool:
        line_h = self.editor.fontMetrics().lineSpacing()
        if line_h <= 0:
            return False
        text = self.editor.toPlainText()
        content_h = self.editor.document().size().height() if text else 0.0
        extra_lines = 0
        if content_h > self._min_editor_h:
            extra_lines = math.ceil((content_h - self._min_editor_h) / line_h)
        target_h = self._min_card_h + extra_lines * line_h
        target_h = max(self._min_card_h, min(self._max_card_h, target_h))
        if abs(target_h - self._input_card.height()) < 1:
            return False
        self._set_input_card_top(CARD_BOTTOM - target_h)
        return True

    # Remember the input top when a drag starts.
    def _start_input_resize(self) -> None:
        self._resize_origin_top = self._card_top

    # Move the input top while dragging.
    def _drag_input_resize(self, delta: float) -> None:
        self._set_input_card_top(self._resize_origin_top + round(delta))

    # Resize the input card and move the editor, feed and drag bar with it.
    def _set_input_card_top(self, top: int) -> None:
        height = max(self._min_card_h, min(self._max_card_h, CARD_BOTTOM - int(top)))
        top = CARD_BOTTOM - height
        self._card_top = top
        self._input_card = QRectF(INPUT_CARD.left(), top, INPUT_CARD.width(), height)

        editor_top = top + EDITOR_TOP_PAD
        self.editor.setGeometry(
            CONTENT_X + 22,
            CONTENT_Y + editor_top,
            EDITOR_W,
            EDITOR_BOTTOM - editor_top,
        )
        self.feed.setGeometry(
            CONTENT_X + 16,
            CONTENT_Y + FEED_TOP,
            W - 16 - FEED_RIGHT_GAP,
            max(1, top - FEED_CARD_GAP - FEED_TOP),
        )
        self.resize_handle.setGeometry(
            CONTENT_X + int(INPUT_CARD.left()) + 4,
            CONTENT_Y + top - RESIZE_HANDLE_H // 2,
            int(INPUT_CARD.width()) - 8,
            RESIZE_HANDLE_H,
        )
        self.resize_handle.raise_()
        self.update()

    # Animate wheel scrolling for both text areas instead of native line jumps.
    def _smooth_wheel_scroll(self, bar, event) -> bool:
        if bar.maximum() <= bar.minimum():
            return False
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            distance = -pixel_delta
        else:
            angle_delta = event.angleDelta().y()
            if not angle_delta:
                return False
            distance = -round(angle_delta / 120.0 * WHEEL_SCROLL_STEP)
        if not distance:
            return False

        anim = self._wheel_anims.get(bar)
        if anim is None:
            anim = QVariantAnimation(self)
            anim.setDuration(WHEEL_SCROLL_MS)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(
                lambda value, b=bar: b.setValue(round(value))
            )
            self._wheel_anims[bar] = anim

        base = anim.endValue() if anim.state() == QAbstractAnimation.Running else bar.value()
        target = max(bar.minimum(), min(bar.maximum(), int(base) + distance))
        if target == bar.value():
            anim.stop()
            return True
        anim.stop()
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()
        return True

    # Drop the text focus when clicking outside the input.
    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(obj, QWidget):
            if (obj is self.feed
                    or obj is self.feed.verticalScrollBar()
                    or self.feed.isAncestorOf(obj)):
                return self._smooth_wheel_scroll(self.feed.verticalScrollBar(), event)
            if (obj is self.editor
                    or obj is self.editor.verticalScrollBar()
                    or self.editor.isAncestorOf(obj)):
                return self._smooth_wheel_scroll(self.editor.verticalScrollBar(), event)
        if (event.type() == QEvent.MouseButtonPress
                and isinstance(obj, QWidget)
                and (obj is self or self.isAncestorOf(obj))
                and obj is not self.resize_handle
                and not self.resize_handle.isAncestorOf(obj)):
            in_editor = obj is self.editor or self.editor.isAncestorOf(obj)
            if not in_editor:
                local = self.mapFromGlobal(event.globalPosition().toPoint())
                in_input_card = self._input_card.translated(CONTENT_X, CONTENT_Y).contains(local)
                if in_input_card:
                    self.editor.setFocus()
                else:
                    self.editor.clearFocus()
        return super().eventFilter(obj, event)

    # Show the placeholder only on hover, with a short fade.
    def _update_placeholder(self, has_text: bool, composing: bool) -> None:
        if has_text or composing:
            self._placeholder_anim.stop()
            self._placeholder_wanted = False
            if self.editor.placeholderText():
                self.editor.setPlaceholderText("")
            if self._placeholder_opacity:
                self._apply_placeholder_opacity(0.0)
            return

        if self.editor.is_hovered():
            if self.editor.placeholderText() != PLACEHOLDER_TEXT:
                self.editor.setPlaceholderText(PLACEHOLDER_TEXT)
            if not self._placeholder_wanted:
                self._placeholder_wanted = True
                self._fade_placeholder(1.0)
        elif self._placeholder_wanted:
            self._placeholder_wanted = False
            self._fade_placeholder(0.0)

    # Start the placeholder fade.
    def _fade_placeholder(self, target: float) -> None:
        self._placeholder_anim.stop()
        if abs(self._placeholder_opacity - target) < 0.01:
            self._apply_placeholder_opacity(target)
            return
        self._placeholder_anim.setStartValue(self._placeholder_opacity)
        self._placeholder_anim.setEndValue(target)
        self._placeholder_anim.start()

    # Apply the placeholder opacity to the palette.
    def _apply_placeholder_opacity(self, value: float) -> None:
        self._placeholder_opacity = float(value)
        color = QColor(PLACEHOLDER)
        color.setAlphaF(self._placeholder_opacity)
        palette = self.editor.palette()
        palette.setColor(QPalette.PlaceholderText, color)
        self.editor.setPalette(palette)
        self.editor.viewport().update()

    # Add a bubble for the typed text, then clear the input.
    def send_message(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        bubble = Bubble(text)
        bubble.setFont(font())
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(340)
        bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setStyleSheet(f"""
            background: transparent; color: {BUBBLE_TEXT}; padding: 8px 12px;
        """)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(bubble)
        # Insert before the bottom stretch so messages stack from the top.
        self.feed_layout.insertLayout(self.feed_layout.count() - 1, row)
        self.editor.clear()
        self.editor.setFocus()
        self._scroll_to_bottom()

    # Scroll to the newest message once the layout knows its final height.
    def _scroll_to_bottom(self) -> None:
        # Keep following the bottom for a moment: a burst of sends keeps growing
        # the scroll range after this call returns.
        self._follow_bottom_until = time.monotonic() + FOLLOW_BOTTOM_S
        bar = self.feed.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    # Jump to the newest message whenever the range grows, just after sending.
    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if time.monotonic() < self._follow_bottom_until:
            self.feed.verticalScrollBar().setValue(maximum)

    # Title bar buttons and window dragging.
    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() == Qt.LeftButton and self._is_titlebar(pos.toPoint()):
            control = self._control_at(pos.toPoint())
            if control == "pin":
                self.toggle_pin()
            elif control == "min":
                self.showMinimized()
            elif control == "close":
                self.close()
            else:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    # Toggle always-on-top; the pin turns black when active.
    def toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self._apply_always_on_top(self._pinned)
        self.update()
        self.editor.clearFocus()

    # Change the top-most state in place, without hiding the window.
    def _apply_always_on_top(self, on: bool) -> None:
        if sys.platform == "win32":
            hwnd = int(self.winId())
            insert_after = HWND_TOPMOST if on else HWND_NOTOPMOST
            ok = _user32.SetWindowPos(
                hwnd, insert_after, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )
            if ok:
                return
        # Fallback: rebuild the window only when the native call is unavailable.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # Check if a point is inside the title bar.
    @staticmethod
    def _is_titlebar(point) -> bool:
        return (OUTER_CARD.left() <= point.x() < OUTER_CARD.right()
                and OUTER_CARD.top() <= point.y()
                < OUTER_CARD.top() + TITLEBAR_H)

    # Find which title bar button is at a point.
    @staticmethod
    def _control_at(point) -> str | None:
        if not WeChatWindow._is_titlebar(point):
            return None
        x = point.x() - CONTENT_X
        for name, center in (("pin", 402), ("min", 446), ("close", 490)):
            if abs(x - center) <= 20:
                return name
        return None

    # Paint the whole window.
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        self._paint_shadow(p)
        p.fillPath(self._card_path, QColor(TITLEBAR))

        p.save()
        p.setClipPath(self._card_path)
        p.translate(CONTENT_X, CONTENT_Y)
        self._paint_content(p)
        p.restore()

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#b9b9c0"), 1))
        p.drawRoundedRect(OUTER_CARD.adjusted(0.5, 0.5, -0.5, -0.5),
                          OUTER_RADIUS - 0.5, OUTER_RADIUS - 0.5)

    # Draw the soft drop shadow around the card.
    def _paint_shadow(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        for grow in range(SHADOW - 2, 0, -1):
            p.setBrush(QColor(0, 0, 0, 3 if grow < 9 else 2))
            p.drawRoundedRect(
                OUTER_CARD.adjusted(-grow, -grow + 1, grow, grow + 1),
                OUTER_RADIUS + grow, OUTER_RADIUS + grow,
            )

    # Draw the page background, header and input.
    def _paint_content(self, p: QPainter) -> None:
        page = QPainterPath()
        page.addRoundedRect(
            QRectF(0, TITLEBAR_H, W, H - TITLEBAR_H), PAGE_RADIUS, PAGE_RADIUS
        )
        p.fillPath(page, QColor(BG))

        p.save()
        p.setClipPath(page)
        self._paint_header(p)
        self._paint_input_card(p)
        p.restore()

        self._paint_window_buttons(p)

    # Draw the minimize, close and pin icons.
    def _paint_window_buttons(self, p: QPainter) -> None:
        cy = (TITLEBAR_H + 1) / 2
        p.setPen(QPen(QColor(MUTED), 1.05, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(441.5, cy), QPointF(450.5, cy))
        p.drawLine(QPointF(485.5, cy - 4), QPointF(494, cy + 4))
        p.drawLine(QPointF(494, cy - 4), QPointF(485.5, cy + 4))

        p.setPen(QPen(QColor(SEND_ON_BG) if self._pinned else QColor(MUTED),
                      1.05, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx = 402
        pin = QPainterPath()
        pin.moveTo(cx - 3.5, cy - 5.5)
        pin.lineTo(cx + 3.5, cy - 5.5)
        pin.cubicTo(cx + 2.2, cy - 3.8, cx + 2.1, cy - 2.5, cx + 2.1, cy + 1.2)
        pin.lineTo(cx + 4.5, cy + 2.8)
        pin.lineTo(cx - 4.5, cy + 2.8)
        pin.lineTo(cx - 2.1, cy + 1.2)
        pin.cubicTo(cx - 2.1, cy - 2.5, cx - 2.2, cy - 3.8, cx - 3.5, cy - 5.5)
        p.drawPath(pin)
        p.drawLine(QPointF(cx, cy + 2.8), QPointF(cx, cy + 6.5))

    # Draw the chat title and the divider line.
    def _paint_header(self, p: QPainter) -> None:
        p.setFont(font(14))
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(16, TITLEBAR_H - 1, 220, HEADER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
        p.fillRect(QRect(0, HEADER_BOTTOM, W, 1), QColor(DIVIDER))

    # Draw the input card border and its bottom shadow.
    def _paint_input_card(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(CARD_BORDER))
        p.drawRoundedRect(self._input_card, 8, 8)
        p.setBrush(QColor(BG))
        p.drawRoundedRect(self._input_card.adjusted(2, 2, -2, -2), 6, 6)
        p.fillRect(QRectF(9, 775, 488, 1), QColor("#f0f0f0"))
        p.fillRect(QRectF(9, 776, 488, 1), QColor("#e3e3e3"))

        shadow = QLinearGradient(0, 777, 0, H)
        shadow.setColorAt(0, QColor("#f2f2f2"))
        shadow.setColorAt(1, QColor("#f7f7f7"))
        p.fillRect(QRectF(8, 777, 492, H - 777), shadow)


# Start the app and center the window.
def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(font())
    window = WeChatWindow()
    screen = app.primaryScreen()
    if screen is not None:
        # Centre on the usable area so the taskbar can never cover the window.
        area = screen.availableGeometry()
        window.move(
            area.x() + max(0, (area.width() - WINDOW_W) // 2),
            area.y() + max(0, (area.height() - WINDOW_H) // 2),
        )
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
