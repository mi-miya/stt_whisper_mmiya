import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from .logger import logger

class Settings(BaseModel):
    hotkey: str = "<ctrl>+<alt>+<shift>+h"
    language: str = "ja"
    model_path: str = "./models/ggml-small.bin"
    whisper_cli_path: str = "./bin/whisper-cli.exe"
    auto_paste: bool = False
    keep_trailing_newline: bool = False
    temp_dir: str = ""
    audio_device: Optional[int] = None
    sample_rate: int = 16000
    n_gpu_layers: int = 0
    initial_prompt: str = ""
    sound_enabled: bool = True
    best_of: int = 5
    beam_size: int = 5
    temperature: float = 0.0
    carry_initial_prompt: bool = False
    silence_threshold: int = 500

    def resolve_paths(self):
        # Resolve relative paths
        base = Path.cwd()
        self.model_path = str((base / self.model_path).resolve())
        self.whisper_cli_path = str((base / self.whisper_cli_path).resolve())
        if self.temp_dir:
            self.temp_dir = str(Path(self.temp_dir).resolve())

def load_settings(path: str = "config.json") -> Settings:
    config_path = Path(path)
    if not config_path.exists():
        logger.warning(f"Config file {path} not found. Using defaults.")
        return Settings()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = Settings(**data)
        settings.resolve_paths()
        logger.info("Settings loaded successfully")
        return settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return Settings()


def save_settings(settings_dict: dict, path: str = "config.json") -> bool:
    """設定をJSONファイルに保存する

    Args:
        settings_dict: 保存する設定の辞書
        path: 保存先のパス

    Returns:
        成功した場合はTrue、失敗した場合はFalse
    """
    config_path = Path(path)

    try:
        # パスを相対パスに変換（model_path, whisper_cli_path）
        base = Path.cwd()
        save_dict = settings_dict.copy()

        # model_path を相対パスに変換
        if 'model_path' in save_dict:
            model_path = Path(save_dict['model_path'])
            try:
                rel_path = model_path.relative_to(base)
                save_dict['model_path'] = './' + str(rel_path).replace('\\', '/')
            except ValueError:
                # 相対パスに変換できない場合はそのまま
                pass

        # whisper_cli_path を相対パスに変換
        if 'whisper_cli_path' in save_dict:
            cli_path = Path(save_dict['whisper_cli_path'])
            try:
                rel_path = cli_path.relative_to(base)
                save_dict['whisper_cli_path'] = './' + str(rel_path).replace('\\', '/')
            except ValueError:
                pass

        # JSONに保存
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(save_dict, f, ensure_ascii=False, indent=2)

        logger.info(f"Settings saved to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def load_settings_as_dict(path: str = "config.json") -> dict:
    """設定を辞書として読み込む（ダイアログ用）"""
    config_path = Path(path)
    if not config_path.exists():
        # デフォルト設定を返す
        return Settings().model_dump()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load settings as dict: {e}")
        return Settings().model_dump()


# Global settings instance
current_settings = load_settings()
