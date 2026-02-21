# 遅延インポート用（メモリ最適化）
# sounddevice, numpy, scipy は録音開始時に初めてインポートされる
import tempfile
import time
import threading
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

        # === 常時録音モード用（VADモード改善） ===
        self._continuous_stream = None      # 常時録音ストリーム
        self._ring_buffer = None            # リングバッファ (numpy array)
        self._ring_buffer_size = 0          # バッファサイズ (サンプル数)
        self._ring_buffer_duration = 65     # バッファ長（秒）- 最大録音時間 + マージン
        self._write_pos = 0                 # 書き込み位置
        self._buffer_lock = threading.Lock() # バッファアクセス用ロック
        self._speech_start_pos = -1         # 発話開始位置
        self._is_capturing = False          # 発話キャプチャ中フラグ

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

    # ========================================
    # 常時録音モード（VADモード改善版）
    # ========================================

    def start_continuous(self):
        """VADモード用常時録音ストリームを開始。

        単一のストリームで常時録音し、音声区間をリングバッファから切り出す方式。
        ストリームの切り替えによる音声の途切れを防ぐ。
        """
        if self._continuous_stream is not None:
            logger.warning("Continuous stream already running")
            return

        sd, np, _, webrtcvad = _lazy_import()

        # WebRTC VADを初期化
        self._vad = webrtcvad.Vad(current_settings.vad_aggressiveness)
        logger.info(f"WebRTC VAD initialized for continuous mode (aggressiveness={current_settings.vad_aggressiveness})")

        # リングバッファを初期化
        buffer_samples = int(self.sample_rate * self._ring_buffer_duration)
        self._ring_buffer = np.zeros(buffer_samples, dtype=np.int16)
        self._ring_buffer_size = buffer_samples
        self._write_pos = 0

        # 状態リセット
        self._speech_start_pos = -1
        self._is_capturing = False
        self._last_vad_result = False
        self._last_amplitude = 0.0
        self._vad_error_count = 0

        def _continuous_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Continuous stream status: {status}")

            # VAD判定
            audio_bytes = indata[:, 0].tobytes() if indata.ndim > 1 else indata.tobytes()
            try:
                self._last_vad_result = self._vad.is_speech(audio_bytes, self.sample_rate)
                self._vad_error_count = 0
            except Exception as e:
                self._vad_error_count += 1
                if self._vad_error_count <= 3:  # 最初の数回だけログ出力
                    logger.error(f"VAD判定エラー（常時録音中）: {e}")
                self._last_vad_result = False

                # エラーが連続する場合はVADを再初期化
                if self._vad_error_count >= self._max_vad_errors:
                    try:
                        self._vad = webrtcvad.Vad(current_settings.vad_aggressiveness)
                        self._vad_error_count = 0
                    except:
                        pass

            # 振幅更新（デバッグ用）
            self._last_amplitude = float(np.max(np.abs(indata.astype(np.float32))))

            # リングバッファに書き込み
            with self._buffer_lock:
                data = indata[:, 0] if indata.ndim > 1 else indata.flatten()
                n = len(data)

                if self._write_pos + n <= self._ring_buffer_size:
                    # バッファの終端を超えない場合
                    self._ring_buffer[self._write_pos:self._write_pos + n] = data
                else:
                    # バッファの終端を超える場合は2回に分けて書き込み
                    first_part = self._ring_buffer_size - self._write_pos
                    self._ring_buffer[self._write_pos:] = data[:first_part]
                    self._ring_buffer[:n - first_part] = data[first_part:]

                self._write_pos = (self._write_pos + n) % self._ring_buffer_size

        try:
            self._continuous_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=_continuous_callback,
                dtype='int16',
                blocksize=self._vad_frame_size,
                device=current_settings.audio_device
            )
            self._continuous_stream.start()
            logger.info(f"Continuous recording stream started (buffer={self._ring_buffer_duration}s, blocksize={self._vad_frame_size})")
        except Exception as e:
            logger.error(f"Failed to start continuous stream: {e}")
            self._continuous_stream = None
            self._ring_buffer = None

    def stop_continuous(self):
        """常時録音ストリームを停止。"""
        if self._continuous_stream is not None:
            try:
                self._continuous_stream.stop()
                self._continuous_stream.close()
            except Exception as e:
                logger.error(f"Error stopping continuous stream: {e}")
            finally:
                self._continuous_stream = None
                self._ring_buffer = None
                self._speech_start_pos = -1
                self._is_capturing = False
                self._last_amplitude = 0.0
                self._last_vad_result = False
                logger.info("Continuous recording stream stopped")

    def mark_speech_start(self):
        """発話開始位置をマーク（プリバッファ分前から）。

        VADで音声検知された時に呼び出す。
        プリバッファ分（0.5秒）前の位置を記録し、発話の冒頭が切れないようにする。
        """
        with self._buffer_lock:
            # プリバッファ分（0.5秒）前の位置を計算
            prebuffer_samples = int(self.sample_rate * self._prebuffer_duration)
            self._speech_start_pos = (self._write_pos - prebuffer_samples) % self._ring_buffer_size
            self._is_capturing = True
        logger.debug(f"Speech start marked at position {self._speech_start_pos} (prebuffer: {self._prebuffer_duration}s)")

    def mark_speech_end(self) -> str:
        """発話終了、バッファから切り出してWAVファイルとして保存。

        VADで無音検知された時に呼び出す。
        リングバッファから音声区間を切り出し、WAVファイルとして保存する。

        Returns:
            保存したWAVファイルのパス、または空文字列（無音/エラー時）
        """
        if not self._is_capturing or self._speech_start_pos < 0:
            logger.warning("mark_speech_end called but not capturing")
            return ""

        sd, np, wav, _ = _lazy_import()

        with self._buffer_lock:
            speech_end_pos = self._write_pos
            start_pos = self._speech_start_pos

            # 区間の長さを計算
            if speech_end_pos >= start_pos:
                length = speech_end_pos - start_pos
            else:
                # ラップアラウンドの場合
                length = (self._ring_buffer_size - start_pos) + speech_end_pos

            # バッファサイズを超える場合は最新のデータのみ使用
            if length > self._ring_buffer_size:
                logger.warning(f"Speech segment too long ({length} samples), truncating")
                length = self._ring_buffer_size
                start_pos = (speech_end_pos - length) % self._ring_buffer_size

            # データを切り出し
            audio_data = np.zeros(length, dtype=np.int16)
            if speech_end_pos >= start_pos:
                audio_data = self._ring_buffer[start_pos:speech_end_pos].copy()
            else:
                # ラップアラウンドの場合
                first_part = self._ring_buffer_size - start_pos
                audio_data[:first_part] = self._ring_buffer[start_pos:]
                audio_data[first_part:] = self._ring_buffer[:speech_end_pos]

            # 状態リセット
            self._speech_start_pos = -1
            self._is_capturing = False

        # 無音チェック（既存ロジックを再利用）
        float_data = audio_data.astype(np.float32)
        rms = np.sqrt(np.mean(float_data**2))
        max_amp = np.max(np.abs(float_data))

        logger.info(f"Speech segment stats: duration={length/self.sample_rate:.2f}s, RMS={rms:.2f}, Max={max_amp:.2f}")

        if max_amp < current_settings.silence_threshold:
            logger.info(f"Audio ignored: Max amplitude too low ({max_amp:.2f})")
            return ""

        MIN_RMS_THRESHOLD = 400
        if rms < MIN_RMS_THRESHOLD:
            logger.info(f"Audio ignored: RMS too low ({rms:.2f})")
            return ""

        # WAVファイル保存
        timestamp = int(time.time() * 1000)
        filename = self.temp_dir / f"rec_{timestamp}.wav"
        wav.write(str(filename), self.sample_rate, audio_data)
        logger.info(f"Saved speech segment to {filename}")

        return str(filename)

    def reset_capture_state(self):
        """キャプチャ状態のみリセット（録音時間が短い場合などに使用）。"""
        with self._buffer_lock:
            self._speech_start_pos = -1
            self._is_capturing = False
        logger.debug("Capture state reset")
