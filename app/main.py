import pystray
from PIL import Image, ImageDraw
import threading
import sys
import os
import logging
import ctypes
import time
from pathlib import Path
from .settings import (current_settings, load_settings_as_dict, save_settings, update_settings,
                       config_load_error, KEEP_MIC_OPEN_QUESTION)
from .logger import logger
from .error_handler import ErrorHandler, show_error
from .recorder import Recorder
from .transcriber import Transcriber
from .clipboard_win import set_text, paste_text
from .hotkey_win import HotkeyListener
from . import sounds
from . import debug_history

# States
IDLE = "IDLE"
RECORDING = "RECORDING"
TRANSCRIBING = "TRANSCRIBING"

VK_ESCAPE = 0x1B

# 自動再起動するのは、使われていない状態がこれだけ続いたとき
AUTO_RESTART_IDLE = 10 * 60
AUTO_RESTART_CHECK_MS = 60 * 1000

MUTEX_NAME = "LocalWhisperDictation_Mutex"
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None


def ensure_single_instance():
    """二重起動を防止する。既に起動中ならメッセージを表示して終了する。"""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    # 再起動では、古いプロセスが終わりきる直前に新しいプロセスが立ち上がる。
    # 取りこぼして「既に起動しています」と出さないよう、少し待って判定し直す
    attempts = 20 if "--restarted" in sys.argv else 1
    for attempt in range(attempts):
        _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            return
        kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        if attempt < attempts - 1:
            time.sleep(0.25)
    ctypes.windll.user32.MessageBoxW(
        0, "音声入力ツールは既に起動しています。\nタスクトレイまたは画面上のアイコンを確認してください。",
        "音声入力ツール", 0x40)
    sys.exit(0)


def release_single_instance():
    """再起動時に新プロセスがミューテックスを取得できるよう解放する。"""
    global _mutex_handle
    if _mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None

