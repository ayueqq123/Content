"""Chat panel for manual reply — shows user conversations and allows sending."""

import time
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QLineEdit,
    QPushButton, QLabel,
)
from PyQt6.QtCore import Qt, pyqtSignal


class ChatPanel(QWidget):
    """Left: user list, Right: message history + reply input."""

    send_requested = pyqtSignal(int, str)  # (uid, text)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: user list ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("用户列表"))
        self.user_list = QListWidget()
        self.user_list.currentItemChanged.connect(self._on_user_selected)
        left_layout.addWidget(self.user_list)
        left.setMaximumWidth(200)
        splitter.addWidget(left)

        # ── Right: chat area ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.chat_label = QLabel("选择左侧用户查看对话")
        right_layout.addWidget(self.chat_label)

        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setObjectName("logPanel")
        right_layout.addWidget(self.chat_view)

        # Reply input
        reply_row = QHBoxLayout()
        self.reply_input = QLineEdit()
        self.reply_input.setPlaceholderText("输入回复消息...")
        self.reply_input.returnPressed.connect(self._on_send)
        reply_row.addWidget(self.reply_input)

        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("startBtn")
        self.send_btn.setFixedWidth(80)
        self.send_btn.clicked.connect(self._on_send)
        reply_row.addWidget(self.send_btn)
        right_layout.addLayout(reply_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

        self._current_uid: int | None = None
        # uid -> QListWidgetItem
        self._uid_items: dict[int, QListWidgetItem] = {}

    def refresh_store(self, chat_store: dict):
        """Update user list from the chat store."""
        for uid, data in chat_store.items():
            if uid not in self._uid_items:
                name = data.get("name", str(uid))
                item = QListWidgetItem(f"{name}  ({uid})")
                item.setData(Qt.ItemDataRole.UserRole, uid)
                self.user_list.addItem(item)
                self._uid_items[uid] = item
            else:
                name = data.get("name", str(uid))
                self._uid_items[uid].setText(f"{name}  ({uid})")

        if self._current_uid is not None:
            self._render_messages(chat_store)

    def on_new_message(self, uid: int, chat_store: dict):
        """Called when a new message arrives for a user."""
        self.refresh_store(chat_store)
        if uid == self._current_uid:
            self._render_messages(chat_store)

    def _on_user_selected(self, current: QListWidgetItem, _prev):
        if current is None:
            return
        uid = current.data(Qt.ItemDataRole.UserRole)
        self._current_uid = uid
        self.chat_label.setText(f"与 {current.text()} 的对话")
        # Need caller to trigger render with store
        self._request_render()

    def _request_render(self):
        """Ask parent to call render with the current store."""
        # This will be connected externally
        pass

    def render_current(self, chat_store: dict):
        """Render messages for the currently selected user."""
        self._render_messages(chat_store)

    def _render_messages(self, chat_store: dict):
        if self._current_uid is None:
            return
        data = chat_store.get(self._current_uid)
        if not data:
            self.chat_view.clear()
            return

        html_parts = []
        for msg in data.get("messages", []):
            ts = datetime.fromtimestamp(msg["time"]).strftime("%H:%M:%S")
            text = msg["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if msg["from"] == "user":
                html_parts.append(
                    f'<div style="margin:4px 0;">'
                    f'<span style="color:#4fc3f7;">[{ts}] 用户:</span> '
                    f'<span style="color:#e0e0e0;">{text}</span></div>'
                )
            else:
                html_parts.append(
                    f'<div style="margin:4px 0;">'
                    f'<span style="color:#81c784;">[{ts}] Bot:</span> '
                    f'<span style="color:#e0e0e0;">{text}</span></div>'
                )

        self.chat_view.setHtml(
            '<div style="font-family:Consolas,monospace; font-size:12px; '
            'background:#1e1e1e; color:#d4d4d4; padding:8px;">'
            + "\n".join(html_parts)
            + '</div>'
        )
        # Scroll to bottom
        sb = self.chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_send(self):
        if self._current_uid is None:
            return
        text = self.reply_input.text().strip()
        if not text:
            return
        self.reply_input.clear()
        self.send_requested.emit(self._current_uid, text)
