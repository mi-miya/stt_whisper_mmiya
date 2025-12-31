import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable
from .logger import logger

user32 = ctypes.windll.user32

# Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

# Map for modifiers
MODIFIERS = {
    '<ctrl>': MOD_CONTROL,
    '<alt>': MOD_ALT,
    '<shift>': MOD_SHIFT,
    '<win>': MOD_WIN,
    'ctrl': MOD_CONTROL,
    'alt': MOD_ALT,
    'shift': MOD_SHIFT,
    'win': MOD_WIN
}

class HotkeyListener(threading.Thread):
    def __init__(self, hotkey_str: str, on_trigger: Callable):
        super().__init__(daemon=True)
        self.hotkey_str = hotkey_str.lower()
        self.on_trigger = on_trigger
        self.running = False
        self.hotkey_id = 1

    def parse_hotkey(self):
        parts = self.hotkey_str.split('+')
        fs_modifiers = 0
        vk = 0

        for part in parts:
            part = part.strip()
            if part in MODIFIERS:
                fs_modifiers |= MODIFIERS[part]
            else:
                # Assume character key
                # Remove <> if present? Logic assumes single char for key usually
                clean_part = part.replace('<', '').replace('>', '')
                if len(clean_part) == 1:
                    vk = ord(clean_part.upper())
                else:
                    # Handle F-keys or others if needed, for now basic support
                    if clean_part.startswith('f'):
                        try:
                            f_num = int(clean_part[1:])
                            vk = 0x70 + (f_num - 1) # VK_F1 is 0x70
                        except:
                            pass

        return fs_modifiers, vk

    def run(self):
        self.running = True
        fs_modifiers, vk = self.parse_hotkey()

        logger.info(f"Registering hotkey: {self.hotkey_str} (Mods: {fs_modifiers}, VK: {vk})")

        if vk == 0:
            logger.error(f"Invalid hotkey configuration: {self.hotkey_str}")
            return

        if not user32.RegisterHotKey(None, self.hotkey_id, fs_modifiers, vk):
            err = ctypes.GetLastError()
            logger.error(f"Failed to register hotkey! Error Code: {err}")
            # Common error codes:
            # 1409: HOTKEY_ALREADY_REGISTERED
            return

        logger.info(f"Hotkey registered: {self.hotkey_str}")

        msg = wintypes.MSG()
        try:
            while self.running:
                try:
                    # GetMessage: > 0 (Message), 0 (WM_QUIT), -1 (Error)
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)

                    if ret == -1:
                        err = ctypes.GetLastError()
                        logger.error(f"GetMessageW failed error={err}")
                        time.sleep(1) # Prevent busy loop on error
                        continue
                    elif ret == 0:
                        logger.info("WM_QUIT received, stopping hotkey listener")
                        break

                    if msg.message == WM_HOTKEY:
                        if callable(self.on_trigger):
                            try:
                                self.on_trigger()
                            except Exception as e:
                                logger.error(f"Error in hotkey callback: {e}")

                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

                except Exception as e:
                    logger.error(f"Unexpected error in hotkey loop: {e}")
                    time.sleep(1)
        finally:
            user32.UnregisterHotKey(None, self.hotkey_id)
            logger.info("Hotkey unregistered")

    def stop(self):
        self.running = False
        # Note: GetMessage is blocking, so strictly speaking this won't unblock it immediately.
        # But since it's a daemon thread, main exit will kill it.
        # For cleaner exit we could PostQuitMessage to this thread ID, but omitting for MVP simplicity.
