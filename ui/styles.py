"""QSS stylesheet for the application."""

MAIN_STYLE = """
QMainWindow {
    background-color: #f0f0f0;
}

QTabWidget::pane {
    border: 1px solid #c0c0c0;
    background: white;
}

QTabBar::tab {
    background: #e0e0e0;
    border: 1px solid #c0c0c0;
    padding: 6px 16px;
    margin-right: 2px;
    min-width: 80px;
}

QTabBar::tab:selected {
    background: white;
    border-bottom: none;
    font-weight: bold;
}

QTabBar::tab:hover {
    background: #d0d0d0;
}

QGroupBox {
    font-weight: bold;
    border: 1px solid #c0c0c0;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    padding: 0 6px;
    color: #0066cc;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    border: 1px solid #c0c0c0;
    border-radius: 3px;
    padding: 4px;
    background: white;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #0078d7;
}

QPushButton {
    background-color: #e0e0e0;
    border: 1px solid #a0a0a0;
    border-radius: 3px;
    padding: 6px 16px;
    min-width: 60px;
}

QPushButton:hover {
    background-color: #d0d0d0;
}

QPushButton:pressed {
    background-color: #c0c0c0;
}

QPushButton#startBtn {
    background-color: #28a745;
    color: white;
    font-weight: bold;
    border: none;
    padding: 8px 20px;
}

QPushButton#startBtn:hover {
    background-color: #218838;
}

QPushButton#stopBtn {
    background-color: #dc3545;
    color: white;
    font-weight: bold;
    border: none;
    padding: 8px 20px;
}

QPushButton#stopBtn:hover {
    background-color: #c82333;
}

QPushButton#manualLoginBtn {
    background-color: #007bff;
    color: white;
    font-weight: bold;
    border: none;
    padding: 8px 20px;
}

QPushButton#manualLoginBtn:hover {
    background-color: #0056b3;
}

QPushButton#saveBtn {
    background-color: #17a2b8;
    color: white;
    font-weight: bold;
    border: none;
    padding: 8px 20px;
}

QPushButton#saveBtn:hover {
    background-color: #138496;
}

QLabel#statusLabel {
    font-weight: bold;
    padding: 4px 8px;
}

QLabel#statusRunning {
    color: #28a745;
    font-weight: bold;
}

QLabel#statusStopped {
    color: #dc3545;
    font-weight: bold;
}

QPlainTextEdit#logPanel {
    background-color: #1e1e1e;
    color: #d4d4d4;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #333;
}

QCheckBox {
    spacing: 6px;
}

QScrollArea {
    border: none;
}
"""
