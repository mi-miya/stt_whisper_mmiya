import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable, Optional
from .logger import logger

# RegisterHotKey 失敗時のエラーコードを確実に取得するため use_last_error で
# ロードする（windll 共有インスタンスでは他の呼び出しに上書きされ得る）
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

# Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# 再起動直後は旧プロセスがまだホットキーを保持していることがあるため、
# 登録済みエラーの場合はしばらく再試行する
REGISTER_RETRY_COUNT = 10
REGISTER_RETRY_INTERVAL = 0.5
STOP_JOIN_TIMEOUT = 2.0

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
    def __init__(self, hotkey_str: str, on_trigger: Callable, hotkey_id: int = 1,
                 on_error: Optional[Callable[[], None]] = None):
        super().__init__(daemon=True)
        self.hotkey_str = hotkey_str.lower()
        self.on_trigger = on_trigger
        self.on_error = on_error
        self.running = False
        self.hotkey_id = hotkey_id
        self._thread_id = None

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

    def _register(self, fs_modifiers: int, vk: int) -> bool:
        """ホットキーを登録する。他で登録済みの場合はしばらく再試行する"""
        for attempt in range(1, REGISTER_RETRY_COUNT + 1):
            if user32.RegisterHotKey(None, self.hotkey_id, fs_modifiers, vk):
                return True

            err = ctypes.get_last_error()
            logger.warning(
                f"Failed to register hotkey (attempt {attempt}/{REGISTER_RETRY_COUNT}), Error Code: {err}"
            )
            if err != ERROR_HOTKEY_ALREADY_REGISTERED or not self.running:
                break
            if attempt < REGISTER_RETRY_COUNT:
                time.sleep(REGISTER_RETRY_INTERVAL)

        return False

    def _notify_error(self):
        if callable(self.on_error):
            try:
                self.on_error()
            except Exception as e:
                logger.error(f"Error in hotkey error callback: {e}")

    def run(self):
        self.running = True
        # stop() から WM_QUIT を送るために、このスレッドのIDを控えておく
        self._thread_id = threading.get_native_id()
        fs_modifiers, vk = self.parse_hotkey()

        logger.info(f"Registering hotkey: {self.hotkey_str} (Mods: {fs_modifiers}, VK: {vk})")

        if vk == 0:
            logger.error(f"Invalid hotkey configuration: {self.hotkey_str}")
            self._notify_error()
            return

        if not self._register(fs_modifiers, vk):
            if self.running:
                logger.error("Failed to register hotkey! Giving up.")
                self._notify_error()
            return

        logger.info(f"Hotkey registered: {self.hotkey_str}")

        msg = wintypes.MSG()
        try:
            while self.running:
                try:
                    # GetMessage: > 0 (Message), 0 (WM_QUIT), -1 (Error)
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)

                    if ret == -1:
                        err = ctypes.get_last_error()
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
        """リスナーを止め、ホットキーの登録を解除する

        登録解除は登録したスレッドでしか行えないため、WM_QUIT を送って
        GetMessage を抜けさせ、スレッド側の finally で解除させる。再起動時に
        新プロセスが同じキーを登録できるよう、解除の完了を待ってから戻る。
        """
        self.running = False
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=STOP_JOIN_TIMEOUT)
