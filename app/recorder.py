# 遅延インポート用（メモリ最適化）
# sounddevice, numpy, scipy は録音開始時に初めてインポートされる
import tempfile
import time
from collections import deque
from pathlib import Path
from .settings import current_settings
from .logger import logger
from .error_handler import show_error

# グローバル変数（遅延インポート後に設定）
_sd = None
_np = None
_wav = None
_webrtcvad = None


def _lazy_import():
    """numpy, sounddevice, scipy, webrtcvad を遅延インポート"""
    global _sd, _np, _wav, _webrtcvad
    if _sd is None:
        import sounddevice as sd
        import numpy as np
        import scipy.io.wavfile as wav
        import webrtcvad
        _sd = sd
        _np = np
        _wav = wav
        _webrtcvad = webrtcvad
        logger.info("Audio libraries loaded (lazy import)")
    return _sd, _np, _wav, _webrtcvad


class Recorder:
    def __init__(self):
        self.frames = []
        self.stream = None
        self.sample_rate = current_settings.sample_rate
        self.channels = 1
        self.is_recording = False
        self.temp_dir = Path(current_settings.temp_dir) if current_settings.temp_dir else Path(tempfile.gettempdir()) / "local_dictation"
        self._monitor_stream = None
        self._last_amplitude = 0.0

        # WebRTC VAD設定
        self._vad = None
        self._vad_frame_duration = 20  # ms (10, 20, 30のいずれか)
        self._vad_frame_size = int(self.sample_rate * self._vad_frame_duration / 1000)
        self._last_vad_result = False
        self._vad_error_count = 0
        self._max_vad_errors = 10  # この回数を超えたらVADを再初期化

        # プリバッファ: モニタリング中の直近音声を保持（発話開始時の音声が切れないように）
        self._prebuffer_duration = 0.5  # 秒
        # フレーム数を動的に計算（0.5秒 / 20ms = 25フレーム）
        prebuffer_frames = int(self._prebuffer_duration * 1000 / self._vad_frame_duration)
        self._prebuffer = deque(maxlen=prebuffer_frames)

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
        # VADモード用: 録音中も振幅を更新（デバッグ用に残す）
        _, np, _, _ = _lazy_import()
        self._last_amplitude = float(np.max(np.abs(indata.astype(np.float32))))

        # 録音中もVAD判定を継続（無音検知のため）
        if self._vad is not None:
            audio_bytes = indata[:, 0].tobytes() if indata.ndim > 1 else indata.tobytes()
            try:
                self._last_vad_result = self._vad.is_speech(audio_bytes, self.sample_rate)
            except Exception as e:
                # フレームサイズが合わない場合などは前回の値を維持
                logger.error(f"VAD判定エラー（録音中）: {e}, frame_size={len(audio_bytes)}, expected={self._vad_frame_size*2}")
                # エラーが連続する場合は音声なしと判定（録音が終わらない問題を回避）
                self._last_vad_result = False

    def start(self, include_prebuffer=False):
        if self.is_recording:
            logger.warning("Attempted to start recording while already recording")
            return

        try:
            # 遅延インポート
            sd, np, wav, webrtcvad = _lazy_import()

            # VADが未初期化の場合は初期化（VADモードで録音中の無音検知に必要）
            if self._vad is None:
                self._vad = webrtcvad.Vad(current_settings.vad_aggressiveness)
                logger.info(f"WebRTC VAD initialized for recording (aggressiveness={current_settings.vad_aggressiveness})")

            # プリバッファを録音の先頭に含める（VADモードで発話開始部分が切れないように）
            if include_prebuffer and self._prebuffer:
                self.frames = list(self._prebuffer)
                prebuffer_samples = sum(len(f) for f in self.frames)
                prebuffer_duration = prebuffer_samples / self.sample_rate
                logger.info(f"Including prebuffer: {len(self.frames)} frames, {prebuffer_duration:.2f}s")
            else:
                self.frames = []

            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self.callback,
                dtype='int16',
                blocksize=self._vad_frame_size,  # WebRTC VADに適したフレームサイズ
                device=current_settings.audio_device
            )
            self.stream.start()
            self.is_recording = True
            logger.info("Recording started")
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.is_recording = False
            show_error("recording_failed", str(e))

    def stop(self, discard=False) -> str:
        if not self.is_recording:
            return ""

        try:
            # 遅延インポート
            sd, np, wav, _ = _lazy_import()

            self.stream.stop()
            self.stream.close()
            self.is_recording = False
            logger.info("Recording stopped")

            if discard:
                logger.info("Recording discarded")
                self.frames = []  # clear frames
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

            # 最大振幅チェック
            if max_amp < current_settings.silence_threshold:
                logger.info(f"Audio ignored: Max amplitude too low ({max_amp:.2f} < {current_settings.silence_threshold})")
                return ""

            # RMSチェック：環境ノイズレベルを除外（人の声のRMSは通常600以上、キーボード音は200-300程度）
            MIN_RMS_THRESHOLD = 400
            if rms < MIN_RMS_THRESHOLD:
                logger.info(f"Audio ignored: RMS too low ({rms:.2f} < {MIN_RMS_THRESHOLD}), likely background noise or keyboard")
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

    def start_monitoring(self):
        """Start lightweight monitoring stream for VAD auto mode."""
        # 既存のストリームがある場合は先に停止
        if self._monitor_stream is not None:
            logger.warning("Monitor stream already exists, stopping it first")
            self.stop_monitoring()

        sd, np, _, webrtcvad = _lazy_import()

        # WebRTC VADを常に再初期化（設定変更を反映するため）
        self._vad = webrtcvad.Vad(current_settings.vad_aggressiveness)
        logger.info(f"WebRTC VAD initialized (aggressiveness={current_settings.vad_aggressiveness})")

        # VAD状態をリセット
        self._last_vad_result = False
        self._vad_error_count = 0

        # プリバッファをクリア
        self._prebuffer.clear()

        def _cb(indata, frames, time, status):
            # デバッグ用に振幅も計算
            self._last_amplitude = float(np.max(np.abs(indata.astype(np.float32))))

            # プリバッファに音声データを保持（発話開始時の音声が切れないように）
            self._prebuffer.append(indata.copy())

            # WebRTC VADで音声判定
            # indata は (frames, channels) の形状なので、1チャンネル目を取得
            audio_bytes = indata[:, 0].tobytes() if indata.ndim > 1 else indata.tobytes()
            try:
                self._last_vad_result = self._vad.is_speech(audio_bytes, self.sample_rate)
                self._vad_error_count = 0  # 成功したらエラーカウントをリセット
            except Exception as e:
                self._vad_error_count += 1
                logger.error(f"VAD判定エラー（モニタリング中）: {e}, error_count={self._vad_error_count}")
                self._last_vad_result = False

                # エラーが連続する場合はVADを再初期化
                if self._vad_error_count >= self._max_vad_errors:
                    logger.warning("Too many VAD errors, reinitializing VAD")
                    try:
                        self._vad = webrtcvad.Vad(current_settings.vad_aggressiveness)
                        self._vad_error_count = 0
                    except Exception as reinit_error:
                        logger.error(f"Failed to reinitialize VAD: {reinit_error}")

        try:
            # WebRTC VADに適したフレームサイズ（20ms = 960サンプル @48kHz）
            self._monitor_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=_cb,
                dtype='int16',
                blocksize=self._vad_frame_size,
                device=current_settings.audio_device
            )
            self._monitor_stream.start()
            logger.info(f"Monitoring stream started (blocksize={self._vad_frame_size}, {self._vad_frame_duration}ms)")
        except Exception as e:
            logger.error(f"Failed to start monitor stream: {e}")
            self._monitor_stream = None

    def stop_monitoring(self):
        """Stop the monitoring stream."""
        if self._monitor_stream is not None:
            try:
                self._monitor_stream.stop()
                self._monitor_stream.close()
            except Exception as e:
                logger.error(f"Error stopping monitor stream: {e}")
            finally:
                self._monitor_stream = None
                self._last_amplitude = 0.0
                self._last_vad_result = False  # VAD状態もリセット
                logger.info("Monitoring stream stopped")

    def get_current_amplitude(self) -> float:
        """Get the current amplitude from the monitoring stream (for debugging)."""
        return self._last_amplitude

    def is_speech_detected(self) -> bool:
        """Get the current VAD result (True if speech detected)."""
        return self._last_vad_result
