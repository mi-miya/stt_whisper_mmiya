# 遅延インポート用（メモリ最適化）
# sounddevice, numpy, scipy は録音開始時に初めてインポートされる
import ctypes
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Optional
from .settings import current_settings
from .logger import logger
from .error_handler import show_error

# Whisperが要求するサンプルレート
WHISPER_SAMPLE_RATE = 16000

# 常時通話モードで、ホットキーを押す前の音をどれだけさかのぼって録音に含めるか
PRE_ROLL_SECONDS = 1.0
# 開いたままのマイクを見張る間隔と、音が届かなくなったら開き直すまでの時間
WATCH_INTERVAL = 2.0
STALL_SECONDS = 3.0

_WAVE_MAPPER = 0xFFFFFFFF
_DRVM_MAPPER_PREFERRED_GET = 0x2000 + 21


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]

# グローバル変数（遅延インポート後に設定）
_sd = None
_np = None
_wav = None


def _lazy_import():
    """numpy, sounddevice, scipy を遅延インポート"""
    global _sd, _np, _wav
    if _sd is None:
        import sounddevice as sd
        import numpy as np
        import scipy.io.wavfile as wav
        _sd = sd
        _np = np
        _wav = wav
        logger.info("Audio libraries loaded (lazy import)")
    return _sd, _np, _wav


@dataclass
class Capture:
    """1回分の録音。デバッグ履歴に残すため、前処理前の音声と判定の材料も持つ"""
    raw: Any  # マイクから入ったままの int16 音声 (sample_rate)
    sample_rate: int
    started_at: float
    rms: float
    max_amp: float
    # Whisper にそのまま渡せる float32 / 16kHz の音声。無音と判定したら None
    audio: Any = None
    skipped_reason: Optional[str] = None
    preprocess_info: dict = field(default_factory=dict)


def _default_input_name() -> Optional[str]:
    """Windows の既定の録音デバイスの名前 (MME の名前、31文字まで)。取れなければ None"""
    try:
        winmm = ctypes.windll.winmm
        dev = wintypes.DWORD()
        flags = wintypes.DWORD()
        winmm.waveInMessage.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
        if winmm.waveInMessage(_WAVE_MAPPER, _DRVM_MAPPER_PREFERRED_GET,
                               ctypes.byref(dev), ctypes.byref(flags)) != 0:
            return None
        caps = _WAVEINCAPSW()
        if winmm.waveInGetDevCapsW(dev.value, ctypes.byref(caps), ctypes.sizeof(caps)) != 0:
            return None
        return caps.szPname
    except Exception as e:
        logger.warning(f"Failed to get default input device: {e}")
        return None


def _refresh_devices():
    """PortAudio のデバイス一覧を読み直す（イヤホンの抜き差しを反映するため）"""
    sd, _, _ = _lazy_import()
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        logger.warning(f"Failed to refresh audio devices: {e}")


