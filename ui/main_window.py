"""Main application window with tabs, control buttons, and log panel."""

import asyncio
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QPlainTextEdit, QMessageBox, QSplitter,
    QLineEdit, QDialog, QFormLayout, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QTextCursor

from core.config_manager import config
from core.bot_runner import bot_runner
from ui.bot_config_tab import BotConfigTab
from ui.reply_config_tab import ReplyConfigTab
from ui.success_config_tab import SuccessConfigTab
from ui.chat_panel import ChatPanel


# ── Log handler that emits to Qt ──────────────────────────────────
class QtLogHandler(logging.Handler, QObject):
    log_signal = pyqtSignal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        msg = self.format(record)
        self.log_signal.emit(msg)


class ManualLoginDialog(QDialog):
    """Simple dialog for manual phone login via the GUI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动登录 TG")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("+86130008800")
        form.addRow("手机号:", self.phone_edit)
        layout.addLayout(form)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Buttons
        btn_row = QHBoxLayout()
        self.send_code_btn = QPushButton("下一步/发送验证码")
        self.send_code_btn.setObjectName("manualLoginBtn")
        self.send_code_btn.clicked.connect(self._on_send_code)
        btn_row.addWidget(self.send_code_btn)
        layout.addLayout(btn_row)

        # Code input (hidden initially)
        self.code_form = QFormLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("输入验证码")
        self.code_form.addRow("验证码:", self.code_edit)
        layout.addLayout(self.code_form)

        self.verify_btn = QPushButton("验证")
        self.verify_btn.setObjectName("startBtn")
        self.verify_btn.clicked.connect(self._on_verify)
        self.verify_btn.hide()
        layout.addWidget(self.verify_btn)

        # 2FA input
        self.tfa_edit = QLineEdit()
        self.tfa_edit.setPlaceholderText("输入两步验证密码")
        self.tfa_edit.hide()
        layout.addWidget(self.tfa_edit)

        self.tfa_btn = QPushButton("提交密码")
        self.tfa_btn.setObjectName("startBtn")
        self.tfa_btn.clicked.connect(self._on_2fa)
        self.tfa_btn.hide()
        layout.addWidget(self.tfa_btn)

        # Log
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setObjectName("logPanel")
        layout.addWidget(self.log_text)

        self._phone = ""

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{ts}] {msg}")

    def _on_send_code(self):
        phone = self.phone_edit.text().strip()
        if not phone:
            self.status_label.setText("请输入手机号")
            return
        if not phone.startswith("+"):
            phone = "+" + phone
        self._phone = phone
        self.send_code_btn.setEnabled(False)
        self.status_label.setText("正在发送验证码...")
        self._log(f"手动登录UI 手机号输入框自动填充: {phone}")
        self._log("已点击「下一步/发送验证码」")

        loop = asyncio.get_event_loop()
        from core.login_engine import login_engine
        task = loop.create_task(login_engine.send_code(phone))
        task.add_done_callback(self._send_code_done)

    def _send_code_done(self, future):
        try:
            result = future.result()
        except Exception as e:
            result = {"success": False, "message": str(e)}

        self.send_code_btn.setEnabled(True)
        if result.get("success"):
            self.status_label.setText("✅ 验证码已发送，请输入验证码")
            self.status_label.setStyleSheet("color: green;")
            self.code_edit.show()
            self.verify_btn.show()
            self._log(f"手动登录流程已启动, 正在请求验证码...")
            self._log(f"会话ID: {id(self)}")
        else:
            err_msg = result.get('message', '发送验证码失败')
            self.status_label.setText(f"❌ {err_msg}")
            self.status_label.setStyleSheet("color: red;")
            self._log(f"发送验证码失败: {err_msg}")

    def _on_verify(self):
        code = self.code_edit.text().strip()
        if not code:
            return
        self.verify_btn.setEnabled(False)
        self.status_label.setText("正在验证...")

        loop = asyncio.get_event_loop()
        from core.login_engine import login_engine
        task = loop.create_task(login_engine.sign_in_with_code(self._phone, code))
        task.add_done_callback(self._verify_done)

    def _verify_done(self, future):
        try:
            result = future.result()
        except Exception as e:
            result = {"success": False, "message": str(e)}

        self.verify_btn.setEnabled(True)
        if result.get("success"):
            info = result.get("user_info", {})
            self.status_label.setText(f"✅ 登录成功: {info.get('first_name', '')} @{info.get('username', '')}")
            self.status_label.setStyleSheet("color: green;")
            self._log(f"✅ 登录成功: {info}")
        elif result.get("need_2fa"):
            self.status_label.setText("🔐 需要两步验证密码")
            self.tfa_edit.show()
            self.tfa_btn.show()
            self._log("需要两步验证密码")
        else:
            err_msg = result.get('message', '验证失败')
            self.status_label.setText(f"❌ {err_msg}")
            self.status_label.setStyleSheet("color: red;")
            self._log(f"验证失败: {err_msg}")

    def _on_2fa(self):
        pwd = self.tfa_edit.text().strip()
        if not pwd:
            return
        self.tfa_btn.setEnabled(False)

        loop = asyncio.get_event_loop()
        from core.login_engine import login_engine
        task = loop.create_task(login_engine.sign_in_with_2fa(self._phone, pwd))
        task.add_done_callback(self._tfa_done)

    def _tfa_done(self, future):
        try:
            result = future.result()
        except Exception as e:
            result = {"success": False, "message": str(e)}

        self.tfa_btn.setEnabled(True)
        if result.get("success"):
            info = result.get("user_info", {})
            self.status_label.setText(f"✅ 登录成功: {info.get('first_name', '')}")
            self.status_label.setStyleSheet("color: green;")
            self._log(f"✅ 2FA 登录成功: {info}")
        else:
            err_msg = result.get('message', '密码错误')
            self.status_label.setText(f"❌ {err_msg}")
            self.status_label.setStyleSheet("color: red;")


# ── Main Window ───────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram Bot - 手动登录交互版")
        self.setMinimumSize(700, 500)
        self.resize(950, 750)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ── Tabs ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.tabs = QTabWidget()
        self.bot_tab = BotConfigTab()
        self.reply_tab = ReplyConfigTab()
        self.success_tab = SuccessConfigTab()
        self.tabs.addTab(self.bot_tab, "Bot 设置")
        self.tabs.addTab(self.reply_tab, "回复配置")
        self.tabs.addTab(self.success_tab, "登录成功")
        splitter.addWidget(self.tabs)

        # ── Control bar ──
        ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("启动 Bot")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止 Bot")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)

        self.manual_btn = QPushButton("手动登录 TG")
        self.manual_btn.setObjectName("manualLoginBtn")
        self.manual_btn.clicked.connect(self._on_manual_login)
        btn_row.addWidget(self.manual_btn)

        self.status_label = QLabel("状态: 已停止")
        self.status_label.setObjectName("statusStopped")
        btn_row.addWidget(self.status_label)

        self.bot_name_label = QLabel("")
        btn_row.addWidget(self.bot_name_label)

        btn_row.addStretch()

        self.copy_username_btn = QPushButton("复制用户名")
        self.copy_username_btn.clicked.connect(self._copy_username)
        self.copy_username_btn.hide()
        btn_row.addWidget(self.copy_username_btn)

        ctrl_layout.addLayout(btn_row)

        # ── Log panel ──
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("实时日志"))
        log_header.addStretch()

        self.show_chat_btn = QPushButton("显示手动回复/聊天")
        self.show_chat_btn.clicked.connect(self._toggle_chat_panel)
        log_header.addWidget(self.show_chat_btn)

        self.clear_log_btn = QPushButton("清理日志")
        self.clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(self.clear_log_btn)
        ctrl_layout.addLayout(log_header)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setObjectName("logPanel")
        self.log_panel.setMinimumHeight(150)
        ctrl_layout.addWidget(self.log_panel)

        # ── Chat panel (hidden by default) ──
        self.chat_panel = ChatPanel()
        self.chat_panel.hide()
        self.chat_panel.send_requested.connect(self._on_chat_send)
        ctrl_layout.addWidget(self.chat_panel)

        splitter.addWidget(ctrl_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

        # ── Status bar ──
        self.statusBar().showMessage(
            "使用说明: 1. 从 @BotFather 获取 Bot Token  2. 输入 Token 并启动  "
            "3. 用户发送手机号后进入手动登录流程  4. Bot 提示用户输入回执码  "
            "5. 用户回复回执码 Bot 自动换文  6. 如有二级密码同样按提示输入  7. 生成 tdata"
        )

        # ── Connect save buttons ──
        self.bot_tab.save_btn.clicked.connect(self._save_all)
        self.reply_tab.save_btn.clicked.connect(self._save_all)
        self.success_tab.save_btn.clicked.connect(self._save_all)

        # ── Setup logging ──
        self._log_handler = QtLogHandler()
        self._log_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
        self._log_handler.log_signal.connect(self._append_log)
        logging.root.addHandler(self._log_handler)
        logging.root.setLevel(logging.INFO)

        # Register chat callback so panel updates in real time
        bot_runner.set_chat_callback(self._on_chat_new_message)

        # User list click -> re-render
        self.chat_panel.user_list.currentItemChanged.connect(
            lambda *_: self.chat_panel.render_current(bot_runner.get_chat_store()))

        # Load config into tabs
        self._load_all()

    def _load_all(self):
        self.bot_tab.load_config(config)
        self.reply_tab.load_config(config)
        self.success_tab.load_config(config)

    def _save_all(self):
        self.bot_tab.save_config(config)
        self.reply_tab.save_config(config)
        self.success_tab.save_config(config)
        self._append_log("✅ 配置已保存")

    def _append_log(self, text: str):
        self.log_panel.appendPlainText(text)
        self.log_panel.moveCursor(QTextCursor.MoveOperation.End)

    def _clear_log(self):
        self.log_panel.clear()

    def _toggle_chat_panel(self):
        if self.chat_panel.isVisible():
            self.chat_panel.hide()
            self.show_chat_btn.setText("显示手动回复/聊天")
        else:
            self.chat_panel.show()
            self.show_chat_btn.setText("隐藏手动回复/聊天")
            store = bot_runner.get_chat_store()
            self.chat_panel.refresh_store(store)

    def _on_chat_new_message(self, cid: str):
        """Called from handlers when a new message arrives for a conversation."""
        if self.chat_panel.isVisible():
            store = bot_runner.get_chat_store()
            self.chat_panel.on_new_message(cid, store)

    def _on_chat_send(self, uid: int, text: str, bot_token: str = ""):
        """Send a manual reply via the conversation's own bot.

        The chat panel emits the bot_token that belongs to the selected
        conversation, so we send through that bot first. If Telegram reports it
        has no chat with this user ("chat not found"), we self-heal by trying the
        other running bots and remember whichever actually reaches the user.
        """
        bot = bot_runner.get_bot_by_token(bot_token) if bot_token else None
        if bot is None:
            self._append_log(f"❌ 该会话的 Bot（{bot_token[:8]}…）未运行，无法发送消息")
            return

        loop = asyncio.get_event_loop()
        loop.create_task(self._do_send_chat(bot, uid, text))

    @staticmethod
    async def _try_send(bot, uid: int, text: str):
        """Send text via one bot, retrying without HTML if entity parsing fails."""
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
        except Exception as html_err:
            if "parse" in str(html_err).lower() or "entit" in str(html_err).lower():
                await bot.send_message(chat_id=uid, text=text, parse_mode=None)
            else:
                raise

    def _bot_label(self, bot) -> str:
        label = getattr(getattr(bot, "_me", None), "username", None)
        return f"@{label}" if label else bot.token[:8]

    async def _do_send_chat(self, bot, uid: int, text: str):
        conv_token = bot.token
        # Deterministic first: the conversation's own bot. Only if it returns
        # "chat not found" (that bot genuinely has no chat with this user) do we
        # fall back to the other running bots — a safety net, not the primary
        # router — and re-point the conversation to whichever one works.
        candidates = [bot]
        for _label, token in bot_runner.running_bots():
            if token != conv_token:
                other = bot_runner.get_bot_by_token(token)
                if other is not None:
                    candidates.append(other)

        last_err = None
        for cand in candidates:
            try:
                await self._try_send(cand, uid, text)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "chat not found" in msg or "chat_id is empty" in msg or "peer" in msg:
                    continue  # this bot can't reach the user — try the next
                self._append_log(f"❌ 发送失败 [{uid}]: {e}")
                return

            self._append_log(f"✉ 手动回复 [{uid}]: {text}")
            if cand.token != conv_token:
                self._append_log(
                    f"ℹ 原会话记录的 Bot 无法送达 [{uid}]，已自动改用 {self._bot_label(cand)} 成功发送，"
                    f"后续该用户的回复将默认走它")
            from bot.handlers import record_bot_reply, get_chat_store
            cid = record_bot_reply(uid, conv_token, cand, text)
            store = get_chat_store()
            self.chat_panel.on_new_message(cid, store)
            return

        self._append_log(
            f"❌ 发送失败 [{uid}]: 所有在运行的 Bot 都无法向该用户发送（{last_err}）")

    def _on_start(self):
        self._save_all()
        self.start_btn.setEnabled(False)
        self.status_label.setText("状态: 启动中...")
        self.status_label.setObjectName("statusLabel")

        loop = asyncio.get_event_loop()
        task = loop.create_task(bot_runner.start(config))
        task.add_done_callback(self._start_done)

    def _start_done(self, future):
        try:
            err = future.result()
        except Exception as e:
            err = str(e)

        self.start_btn.setEnabled(True)
        if err:
            self.status_label.setText("状态: 已停止")
            self.status_label.setObjectName("statusStopped")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            QMessageBox.warning(self, "启动失败", err)
        else:
            count = bot_runner.running_count
            if count > 1:
                self.status_label.setText(f"状态: 运行中 ({count} 个 Bot)")
                labels = ", ".join(bot_runner.bot_labels)
                self.bot_name_label.setText(f"当前: {labels}")
            else:
                self.status_label.setText("状态: 运行中")
                username = bot_runner.bot_username
                if username:
                    self.bot_name_label.setText(f"当前 Bot: {username}")
            self.status_label.setObjectName("statusRunning")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.copy_username_btn.show()

    def _on_stop(self):
        self.stop_btn.setEnabled(False)
        self.status_label.setText("状态: 停止中...")

        loop = asyncio.get_event_loop()
        task = loop.create_task(bot_runner.stop())
        task.add_done_callback(self._stop_done)

    def _stop_done(self, future):
        self.stop_btn.setEnabled(True)
        self.status_label.setText("状态: 已停止")
        self.status_label.setObjectName("statusStopped")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.bot_name_label.setText("")
        self.copy_username_btn.hide()

    def _on_manual_login(self):
        # Ensure login engine is configured
        from core.login_engine import login_engine, parse_proxy_list
        if not login_engine._api_id or not login_engine._api_hash:
            # Configure from current config
            import os
            api_id = int(config.get("api_id", 0))
            api_hash = config.get("api_hash", "")
            proxy = config.get("proxy", "")
            proxy_list = parse_proxy_list(config.get("proxy_list", ""))
            sessions_dir = config.get("output_dir", "accounts")
            if not os.path.isabs(sessions_dir):
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sessions_dir = os.path.join(base, sessions_dir)
            device_model = config.get("login_device_model", "Desktop Client")
            login_engine.configure(sessions_dir, proxy, device_model, api_id, api_hash,
                                   proxy_list=proxy_list)
        # Ensure remote server is configured
        if not login_engine._server_url:
            from license.activator import LICENSE_SERVER_URL
            from license.hwid import get_hwid
            license_key = config.get("license_key", "")
            login_engine.set_server(LICENSE_SERVER_URL, license_key, get_hwid())

        dlg = ManualLoginDialog(self)
        dlg.exec()

    def _copy_username(self):
        from PyQt6.QtWidgets import QApplication
        username = bot_runner.bot_username
        if username:
            QApplication.clipboard().setText(username)

    def closeEvent(self, event):
        if bot_runner.is_running:
            reply = QMessageBox.question(
                self, "确认退出",
                "Bot 正在运行中，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

            loop = asyncio.get_event_loop()
            loop.create_task(bot_runner.stop())

        logging.root.removeHandler(self._log_handler)
        event.accept()
