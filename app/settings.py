import json
import os
import shutil
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ConfigDict, ValidationError
from .logger import logger


# 共有定数: モデル、言語、デバイス、計算精度
AVAILABLE_MODELS = [
    ("large-v3-turbo", "高精度・高速・推奨", "~800 MB"),
    ("large-v3", "最高精度", "~1.5 GB"),
    ("medium", "バランス型", "~800 MB"),
    ("small", "高速・軽量", "~250 MB"),
    ("base", "最軽量", "~75 MB"),
]

LANGUAGES = [
    ("日本語", "ja"),
    ("英語", "en"),
    ("中国語", "zh"),
    ("韓国語", "ko"),
    ("自動検出", "auto"),
]

DEVICE_OPTIONS = [
    ("auto (自動検出)", "auto"),
    ("cuda:0 (GPU)", "cuda:0"),
    ("cpu (CPUのみ)", "cpu"),
]

COMPUTE_TYPE_OPTIONS = [
    ("float16 (高速・推奨)", "float16"),
    ("int8_float16 (最速・省メモリ)", "int8_float16"),
    ("float32 (互換性重視)", "float32"),
]

# 非推奨フィールド（保存時に自動削除）
_DEPRECATED_KEYS = ['whisper_cli_path', 'model_path', 'n_gpu_layers', 'best_of', 'carry_initial_prompt', 'batch_size', 'chunk_length_s']

# 旧モデルパス/HuggingFace IDから faster-whisper モデル名へのマッピング
MODEL_MIGRATION_MAP = {
    "ggml-large-v3-turbo": "large-v3-turbo",
    "ggml-large-v3": "large-v3",
    "ggml-large": "large-v3",
    "ggml-medium": "medium",
    "ggml-small": "small",
    "ggml-base": "base",
    "ggml-tiny": "tiny",
}

HF_MODEL_MIGRATION_MAP = {
    "openai/whisper-large-v3-turbo": "large-v3-turbo",
    "openai/whisper-large-v3": "large-v3",
    "openai/whisper-medium": "medium",
    "openai/whisper-small": "small",
    "openai/whisper-base": "base",
    "openai/whisper-tiny": "tiny",
}


def migrate_config(data: dict) -> dict:
    """旧形式の config.json を新形式にマイグレーションする"""
    migrated = data.copy()

    # model_path → model_name (whisper.cpp 形式からの移行)
    if 'model_path' in migrated and 'model_name' not in migrated:
        old_path = migrated.pop('model_path', '')
        filename = Path(old_path).stem  # e.g. "ggml-large-v3-turbo"
        model_name = "large-v3-turbo"  # デフォルト
        for key, value in MODEL_MIGRATION_MAP.items():
            if key in filename:
                model_name = value
                break
        migrated['model_name'] = model_name
        logger.info(f"Migrated model_path '{old_path}' -> model_name '{model_name}'")

    # HuggingFace モデルID → faster-whisper 短縮名
    if 'model_name' in migrated and migrated['model_name'] in HF_MODEL_MIGRATION_MAP:
        old_name = migrated['model_name']
        migrated['model_name'] = HF_MODEL_MIGRATION_MAP[old_name]
        logger.info(f"Migrated HF model '{old_name}' -> '{migrated['model_name']}'")

    # n_gpu_layers → device
    if 'n_gpu_layers' in migrated and 'device' not in migrated:
        gpu_layers = migrated.pop('n_gpu_layers', 0)
        migrated['device'] = 'auto' if gpu_layers > 0 else 'cpu'
        logger.info(f"Migrated n_gpu_layers={gpu_layers} -> device='{migrated['device']}'")

    # 不要になったフィールドを削除
    for old_key in _DEPRECATED_KEYS:
        if old_key in migrated:
            migrated.pop(old_key)
            logger.info(f"Removed deprecated config key: {old_key}")

    return migrated


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hotkey: str = "<ctrl>+<alt>+<shift>+j"
    language: str = "ja"
    model_name: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "float16"
    auto_paste: bool = True
    keep_trailing_newline: bool = False
    temp_dir: str = ""
    audio_device: Optional[int] = None
    sample_rate: int = 16000
    initial_prompt: str = "日本語の音声認識を行います。以下の文章は日本語で、日常会話やビジネス文書の内容です。句読点や改行を適切に挿入してください。専門用語や固有名詞は正確に認識してください。"
    sound_enabled: bool = True
    beam_size: int = 1
    temperature: float = 0.0
    silence_threshold: int = 500  # 発話/無音の判定閾値
    noise_floor: int = 200  # この振幅以下の音は無視
    widget_x: Optional[int] = None  # フローティングアイコンの保存位置 (未設定なら画面左下)
    widget_y: Optional[int] = None

    def resolve_paths(self):
        # Resolve relative paths
        if self.temp_dir:
            self.temp_dir = str(Path(self.temp_dir).resolve())

