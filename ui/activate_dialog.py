"""License activation dialog shown on startup if not activated."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from license.hwid import get_hwid
from license.activator import activate_key, get_license_info


class ActivateDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle("卡密激活")
        self.setFixedSize(480, 280)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        title = QLabel("Telegram Bot 桌面版 — 卡密激活")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # HWID display
        hwid = get_hwid()
        hwid_row = QHBoxLayout()
        hwid_row.addWidget(QLabel("机器码:"))
        self.hwid_label = QLineEdit(hwid)
        self.hwid_label.setReadOnly(True)
        hwid_row.addWidget(self.hwid_label)
        copy_btn = QPushButton("复制")
        copy_btn.setFixedWidth(50)
        copy_btn.clicked.connect(self._copy_hwid)
        hwid_row.addWidget(copy_btn)
        layout.addLayout(hwid_row)

        # Key input
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("卡密:"))
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("请输入卡密激活码")
        key_row.addWidget(self.key_edit)
        layout.addLayout(key_row)

        # License info
        info = get_license_info(cfg)
        if info.get("activated"):
            self.info_label = QLabel(
                f"✅ 已激活 | 到期: {info.get('expiry', '未知')}"
            )
            self.info_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.info_label = QLabel("❌ 未激活")
            self.info_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.info_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.activate_btn = QPushButton("激活")
        self.activate_btn.setObjectName("startBtn")
        self.activate_btn.clicked.connect(self._activate)
        btn_row.addWidget(self.activate_btn)

        if info.get("activated"):
            enter_btn = QPushButton("进入软件")
            enter_btn.setObjectName("manualLoginBtn")
            enter_btn.clicked.connect(self.accept)
            btn_row.addWidget(enter_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

    def _copy_hwid(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.hwid_label.text())
        QMessageBox.information(self, "复制成功", "机器码已复制到剪贴板")

    def _activate(self):
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "提示", "请输入卡密")
            return

        result = activate_key(key, self._cfg)
        if result["valid"]:
            self.info_label.setText(f"✅ 激活成功!")
            self.info_label.setStyleSheet("color: green; font-weight: bold;")
            QMessageBox.information(self, "激活成功", "卡密激活成功，欢迎使用！")
            self.accept()
        else:
            QMessageBox.warning(self, "激活失败", result.get("message", "激活失败"))
