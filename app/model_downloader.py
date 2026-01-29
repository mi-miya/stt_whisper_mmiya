"""モデルダウンローダー - Whisperモデルをダウンロード"""
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import threading
import urllib.request
import urllib.error
from typing import Callable, Optional
from .logger import logger


# モデル情報
MODELS = {
    "ggml-small.bin": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
        "size": "466 MB",
        "size_bytes": 488677888,
        "description": "標準サイズ - 高速・軽量",
    },
    "ggml-medium.bin": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
        "size": "1.5 GB",
        "size_bytes": 1533774848,
        "description": "中サイズ - バランス型",
    },
    "ggml-large-v3-turbo.bin": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
        "size": "1.5 GB",
        "size_bytes": 1649062400,
        "description": "大サイズ (Turbo) - 高精度・高速・推奨",
    },
}


class ModelDownloader:
    """モデルダウンローダーダイアログ"""

    def __init__(self, parent: tk.Tk, on_complete: Optional[Callable[[str], None]] = None):
        """
        Args:
            parent: 親ウィンドウ
            on_complete: ダウンロード完了時のコールバック（モデルパスを受け取る）
        """
        self.parent = parent
        self.on_complete = on_complete
        self.download_thread: Optional[threading.Thread] = None
        self.cancel_flag = False

        # ダイアログウィンドウ
        self.window = tk.Toplevel(parent)
        self.window.title("モデルのダウンロード")
        self.window.geometry("500x400")
        self.window.resizable(False, False)

        # モーダル
        self.window.transient(parent)
        self.window.grab_set()

        # 中央に配置
        self._center_window()

        # UI構築
        self._build_ui()

        # 閉じる処理
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self):
        """ウィンドウを画面中央に配置"""
        self.window.update_idletasks()
        width = 500
        height = 400
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _build_ui(self):
        """UIを構築"""
        main_frame = ttk.Frame(self.window, padding=20)
        main_frame.pack(fill='both', expand=True)

        # タイトル
        ttk.Label(
            main_frame,
            text="音声認識モデルのダウンロード",
            font=('', 14, 'bold')
        ).pack(pady=(0, 15))

        # 説明
        ttk.Label(
            main_frame,
            text="ダウンロードするモデルを選択してください:",
            justify='left'
        ).pack(anchor='w')

        # モデル選択
        self.model_var = tk.StringVar(value="ggml-large-v3-turbo.bin")

        model_frame = ttk.Frame(main_frame)
        model_frame.pack(fill='x', pady=15)

        for model_name, info in MODELS.items():
            frame = ttk.Frame(model_frame)
            frame.pack(fill='x', pady=3)

            rb = ttk.Radiobutton(
                frame,
                text=model_name,
                variable=self.model_var,
                value=model_name
            )
            rb.pack(side='left')

            ttk.Label(
                frame,
                text=f"({info['size']}) - {info['description']}",
                foreground='gray'
            ).pack(side='left', padx=(10, 0))

        # 進捗フレーム
        self.progress_frame = ttk.Frame(main_frame)
        self.progress_frame.pack(fill='x', pady=15)

        self.progress_label = ttk.Label(self.progress_frame, text="")
        self.progress_label.pack(anchor='w')

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            variable=self.progress_var,
            maximum=100,
            length=450
        )
        self.progress_bar.pack(fill='x', pady=5)

        self.status_label = ttk.Label(self.progress_frame, text="", foreground='gray')
        self.status_label.pack(anchor='w')

        # ボタン
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill='x', pady=(20, 0))

        self.cancel_btn = ttk.Button(
            btn_frame,
            text="閉じる",
            command=self._on_close
        )
        self.cancel_btn.pack(side='right')

        self.download_btn = ttk.Button(
            btn_frame,
            text="ダウンロード開始",
            command=self._start_download
        )
        self.download_btn.pack(side='right', padx=5)

    def _start_download(self):
        """ダウンロードを開始"""
        model_name = self.model_var.get()
        if model_name not in MODELS:
            return

        model_info = MODELS[model_name]

        # UI更新
        self.download_btn.config(state='disabled')
        self.cancel_btn.config(text='キャンセル')
        self.progress_label.config(text=f"ダウンロード中: {model_name}")
        self.cancel_flag = False

        # ダウンロードスレッド開始
        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(model_name, model_info),
            daemon=True
        )
        self.download_thread.start()

    def _download_worker(self, model_name: str, model_info: dict):
        """ダウンロードワーカー（別スレッド）"""
        try:
            # 保存先
            models_dir = Path.cwd() / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            filepath = models_dir / model_name

            # 既存ファイルがある場合
            if filepath.exists():
                self._update_status("既存のファイルを上書きします...")

            url = model_info['url']
            total_size = model_info['size_bytes']

            # ダウンロード
            logger.info(f"Downloading {model_name} from {url}")

            request = urllib.request.Request(url)
            request.add_header('User-Agent', 'Mozilla/5.0')

            with urllib.request.urlopen(request, timeout=30) as response:
                with open(filepath, 'wb') as f:
                    downloaded = 0
                    block_size = 8192

                    while True:
                        if self.cancel_flag:
                            logger.info("Download cancelled")
                            # 不完全なファイルを削除
                            try:
                                filepath.unlink()
                            except:
                                pass
                            self._on_download_cancelled()
                            return

                        data = response.read(block_size)
                        if not data:
                            break

                        f.write(data)
                        downloaded += len(data)

                        # 進捗更新
                        progress = (downloaded / total_size) * 100
                        speed = self._format_size(downloaded)
                        total = self._format_size(total_size)
                        self._update_progress(progress, f"{speed} / {total}")

            logger.info(f"Download complete: {filepath}")
            self._on_download_complete(str(filepath))

        except urllib.error.URLError as e:
            logger.error(f"Download failed: {e}")
            self._on_download_error(f"ネットワークエラー: {e.reason}")
        except Exception as e:
            logger.error(f"Download failed: {e}")
            self._on_download_error(str(e))

    def _format_size(self, size: int) -> str:
        """サイズを人間が読める形式に変換"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"

    def _update_progress(self, progress: float, status: str):
        """進捗を更新（メインスレッドで実行）"""
        self.window.after(0, lambda: self._do_update_progress(progress, status))

    def _do_update_progress(self, progress: float, status: str):
        """実際の進捗更新"""
        self.progress_var.set(progress)
        self.status_label.config(text=status)

    def _update_status(self, status: str):
        """ステータスを更新"""
        self.window.after(0, lambda: self.status_label.config(text=status))

    def _on_download_complete(self, filepath: str):
        """ダウンロード完了"""
        def complete():
            self.progress_var.set(100)
            self.progress_label.config(text="ダウンロード完了!")
            self.status_label.config(text=f"保存先: {filepath}")
            self.download_btn.config(state='normal', text='ダウンロード開始')
            self.cancel_btn.config(text='閉じる')

            messagebox.showinfo("完了", "モデルのダウンロードが完了しました。")

            if self.on_complete:
                self.on_complete(f"./models/{Path(filepath).name}")

            self.window.destroy()

        self.window.after(0, complete)

    def _on_download_cancelled(self):
        """ダウンロードキャンセル"""
        def cancelled():
            self.progress_var.set(0)
            self.progress_label.config(text="")
            self.status_label.config(text="キャンセルされました")
            self.download_btn.config(state='normal')
            self.cancel_btn.config(text='閉じる')

        self.window.after(0, cancelled)

    def _on_download_error(self, error: str):
        """ダウンロードエラー"""
        def show_error():
            self.progress_label.config(text="エラー")
            self.status_label.config(text=error)
            self.download_btn.config(state='normal')
            self.cancel_btn.config(text='閉じる')
            messagebox.showerror("エラー", f"ダウンロードに失敗しました:\n{error}")

        self.window.after(0, show_error)

    def _on_close(self):
        """閉じる処理"""
        if self.download_thread and self.download_thread.is_alive():
            if messagebox.askyesno("確認", "ダウンロードを中止しますか？"):
                self.cancel_flag = True
                return
            else:
                return

        self.window.destroy()
