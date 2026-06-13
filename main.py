"""Entry point for the Telegram Bot Desktop Application."""

import os
import sys

# Ensure the project root is on sys.path (needed for embedded Python / .pth builds)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio
import logging

from PyQt6.QtWidgets import QApplication

from core.config_manager import config
from license.activator import is_activated

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    app = QApplication(sys.argv)

    # Apply stylesheet
    from ui.styles import MAIN_STYLE
    app.setStyleSheet(MAIN_STYLE)

    # License check
    if not is_activated(config):
        from ui.activate_dialog import ActivateDialog
        dlg = ActivateDialog(config)
        if dlg.exec() == 0:  # Rejected / closed
            sys.exit(0)

    # Setup qasync event loop
    try:
        import qasync
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
    except ImportError:
        logger.warning("qasync not installed, using basic asyncio integration")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Show main window
    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    # Run the Qt + asyncio event loop
    try:
        if hasattr(loop, 'run_forever'):
            with loop:
                loop.run_forever()
        else:
            loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Cleanup
        try:
            from core.bot_runner import bot_runner
            if bot_runner.is_running:
                loop.run_until_complete(bot_runner.stop())
        except Exception:
            pass


if __name__ == "__main__":
    main()
