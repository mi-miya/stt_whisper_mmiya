import subprocess
import os
import re
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

    def transcribe(self, audio_file: str) -> str:
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

            return text

        except Exception as e:
            logger.error(f"Transcription execution error: {e}")
            return ""