class MainApp:
    def __init__(self):
        self.state = IDLE
        self.recorder = Recorder()
        self.transcriber = Transcriber()
        self.icon = None
        self.hotkey_thread = None
        self.gui = None # Tkinter widget reference
        self._settings_dialog = None

        # 自動再起動の判定用
        self._started = time.monotonic()
        self._last_active = self._started
        if "--auto" in sys.argv:
            logger.info("Started (auto restart after long uptime)")
        elif "--restarted" in sys.argv:
            logger.info("Started (restarted)")

        # Lock for state transition
        self.lock = threading.Lock()

        # バックグラウンドでWhisperモデルをウォームアップ
        self._start_warmup()

        if current_settings.keep_mic_open:
            self._apply_keep_mic_open(True)

    def _apply_keep_mic_open(self, enabled: bool):
        # マイクを開くには音声ライブラリの読み込みとイヤホンの切り替えで数秒かかるため、
        # 画面やホットキーを止めないよう別スレッドで行う
        threading.Thread(target=self.recorder.set_keep_open, args=(enabled,), daemon=True).start()

    def set_keep_mic_open(self, enabled: bool):
        """常時通話モードを切り替えて保存する（右クリックメニュー・起動時の質問から）"""
        logger.info(f"Always-on call mode set to {enabled}")
        if not update_settings({'keep_mic_open': enabled}):
            logger.error("Failed to save keep_mic_open")
        current_settings.keep_mic_open = enabled
        self._apply_keep_mic_open(enabled)
        if self.gui:
            self.gui.set_keep_mic_open(enabled)

    def ask_keep_mic_open(self):
        """常時通話モードを使うか、まだ答えていない人に一度だけ聞く"""
        from tkinter import messagebox
        use = messagebox.askyesno(
            "音声入力ツール: 常時通話モード",
            KEEP_MIC_OPEN_QUESTION,
            parent=self.gui.root if self.gui else None,
        )
        self.set_keep_mic_open(bool(use))

    def _start_warmup(self):
        """バックグラウンドでWhisperモデルをウォームアップ開始"""
        def warmup_task():
            logger.info("Starting background warmup...")
            success = self.transcriber.warmup()
            if success:
                logger.info("Background warmup completed successfully")
            else:
                logger.warning("Background warmup failed or skipped")

        warmup_thread = threading.Thread(target=warmup_task, daemon=True)
        warmup_thread.start()

    def create_image(self, color):
        # Generate generic icon
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), color)
        dc = ImageDraw.Draw(image)
        # Draw a circle/mic shape
        dc.ellipse((16, 16, 48, 48), fill=(255, 255, 255))
        return image

    def setup_tray(self):
        self.icon = pystray.Icon(
            "LocalDictation",
            self.create_image("green"),
            menu=pystray.Menu(
                pystray.MenuItem("再起動", lambda icon, item: self.restart_app()),
                pystray.MenuItem("Exit", self.exit_app)
            )
        )

    def update_icon_state(self):
        color = "green"
        if self.state == RECORDING:
            color = "red"
        elif self.state == TRANSCRIBING:
            color = "yellow"

        # Update Tray
        if self.icon:
            try:
                self.icon.icon = self.create_image(color)
                self.icon.title = f"Local Whisper: {self.state}"
            except Exception as e:
                logger.error(f"Failed to update tray: {e}")

        # Update Floating GUI
        if self.gui:
            self.gui.set_state(self.state, color)

    def on_hotkey(self):
        """ホットキーコールバック"""
        self._mark_active()
        # 状態を確認してアクションを決定（ロックを最小限に保持）
        with self.lock:
            current_state = self.state
            if current_state == TRANSCRIBING:
                logger.info("Ignored hotkey during transcription")
                return

        # ロックの外で各アクションを実行（デッドロック回避）
        if current_state == IDLE:
            self.start_recording()
        elif current_state == RECORDING:
            self.stop_and_transcribe()

    def start_recording(self):
        logger.info("Start Recording")
        sounds.play_start()

        self.recorder.start()
        self.state = RECORDING
        self.update_icon_state()

        # Start monitoring for Esc key
        threading.Thread(target=self._monitor_cancellation, daemon=True).start()

    def _monitor_cancellation(self):
        logger.info("Started cancellation monitor")
        while self.state == RECORDING:
            # Check if Esc is pressed
            # GetAsyncKeyState returns short (16-bit). MSB set means key is down.
            if ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                logger.info("Esc pressed! Cancelling...")
                self.cancel_recording()
                break
            time.sleep(0.05) # Poll every 50ms

    def cancel_recording(self):
        with self.lock:
            if self.state != RECORDING:
                return

            logger.info("Cancelling recording...")
            self.state = IDLE
            self.update_icon_state()

        self.recorder.stop(discard=True)
        sounds.play_cancel()

    def stop_and_transcribe(self):
        logger.info("Hotkey: Stop Recording")
        # 先に状態を遷移させてから、停止処理（配列結合・無音判定）ごと
        # ワーカースレッドに逃がす。ホットキーの message loop を
        # ブロックしないようにするため
        with self.lock:
            self.state = TRANSCRIBING
            self.update_icon_state()

        t = threading.Thread(target=self._transcribe_task)
        t.start()

    def _transcribe_task(self):
        stopped_at = time.time()
        capture = None
        result = None
        try:
            capture = self.recorder.stop()
            if capture is None or capture.audio is None:
                # Failed to record or short or silent
                logger.info("Recording was silent or invalid. Returning to IDLE.")
                sounds.play_cancel()
                return

            # モデルロード中なら待機中であることをGUIに表示
            if not self.transcriber.is_ready():
                logger.info("Model still loading, showing LOADING state...")
                if self.gui:
                    self.gui.set_state("LOADING", "orange")
                ready = self.transcriber.ensure_ready()
                self.update_icon_state()
                if not ready:
                    # エラー表示は transcriber 側で済んでいる
                    sounds.play_cancel()
                    return

            result = self.transcriber.transcribe(capture.audio)
            if result.text:
                set_text(result.text)
                if current_settings.auto_paste:
                    paste_text()
                sounds.play_finish()
            else:
                sounds.play_cancel()
        finally:
            finished_at = time.time()
            self._mark_active()
            with self.lock:
                self.state = IDLE
                self.update_icon_state()
            # 待ち時間を延ばさないよう、貼り付けと状態の復帰が済んでから書く
            if capture is not None:
                debug_history.record(capture, result, stopped_at, finished_at)

    def run_hotkey(self):
        logger.info(f"App starting. Hotkey: {current_settings.hotkey}")
        # Start hotkey listener
        self.hotkey_thread = HotkeyListener(
            current_settings.hotkey, self.on_hotkey, hotkey_id=1,
            on_error=self._on_hotkey_error
        )
        self.hotkey_thread.start()

    def _on_hotkey_error(self):
        # 登録に失敗するとアイコンは正常に見えたままキーだけ効かなくなるため、
        # 必ずユーザーに知らせる
        show_error("hotkey_failed", f"ホットキー: {current_settings.hotkey}")

    def run_tray(self):
        self.setup_tray()
        self.update_icon_state()
        logger.info("System tray initialized.")
        self.icon.run()

    def show_settings_dialog(self):
        """設定ダイアログを表示"""
        if not self.gui:
            return

        # 遅延インポート
        from .settings_dialog import SettingsDialog

        current_dict = load_settings_as_dict()

        def on_save(new_settings: dict):
            if save_settings(new_settings):
                logger.info("Settings saved. Restarting application...")
                self.restart_app()

        self._settings_dialog = SettingsDialog(self.gui.root, current_dict, on_save)

    def _mark_active(self):
        self._last_active = time.monotonic()

    def auto_restart_due(self) -> bool:
        """長く動き続けたあと、使われていない時間が続いたら再起動どきとみなす

        録音中・文字起こし中・設定画面を開いている間は、使用中として扱う。
        """
        hours = current_settings.auto_restart_hours
        if hours <= 0:
            return False

        dialog = self._settings_dialog
        dialog_open = dialog is not None and dialog.window.winfo_exists()
        if self.state != IDLE or dialog_open:
            self._mark_active()

        now = time.monotonic()
        return (now - self._started >= hours * 3600
                and now - self._last_active >= AUTO_RESTART_IDLE)

    def schedule_auto_restart_check(self):
        """Tk のスレッドで定期的に、自動再起動の時期かどうかを確かめる"""
        if self.auto_restart_due():
            uptime_hours = (time.monotonic() - self._started) / 3600
            logger.info(f"Auto restart: running for {uptime_hours:.1f}h and idle")
            self.restart_app(auto=True)
            return
        self.gui.root.after(AUTO_RESTART_CHECK_MS, self.schedule_auto_restart_check)

    def restart_app(self, auto=False):
        """アプリケーションを再起動

        新しいプロセスを立ち上げてから自分は終わる。設定の反映のほか、
        動きが悪くなったときにメニューから、また長時間動かしたあとの
        空き時間にも自動で呼ばれる。
        """
        logger.info(f"Restarting application... (auto={auto})")

        # リソースをクリーンアップ
        self.recorder.close()
        self.transcriber.cleanup()
        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
        if self.hotkey_thread:
            try:
                self.hotkey_thread.stop()
            except:
                pass

        # 新しいプロセスがミューテックスを取得できるよう先に解放する
        release_single_instance()

        # 新しいプロセスを起動
        import subprocess
        args = [sys.executable, "-m", "app.main", "--restarted"] + (["--auto"] if auto else [])
        try:
            subprocess.Popen(args, cwd=str(Path.cwd()))
        finally:
            # トレイのスレッドから呼ばれても確実にプロセスごと終わるよう、
            # sys.exit ではなく os._exit を使う。ログは先に書き出しておく
            logging.shutdown()
            os._exit(0)

    def exit_app(self, icon, item):
        logger.info("Exit requested")
        self.recorder.close()
        self.transcriber.cleanup()
        if self.icon:
            self.icon.stop()
        if self.hotkey_thread:
            self.hotkey_thread.stop()
        sys.exit(0)

