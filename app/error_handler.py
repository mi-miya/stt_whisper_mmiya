"""エラーハンドラー - ユーザーフレンドリーなエラーメッセージを表示"""
import queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional
from .logger import logger

# ワーカースレッドからのダイアログ要求を確認する間隔
DIALOG_POLL_INTERVAL_MS = 100


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
                   "・cublas64_12.dll が見つからない場合は\n"
                   "　cuda_libs/ フォルダの中身を確認する\n"
                   "　（CUDA 12 系のランタイムが必要です）\n"
                   "・GPUメモリ不足の場合はより小さいモデルに変更する\n"
                   "・GPUドライバを更新する\n"
                   "・設定でデバイスを「CPU」に変更する"
    },
    "config_error": {
        "title": "設定ファイルのエラー",
        "message": "設定ファイル (config.json) を正しく\n"
                   "読み込めませんでした。\n\n"
                   "読み込めなかった項目は既定値で動作しています。\n"
                   "アイコンを右クリック →「設定」から\n"
                   "設定し直してください。"
    },
    "unknown_error": {
        "title": "エラーが発生しました",
        "message": "予期しないエラーが発生しました。\n\n"
                   "詳細は logs/app.log を確認してください。"
    }
}


class ErrorHandler:
    """エラーハンドラークラス

    ダイアログは録音・文字起こし・ホットキーなどのワーカースレッドからも
    要求されるが、Tkinter はメインスレッドからしか操作できない。そのため
    ワーカースレッドからの要求はキューに積むだけにして呼び出し元を待たせず、
    メインスレッドが定期的に取り出して表示する。ルートウィンドウがまだ無い
    時点の要求も、set_root() で登録された後に表示される。
    """

    _root: Optional[tk.Tk] = None
    _queue: "queue.Queue" = queue.Queue()

    @classmethod
    def set_root(cls, root: tk.Tk):
        """Tkinter ルートウィンドウを設定し、ダイアログ要求の監視を始める

        メインスレッドから呼ぶこと。
        """
        cls._root = root
        cls._poll()

    @classmethod
    def _poll(cls):
        root = cls._root
        try:
            while True:
                show_func, title, message = cls._queue.get_nowait()
                cls._show_now(show_func, title, message)
        except queue.Empty:
            pass

        try:
            root.after(DIALOG_POLL_INTERVAL_MS, cls._poll)
        except tk.TclError:
            # ルートウィンドウが破棄された（終了処理中）
            pass

    @classmethod
    def _show_now(cls, show_func: Callable, title: str, message: str):
        try:
            if cls._root is not None:
                show_func(title, message, parent=cls._root)
            else:
                show_func(title, message)
        except Exception as e:
            logger.warning(f"Could not show dialog '{title}': {e}")

    @classmethod
    def _dispatch(cls, show_func: Callable, title: str, message: str):
        if threading.current_thread() is threading.main_thread():
            cls._show_now(show_func, title, message)
        else:
            cls._queue.put((show_func, title, message))

    @classmethod
    def show_error(cls, error_type: str, details: Optional[str] = None):
        """エラーダイアログを表示

        Args:
            error_type: エラータイプ (ERROR_MESSAGES のキー)
            details: 追加の詳細情報
        """
        error_info = ERROR_MESSAGES.get(error_type, ERROR_MESSAGES["unknown_error"])

        title = error_info["title"]
        message = error_info["message"]

        if details:
            message += f"\n\n詳細: {details}"

        logger.error(f"[{error_type}] {title}: {details or ''}")

        cls._dispatch(messagebox.showerror, title, message)

    @classmethod
    def show_warning(cls, title: str, message: str):
        """警告ダイアログを表示"""
        logger.warning(f"{title}: {message}")
        cls._dispatch(messagebox.showwarning, title, message)

    @classmethod
    def show_info(cls, title: str, message: str):
        """情報ダイアログを表示"""
        logger.info(f"{title}: {message}")
        cls._dispatch(messagebox.showinfo, title, message)


# 便利な関数
def show_error(error_type: str, details: Optional[str] = None):
    """エラーを表示（ショートカット関数）"""
    ErrorHandler.show_error(error_type, details)


def show_warning(title: str, message: str):
    """警告を表示（ショートカット関数）"""
    ErrorHandler.show_warning(title, message)


def show_info(title: str, message: str):
    """情報を表示（ショートカット関数）"""
    ErrorHandler.show_info(title, message)
