import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import tempfile
import time
from pathlib import Path
from .settings import current_settings
from .logger import logger

class Recorder:
    def __init__(self):
        self.frames = []
        self.stream = None
        self.sample_rate = current_settings.sample_rate
        self.channels = 1
        self.is_recording = False
        self.temp_dir = Path(current_settings.temp_dir) if current_settings.temp_dir else Path(tempfile.gettempdir()) / "local_dictation"

        # Ensure temp directory exists
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Temporary directory set to: {self.temp_dir}")
            self._cleanup_old_files()
        except Exception as e:
            logger.error(f"Failed to create temp dir {self.temp_dir}: {e}")

    def _cleanup_old_files(self):
        """Delete temporary files older than 1 day."""
        try:
            now = time.time()
            # 1 day in seconds
            expiration = 24 * 60 * 60
            count = 0
            for f in self.temp_dir.glob("rec_*.wav"):
                if f.is_file():
                    mtime = f.stat().st_mtime
                    if now - mtime > expiration:
                        try:
                            f.unlink()
                            count += 1
                        except Exception as e:
                            logger.warn(f"Failed to delete old file {f}: {e}")
            if count > 0:
                logger.info(f"Cleaned up {count} old recording files.")
        except Exception as e:
            logger.error(f"Error during file cleanup: {e}")

    def callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Audio status: {status}")
        self.frames.append(indata.copy())

    def start(self):
        if self.is_recording:
            logger.warning("Attempted to start recording while already recording")
            return

        try:
            self.frames = []
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self.callback,
                dtype='int16',
                device=current_settings.audio_device
            )
            self.stream.start()
            self.is_recording = True
            logger.info("Recording started")
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.is_recording = False

    def stop(self, discard=False) -> str:
        if not self.is_recording:
            return ""

        try:
            self.stream.stop()
            self.stream.close()
            self.is_recording = False
            logger.info("Recording stopped")

            if discard:
                logger.info("Recording discarded")
                self.frames = [] # clear frames
                return ""

            if not self.frames:
                logger.warning("No frames recorded")
                return ""

            recording = np.concatenate(self.frames, axis=0)

            # --- VAD Check ---
            # Calculate RMS amplitude
            # frames are int16, so values are between -32768 and 32767
            # We want to check if the audio is mostly silence

            # Simple RMS of the entire clip
            float_data = recording.astype(np.float32)
            rms = np.sqrt(np.mean(float_data**2))
            max_amp = np.max(np.abs(float_data))

            logger.info(f"Audio Stats: RMS={rms:.2f}, Max={max_amp:.2f}")

            if max_amp < current_settings.silence_threshold:
               logger.info(f"Audio ignored: detailed silence check (Max Amp {max_amp:.2f} < {current_settings.silence_threshold})")
               return ""

            # -----------------

            timestamp = int(time.time() * 1000)
            filename = self.temp_dir / f"rec_{timestamp}.wav"

            wav.write(str(filename), self.sample_rate, recording)
            logger.info(f"Saved audio to {filename}")
            return str(filename)
        except Exception as e:
            logger.error(f"Failed to stop recording/save file: {e}")
            return ""

    def cleanup_file(self, filepath: str):
        try:
            path = Path(filepath)
            if path.exists():
                path.unlink()
                logger.debug(f"Deleted temp file: {filepath}")
        except Exception as e:
            logger.error(f"Failed to delete file {filepath}: {e}")