import signal
import time
from .gui import FloatingWidget


def check_first_run() -> bool:
    """初回起動かどうかをチェック"""
    config_path = Path.cwd() / "config.json"
    return not config_path.exists()


def run_setup_wizard_and_start():
    """セットアップウィザードを実行してからアプリを起動"""
    from .setup_wizard import SetupWizard

    def on_complete(settings):
        logger.info("Setup wizard completed, starting app...")
        # アプリを再起動（設定を反映するため）
        release_single_instance()
        import subprocess
        subprocess.Popen([sys.executable, "-m", "app.main"], cwd=str(Path.cwd()))
        sys.exit(0)

    def on_cancel():
        logger.info("Setup wizard cancelled")
        sys.exit(0)

    wizard = SetupWizard(on_complete, on_cancel)
    wizard.run()


if __name__ == "__main__":
    # 二重起動チェック
    ensure_single_instance()

    # 初回起動チェック
    if check_first_run():
        logger.info("First run detected, starting setup wizard...")
        run_setup_wizard_and_start()
    else:
        app = MainApp()

        # Initialize GUI
        app.gui = FloatingWidget(
            on_click_callback=app.on_hotkey,
            on_exit_callback=lambda: app.exit_app(None, None),
            on_settings_callback=app.show_settings_dialog,
            on_restart_callback=app.restart_app,
            on_keep_mic_open_callback=app.set_keep_mic_open,
            keep_mic_open=bool(current_settings.keep_mic_open),
        )
        app.gui.root.after(AUTO_RESTART_CHECK_MS, app.schedule_auto_restart_check)

        # ワーカースレッドからのエラーダイアログをメインスレッドで表示させる
        ErrorHandler.set_root(app.gui.root)

        # 設定ファイルの読み込みに問題があった場合は、起動後に知らせる
        if config_load_error:
            app.gui.root.after(0, lambda: show_error("config_error", config_load_error))

        # 常時通話モードを使うか、まだ答えていなければ聞く（このモードを足す前から使っている人向け）
        if current_settings.keep_mic_open is None:
            app.gui.root.after(500, app.ask_keep_mic_open)

        # Override exit to close GUI too
        original_exit = app.exit_app
        def exit_wrapper(icon, item):
            logger.info("Exit wrapper called")
            if app.gui:
                app.gui.quit()
            original_exit(icon, item)

        app.exit_app = exit_wrapper

        # Handle Ctrl+C
        def signal_handler(sig, frame):
            logger.info("Ctrl+C detected")
            exit_wrapper(None, None)

        signal.signal(signal.SIGINT, signal_handler)

        # Run Tray in background thread
        tray_thread = threading.Thread(target=app.run_tray, daemon=True)
        tray_thread.start()

        # Run Hotkey in background thread (MainApp.run logic split)
        app.run_hotkey()

        # Run GUI on Main Thread (Blocking)
        logger.info("Starting GUI...")
        try:
            app.gui.run()
        except KeyboardInterrupt:
            signal_handler(None, None)
