import pystray
from PIL import Image, ImageDraw
import threading
import queue
import sys
import ctypes
import time
from pathlib import Path
from .settings import current_settings, load_settings_as_dict, save_settings
from .logger import logger
from .recorder import Recorder
from .transcriber import Transcriber
from .clipboard_win import set_text, paste_text
from .hotkey_win import HotkeyListener
from . import sounds

# States
IDLE = "IDLE"
RECORDING = "RECORDING"
TRANSCRIBING = "TRANSCRIBING"
LISTENING = "LISTENING"

VK_ESCAPE = 0x1B

class MainApp:
    def __init__(self):
        self.state = IDLE
        self.recorder = Recorder()
        self.transcriber = Transcriber()
        self.icon = None
        self.manual_hotkey_thread = None
        self.vad_hotkey_thread = None
        self.gui = None # Tkinter widget reference

        # Lock for state transition
        self.lock = threading.Lock()

        # VAD auto mode
        self._vad_stop_event = None

        # FIFO transcription queue for VAD mode
        self._transcribe_queue = queue.Queue()
        self._transcribe_worker_thread = None

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
                pystray.MenuItem("Exit", self.exit_app)
            )
        )

    def update_icon_state(self):
        color = "green"
        if self.state == RECORDING:
            color = "red"
        elif self.state == TRANSCRIBING:
            color = "yellow"
        elif self.state == LISTENING:
            color = "blue"

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

    def on_manual_hotkey(self):
        """マニュアルモード用ホットキーコールバック"""
        # 状態を確認してアクションを決定（ロックを最小限に保持）
        with self.lock:
            current_state = self.state
            if current_state == TRANSCRIBING:
                logger.info("Ignored manual hotkey during transcription")
                return

        # ロックの外で各アクションを実行（デッドロック回避）
        if current_state == IDLE:
            self.start_recording()
        elif current_state == RECORDING:
            self.stop_and_transcribe()
        elif current_state == LISTENING:
            # VADモード中にマニュアルホットキーが押された場合は無視
            logger.info("Ignored manual hotkey during VAD listening")

    def on_vad_hotkey(self):
        """VADモード用ホットキーコールバック"""
        # 状態を確認してアクションを決定（ロックを最小限に保持）
        with self.lock:
            current_state = self.state
            if current_state == TRANSCRIBING:
                logger.info("Ignored VAD hotkey during transcription")
                return

        # ロックの外で各アクションを実行（デッドロック回避）
        if current_state == IDLE:
            self.start_listening()
        elif current_state == RECORDING:
            # 録音中にVADホットキーが押された場合はキャンセル
            self.cancel_recording()
        elif current_state == LISTENING:
            self.stop_listening()

    def start_recording(self, from_vad=False):
        logger.info("Start Recording")
        self.recorder.stop_monitoring()

        # VADモードからの呼び出しの場合はビープ音を鳴らさない（シームレスに録音）
        if not from_vad:
            sounds.play_start()

        # VADモードの場合はプリバッファを含める（発話開始部分が切れないように）
        self.recorder.start(include_prebuffer=from_vad)
        self.state = RECORDING
        self.update_icon_state()

        # Start monitoring for Esc key (unless already being monitored, e.g., in VAD mode)
        if not from_vad:
            threading.Thread(target=self._monitor_cancellation, daemon=True).start()

    def _monitor_cancellation(self):
        logger.info("Started cancellation monitor")
        while self.state in (RECORDING, LISTENING):
            # Check if Esc is pressed
            # GetAsyncKeyState returns short (16-bit). MSB set means key is down.
            if ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                logger.info("Esc pressed! Cancelling...")
                if self.state == RECORDING:
                    self.cancel_recording()
                elif self.state == LISTENING:
                    self.stop_listening()
                break
            time.sleep(0.05) # Poll every 50ms

    def cancel_recording(self):
        is_vad_mode = False
        with self.lock:
            if self.state != RECORDING:
                return

            logger.info("Cancelling recording...")

            # VADモードの場合はLISTENINGに戻る（_vad_stop_eventがアクティブな場合）
            if self._vad_stop_event and not self._vad_stop_event.is_set():
                is_vad_mode = True
                self.state = LISTENING
            else:
                self.state = IDLE

            self.update_icon_state()

        # VADモード（常時録音）の場合はキャプチャ状態のみリセット
        # マニュアルモードの場合はストリームを停止
        if is_vad_mode:
            self.recorder.reset_capture_state()
        else:
            self.recorder.stop(discard=True)
        sounds.play_cancel()


    def stop_and_transcribe(self):
        logger.info("Hotkey: Stop Recording")
        audio_file = self.recorder.stop()
        if not audio_file:
            # Failed to record or short or silent
            logger.info("Recording was silent or invalid. Returning to IDLE.")
            with self.lock:
                self.state = IDLE
                self.update_icon_state()
            return

        with self.lock:
            self.state = TRANSCRIBING
            self.update_icon_state()

        # Run transcription in separate thread to not block hotkey listener
        t = threading.Thread(target=self._transcribe_task, args=(audio_file,))
        t.start()

    def _transcribe_task(self, audio_file):
        try:
            text = self.transcriber.transcribe(audio_file)
            if text:
                set_text(text)
                if current_settings.auto_paste:
                    paste_text()
                sounds.play_finish()
        finally:
            self.recorder.cleanup_file(audio_file)
            with self.lock:
                # VADモードがアクティブな場合は状態を変更しない（次の発話を処理中の可能性があるため）
                # manualモードの場合のみIDLEに戻す
                if self._vad_stop_event is None or self._vad_stop_event.is_set():
                    self.state = IDLE
                    self.update_icon_state()

    def _transcribe_worker(self, stop_event):
        """FIFO transcription worker for VAD mode."""
        logger.info("FIFO transcription worker started")
        while not stop_event.is_set():
            try:
                # Wait for audio file with timeout (to check stop_event periodically)
                audio_file = self._transcribe_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if audio_file is None:
                # Poison pill to stop worker
                logger.info("FIFO worker received stop signal")
                break

            logger.info(f"FIFO worker processing: {audio_file}")
            try:
                text = self.transcriber.transcribe(audio_file)
                if text:
                    set_text(text)
                    if current_settings.auto_paste:
                        paste_text()
                    # VADモードでは個々の文字起こし完了時にビープ音を鳴らさない
            finally:
                self.recorder.cleanup_file(audio_file)
                self._transcribe_queue.task_done()

        logger.info("FIFO transcription worker stopped")

    def start_listening(self):
        """Start VAD auto mode listening (常時録音方式)."""
        logger.info("VAD Auto: Start Listening (Continuous Mode)")

        # 前のセッションのクリーンアップ
        self._cleanup_previous_vad_session()

        sounds.play_start()  # VADモード開始のビープ音

        with self.lock:
            self.state = LISTENING
            self.update_icon_state()

        # 常時録音を開始（ストリームの切り替えなし）
        self.recorder.start_continuous()
        self._vad_stop_event = threading.Event()

        # 新しいキューを作成（前のセッションの残骸を確実に除去）
        self._transcribe_queue = queue.Queue()

        # Start FIFO transcription worker
        self._transcribe_worker_thread = threading.Thread(
            target=self._transcribe_worker,
            args=(self._vad_stop_event,),
            daemon=True
        )
        self._transcribe_worker_thread.start()

        threading.Thread(target=self._vad_listen_loop_continuous,
                         args=(self._vad_stop_event,), daemon=True).start()
        threading.Thread(target=self._monitor_cancellation, daemon=True).start()

    def stop_listening(self):
        """Stop VAD auto mode listening (常時録音方式)."""
        logger.info("VAD Auto: Stop Listening (Continuous Mode)")
        if self._vad_stop_event:
            self._vad_stop_event.set()

        # 常時録音を停止
        self.recorder.stop_continuous()

        # キューをクリア（残っているアイテムの一時ファイルを削除）
        self._clear_transcribe_queue()

        # Stop FIFO worker by sending poison pill
        self._transcribe_queue.put(None)

        # ワーカースレッドの終了を待機（タイムアウト付き）
        if self._transcribe_worker_thread and self._transcribe_worker_thread.is_alive():
            self._transcribe_worker_thread.join(timeout=2.0)
            if self._transcribe_worker_thread.is_alive():
                logger.warning("Transcribe worker thread did not stop in time")

        with self.lock:
            if self.state in (LISTENING, RECORDING):
                self.state = IDLE
                self.update_icon_state()

        sounds.play_finish()  # VADモード終了のビープ音

    def _cleanup_previous_vad_session(self):
        """前のVADセッションのリソースをクリーンアップ"""
        # 前のstop_eventを設定
        if self._vad_stop_event and not self._vad_stop_event.is_set():
            logger.warning("Previous VAD session was not properly stopped, cleaning up")
            self._vad_stop_event.set()

        # 前のワーカースレッドの終了を待機
        if self._transcribe_worker_thread and self._transcribe_worker_thread.is_alive():
            self._transcribe_queue.put(None)  # ポイズンピル
            self._transcribe_worker_thread.join(timeout=1.0)
            if self._transcribe_worker_thread.is_alive():
                logger.warning("Previous worker thread did not stop, proceeding anyway")

        # キューをクリア
        self._clear_transcribe_queue()

    def _clear_transcribe_queue(self):
        """キューに残っているアイテムをクリアし、一時ファイルを削除"""
        cleared_count = 0
        while True:
            try:
                audio_file = self._transcribe_queue.get_nowait()
                if audio_file is not None:
                    self.recorder.cleanup_file(audio_file)
                    cleared_count += 1
            except queue.Empty:
                break
        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} items from transcribe queue")

    def _vad_listen_loop_continuous(self, stop_event):
        """VAD auto mode main loop (常時録音方式).

        単一のストリームで常時録音し、音声区間をリングバッファから切り出す。
        ストリームの切り替えによる音声の途切れを防ぐ。
        """
        poll_interval = 0.05  # 50ms
        silence_needed = int(current_settings.vad_silence_duration / poll_interval)
        min_duration = current_settings.min_recording_duration
        PRE_SPEECH_COUNT = 10  # 約500ms連続して音声があれば録音開始
        MIN_SPEECH_AMPLITUDE = 2000  # 発話開始時の最低振幅
        MAX_SILENCE_DURATION = 3.0  # 録音中の最大無音期間（秒）

        speech_count = 0
        is_capturing = False  # 発話キャプチャ中かどうか
        recording_start_time = 0

        logger.info(f"VAD Continuous: Using WebRTC VAD (aggressiveness={current_settings.vad_aggressiveness})")

        while not stop_event.is_set():
            # 状態チェック（キャンセルされた場合など）
            current_state = self.state
            if current_state not in (LISTENING, RECORDING):
                break

            is_speech = self.recorder.is_speech_detected()
            current_amp = self.recorder.get_current_amplitude()

            if not is_capturing:
                # フェーズ1: 音声検知待ち (LISTENING状態)
                if is_speech:
                    speech_count += 1
                    if speech_count >= PRE_SPEECH_COUNT:
                        if current_amp >= MIN_SPEECH_AMPLITUDE:
                            # 発話開始をマーク
                            logger.info(f"VAD: Speech detected (amp={current_amp:.0f}), marking start")
                            recording_start_time = time.time()
                            self.recorder.mark_speech_start()
                            is_capturing = True
                            speech_count = 0

                            # UIを更新（RECORDING状態）
                            with self.lock:
                                if self.state == LISTENING:
                                    self.state = RECORDING
                                    self.update_icon_state()
                        else:
                            logger.debug(f"VAD: Speech but amplitude too low ({current_amp:.0f})")
                            speech_count = 0
                else:
                    speech_count = 0
            else:
                # フェーズ2: 無音検知待ち (RECORDING状態、キャプチャ中)
                SILENCE_AMP_THRESHOLD = 300
                is_silence = (not is_speech) or (current_amp < SILENCE_AMP_THRESHOLD)

                if is_silence:
                    speech_count += 1  # ここでは silence_count として使用
                else:
                    speech_count = 0

                max_silence_count = int(MAX_SILENCE_DURATION / poll_interval)
                if speech_count >= silence_needed or speech_count >= max_silence_count:
                    # 無音検知: 発話終了
                    recording_duration = time.time() - recording_start_time
                    logger.info(f"VAD: Silence detected (duration: {recording_duration:.2f}s)")

                    # 録音時間チェック
                    if recording_duration >= min_duration:
                        audio_file = self.recorder.mark_speech_end()
                        if audio_file:
                            logger.info(f"VAD: Queuing transcription: {audio_file}")
                            self._transcribe_queue.put(audio_file)
                        else:
                            logger.info("VAD: Audio rejected by silence check")
                    else:
                        logger.info(f"VAD: Too short ({recording_duration:.2f}s), discarding")
                        self.recorder.reset_capture_state()

                    is_capturing = False
                    speech_count = 0

                    # UIを更新（LISTENINGに戻る）
                    with self.lock:
                        if not stop_event.is_set() and self.state == RECORDING:
                            self.state = LISTENING
                            self.update_icon_state()

            time.sleep(poll_interval)

        # ループ終了
        with self.lock:
            if self.state in (LISTENING, RECORDING):
                self.state = IDLE
                self.update_icon_state()
        logger.info("VAD continuous listen loop exited")

    def run_hotkey(self):
        logger.info(f"App starting. Manual hotkey: {current_settings.manual_hotkey}, VAD hotkey: {current_settings.vad_hotkey}")
        # Start manual hotkey listener
        self.manual_hotkey_thread = HotkeyListener(current_settings.manual_hotkey, self.on_manual_hotkey, hotkey_id=1)
        self.manual_hotkey_thread.start()
        # Start VAD hotkey listener
        self.vad_hotkey_thread = HotkeyListener(current_settings.vad_hotkey, self.on_vad_hotkey, hotkey_id=2)
        self.vad_hotkey_thread.start()

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

        SettingsDialog(self.gui.root, current_dict, on_save)

    def restart_app(self):
        """アプリケーションを再起動"""
        logger.info("Restarting application...")

        # 現在のプロセスを終了して再起動
        python = sys.executable
        script = sys.argv[0]

        # リソースをクリーンアップ
        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
        if self.manual_hotkey_thread:
            try:
                self.manual_hotkey_thread.stop()
            except:
                pass
        if self.vad_hotkey_thread:
            try:
                self.vad_hotkey_thread.stop()
            except:
                pass

        # 新しいプロセスを起動
        import subprocess
        subprocess.Popen([python, "-m", "app.main"], cwd=str(Path.cwd()))

        # 現在のプロセスを終了
        sys.exit(0)

    def exit_app(self, icon, item):
        logger.info("Exit requested")
        if self.icon:
            self.icon.stop()
        if self.manual_hotkey_thread:
            self.manual_hotkey_thread.stop()
        if self.vad_hotkey_thread:
            self.vad_hotkey_thread.stop()
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
        import subprocess
        subprocess.Popen([sys.executable, "-m", "app.main"], cwd=str(Path.cwd()))
        sys.exit(0)

    def on_cancel():
        logger.info("Setup wizard cancelled")
        sys.exit(0)

    wizard = SetupWizard(on_complete, on_cancel)
    wizard.run()


if __name__ == "__main__":
    # 初回起動チェック
    if check_first_run():
        logger.info("First run detected, starting setup wizard...")
        run_setup_wizard_and_start()
    else:
        app = MainApp()

        # Initialize GUI
        app.gui = FloatingWidget(
            on_click_callback=app.on_manual_hotkey,  # GUIクリックはマニュアルモードとして扱う
            on_exit_callback=lambda: app.exit_app(None, None),
            on_settings_callback=app.show_settings_dialog
        )

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
