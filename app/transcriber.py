import re
import threading
import time
from .settings import current_settings
from .logger import logger
from .error_handler import show_error
from .cuda_libs import ensure_cuda12_libs

_LANG_CODE_RE = re.compile(r'\(([a-z]{2,})\)')

# GPU 起因と判断してCPUフォールバックさせる例外メッセージの手がかり。
# CUDA ランタイムDLLの欠落は "CUDA" も "out of memory" も含まないため、
# ライブラリ名でも拾う必要がある
_GPU_ERROR_HINTS = ("cuda", "cublas", "cudnn", "out of memory", "gpu")


def is_gpu_error(message: str) -> bool:
    """例外メッセージが GPU 起因かどうかを判定する"""
    lowered = message.lower()
    return any(hint in lowered for hint in _GPU_ERROR_HINTS)


def extract_language_code(language: str) -> str:
    """言語設定から言語コードを抽出する

    "日本語 (ja)" -> "ja"
    "ja" -> "ja"
    """
    match = _LANG_CODE_RE.search(language)
    if match:
        return match.group(1)
    return language


class Transcriber:
    def __init__(self):
        self._model = None
        self._device = None
        self._warmup_lock = threading.Lock()
        # モデルが使える状態になったら立つ
        self._ready_event = threading.Event()
        # ロードの成否が確定したら立つ。失敗時に _ready_event を待ち続けて
        # タイムアウトまで固まらないよう、待機側はこちらを待つ
        self._settled_event = threading.Event()

    def _parse_device(self) -> tuple:
        """device設定からfaster-whisper用の (device, device_index) を返す"""
        device_setting = current_settings.device
        if device_setting == "auto":
            try:
                import ctranslate2
                ctranslate2.get_supported_compute_types("cuda")
            except Exception:
                return ("cpu", 0)
            # 自動検出では、cuBLAS が無ければエラーにせず静かにCPUを使う
            if not ensure_cuda12_libs():
                logger.warning("CUDA 12 libraries not found, using CPU (device=auto)")
                return ("cpu", 0)
            return ("cuda", 0)
        if device_setting.startswith("cuda"):
            parts = device_setting.split(":")
            index = int(parts[1]) if len(parts) > 1 else 0
            return ("cuda", index)
        return ("cpu", 0)

    def _build_transcribe_kwargs(self) -> tuple:
        """transcribe() に渡す language と kwargs を組み立てる"""
        lang = extract_language_code(current_settings.language)
        language = lang if lang != "auto" else None

        # faster-whisper は省略時 beam_size=5、temperature=[0.0〜1.0] の
        # フォールバック探索が既定値のため、設定値を必ず明示的に渡す
        kwargs = {
            "beam_size": current_settings.beam_size,
            "temperature": current_settings.temperature,
            # 発話ごとに独立した文字起こしを行うため前セグメントへの
            # 条件付けは無効化する（繰り返しハルシネーション対策も兼ねる）
            "condition_on_previous_text": False,
        }
        if current_settings.initial_prompt:
            kwargs["initial_prompt"] = current_settings.initial_prompt

        return language, kwargs

    def _warmup_inference(self) -> None:
        """本番と同じコードパスを温める。

        - 本番と同じ numpy 配列直接渡しで推論パスを温める
        - 本番と同じ initial_prompt / beam_size / temperature を使い
          CUDA カーネルの autotune を済ませる
        - 録音側ライブラリ (sounddevice / scipy) も同時にロードして
          初回ホットキー押下時のレイテンシを消す
        """
        from .recorder import _lazy_import

        _sd, np_mod, _wav = _lazy_import()

        sample_rate = 16000
        # 完全な無音だと内部で短絡して推論パスが温まらないため、
        # 低振幅のホワイトノイズを 1 秒生成する
        audio = (np_mod.random.randn(sample_rate) * (50.0 / 32768.0)).astype(np_mod.float32)

        language, kwargs = self._build_transcribe_kwargs()
        segments, _ = self._model.transcribe(audio, language=language, **kwargs)
        for _ in segments:
            pass

    def _load_model(self, device: str, device_index: int, compute_type: str) -> bool:
        """モデルをロードしウォームアップ推論を実行する"""
        if device == "cuda":
            # cuBLAS は最初の推論時に遅延ロードされるため、モデル構築より
            # 前に PATH を通しておく必要がある。見つからない場合はGPUへ
            # ロードしても最初の推論で必ず失敗するため、ロード前に打ち切る
            if not ensure_cuda12_libs():
                raise RuntimeError("cublas64_12.dll が見つかりません (CUDA 12 ランタイムが必要です)")

        from faster_whisper import WhisperModel

        logger.info(f"Loading model: {current_settings.model_name} on {device} with {compute_type}")

        self._model = WhisperModel(
            current_settings.model_name,
            device=device,
            device_index=device_index,
            compute_type=compute_type,
        )
        self._device = device

        self._warmup_inference()
        return True

    def warmup(self) -> bool:
        """Whisperモデルをロードしてウォームアップ

        既にロード済みなら何もしない。前回のロードが失敗していた場合は
        ロードし直す。

        Returns:
            モデルが使える状態になったかどうか
        """
        with self._warmup_lock:
            if self._ready_event.is_set():
                logger.debug("Warmup already done, skipping")
                return True

            self._settled_event.clear()

            start_time = time.time()
            logger.info("Starting Whisper model warmup...")

            device, device_index = self._parse_device()
            compute_type = current_settings.compute_type
            # CPUではfloat16系が使えない。float32へ落とすとint8の数倍遅いため、
            # ユーザーが明示的にfloat32を選んだ場合以外はint8を使う
            if device == "cpu" and compute_type not in ("int8", "float32"):
                compute_type = "int8"

            gpu_error_msg = None
            try:
                try:
                    self._load_model(device, device_index, compute_type)
                except Exception as e:
                    # ダウンロード失敗などGPUと無関係な原因でCPUへ切り替えても
                    # 同じ失敗を繰り返すだけなので、GPU起因の場合に限る
                    if device != "cuda" or not is_gpu_error(str(e)):
                        raise
                    gpu_error_msg = str(e)
                    logger.error(f"Warmup error: {gpu_error_msg}")
                    logger.warning("GPU error detected, falling back to CPU...")
                    self._load_model("cpu", 0, "int8")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Warmup error: {error_msg}")
                self._mark_load_failed()
                show_error("pipeline_not_loaded", error_msg[:200])
                return False

            self._ready_event.set()
            self._settled_event.set()
            elapsed = time.time() - start_time
            logger.info(f"Whisper model warmup completed in {elapsed:.2f}s (device={self._device})")
            if gpu_error_msg:
                show_error("gpu_error", f"CPUモードで動作しています。\n{gpu_error_msg[:200]}")
            return True

    def _mark_load_failed(self) -> None:
        """モデルが使えない状態であることを記録し、待機中のスレッドを解放する"""
        self._model = None
        self._device = None
        self._ready_event.clear()
        self._settled_event.set()

    def is_ready(self) -> bool:
        """モデルのロードが完了しているかどうか"""
        return self._ready_event.is_set()

    def wait_until_ready(self, timeout: float = 120) -> bool:
        """モデルのロード完了を待つ。ロードが失敗した場合は待たずに False を返す"""
        self._settled_event.wait(timeout=timeout)
        return self._ready_event.is_set()

    def ensure_ready(self, timeout: float = 120) -> bool:
        """モデルが使える状態になるまで待つ

        ロードが失敗していた場合は1回だけロードし直す（ネットワーク断などが
        解消していれば、アプリを再起動しなくても復旧できる）。
        """
        if self.wait_until_ready(timeout):
            return True

        if not self._settled_event.is_set():
            logger.error("Model is still loading after waiting.")
            show_error("pipeline_not_loaded")
            return False

        logger.info("Previous model load failed, retrying...")
        return self.warmup()

    def _run_transcription(self, audio, language, kwargs: dict) -> str:
        segments, _ = self._model.transcribe(audio, language=language, **kwargs)
        return "".join(segment.text for segment in segments).strip().replace("\u3000", "")

    def transcribe(self, audio) -> str:
        """音声データ (float32 / 16kHz の numpy 配列) を文字起こしする"""
        if not self.ensure_ready():
            return ""

        language, kwargs = self._build_transcribe_kwargs()

        logger.info(f"Running transcription: model={current_settings.model_name}")

        try:
            text = self._run_transcription(audio, language, kwargs)
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Transcription error: {error_msg}")

            if self._device == "cuda" and is_gpu_error(error_msg):
                text = self._retry_on_cpu(audio, language, kwargs, error_msg)
            else:
                show_error("transcription_failed", error_msg[:200])
                return ""

        if not text:
            logger.warning("Transcription returned empty text")
        else:
            logger.info(f"Transcribed length: {len(text)}")

        return text

    def _retry_on_cpu(self, audio, language, kwargs: dict, gpu_error_msg: str) -> str:
        """GPUエラー時にCPUへ切り替え、同じ音声をもう一度文字起こしする

        話した内容を捨てずに済むよう、切り替え後に再実行する。
        """
        logger.warning("GPU error during transcription, falling back to CPU...")
        try:
            self._load_model("cpu", 0, "int8")
        except Exception as e:
            logger.error(f"CPU fallback also failed: {e}")
            # モデルが無い状態で残さない。次回の文字起こし時にロードし直す
            self._mark_load_failed()
            show_error("gpu_error", gpu_error_msg[:200])
            return ""

        show_error("gpu_error", f"CPUモードに切り替えました。\n{gpu_error_msg[:200]}")

        try:
            return self._run_transcription(audio, language, kwargs)
        except Exception as e:
            logger.exception(f"Transcription error on CPU fallback: {e}")
            show_error("transcription_failed", str(e)[:200])
            return ""

    def cleanup(self):
        """モデルを解放"""
        logger.info("Cleaning up transcriber resources...")
        self._model = None
        self._ready_event.clear()
        self._settled_event.clear()