# 起動時の設定読み込みで問題があった場合の、ユーザー向けの説明。
# 読み込み時点ではまだウィンドウが無いため、表示は main 側で行う
config_load_error: Optional[str] = None


def _read_config(config_path: Path) -> dict:
    """設定ファイルを読み、マイグレーション済みの辞書を返す。読めなければ例外を送出する"""
    # メモ帳などで保存すると付くBOMを許容するため utf-8-sig で読む
    with open(config_path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    # 旧形式からのマイグレーション
    return migrate_config(data)


def _backup_broken_config(config_path: Path) -> Optional[Path]:
    """読めない設定ファイルを .bak に退避する

    この後の保存で既定値に上書きされても、手で直せば復旧できるようにする。
    """
    backup_path = config_path.with_name(config_path.name + ".bak")
    try:
        shutil.copy2(config_path, backup_path)
        logger.warning(f"Unreadable config backed up to {backup_path}")
        return backup_path
    except OSError as e:
        logger.error(f"Failed to back up config: {e}")
        return None


def load_settings(path: str = "config.json") -> Settings:
    global config_load_error

    config_path = Path(path)
    if not config_path.exists():
        logger.warning(f"Config file {path} not found. Using defaults.")
        return Settings()

    try:
        data = _read_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        backup_path = _backup_broken_config(config_path)
        config_load_error = f"{config_path.name} を読み取れないため、既定の設定で起動しました。"
        if backup_path:
            config_load_error += f"\n元のファイルは {backup_path.name} に退避しました。"
        return Settings()

    try:
        settings = Settings(**data)
    except ValidationError as e:
        # 不正な項目が1つあるだけで全設定を既定値に戻さないよう、
        # 問題のある項目だけを既定値にする
        bad_keys = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
        logger.error(f"Invalid settings ignored ({', '.join(bad_keys)}): {e}")
        settings = Settings(**{k: v for k, v in data.items() if k not in bad_keys})
        config_load_error = f"次の項目の値が不正なため、既定値を使っています: {', '.join(bad_keys)}"

    settings.resolve_paths()
    logger.info("Settings loaded successfully")
    return settings


def save_settings(settings_dict: dict, path: str = "config.json") -> bool:
    """設定をJSONファイルに保存する

    Args:
        settings_dict: 保存する設定の辞書
        path: 保存先のパス

    Returns:
        成功した場合はTrue、失敗した場合はFalse
    """
    config_path = Path(path)
    # 書き込み途中で落ちても元のファイルが空や書きかけにならないよう、
    # 一時ファイルに書き切ってから置き換える
    tmp_path = config_path.with_name(config_path.name + ".tmp")

    try:
        save_dict = {k: v for k, v in settings_dict.items() if k not in _DEPRECATED_KEYS}

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(save_dict, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)

        logger.info(f"Settings saved to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_settings_as_dict(path: str = "config.json") -> dict:
    """設定を辞書として読み込む（ダイアログ用）"""
    config_path = Path(path)
    if not config_path.exists():
        # デフォルト設定を返す
        return Settings().model_dump()

    try:
        return _read_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load settings as dict: {e}")
        # 呼び出し側がこの既定値を保存しても元の内容を失わないようにする
        _backup_broken_config(config_path)
        return Settings().model_dump()


def update_settings(changes: dict, path: str = "config.json") -> bool:
    """設定ファイルの一部の項目だけを書き換える

    既存のファイルが読めない場合は書き込まない（壊れた、あるいは編集途中の
    ファイルを、ユーザーの操作なしに既定値で上書きしないため）。

    Returns:
        保存できた場合はTrue
    """
    config_path = Path(path)
    if config_path.exists():
        try:
            data = _read_config(config_path)
        except Exception as e:
            logger.error(f"Settings not updated because {path} is unreadable: {e}")
            return False
    else:
        data = Settings().model_dump()

    data.update(changes)
    return save_settings(data, path)


# Global settings instance
current_settings = load_settings()
