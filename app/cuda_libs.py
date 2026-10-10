"""CUDA 12 ランタイムDLLの探索とロード準備

ctranslate2 (faster-whisper のバックエンド) は CUDA 12 ビルドのため
cublas64_12.dll / cublasLt64_12.dll を必要とする。CUDA Toolkit 13 系は
cublas64_13.dll しか持たないため、12 系を消すと GPU 推論だけが動かなくなる。

これらのDLLはモデル構築時ではなく最初の推論時に LoadLibrary で遅延ロード
される。LoadLibrary は PATH を検索するため、os.add_dll_directory() では
届かない。PATH そのものを書き換える必要がある。
"""
import os
import sys
import threading
from pathlib import Path

from .logger import logger

# ctranslate2 が必要とする CUDA 12 ライブラリ。
# cudart は ctranslate2 に静的リンクされているため不要。
REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")

_lock = threading.Lock()
_prepared = False


def _candidate_dirs():
    """cuBLAS 12 が置かれている可能性のあるディレクトリを優先順に返す"""
    project_root = Path(__file__).resolve().parent.parent

    # 同梱分（whisper.cpp 時代のバイナリから救出したもの）
    yield project_root / "cuda_libs"
    yield project_root / "bin"

    # pip install nvidia-cublas-cu12 で入る場所
    for site_dir in sys.path:
        if site_dir:
            yield Path(site_dir) / "nvidia" / "cublas" / "bin"

    # CUDA Toolkit 12.x が入っていればそこから
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    cuda_root = Path(program_files) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if cuda_root.is_dir():
        for version_dir in sorted(cuda_root.glob("v12.*"), reverse=True):
            yield version_dir / "bin"

    # 既に PATH が通っている場所。ここで見つかれば追加の準備は不要だが、
    # 呼び出し側が戻り値で GPU の可否を判断できるよう探索対象に含める
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if path_dir:
            yield Path(path_dir)


def ensure_cuda12_libs() -> bool:
    """cuBLAS 12 を含むディレクトリを PATH の先頭に追加する

    Returns:
        必要なDLLが見つかったかどうか。False の場合 GPU 推論は失敗するため、
        呼び出し側は GPU へのロードを試みずに CPU を使うこと。
    """
    global _prepared

    if sys.platform != "win32":
        return True

    with _lock:
        if _prepared:
            return True

        for directory in _candidate_dirs():
            try:
                if not all((directory / name).is_file() for name in REQUIRED_DLLS):
                    continue
            except OSError:
                continue

            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"CUDA 12 libraries found: {directory}")
            _prepared = True
            return True

        logger.warning(
            f"{REQUIRED_DLLS[0]} not found in any candidate directory. "
            "GPU inference is unavailable."
        )
        return False
