import pyperclip
import time
from pynput.keyboard import Key, Controller
from .logger import logger
from .settings import current_settings

keyboard = Controller()

def set_text(text: str):
    """Copies text to clipboard."""
    if not text:
        return

    try:
        pyperclip.copy(text)
        logger.info("Text copied to clipboard")
    except Exception as e:
        logger.error(f"Failed to copy to clipboard: {e}")

def paste_text():
    """Simulates Ctrl+V to paste."""
    if not current_settings.auto_paste:
        return

    try:
        # Small delay to ensure clipboard is ready or target window is focused
        time.sleep(0.1)

        with keyboard.pressed(Key.ctrl):
            keyboard.press('v')
            keyboard.release('v')
        logger.info("Sent paste command (Ctrl+V)")
    except Exception as e:
        logger.error(f"Failed to paste text: {e}")
