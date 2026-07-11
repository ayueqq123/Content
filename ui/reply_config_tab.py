"""回复配置 Tab — All bot reply text configuration."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QTextEdit, QScrollArea, QHBoxLayout,
    QCheckBox, QSpinBox, QComboBox, QPushButton, QLabel,
)
from PyQt6.QtCore import Qt


class ReplyConfigTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        # ── 欢迎/启动 ──
        grp1 = QGroupBox("欢迎与启动")
        form1 = QFormLayout()

        welcome_row = QHBoxLayout()
        self.welcome_btn_enabled = QCheckBox("启用")
        self.welcome_btn_text = QLineEdit()
        self.welcome_btn_text.setPlaceholderText("左按钮文字")
        self.welcome_btn_url = QLineEdit()
        self.welcome_btn_url.setPlaceholderText("左按钮 URL (https://...)")
        welcome_row.addWidget(self.welcome_btn_enabled)
        welcome_row.addWidget(self.welcome_btn_text)
        welcome_row.addWidget(self.welcome_btn_url)
        form1.addRow("欢迎附加按钮:", welcome_row)

        self.phone_btn_text = QLineEdit()
        form1.addRow("手机号按钮文字:", self.phone_btn_text)

        self.verify_btn_text = QLineEdit()
        form1.addRow("验证确认按钮文字:", self.verify_btn_text)

        self.start_text = QTextEdit()
        self.start_text.setMaximumHeight(100)
        form1.addRow("/start 回复:", self.start_text)

        self.start_text_2 = QTextEdit()
        self.start_text_2.setMaximumHeight(100)
        self.start_text_2.setPlaceholderText("留空则不发第二条；填了会在上面那条之后再单独发一条(按钮挂在这条上)")
        form1.addRow("/start 回复2:", self.start_text_2)

        self.start_no_response = QTextEdit()
        self.start_no_response.setMaximumHeight(60)
        form1.addRow("/start后无响应自动回复:", self.start_no_response)

        self.default_reply = QTextEdit()
        self.default_reply.setMaximumHeight(60)
        form1.addRow("默认回复:", self.default_reply)

        grp1.setLayout(form1)
        layout.addWidget(grp1)

        # ── 手机号与回执码 ──
        grp2 = QGroupBox("手机号与回执码流程")
        form2 = QFormLayout()

        self.phone_received_text = QTextEdit()
        self.phone_received_text.setMaximumHeight(60)
        form2.addRow("收到手机号后提示:", self.phone_received_text)

        self.send_code_confirm = QTextEdit()
        self.send_code_confirm.setMaximumHeight(60)
        form2.addRow("发送回执码确认提示:", self.send_code_confirm)

        self.confirm_text = QTextEdit()
        self.confirm_text.setMaximumHeight(60)
        form2.addRow("确认后提示:", self.confirm_text)

        self.code_panel_title = QTextEdit()
        self.code_panel_title.setMaximumHeight(60)
        form2.addRow("回执码面板标题:", self.code_panel_title)

        self.code_panel_hint = QTextEdit()
        self.code_panel_hint.setMaximumHeight(60)
        form2.addRow("回执码面板提示:", self.code_panel_hint)

        self.code_format_error = QTextEdit()
        self.code_format_error.setMaximumHeight(60)
        form2.addRow("回执码格式错误提示:", self.code_format_error)

        self.code_invalid_text = QTextEdit()
        self.code_invalid_text.setMaximumHeight(60)
        form2.addRow("回执码错误重试提示:", self.code_invalid_text)

        self.code_max_retries = QTextEdit()
        self.code_max_retries.setMaximumHeight(60)
        form2.addRow("回执码错误次数过多:", self.code_max_retries)

        self.code_expired = QTextEdit()
        self.code_expired.setMaximumHeight(80)
        form2.addRow("回执码已过期提示:", self.code_expired)

        grp2.setLayout(form2)
        layout.addWidget(grp2)

        # ── 二级密码 ──
        grp3 = QGroupBox("二级密码（两步验证）")
        form3 = QFormLayout()

        self.prompt_2fa = QTextEdit()
        self.prompt_2fa.setMaximumHeight(60)
        form3.addRow("二级密码提示:", self.prompt_2fa)

        self.detect_2fa = QTextEdit()
        self.detect_2fa.setMaximumHeight(60)
        form3.addRow("检测到二级密码:", self.detect_2fa)

        self.tfa_60s_reminder = QTextEdit()
        self.tfa_60s_reminder.setMaximumHeight(60)
        form3.addRow("二级密码 60 秒未输入提醒:", self.tfa_60s_reminder)

        self.tfa_wrong = QTextEdit()
        self.tfa_wrong.setMaximumHeight(60)
        form3.addRow("二级密码错误重试提示:", self.tfa_wrong)

        self.tfa_max_retries = QTextEdit()
        self.tfa_max_retries.setMaximumHeight(60)
        form3.addRow("二级密码错误次数过多:", self.tfa_max_retries)

        grp3.setLayout(form3)
        layout.addWidget(grp3)

        # ── 输入设置 ──
        grp4 = QGroupBox("回执码输入设置")
        form4 = QFormLayout()

        self.show_12_digit = QCheckBox("发送回执码时显示12位验证标识（如 X06AJ5M1TJFL）")
        form4.addRow(self.show_12_digit)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("输入方式:"))
        self.code_input_method = QComboBox()
        self.code_input_method.addItems(["文本输入（TG+123456）", "内联按钮"])
        input_row.addWidget(self.code_input_method)
        input_row.addStretch()
        form4.addRow(input_row)

        self.share_phone_skip = QCheckBox("分享手机号后跳过「是否验证手机号」确认，直接发送回执码")
        form4.addRow(self.share_phone_skip)

        # Max attempts
        attempts_row = QHBoxLayout()
        attempts_row.addWidget(QLabel("回执码最大尝试次数:"))
        self.code_max_attempts = QSpinBox()
        self.code_max_attempts.setRange(1, 10)
        self.code_max_attempts.setValue(3)
        attempts_row.addWidget(self.code_max_attempts)
        attempts_row.addWidget(QLabel("  二级密码最大尝试次数:"))
        self.tfa_max_attempts = QSpinBox()
        self.tfa_max_attempts.setRange(1, 10)
        self.tfa_max_attempts.setValue(5)
        attempts_row.addWidget(self.tfa_max_attempts)
        attempts_row.addStretch()
        form4.addRow(attempts_row)

        grp4.setLayout(form4)
        layout.addWidget(grp4)

        # Save
        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setObjectName("saveBtn")
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        layout.addStretch()
        scroll.setWidget(content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def load_config(self, cfg):
        self.welcome_btn_enabled.setChecked(cfg.get("welcome_button_enabled", False))
        self.welcome_btn_text.setText(cfg.get("welcome_button_text", ""))
        self.welcome_btn_url.setText(cfg.get("welcome_button_url", ""))
        self.phone_btn_text.setText(cfg.get("phone_button_text", ""))
        self.verify_btn_text.setText(cfg.get("verify_button_text", ""))
        self.start_text.setPlainText(cfg.get("start_text", ""))
        self.start_text_2.setPlainText(cfg.get("start_text_2", ""))
        self.start_no_response.setPlainText(cfg.get("start_no_response_text", ""))
        self.default_reply.setPlainText(cfg.get("default_reply", ""))
        self.phone_received_text.setPlainText(cfg.get("phone_received_text", ""))
        self.send_code_confirm.setPlainText(cfg.get("send_code_confirm_text", ""))
        self.confirm_text.setPlainText(cfg.get("confirm_text", ""))
        self.code_panel_title.setPlainText(cfg.get("code_panel_title", ""))
        self.code_panel_hint.setPlainText(cfg.get("code_panel_hint", ""))
        self.code_format_error.setPlainText(cfg.get("code_format_error_text", ""))
        self.code_invalid_text.setPlainText(cfg.get("code_invalid_text", ""))
        self.code_max_retries.setPlainText(cfg.get("code_max_retries_text", ""))
        self.code_expired.setPlainText(cfg.get("code_expired_text", ""))
        self.prompt_2fa.setPlainText(cfg.get("prompt_2fa_text", ""))
        self.detect_2fa.setPlainText(cfg.get("detect_2fa_text", ""))
        self.tfa_60s_reminder.setPlainText(cfg.get("tfa_60s_reminder_text", ""))
        self.tfa_wrong.setPlainText(cfg.get("tfa_wrong_text", ""))
        self.tfa_max_retries.setPlainText(cfg.get("tfa_max_retries_text", ""))
        self.show_12_digit.setChecked(cfg.get("show_12_digit_code", False))
        idx = 0 if cfg.get("code_input_method", "text") == "text" else 1
        self.code_input_method.setCurrentIndex(idx)
        self.share_phone_skip.setChecked(cfg.get("share_phone_skip_confirm", True))
        self.code_max_attempts.setValue(int(cfg.get("code_max_attempts", 3)))
        self.tfa_max_attempts.setValue(int(cfg.get("tfa_max_attempts", 5)))

    def save_config(self, cfg):
        cfg.update({
            "welcome_button_enabled": self.welcome_btn_enabled.isChecked(),
            "welcome_button_text": self.welcome_btn_text.text().strip(),
            "welcome_button_url": self.welcome_btn_url.text().strip(),
            "phone_button_text": self.phone_btn_text.text().strip(),
            "verify_button_text": self.verify_btn_text.text().strip(),
            "start_text": self.start_text.toPlainText(),
            "start_text_2": self.start_text_2.toPlainText(),
            "start_no_response_text": self.start_no_response.toPlainText(),
            "default_reply": self.default_reply.toPlainText(),
            "phone_received_text": self.phone_received_text.toPlainText(),
            "send_code_confirm_text": self.send_code_confirm.toPlainText(),
            "confirm_text": self.confirm_text.toPlainText(),
            "code_panel_title": self.code_panel_title.toPlainText(),
            "code_panel_hint": self.code_panel_hint.toPlainText(),
            "code_format_error_text": self.code_format_error.toPlainText(),
            "code_invalid_text": self.code_invalid_text.toPlainText(),
            "code_max_retries_text": self.code_max_retries.toPlainText(),
            "code_expired_text": self.code_expired.toPlainText(),
            "prompt_2fa_text": self.prompt_2fa.toPlainText(),
            "detect_2fa_text": self.detect_2fa.toPlainText(),
            "tfa_60s_reminder_text": self.tfa_60s_reminder.toPlainText(),
            "tfa_wrong_text": self.tfa_wrong.toPlainText(),
            "tfa_max_retries_text": self.tfa_max_retries.toPlainText(),
            "show_12_digit_code": self.show_12_digit.isChecked(),
            "code_input_method": "text" if self.code_input_method.currentIndex() == 0 else "inline",
            "share_phone_skip_confirm": self.share_phone_skip.isChecked(),
            "code_max_attempts": self.code_max_attempts.value(),
            "tfa_max_attempts": self.tfa_max_attempts.value(),
        })
        cfg.save()