class Recorder:
    def __init__(self):
        self.frames = []
        self.stream = None
        self.sample_rate = current_settings.sample_rate
        self.channels = 1
        self.is_recording = False
        self.started_at = 0.0

        # frames と、押す前の音 (_preroll) を、音声のスレッドと取り合わないための鍵
        self._lock = threading.Lock()
        # マイクを開く・閉じる・開き直す操作を、1つずつ行うための鍵
        self._stream_lock = threading.RLock()

        # 常時通話モード
        self._keep_open = False
        self._preroll = deque()
        self._preroll_len = 0
        self._last_callback = 0.0
        self._opened_default = None
        self._watch_thread = None
        self._open_failed_logged = False

    def callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"Audio status: {status}")
        self._last_callback = time.monotonic()
        chunk = indata.copy()
        with self._lock:
            if self.is_recording:
                self.frames.append(chunk)
            elif self._keep_open:
                # 録音していない間は、直近の PRE_ROLL_SECONDS 秒だけを持っておく
                self._preroll.append(chunk)
                self._preroll_len += len(chunk)
                limit = int(self.sample_rate * PRE_ROLL_SECONDS)
                while self._preroll and self._preroll_len - len(self._preroll[0]) >= limit:
                    self._preroll_len -= len(self._preroll.popleft())

    def _open_stream(self):
        sd, _, _ = _lazy_import()
        # 開いた直後は音が届くまで時間がかかるので、その間を「止まった」と見なさない
        self._last_callback = time.monotonic()
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=self.callback,
            dtype='int16',
            device=current_settings.audio_device
        )
        stream.start()
        self.stream = stream
        if self._keep_open:
            self._opened_default = _default_input_name()

    def _close_stream(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            logger.warning(f"Failed to close audio stream: {e}")

    # --- 常時通話モード ---

    def set_keep_open(self, enabled: bool):
        """常時通話モードを切り替える。オンの間はマイクを開いたままにする"""
        with self._stream_lock:
            self._keep_open = enabled
            if enabled:
                if self.stream is None:
                    self._try_open_kept()
                if self._watch_thread is None or not self._watch_thread.is_alive():
                    self._watch_thread = threading.Thread(target=self._watch, daemon=True)
                    self._watch_thread.start()
            else:
                with self._lock:
                    self._preroll.clear()
                    self._preroll_len = 0
                # 録音中なら、止めるときに閉じる
                if not self.is_recording:
                    self._close_stream()
                logger.info("Always-on microphone turned off")

    def _try_open_kept(self) -> bool:
        try:
            self._open_stream()
            logger.info(f"Microphone kept open (always-on call mode): {self._opened_default}")
            self._open_failed_logged = False
            return True
        except Exception as e:
            # マイクが無い間は見張りが何度も試すので、ログは最初の1回だけ
            if not self._open_failed_logged:
                logger.warning(f"Failed to keep microphone open (will retry): {e}")
                self._open_failed_logged = True
            self._close_stream()
            return False

    def _watch(self):
        """マイクが止まったり、既定のマイクが変わったりしたら開き直す"""
        while self._keep_open:
            time.sleep(WATCH_INTERVAL)
            try:
                self._check_kept_stream()
            except Exception as e:
                logger.error(f"Microphone watch failed: {e}")

    def _check_kept_stream(self):
        with self._stream_lock:
            if not self._keep_open:
                return
            reason = None
            if self.stream is None:
                reason = "not open"
            elif time.monotonic() - self._last_callback > STALL_SECONDS:
                # イヤホンの電源を切った、PC がスリープから戻った、など
                reason = "no audio arriving"
            elif not self.is_recording and current_settings.audio_device is None:
                # イヤホンをつなぎ直すと、既定のマイクが入れ替わる
                now_default = _default_input_name()
                if now_default and now_default != self._opened_default:
                    reason = f"default input changed: {self._opened_default} -> {now_default}"
            if reason is None:
                return
            if self.stream is not None:
                logger.info(f"Reopening microphone ({reason})")
            self._close_stream()
            _refresh_devices()
            self._try_open_kept()

    def close(self):
        """終了時に呼ぶ"""
        with self._stream_lock:
            self._keep_open = False
            self._close_stream()

    # --- 録音 ---

    def start(self) -> bool:
        """録音を始める。マイクを開けなかったら False を返す"""
        if self.is_recording:
            logger.warning("Attempted to start recording while already recording")
            return False

        try:
            with self._stream_lock:
                if self._keep_open and self.stream is not None:
                    # マイクは開いたままなので、押す前の音を頭に付けて、すぐに録音を始める
                    with self._lock:
                        self.frames = list(self._preroll)
                        preroll = self._preroll_len
                        self._preroll.clear()
                        self._preroll_len = 0
                        self.is_recording = True
                    self.started_at = time.time() - preroll / self.sample_rate
                    logger.info(f"Recording started (kept-open mic, {preroll / self.sample_rate:.2f}s pre-roll)")
                    return True

                with self._lock:
                    self.frames = []
                    self.is_recording = True
                opening = time.monotonic()
                self._open_stream()
                open_seconds = time.monotonic() - opening
                self.started_at = time.time()
            logger.info(f"Recording started (mic opened in {open_seconds:.2f}s)")
            return True
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            with self._lock:
                self.is_recording = False
            self._close_stream()
            show_error("recording_failed", str(e))
            return False

    def stop(self, discard=False):
        """録音を停止し、Capture を返す。破棄・失敗時は None を返す。

        無音と判定した場合は、Capture.audio を None にして理由を入れる。

        WAVファイルへの書き出しは行わない（ディスクI/Oとデコードの
        往復を省き、文字起こし開始までのレイテンシを削減するため）。
        """
        if not self.is_recording:
            return None

        try:
            # 遅延インポート
            sd, np, wav = _lazy_import()

            with self._stream_lock:
                if not self._keep_open:
                    # 先に閉じて、届きかけの音まで frames に入れ切る
                    self._close_stream()
                with self._lock:
                    self.is_recording = False
                    frames = self.frames
                    self.frames = []
            logger.info("Recording stopped")

            if discard:
                logger.info("Recording discarded")
                return None

            if not frames:
                logger.warning("No frames recorded")
                return None

            recording = np.concatenate(frames, axis=0)

            # --- VAD Check ---
            # Calculate RMS amplitude
            # frames are int16, so values are between -32768 and 32767
            # We want to check if the audio is mostly silence

            # Simple RMS of the entire clip
            float_data = recording.astype(np.float32)
            rms = np.sqrt(np.mean(float_data**2))
            max_amp = np.max(np.abs(float_data))

            logger.info(f"Audio Stats: RMS={rms:.2f}, Max={max_amp:.2f}")

            capture = Capture(
                raw=recording.reshape(-1),
                sample_rate=self.sample_rate,
                started_at=self.started_at,
                rms=float(rms),
                max_amp=float(max_amp),
            )

            # 最大振幅チェック
            if max_amp < current_settings.silence_threshold:
                logger.info(f"Audio ignored: Max amplitude too low ({max_amp:.2f} < {current_settings.silence_threshold})")
                capture.skipped_reason = "max_amp_below_silence_threshold"
                return capture

            # RMSチェック：環境ノイズレベルを除外（人の声のRMSは通常600以上、キーボード音は200-300程度）
            noise_floor = current_settings.noise_floor
            if rms < noise_floor:
                logger.info(f"Audio ignored: RMS too low ({rms:.2f} < {noise_floor}), likely background noise or keyboard")
                capture.skipped_reason = "rms_below_noise_floor"
                return capture

            # -----------------

            # int16 -> float32 [-1.0, 1.0] に正規化し、1次元モノラルにする
            audio = (float_data / 32768.0).reshape(-1)

            # Whisperは16kHz前提のため、異なる場合はリサンプリング
            if self.sample_rate != WHISPER_SAMPLE_RATE:
                from scipy.signal import resample_poly
                audio = resample_poly(audio, WHISPER_SAMPLE_RATE, self.sample_rate).astype(np.float32)
                logger.info(f"Resampled audio from {self.sample_rate}Hz to {WHISPER_SAMPLE_RATE}Hz")

            from .audio_preprocess import preprocess
            capture.audio, capture.preprocess_info = preprocess(
                audio,
                WHISPER_SAMPLE_RATE,
                highpass_hz=current_settings.highpass_hz,
                normalize=current_settings.normalize_audio,
            )
            return capture
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            return None
