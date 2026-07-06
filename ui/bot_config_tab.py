"""Bot 设置 Tab — Bot Token, proxy, output dir, health check, etc."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QCheckBox, QSpinBox, QPushButton, QFileDialog, QLabel,
    QScrollArea, QPlainTextEdit,
)
from PyQt6.QtCore import Qt


class BotConfigTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Bot 配置 ──
        grp = QGroupBox("Bot 配置")
        form = QFormLayout()

        # Bot Token
        token_row = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("从 @BotFather 获取的 Bot Token")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_toggle = QPushButton("显示")
        self.token_toggle.setFixedWidth(50)
        self.token_toggle.clicked.connect(self._toggle_token)
        token_row.addWidget(self.token_edit)
        token_row.addWidget(self.token_toggle)
        form.addRow("Bot Token:", token_row)

        # Token File
        file_row = QHBoxLayout()
        self.token_file_edit = QLineEdit()
        self.token_file_edit.setPlaceholderText("可选: Token文件(每行一个Token, 批量运行多个Bot)")
        browse_btn = QPushButton("浏览")
        browse_btn.setFixedWidth(50)
        browse_btn.clicked.connect(self._browse_token_file)
        file_row.addWidget(self.token_file_edit)
        file_row.addWidget(browse_btn)
        form.addRow("Token 文件:", file_row)

        # Bot Name
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("可选: 启动后自动设置 Bot 昵称")
        form.addRow("Bot 昵称:", self.name_edit)

        # Bot Avatar
        avatar_row = QHBoxLayout()
        self.avatar_edit = QLineEdit()
        self.avatar_edit.setPlaceholderText("可选: Bot 头像图片文件路径")
        avatar_btn = QPushButton("选择图片")
        avatar_btn.setFixedWidth(70)
        avatar_btn.clicked.connect(self._browse_avatar)
        avatar_row.addWidget(self.avatar_edit)
        avatar_row.addWidget(avatar_btn)
        form.addRow("Bot 头像:", avatar_row)

        # API ID / Hash
        self.api_id_edit = QLineEdit()
        self.api_id_edit.setPlaceholderText("从 https://my.telegram.org 获取")
        form.addRow("API ID:", self.api_id_edit)

        self.api_hash_edit = QLineEdit()
        self.api_hash_edit.setPlaceholderText("从 https://my.telegram.org 获取")
        form.addRow("API Hash:", self.api_hash_edit)

        # Proxy
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("代理 (可选): IP:端口:用户名:密码 或 socks5://host:port")
        form.addRow("代理设置:", self.proxy_edit)

        # Proxy pool (per-account round-robin, 防关联)
        self.proxy_list_edit = QPlainTextEdit()
        self.proxy_list_edit.setPlaceholderText(
            "代理池 (可选，每行一个)：登录时按账号轮流分配，避免多个号共用同一 IP 被判关联。\n"
            "格式同上，如 IP:端口:用户名:密码。留空则使用上面的单个代理。")
        self.proxy_list_edit.setMaximumHeight(120)
        form.addRow("代理池(每行一个):", self.proxy_list_edit)

        # Output Dir
        dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setText("accounts")
        dir_btn = QPushButton("浏览")
        dir_btn.setFixedWidth(50)
        dir_btn.clicked.connect(self._browse_output_dir)
        dir_row.addWidget(self.output_dir_edit)
        dir_row.addWidget(dir_btn)
        form.addRow("输出目录:", dir_row)

        # Device Model
        self.device_edit = QLineEdit()
        self.device_edit.setPlaceholderText("登录后显示的设备名")
        form.addRow("设备名称:", self.device_edit)

        grp.setLayout(form)
        layout.addWidget(grp)

        # ── 健康检测 ──
        health_grp = QGroupBox("健康检测")
        health_layout = QHBoxLayout()
        self.health_check = QCheckBox("启用 Bot 健康检查（检测到被封停自动切号）")
        health_layout.addWidget(self.health_check)
        health_layout.addWidget(QLabel("检测间隔(秒):"))
        self.health_interval = QSpinBox()
        self.health_interval.setRange(30, 3600)
        self.health_interval.setValue(180)
        health_layout.addWidget(self.health_interval)
        health_layout.addStretch()
        health_grp.setLayout(health_layout)
        layout.addWidget(health_grp)

        # Save button
        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setObjectName("saveBtn")
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _toggle_token(self):
        if self.token_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.token_toggle.setText("隐藏")
        else:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_toggle.setText("显示")

    def _browse_token_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Token 文件", "", "Text files (*.txt);;All (*)")
        if path:
            self.token_file_edit.setText(path)

    def _browse_avatar(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择头像图片", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.avatar_edit.setText(path)

    def _browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_dir_edit.setText(path)

    def load_config(self, cfg):
        self.token_edit.setText(cfg.get("bot_token", ""))
        self.token_file_edit.setText(cfg.get("token_file", ""))
        self.name_edit.setText(cfg.get("bot_name", ""))
        self.avatar_edit.setText(cfg.get("bot_avatar", ""))
        self.api_id_edit.setText(str(cfg.get("api_id", 0) or ""))
        self.api_hash_edit.setText(cfg.get("api_hash", ""))
        self.proxy_edit.setText(cfg.get("proxy", ""))
        self.proxy_list_edit.setPlainText(cfg.get("proxy_list", ""))
        self.output_dir_edit.setText(cfg.get("output_dir", "accounts"))
        self.device_edit.setText(cfg.get("login_device_model", ""))
        self.health_check.setChecked(cfg.get("health_check_enabled", False))
        self.health_interval.setValue(cfg.get("health_check_interval", 180))

    def save_config(self, cfg):
        api_id_str = self.api_id_edit.text().strip()
        try:
            api_id = int(api_id_str) if api_id_str else 0
        except ValueError:
            api_id = 0

        cfg.update({
            "bot_token": self.token_edit.text().strip(),
            "token_file": self.token_file_edit.text().strip(),
            "bot_name": self.name_edit.text().strip(),
            "bot_avatar": self.avatar_edit.text().strip(),
            "api_id": api_id,
            "api_hash": self.api_hash_edit.text().strip(),
            "proxy": self.proxy_edit.text().strip(),
            "proxy_list": self.proxy_list_edit.toPlainText().strip(),
            "output_dir": self.output_dir_edit.text().strip(),
            "login_device_model": self.device_edit.text().strip(),
            "health_check_enabled": self.health_check.isChecked(),
            "health_check_interval": self.health_interval.value(),
        })
        cfg.save()
