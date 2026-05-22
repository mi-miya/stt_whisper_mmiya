import os
import re
import tempfile
import threading
import numpy as np
from .settings import current_settings
from .logger import logger
from .error_handler import show_error

_LANG_CODE_RE = re.compile(r'\(([a-z]{2,})\)')


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
        self._warmup_done = False
        self._warmup_lock = threading.Lock()
        self._ready_event = threading.Event()

    def _parse_device(self) -> tuple:
        """device設定からfaster-whisper用の (device, device_index) を返す"""
        device_setting = current_settings.device
        if device_setting == "auto":
            try:
                import ctranslate2
                ctranslate2.get_supported_compute_types("cuda")
                return ("cuda", 0)
            except Exception:
                return ("cpu", 0)
        if device_setting.startswith("cuda"):
            parts = device_setting.split(":")
            index = int(parts[1]) if len(parts) > 1 else 0
            return ("cuda", index)
        return ("cpu", 0)

    def _build_transcribe_kwargs(self) -> tuple:
        """transcribe() に渡す language と kwargs を組み立てる"""
        lang = extract_language_code(current_settings.language)
        language = lang if lang != "auto" else None

        kwargs = {}
        if current_settings.initial_prompt:
            kwargs["initial_prompt"] = current_settings.initial_prompt
        if current_settings.beam_size != 1:
            kwargs["beam_size"] = current_settings.beam_size
        if current_settings.temperature > 0.0:
            kwargs["temperature"] = current_settings.temperature

        return language, kwargs

    def _warmup_inference(self) -> None:
        """本番と同じコードパスを温める。

        - WAV ファイル経由で PyAV のオーディオデコーダ初期化を済ませる
        - 本番と同じ initial_prompt / beam_size / temperature を使い
          CUDA カーネルの autotune を済ませる
        - 録音側ライブラリ (sounddevice / scipy) も同時にロードして
          初回ホットキー押下時のレイテンシを消す
        """
        from .recorder import _lazy_import

        _sd, np_mod, wav = _lazy_import()

        sample_rate = 16000
        # 完全な無音だと内部で短絡してデコーダ/デコーダパスが温まらないため、
        # 低振幅のホワイトノイズを 1 秒生成する
        n_samples = sample_rate
        audio = (np_mod.random.randn(n_samples) * 50).astype(np_mod.int16)

        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="warmup_")
        os.close(fd)
        try:
            wav.write(tmp_path, sample_rate, audio)
            language, kwargs = self._build_transcribe_kwargs()
            segments, _ = self._model.transcribe(tmp_path, language=language, **kwargs)
            for _ in segments:
                pass
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _load_model(self, device: str, device_index: int, compute_type: str) -> bool:
        """モデルをロードしウォームアップ推論を実行する"""
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

    def warmup(self, force: bool = False) -> bool:
        """Whisperモデルをロードしてウォームアップ

        Returns:
            ウォームアップが成功したかどうか
        """
        with self._warmup_lock:
            if self._warmup_done and not force:
                logger.debug("Warmup already done, skipping")
                return True

            if force:
                self._ready_event.clear()

            import time

            start_time = time.time()
            logger.info("Starting Whisper model warmup...")

            device, device_index = self._parse_device()
            compute_type = current_settings.compute_type
            if device == "cpu" and compute_type != "float32":
                compute_type = "float32"

            try:
                self._load_model(device, device_index, compute_type)
                elapsed = time.time() - start_time
                self._warmup_done = True
                self._ready_event.set()
                logger.info(f"Whisper model warmup completed in {elapsed:.2f}s (device={device})")
                return True

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Warmup error: {error_msg}")

                if device == "cuda":
                    logger.warning("GPU error detected, falling back to CPU...")
                    self._model = None
                    try:
                        self._load_model("cpu", 0, "float32")
                        elapsed = time.time() - start_time
                        self._warmup_done = True
                        self._ready_event.set()
                        logger.info(f"Whisper model warmup completed on CPU fallback in {elapsed:.2f}s")
                        show_error("gpu_error", "GPUメモリ不足のため、CPUモードで動作しています。")
                        return True
                    except Exception as fallback_e:
                        logger.error(f"CPU fallback also failed: {fallback_e}")

                return False

    def transcribe(self, audio_file: str) -> str:
        """音声ファイルを文字起こしする"""
        if not self._ready_event.wait(timeout=120):
            logger.error("Model not loaded after waiting. Warmup may have failed.")
            show_error("pipeline_not_loaded")
            return ""

        language, kwargs = self._build_transcribe_kwargs()

        logger.info(f"Running transcription: model={current_settings.model_name}")

        try:
            segments, info = self._model.transcribe(
                audio_file,
                language=language,
                **kwargs,
            )
            text = "".join(segment.text for segment in segments).strip().replace("\u3000", "")

            if not text:
                logger.warning("Transcription returned empty text")
            else:
                logger.info(f"Transcribed length: {len(text)}")

            return text

        except Exception as e:
            import traceback
            error_msg = str(e)
            logger.error(f"Transcription error: {error_msg}")
            logger.error(f"Traceback:\n{traceback.format_exc()}")

            if "CUDA" in error_msg or "out of memory" in error_msg.lower():
                logger.warning("GPU OOM during transcription, falling back to CPU...")
                try:
                    self._model = None
                    self._load_model("cpu", 0, "float32")
                    show_error("gpu_error", "GPUメモリ不足のため、CPUモードに切り替えました。")
                except Exception:
                    show_error("gpu_error", error_msg[:200])
            else:
                show_error("transcription_failed", error_msg[:200])

            return ""

    def cleanup(self):
        """モデルを解放"""
        logger.info("Cleaning up transcriber resources...")
        self._model = None
        self._warmup_done = False
        self._ready_event.clear()
