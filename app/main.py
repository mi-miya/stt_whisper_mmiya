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
        self.hotkey_thread = None
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

    def on_hotkey(self):
        # 状態を確認してアクションを決定（ロックを最小限に保持）
        with self.lock:
            current_state = self.state
            if current_state == TRANSCRIBING:
                logger.info("Ignored hotkey during transcription")
                return

        # ロックの外で各アクションを実行（デッドロック回避）
        if current_state == IDLE:
            if current_settings.input_mode == "vad_auto":
                self.start_listening()
            else:
                self.start_recording()
        elif current_state == RECORDING:
            if current_settings.input_mode == "manual":
                self.stop_and_transcribe()
            else:
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
        should_resume_vad = False
        with self.lock:
            if self.state != RECORDING:
                return

            logger.info("Cancelling recording...")

            # VADモードの場合はLISTENINGに戻る、それ以外はIDLE
            if current_settings.input_mode == "vad_auto" and self._vad_stop_event and not self._vad_stop_event.is_set():
                should_resume_vad = True
                self.state = LISTENING
            else:
                self.state = IDLE

            self.update_icon_state()

        # ストリーム操作はロックの外で実行
        self.recorder.stop(discard=True)
        if should_resume_vad:
            self.recorder.start_monitoring()
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
                    sounds.play_finish()
            finally:
                self.recorder.cleanup_file(audio_file)
                self._transcribe_queue.task_done()

        logger.info("FIFO transcription worker stopped")

    def start_listening(self):
        """Start VAD auto mode listening."""
        logger.info("VAD Auto: Start Listening")
        self.state = LISTENING
        self.update_icon_state()
        self.recorder.start_monitoring()
        self._vad_stop_event = threading.Event()

        # Start FIFO transcription worker
        self._transcribe_worker_thread = threading.Thread(
            target=self._transcribe_worker,
            args=(self._vad_stop_event,),
            daemon=True
        )
        self._transcribe_worker_thread.start()

        threading.Thread(target=self._vad_listen_loop,
                         args=(self._vad_stop_event,), daemon=True).start()
        threading.Thread(target=self._monitor_cancellation, daemon=True).start()

    def stop_listening(self):
        """Stop VAD auto mode listening."""
        logger.info("VAD Auto: Stop Listening")
        if self._vad_stop_event:
            self._vad_stop_event.set()
        self.recorder.stop_monitoring()

        # Stop FIFO worker by sending poison pill
        self._transcribe_queue.put(None)

        with self.lock:
            if self.state == LISTENING:
                self.state = IDLE
                self.update_icon_state()

    def _vad_listen_loop(self, stop_event):
        """VAD auto mode main loop with FIFO transcription using WebRTC VAD."""
        poll_interval = 0.05  # 50ms
        silence_needed = int(current_settings.vad_silence_duration / poll_interval)
        min_duration = current_settings.min_recording_duration
        PRE_SPEECH_COUNT = 10  # 約500ms連続して音声があれば録音開始（キーボード音などの誤検知を防止）
        MIN_SPEECH_AMPLITUDE = 2000  # 発話開始時の最低振幅（キーボード音などのノイズを除外）
        MAX_SILENCE_DURATION = 3.0  # 録音中の最大無音期間（秒）：これを超えたら強制停止
        speech_count = 0

        logger.info(f"VAD: Using WebRTC VAD (aggressiveness={current_settings.vad_aggressiveness}, pre-speech={PRE_SPEECH_COUNT * poll_interval:.2f}s, min-amp={MIN_SPEECH_AMPLITUDE})")

        while not stop_event.is_set():
            # フェーズ1: 音声検知待ち
            if self.state != LISTENING:
                break

            # WebRTC VADで音声判定
            is_speech = self.recorder.is_speech_detected()

            if is_speech:
                speech_count += 1
                if speech_count < PRE_SPEECH_COUNT:
                    time.sleep(poll_interval)
                    continue

                # 振幅チェック：音声検知はされたが振幅が低い場合はキーボード音などの可能性
                current_amp = self.recorder.get_current_amplitude()
                if current_amp < MIN_SPEECH_AMPLITUDE:
                    logger.info(f"VAD: Speech detected but amplitude too low ({current_amp:.0f} < {MIN_SPEECH_AMPLITUDE}), ignoring")
                    speech_count = 0
                    time.sleep(poll_interval)
                    continue

                # 音声検知: 録音開始
                logger.info(f"VAD: Speech detected (amp={current_amp:.0f}), starting recording")
                recording_start_time = time.time()
                with self.lock:
                    if self.state == LISTENING:
                        # VADモードからの呼び出しなので、キャンセルモニターは既に起動済み
                        self.start_recording(from_vad=True)
                    else:
                        break
                speech_count = 0

                # フェーズ2: 無音検知待ち
                silence_count = 0
                loop_count = 0
                max_silence_count = int(MAX_SILENCE_DURATION / poll_interval)
                while not stop_event.is_set():
                    # 状態が変わった場合（キャンセルなど）はループを抜ける
                    if self.state != RECORDING:
                        logger.info("VAD: State changed during recording, exiting silence detection")
                        break

                    # WebRTC VADで音声判定
                    is_speech = self.recorder.is_speech_detected()
                    current_amp = self.recorder.get_current_amplitude()

                    # デバッグログ（10回に1回）
                    loop_count += 1
                    if loop_count % 10 == 0:
                        logger.debug(f"VAD無音検知: is_speech={is_speech}, amp={current_amp:.0f}, silence_count={silence_count}/{silence_needed}")

                    # 無音判定: VADが音声なしと判定 OR 振幅が極めて低い場合
                    SILENCE_AMP_THRESHOLD = 300  # この振幅以下は無音とみなす
                    is_silence = (not is_speech) or (current_amp < SILENCE_AMP_THRESHOLD)

                    if is_silence:
                        silence_count += 1
                    else:
                        silence_count = 0

                    # 設定された無音期間で停止、または最大無音期間（安全装置）で停止
                    if silence_count >= silence_needed or silence_count >= max_silence_count:
                        # 無音検知: 録音停止 & すぐLISTENINGに戻る
                        recording_duration = time.time() - recording_start_time
                        logger.info(f"VAD: Silence detected, stopping recording (duration: {recording_duration:.2f}s)")
                        audio_file = self.recorder.stop()

                        with self.lock:
                            # VADモードがまだアクティブな場合のみLISTENINGに戻る
                            if not stop_event.is_set() and self.state == RECORDING:
                                self.state = LISTENING
                                self.update_icon_state()

                        # モニタリング再開（次の発話を検知可能に）
                        # ただし、VADモードがまだアクティブな場合のみ
                        if not stop_event.is_set() and self.state == LISTENING:
                            self.recorder.start_monitoring()

                        # 録音時間が短すぎる場合はスキップ（咳、息継ぎなど）
                        if audio_file:
                            if recording_duration < min_duration:
                                logger.info(f"VAD: Recording too short ({recording_duration:.2f}s < {min_duration}s), skipping")
                                self.recorder.cleanup_file(audio_file)
                            else:
                                logger.info(f"VAD: Recording accepted ({recording_duration:.2f}s >= {min_duration}s), queuing transcription: {audio_file}")
                                self._transcribe_queue.put(audio_file)
                        else:
                            logger.info(f"VAD: Recording rejected by silence check (duration: {recording_duration:.2f}s)")

                        break  # 内側ループを抜けて外側ループへ（次の発話待機）
                    time.sleep(poll_interval)
                continue
            else:
                speech_count = 0

            time.sleep(poll_interval)

        # ループ終了: IDLEに戻す
        with self.lock:
            if self.state == LISTENING:
                self.state = IDLE
                self.update_icon_state()
        logger.info("VAD listen loop exited")

    def run_hotkey(self):
        logger.info(f"App starting. Hotkey: {current_settings.hotkey}")
        # Start hotkey listener
        self.hotkey_thread = HotkeyListener(current_settings.hotkey, self.on_hotkey)
        self.hotkey_thread.start()

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
        if self.hotkey_thread:
            try:
                self.hotkey_thread.stop()
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
            on_click_callback=app.on_hotkey,  # Re-use toggle logic
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
