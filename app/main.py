import pystray
from PIL import Image, ImageDraw
import threading
import sys
from .settings import current_settings
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
        with self.lock:
            if self.state == TRANSCRIBING:
                logger.info("Ignored hotkey during transcription")
                return

            if self.state == IDLE:
                self.start_recording()
            elif self.state == RECORDING:
                self.stop_and_transcribe()

    def start_recording(self):
        logger.info("Hotkey: Start Recording")
        sounds.play_start()
        self.recorder.start()
        self.state = RECORDING
        self.update_icon_state()

    def stop_and_transcribe(self):
        logger.info("Hotkey: Stop Recording")
        audio_file = self.recorder.stop()
        if not audio_file:
            # Failed to record or short
            self.state = IDLE
            self.update_icon_state()
            return

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
                paste_text()
                sounds.play_finish()
        finally:
            self.recorder.cleanup_file(audio_file)
            with self.lock:
                self.state = IDLE
                self.update_icon_state()

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

if __name__ == "__main__":
    app = MainApp()

    # Initialize GUI
    app.gui = FloatingWidget(
        on_click_callback=app.on_hotkey, # Re-use toggle logic
        on_exit_callback=lambda: app.exit_app(None, None)
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
