import asyncio

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog
from aiogram.types import FSInputFile

from ui.chat_panel import ChatPanel
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_chat_panel_sends_selected_photo_with_caption(qapp, monkeypatch):
    panel = ChatPanel()
    panel.refresh_store({
        "1:tok": {
            "uid": 123456789012,
            "bot_token": "tok",
            "bot_label": "@b",
            "name": "n",
            "messages": [],
        }
    })
    panel.user_list.setCurrentRow(0)
    panel.reply_input.setText("hi")
    emitted = []
    panel.photo_send_requested.connect(lambda *args: emitted.append(args))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: ("/tmp/x.png", "")
    )

    panel.photo_btn.click()

    assert emitted == [(123456789012, "/tmp/x.png", "hi", "tok")]
    assert panel.reply_input.text() == ""


def test_chat_panel_cancelled_photo_dialog_emits_nothing(qapp, monkeypatch):
    panel = ChatPanel()
    panel.refresh_store({
        "1:tok": {
            "uid": 123456789012,
            "bot_token": "tok",
            "bot_label": "@b",
            "name": "n",
            "messages": [],
        }
    })
    panel.user_list.setCurrentRow(0)
    emitted = []
    panel.photo_send_requested.connect(lambda *args: emitted.append(args))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))

    panel.photo_btn.click()

    assert emitted == []


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_photo(self, **kwargs):
        self.calls.append(("photo", kwargs))

    async def send_message(self, **kwargs):
        self.calls.append(("message", kwargs))


def test_try_send_photo_uses_file_and_caption():
    bot = FakeBot()

    asyncio.run(MainWindow._try_send(bot, 123, "caption", photo_path="/tmp/x.png"))

    assert len(bot.calls) == 1
    kind, kwargs = bot.calls[0]
    assert kind == "photo"
    assert kwargs["chat_id"] == 123
    assert isinstance(kwargs["photo"], FSInputFile)
    assert kwargs["photo"].path == "/tmp/x.png"
    assert kwargs["caption"] == "caption"
    assert kwargs["parse_mode"] == "HTML"


def test_try_send_photo_uses_none_for_empty_caption():
    bot = FakeBot()

    asyncio.run(MainWindow._try_send(bot, 123, "", photo_path="/tmp/x.png"))

    assert bot.calls[0][0] == "photo"
    assert bot.calls[0][1]["caption"] is None


def test_try_send_without_photo_uses_send_message():
    bot = FakeBot()

    asyncio.run(MainWindow._try_send(bot, 123, "text"))

    assert bot.calls == [
        ("message", {"chat_id": 123, "text": "text", "parse_mode": "HTML"})
    ]
