"""エラーハンドラー - ユーザーフレンドリーなエラーメッセージを表示"""
import tkinter as tk
from tkinter import messagebox
from typing import Optional
from .logger import logger


# エラーメッセージの定義
ERROR_MESSAGES = {
    "model_not_found": {
        "title": "モデルのダウンロードに失敗しました",
        "message": "音声認識モデルをダウンロードできませんでした。\n\n"
                   "以下を確認してください：\n"
                   "・インターネット接続が有効か\n"
                   "・モデル名が正しいか\n"
                   "・ディスク容量が十分か"
    },
    "pipeline_not_loaded": {
        "title": "音声認識エンジンが準備できていません",
        "message": "音声認識パイプラインの読み込みに失敗しました。\n\n"
                   "以下を確認してください：\n"
                   "・インターネット接続（初回はモデルダウンロードが必要）\n"
                   "・GPUドライバが最新か\n"
                   "・十分なメモリ/VRAMがあるか\n\n"
                   "詳細は logs/app.log を確認してください。"
    },
    "recording_failed": {
        "title": "録音に失敗しました",
        "message": "マイクからの録音に失敗しました。\n\n"
                   "以下を確認してください：\n"
                   "・マイクが正しく接続されているか\n"
                   "・他のアプリがマイクを使用していないか\n"
                   "・Windowsのマイク設定でアクセスが許可されているか"
    },
    "transcription_failed": {
        "title": "音声認識に失敗しました",
        "message": "音声の文字変換に失敗しました。\n\n"
                   "以下を確認してください：\n"
                   "・モデルが正しくロードされているか\n"
                   "・録音された音声が十分な長さか\n"
                   "・GPUメモリが不足していないか"
    },
    "hotkey_failed": {
        "title": "ホットキーの登録に失敗しました",
        "message": "ショートカットキーを登録できませんでした。\n\n"
                   "他のアプリケーションが同じキーを\n"
                   "使用している可能性があります。\n\n"
                   "設定から別のキーに変更してください。"
    },
    "gpu_error": {
        "title": "GPU処理でエラーが発生しました",
        "message": "GPUでの処理中にエラーが発生しました。\n\n"
                   "以下を試してください：\n"
                   "・設定でデバイスを「CPU」に変更する\n"
                   "・計算精度を「float32」に変更する\n"
                   "・GPUドライバを更新する\n"
                   "・より小さいモデルに変更する"
    },
    "config_error": {
        "title": "設定ファイルのエラー",
        "message": "設定ファイル (config.json) の読み込みに\n"
                   "失敗しました。\n\n"
                   "config.default.json をコピーして\n"
                   "config.json として保存してください。"
    },
    "unknown_error": {
        "title": "エラーが発生しました",
        "message": "予期しないエラーが発生しました。\n\n"
                   "詳細は logs/app.log を確認してください。"
    }
}


class ErrorHandler:
    """エラーハンドラークラス"""

    _root: Optional[tk.Tk] = None

    @classmethod
    def set_root(cls, root: tk.Tk):
        """Tkinter ルートウィンドウを設定"""
        cls._root = root

    @classmethod
    def show_error(cls, error_type: str, details: Optional[str] = None) -> bool:
        """エラーダイアログを表示

        Args:
            error_type: エラータイプ (ERROR_MESSAGES のキー)
            details: 追加の詳細情報

        Returns:
            ユーザーがアクションを選択した場合は True
        """
        error_info = ERROR_MESSAGES.get(error_type, ERROR_MESSAGES["unknown_error"])

        title = error_info["title"]
        message = error_info["message"]

        if details:
            message += f"\n\n詳細: {details}"

        logger.error(f"[{error_type}] {title}: {details or ''}")

        messagebox.showerror(title, message)
        return False

    @classmethod
    def show_warning(cls, title: str, message: str):
        """警告ダイアログを表示"""
        logger.warning(f"{title}: {message}")
        messagebox.showwarning(title, message)

    @classmethod
    def show_info(cls, title: str, message: str):
        """情報ダイアログを表示"""
        logger.info(f"{title}: {message}")
        messagebox.showinfo(title, message)


# 便利な関数
def show_error(error_type: str, details: Optional[str] = None) -> bool:
    """エラーを表示（ショートカット関数）"""
    return ErrorHandler.show_error(error_type, details)


def show_warning(title: str, message: str):
    """警告を表示（ショートカット関数）"""
    ErrorHandler.show_warning(title, message)


def show_info(title: str, message: str):
    """情報を表示（ショートカット関数）"""
    ErrorHandler.show_info(title, message)
