"""セットアップウィザード - 初回起動時のガイド付きセットアップ"""
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import webbrowser
import subprocess
from typing import Callable, Optional, Dict, Any
from .logger import logger
from .settings import save_settings, Settings


class SetupWizard:
    """初回セットアップウィザード"""

    # 言語オプション
    LANGUAGES = [
        ("日本語", "ja"),
        ("英語", "en"),
        ("中国語", "zh"),
        ("韓国語", "ko"),
    ]

    # GPUオプション
    GPU_OPTIONS = [
        ("CPUのみを使用", 0),
        ("GPUを使用（推奨）", 60),
        ("GPUを最大限使用", 99),
    ]

    def __init__(self, on_complete: Callable[[Dict[str, Any]], None], on_cancel: Callable[[], None]):
        """
        Args:
            on_complete: セットアップ完了時のコールバック（設定dictを受け取る）
            on_cancel: キャンセル時のコールバック
        """
        self.on_complete = on_complete
        self.on_cancel = on_cancel

        self.current_step = 0
        self.total_steps = 5

        # 設定を格納
        self.settings: Dict[str, Any] = Settings().model_dump()

        # ウィンドウ作成
        self.root = tk.Tk()
        self.root.title("ローカルWhisper セットアップ")
        self.root.geometry("550x450")
        self.root.resizable(False, False)

        # 画面中央に配置
        self._center_window()

        # メインフレーム
        self.main_frame = ttk.Frame(self.root, padding=20)
        self.main_frame.pack(fill='both', expand=True)

        # コンテンツフレーム（各ステップの内容）
        self.content_frame = ttk.Frame(self.main_frame)
        self.content_frame.pack(fill='both', expand=True)

        # ナビゲーションフレーム
        self.nav_frame = ttk.Frame(self.main_frame)
        self.nav_frame.pack(fill='x', pady=(20, 0))

        # プログレスバー
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(
            self.nav_frame,
            variable=self.progress_var,
            maximum=self.total_steps,
            length=200
        )
        self.progress.pack(side='left')

        # ステップラベル
        self.step_label = ttk.Label(self.nav_frame, text="")
        self.step_label.pack(side='left', padx=10)

        # ボタン
        self.next_btn = ttk.Button(self.nav_frame, text="次へ >", command=self._next_step)
        self.next_btn.pack(side='right')

        self.back_btn = ttk.Button(self.nav_frame, text="< 戻る", command=self._prev_step)
        self.back_btn.pack(side='right', padx=5)

        # 閉じるボタンの処理
        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 最初のステップを表示
        self._show_step(0)

    def _center_window(self):
        """ウィンドウを画面中央に配置"""
        self.root.update_idletasks()
        width = 550
        height = 450
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def _clear_content(self):
        """コンテンツフレームをクリア"""
        for widget in self.content_frame.winfo_children():
            widget.destroy()

    def _update_nav(self):
        """ナビゲーションを更新"""
        self.progress_var.set(self.current_step + 1)
        self.step_label.config(text=f"ステップ {self.current_step + 1} / {self.total_steps}")

        # 戻るボタン
        if self.current_step == 0:
            self.back_btn.config(state='disabled')
        else:
            self.back_btn.config(state='normal')

        # 次へボタン
        if self.current_step == self.total_steps - 1:
            self.next_btn.config(text="完了")
        else:
            self.next_btn.config(text="次へ >")

    def _show_step(self, step: int):
        """指定されたステップを表示"""
        self.current_step = step
        self._clear_content()
        self._update_nav()

        step_methods = [
            self._step_welcome,
            self._step_whisper_cli,
            self._step_model,
            self._step_basic_settings,
            self._step_complete,
        ]

        if 0 <= step < len(step_methods):
            step_methods[step]()

    def _next_step(self):
        """次のステップへ"""
        if self.current_step == self.total_steps - 1:
            self._finish()
        else:
            self._show_step(self.current_step + 1)

    def _prev_step(self):
        """前のステップへ"""
        if self.current_step > 0:
            self._show_step(self.current_step - 1)

    def _on_cancel(self):
        """キャンセル処理"""
        if messagebox.askyesno("確認", "セットアップを中断しますか？"):
            self.root.destroy()
            self.on_cancel()

    def _finish(self):
        """セットアップを完了"""
        # 設定を保存
        if save_settings(self.settings):
            logger.info("Setup wizard completed, settings saved")
            self.root.destroy()
            self.on_complete(self.settings)
        else:
            messagebox.showerror("エラー", "設定の保存に失敗しました")

    # === 各ステップのUI ===

    def _step_welcome(self):
        """ステップ1: ようこそ"""
        ttk.Label(
            self.content_frame,
            text="ローカルWhisper へようこそ",
            font=('', 16, 'bold')
        ).pack(pady=(0, 20))

        welcome_text = """
このアプリケーションは、OpenAI Whisperを使用した
ローカル音声認識ツールです。

主な特徴:
・インターネット接続なしで動作
・プライバシーを重視（音声データはローカルで処理）
・ホットキーで簡単に録音・文字起こし

このウィザードでは、以下を設定します:
1. Whisper CLI の確認
2. 音声認識モデルの選択
3. 基本設定（言語、GPU使用など）
"""
        ttk.Label(
            self.content_frame,
            text=welcome_text,
            justify='left'
        ).pack(anchor='w')

    def _step_whisper_cli(self):
        """ステップ2: Whisper CLIの確認"""
        ttk.Label(
            self.content_frame,
            text="Whisper CLI の確認",
            font=('', 14, 'bold')
        ).pack(pady=(0, 15))

        # 存在チェック
        cli_path = Path.cwd() / "bin" / "whisper-cli.exe"
        cli_exists = cli_path.exists()

        if cli_exists:
            status_text = "✓ whisper-cli.exe が見つかりました"
            status_color = "green"
        else:
            status_text = "✗ whisper-cli.exe が見つかりません"
            status_color = "red"

        status_label = ttk.Label(
            self.content_frame,
            text=status_text,
            font=('', 11)
        )
        status_label.pack(pady=10)

        if not cli_exists:
            info_text = """
Whisper CLI は音声認識の実行に必要です。

以下の手順でダウンロードしてください:
1. 下のボタンでダウンロードページを開く
2. Assets から whisper-*-bin-x64.zip をダウンロード
3. 解凍して whisper-cli.exe を bin/ フォルダに配置
"""
            ttk.Label(
                self.content_frame,
                text=info_text,
                justify='left'
            ).pack(anchor='w', pady=10)

            ttk.Button(
                self.content_frame,
                text="ダウンロードページを開く",
                command=lambda: webbrowser.open("https://github.com/ggerganov/whisper.cpp/releases")
            ).pack(pady=10)

            ttk.Label(
                self.content_frame,
                text="※ ダウンロード後、「次へ」を押してください",
                foreground='gray'
            ).pack()

    def _step_model(self):
        """ステップ3: モデル選択"""
        ttk.Label(
            self.content_frame,
            text="音声認識モデルの選択",
            font=('', 14, 'bold')
        ).pack(pady=(0, 15))

        # models/ フォルダをスキャン
        models_dir = Path.cwd() / "models"
        models = []

        if models_dir.exists():
            models = [f.name for f in models_dir.glob("*.bin")]

        if models:
            ttk.Label(
                self.content_frame,
                text="以下のモデルが見つかりました:"
            ).pack(anchor='w')

            # モデル選択
            self.model_var = tk.StringVar()

            model_frame = ttk.Frame(self.content_frame)
            model_frame.pack(fill='x', pady=10)

            for model in sorted(models):
                size_info = self._get_model_info(model)
                rb = ttk.Radiobutton(
                    model_frame,
                    text=f"{model} {size_info}",
                    variable=self.model_var,
                    value=model
                )
                rb.pack(anchor='w', pady=2)

            # デフォルト選択
            if models:
                # 優先順位: large-v3-turbo > medium > small
                for preferred in ['large-v3-turbo', 'medium', 'small']:
                    for m in models:
                        if preferred in m.lower():
                            self.model_var.set(m)
                            break
                    if self.model_var.get():
                        break
                if not self.model_var.get():
                    self.model_var.set(models[0])

            # 設定に反映
            self.model_var.trace_add('write', lambda *args: self._update_model())
            self._update_model()

        else:
            ttk.Label(
                self.content_frame,
                text="モデルが見つかりません",
                foreground='red'
            ).pack(pady=10)

            info_text = """
音声認識モデルをダウンロードする必要があります。

推奨モデル:
・ggml-small.bin (466MB) - 高速・軽量
・ggml-medium.bin (1.5GB) - バランス型
・ggml-large-v3-turbo.bin (1.5GB) - 高精度・高速

下のボタンでダウンロードできます。
"""
            ttk.Label(
                self.content_frame,
                text=info_text,
                justify='left'
            ).pack(anchor='w')

            ttk.Button(
                self.content_frame,
                text="モデルをダウンロード...",
                command=self._open_model_downloader
            ).pack(pady=10)

    def _get_model_info(self, model_name: str) -> str:
        """モデル情報を取得"""
        name_lower = model_name.lower()
        if 'tiny' in name_lower:
            return "(最軽量・低精度)"
        elif 'base' in name_lower:
            return "(軽量)"
        elif 'small' in name_lower:
            return "(標準)"
        elif 'medium' in name_lower:
            return "(バランス型)"
        elif 'large' in name_lower:
            if 'turbo' in name_lower:
                return "(高精度・高速・推奨)"
            return "(高精度)"
        return ""

    def _update_model(self):
        """モデル設定を更新"""
        if hasattr(self, 'model_var') and self.model_var.get():
            self.settings['model_path'] = f"./models/{self.model_var.get()}"

    def _open_model_downloader(self):
        """モデルダウンローダーを開く"""
        try:
            from .model_downloader import ModelDownloader
            ModelDownloader(self.root, self._on_model_downloaded)
        except Exception as e:
            logger.error(f"Failed to open model downloader: {e}")
            webbrowser.open("https://huggingface.co/ggerganov/whisper.cpp/tree/main")

    def _on_model_downloaded(self, model_path: str):
        """モデルダウンロード完了"""
        self.settings['model_path'] = model_path
        # ステップを再表示してリストを更新
        self._show_step(self.current_step)

    def _step_basic_settings(self):
        """ステップ4: 基本設定"""
        ttk.Label(
            self.content_frame,
            text="基本設定",
            font=('', 14, 'bold')
        ).pack(pady=(0, 15))

        # 言語選択
        lang_frame = ttk.Frame(self.content_frame)
        lang_frame.pack(fill='x', pady=10)

        ttk.Label(lang_frame, text="認識言語:", width=15).pack(side='left')

        self.lang_var = tk.StringVar(value=self.settings.get('language', 'ja'))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.lang_var, state='readonly', width=20)
        lang_combo['values'] = [f"{name} ({code})" for name, code in self.LANGUAGES]

        # 現在値を設定
        for name, code in self.LANGUAGES:
            if code == self.lang_var.get():
                lang_combo.set(f"{name} ({code})")
                break

        lang_combo.bind('<<ComboboxSelected>>', self._on_lang_select)
        lang_combo.pack(side='left')

        # GPU設定
        gpu_frame = ttk.Frame(self.content_frame)
        gpu_frame.pack(fill='x', pady=10)

        ttk.Label(gpu_frame, text="GPU設定:", width=15).pack(side='left')

        self.gpu_var = tk.IntVar(value=self.settings.get('n_gpu_layers', 0))

        gpu_inner = ttk.Frame(gpu_frame)
        gpu_inner.pack(side='left', fill='x')

        for label, value in self.GPU_OPTIONS:
            rb = ttk.Radiobutton(
                gpu_inner,
                text=label,
                variable=self.gpu_var,
                value=value,
                command=self._on_gpu_change
            )
            rb.pack(anchor='w')

        ttk.Label(
            self.content_frame,
            text="※ NVIDIA GPU搭載PCの場合は「GPUを使用」がおすすめです",
            foreground='gray'
        ).pack(pady=(5, 15))

        # 自動貼り付け
        self.auto_paste_var = tk.BooleanVar(value=self.settings.get('auto_paste', True))
        ttk.Checkbutton(
            self.content_frame,
            text="認識後に自動で貼り付け (Ctrl+V を自動実行)",
            variable=self.auto_paste_var,
            command=self._on_auto_paste_change
        ).pack(anchor='w', pady=5)

        # 効果音
        self.sound_var = tk.BooleanVar(value=self.settings.get('sound_enabled', True))
        ttk.Checkbutton(
            self.content_frame,
            text="効果音を鳴らす",
            variable=self.sound_var,
            command=self._on_sound_change
        ).pack(anchor='w', pady=5)

    def _on_lang_select(self, event=None):
        """言語選択ハンドラ"""
        for name, code in self.LANGUAGES:
            if f"{name} ({code})" in self.lang_var.get():
                self.settings['language'] = code
                break

    def _on_gpu_change(self):
        """GPU設定変更ハンドラ"""
        self.settings['n_gpu_layers'] = self.gpu_var.get()

    def _on_auto_paste_change(self):
        """自動貼り付け設定変更"""
        self.settings['auto_paste'] = self.auto_paste_var.get()

    def _on_sound_change(self):
        """効果音設定変更"""
        self.settings['sound_enabled'] = self.sound_var.get()

    def _step_complete(self):
        """ステップ5: 完了"""
        ttk.Label(
            self.content_frame,
            text="セットアップ完了",
            font=('', 16, 'bold')
        ).pack(pady=(0, 20))

        # 設定サマリー
        summary_text = f"""
以下の設定でセットアップを完了します:

モデル: {Path(self.settings.get('model_path', '')).name}
言語: {self.settings.get('language', 'ja')}
GPU使用: {self.settings.get('n_gpu_layers', 0)} レイヤー
自動貼り付け: {'有効' if self.settings.get('auto_paste') else '無効'}
効果音: {'有効' if self.settings.get('sound_enabled') else '無効'}

使い方:
1. Ctrl+Alt+Shift+J を押して録音開始
2. 話し終わったらもう一度同じキーを押す
3. 認識結果がクリップボードにコピーされます

設定は右クリックメニューからいつでも変更できます。
"""
        ttk.Label(
            self.content_frame,
            text=summary_text,
            justify='left'
        ).pack(anchor='w')

    def run(self):
        """ウィザードを実行"""
        self.root.mainloop()


def run_setup_wizard(on_complete: Callable[[Dict[str, Any]], None], on_cancel: Callable[[], None]):
    """セットアップウィザードを実行する便利関数"""
    wizard = SetupWizard(on_complete, on_cancel)
    wizard.run()
