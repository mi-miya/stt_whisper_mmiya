import subprocess
import os
import re
import time
import tempfile
import threading
from pathlib import Path
from .settings import current_settings
from .logger import logger
from .error_handler import show_error


def extract_language_code(language: str) -> str:
    """言語設定から言語コードを抽出する

    "日本語 (ja)" -> "ja"
    "ja" -> "ja"
    """
    # "(xx)" パターンを探す
    match = re.search(r'\(([a-z]{2,})\)', language)
    if match:
        return match.group(1)
    # すでにコードのみの場合
    return language


class Transcriber:
    def __init__(self):
        self.cli_path = current_settings.whisper_cli_path
        self.model_path = current_settings.model_path
        self._cli_error_shown = False
        self._model_error_shown = False
        self._warmup_done = False
        self._warmup_lock = threading.Lock()
        self._last_transcribe_time = 0
        # ウォームアップ間隔（秒）: この時間以上使用されていない場合、再ウォームアップ
        self._warmup_interval = 300  # 5分

    def _create_silent_wav(self) -> str:
        """短い無音WAVファイルを作成（ウォームアップ用）"""
        try:
            import numpy as np
            import scipy.io.wavfile as wav
        except ImportError:
            # 遅延インポートが失敗した場合はNone
            return None

        # 0.5秒の無音（16kHzサンプルレート）
        sample_rate = 16000
        duration = 0.5
        samples = int(sample_rate * duration)
        audio_data = np.zeros(samples, dtype=np.int16)

        # 一時ファイルに保存
        fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix='warmup_')
        os.close(fd)
        wav.write(temp_path, sample_rate, audio_data)
        return temp_path

    def warmup(self, force: bool = False) -> bool:
        """Whisperモデルをウォームアップして初回遅延を軽減

        Args:
            force: Trueの場合、前回のウォームアップ状態を無視して強制実行

        Returns:
            ウォームアップが成功したかどうか
        """
        with self._warmup_lock:
            if self._warmup_done and not force:
                logger.debug("Warmup already done, skipping")
                return True

        start_time = time.time()
        logger.info("Starting Whisper warmup...")

        # パスチェック
        if not os.path.exists(self.cli_path):
            logger.warning(f"Warmup skipped: Whisper CLI not found at {self.cli_path}")
            return False

        if not os.path.exists(self.model_path):
            logger.warning(f"Warmup skipped: Model not found at {self.model_path}")
            return False

        # 短い無音ファイルを作成
        temp_wav = self._create_silent_wav()
        if not temp_wav:
            logger.warning("Warmup skipped: Failed to create silent WAV")
            return False

        try:
            # ウォームアップ用コマンド（最小限のオプション）
            cmd = [
                self.cli_path,
                "-m", self.model_path,
                "-f", temp_wav,
                "-l", "ja",
                "-nt"
            ]

            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                timeout=60  # ウォームアップのタイムアウト
            )

            elapsed = time.time() - start_time

            if result.returncode == 0:
                with self._warmup_lock:
                    self._warmup_done = True
                    self._last_transcribe_time = time.time()
                logger.info(f"Whisper warmup completed in {elapsed:.2f}s")
                return True
            else:
                logger.warning(f"Warmup failed with code {result.returncode}: {result.stderr[:200]}")
                return False

        except subprocess.TimeoutExpired:
            logger.warning("Warmup timed out")
            return False
        except Exception as e:
            logger.warning(f"Warmup error: {e}")
            return False
        finally:
            # 一時ファイルを削除
            try:
                if temp_wav and os.path.exists(temp_wav):
                    os.remove(temp_wav)
            except:
                pass

    def _check_and_warmup(self):
        """必要に応じてウォームアップを実行（長時間アイドル時）"""
        current_time = time.time()
        if self._last_transcribe_time > 0:
            elapsed = current_time - self._last_transcribe_time
            if elapsed > self._warmup_interval:
                logger.info(f"Re-warming up after {elapsed:.0f}s idle")
                self.warmup(force=True)

    def transcribe(self, audio_file: str) -> str:
        # 長時間アイドル後の再ウォームアップチェック
        self._check_and_warmup()

        if not os.path.exists(self.cli_path):
            logger.error(f"Whisper CLI not found at {self.cli_path}")
            if not self._cli_error_shown:
                self._cli_error_shown = True
                show_error("whisper_not_found", self.cli_path)
            return ""

        if not os.path.exists(self.model_path):
            logger.error(f"Model file not found at {self.model_path}")
            if not self._model_error_shown:
                self._model_error_shown = True
                show_error("model_not_found", self.model_path)
            return ""

        # Build command: whisper-cli -m model -f wav -l lang -nt
        # -nt: no timestamps (output just text)
        language_code = extract_language_code(current_settings.language)
        cmd = [
            self.cli_path,
            "-m", self.model_path,
            "-f", audio_file,
            "-l", language_code,
            "-nt"
        ]

        if current_settings.initial_prompt:
             cmd.extend(["--prompt", current_settings.initial_prompt])

        if current_settings.carry_initial_prompt:
             cmd.append("--carry-initial-prompt")

        if current_settings.best_of != 5:
             cmd.extend(["--best-of", str(current_settings.best_of)])

        if current_settings.beam_size != 5:
             cmd.extend(["--beam-size", str(current_settings.beam_size)])

        if current_settings.temperature != 0.0:
             cmd.extend(["--temperature", str(current_settings.temperature)])

        logger.info(f"Running transcription: {' '.join(cmd)}")

        try:
            # Run subprocess
            # startupinfo to hide console window on Windows
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo
            )

            if result.returncode != 0:
                logger.error(f"Transcription process failed with code {result.returncode}")
                logger.error(f"Stderr: {result.stderr}")
                # GPU メモリエラーの検出
                if "CUDA" in result.stderr or "GPU" in result.stderr or "memory" in result.stderr.lower():
                    show_error("gpu_error", result.stderr[:200])
                else:
                    show_error("transcription_failed", f"Exit code: {result.returncode}")
                return ""

            # Retrieve output.
            # whisper-cli typically outputs to stdout with -nt.
            text = result.stdout.strip().replace(" ", "")

            # Some versions might output logs to stdout or stderr.
            # If text is empty, check stderr just in case or if output file was generated.
            # For now assume stdout has the text.

            if not text:
                logger.warning("Transcription returned empty text")
                if result.stderr:
                    logger.warning(f"Stderr content: {result.stderr}")
            else:
                logger.info(f"Transcribed length: {len(text)}")

            # 最後の文字起こし時刻を更新（ウォームアップ判定用）
            self._last_transcribe_time = time.time()

            return text

        except Exception as e:
            logger.error(f"Transcription execution error: {e}")
            return ""
