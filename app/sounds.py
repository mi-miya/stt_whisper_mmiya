import winsound
import threading
from .settings import current_settings
from .logger import logger

def beep_start():
    if not current_settings.sound_enabled:
        return
    try:
        # High pitch short beep
        winsound.Beep(1000, 200)
    except Exception as e:
        logger.error(f"Failed to play start sound: {e}")

def beep_finish():
    if not current_settings.sound_enabled:
        return
    try:
        # Success chime (Ascending)
        winsound.Beep(1000, 100)
        winsound.Beep(1500, 100)
    except Exception as e:
        logger.error(f"Failed to play finish sound: {e}")

def play_start():
    threading.Thread(target=beep_start, daemon=True).start()

def play_finish():
    threading.Thread(target=beep_finish, daemon=True).start()
