"""Chat panel for manual reply — shows user conversations and allows sending.

Each conversation is keyed by a composite "uid:token" id so that the same
user talking to different bots shows as separate entries and replies always
route through the correct bot automatically.
"""

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QLineEdit,
    QPushButton, QLabel, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal


class ChatPanel(QWidget):
    """Left: conversation list, Right: message history + reply input."""

    # (uid, text, bot_token) — uid is 'qlonglong' (64-bit) because modern
    # Telegram user IDs can exceed the 32-bit range of a plain `int` signal,
    # which would silently truncate the id and cause "chat not found".
    send_requested = pyqtSignal('qlonglong', str, str)
    photo_send_requested = pyqtSignal('qlonglong', str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: conversation list ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("用户列表"))
        self.user_list = QListWidget()
        self.user_list.currentItemChanged.connect(self._on_user_selected)
        left_layout.addWidget(self.user_list)
        left.setMaximumWidth(250)
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

        self.photo_btn = QPushButton("图片")
        self.photo_btn.setFixedWidth(70)
        self.photo_btn.clicked.connect(self._on_send_photo)
        reply_row.addWidget(self.photo_btn)

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

        self._current_cid: str | None = None
        # cid -> QListWidgetItem
        self._cid_items: dict[str, QListWidgetItem] = {}
        self._store: dict = {}

    def refresh_store(self, chat_store: dict):
        """Update conversation list from the chat store (keyed by cid)."""
        self._store = chat_store
        for cid, data in chat_store.items():
            name = data.get("name", "")
            uid = data.get("uid", cid)
            bot_label = data.get("bot_label", "")
            display = f"{name}  {bot_label}  ({uid})" if bot_label else f"{name}  ({uid})"
            if cid not in self._cid_items:
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, cid)
                self.user_list.addItem(item)
                self._cid_items[cid] = item
            else:
                self._cid_items[cid].setText(display)

        if self._current_cid is not None:
            self._render_messages(chat_store)

    def on_new_message(self, cid: str, chat_store: dict):
        """Called when a new message arrives for a conversation."""
        self.refresh_store(chat_store)
        if cid == self._current_cid:
            self._render_messages(chat_store)

    def _on_user_selected(self, current: QListWidgetItem, _prev):
        if current is None:
            return
        cid = current.data(Qt.ItemDataRole.UserRole)
        self._current_cid = cid
        data = self._store.get(cid) or {}
        bot_label = data.get("bot_label", "")
        header = f"与 {current.text()} 的对话"
        if bot_label:
            header += f"  —  回复经 {bot_label}"
        self.chat_label.setText(header)
        self._request_render()

    def _request_render(self):
        pass

    def render_current(self, chat_store: dict):
        """Render messages for the currently selected conversation."""
        self._store = chat_store
        self._render_messages(chat_store)

    def _render_messages(self, chat_store: dict):
        if self._current_cid is None:
            return
        data = chat_store.get(self._current_cid)
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
        sb = self.chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_send(self):
        if self._current_cid is None:
            return
        text = self.reply_input.text().strip()
        if not text:
            return
        self.reply_input.clear()
        data = self._store.get(self._current_cid) or {}
        uid = data.get("uid")
        token = data.get("bot_token", "")
        if uid is None:
            return
        self.send_requested.emit(uid, text, token)

    def _on_send_photo(self):
        if self._current_cid is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要发送的图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"
        )
        if not path:
            return
        caption = self.reply_input.text().strip()
        self.reply_input.clear()
        data = self._store.get(self._current_cid) or {}
        uid = data.get("uid")
        token = data.get("bot_token", "")
        if uid is None:
            return
        self.photo_send_requested.emit(uid, path, caption, token)
