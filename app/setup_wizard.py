"""セットアップウィザード - 初回起動時のガイド付きセットアップ"""
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Dict, Any
from .logger import logger
from .settings import save_settings, Settings, AVAILABLE_MODELS, LANGUAGES, DEVICE_OPTIONS


class SetupWizard:
    """初回セットアップウィザード"""

    def __init__(self, on_complete: Callable[[Dict[str, Any]], None], on_cancel: Callable[[], None]):
        """
        Args:
            on_complete: セットアップ完了時のコールバック（設定dictを受け取る）
            on_cancel: キャンセル時のコールバック
        """
        self.on_complete = on_complete
        self.on_cancel = on_cancel

        self.current_step = 0
        self.total_steps = 4

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
・高速・高精度な音声認識 (faster-whisper)
・プライバシーを重視（音声データはローカルで処理）
・ホットキーで簡単に録音・文字起こし
・GPU対応で高速処理

このウィザードでは、以下を設定します:
1. 音声認識モデルの選択
2. 基本設定（言語、デバイスなど）
"""
        ttk.Label(
            self.content_frame,
            text=welcome_text,
            justify='left'
        ).pack(anchor='w')

    def _step_model(self):
        """ステップ2: モデル選択"""
        ttk.Label(
            self.content_frame,
            text="音声認識モデルの選択",
            font=('', 14, 'bold')
        ).pack(pady=(0, 15))

        ttk.Label(
            self.content_frame,
            text="使用するモデルを選択してください:"
        ).pack(anchor='w')

        # モデル選択
        self.model_var = tk.StringVar(value=self.settings.get('model_name', 'large-v3-turbo'))

        model_frame = ttk.Frame(self.content_frame)
        model_frame.pack(fill='x', pady=10)

        for model_id, desc, size in AVAILABLE_MODELS:
            rb = ttk.Radiobutton(
                model_frame,
                text=f"{model_id}",
                variable=self.model_var,
                value=model_id
            )
            rb.pack(anchor='w', pady=2)

            ttk.Label(
                model_frame,
                text=f"    {desc} ({size})",
                foreground='gray'
            ).pack(anchor='w')

        # 設定に反映
        self.model_var.trace_add('write', lambda *args: self._update_model())
        self._update_model()

        ttk.Separator(self.content_frame, orient='horizontal').pack(fill='x', pady=15)

        ttk.Label(
            self.content_frame,
            text="モデルは初回使用時に自動でダウンロードされます。\n大きいモデルほど精度が高くなりますが、\nダウンロードサイズとメモリ使用量が増えます。",
            foreground='gray',
            justify='left'
        ).pack(anchor='w')

    def _update_model(self):
        """モデル設定を更新"""
        if hasattr(self, 'model_var') and self.model_var.get():
            self.settings['model_name'] = self.model_var.get()

    def _step_basic_settings(self):
        """ステップ3: 基本設定"""
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
        lang_combo['values'] = [f"{name} ({code})" for name, code in LANGUAGES]

        # 現在値を設定
        for name, code in LANGUAGES:
            if code == self.lang_var.get():
                lang_combo.set(f"{name} ({code})")
                break

        lang_combo.bind('<<ComboboxSelected>>', self._on_lang_select)
        lang_combo.pack(side='left')

        # デバイス設定
        device_frame = ttk.Frame(self.content_frame)
        device_frame.pack(fill='x', pady=10)

        ttk.Label(device_frame, text="デバイス:", width=15).pack(side='left')

        self.device_var = tk.StringVar(value=self.settings.get('device', 'auto'))

        device_inner = ttk.Frame(device_frame)
        device_inner.pack(side='left', fill='x')

        for label, value in DEVICE_OPTIONS:
            rb = ttk.Radiobutton(
                device_inner,
                text=label,
                variable=self.device_var,
                value=value,
                command=self._on_device_change
            )
            rb.pack(anchor='w')

        ttk.Label(
            self.content_frame,
            text="※ NVIDIA GPU搭載PCの場合は「自動検出」がおすすめです\n※ CUDAが利用可能な場合は自動的にGPUが使用されます",
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
        for name, code in LANGUAGES:
            if f"{name} ({code})" in self.lang_var.get():
                self.settings['language'] = code
                break

    def _on_device_change(self):
        """デバイス設定変更ハンドラ"""
        self.settings['device'] = self.device_var.get()

    def _on_auto_paste_change(self):
        """自動貼り付け設定変更"""
        self.settings['auto_paste'] = self.auto_paste_var.get()

    def _on_sound_change(self):
        """効果音設定変更"""
        self.settings['sound_enabled'] = self.sound_var.get()

    def _step_complete(self):
        """ステップ4: 完了"""
        ttk.Label(
            self.content_frame,
            text="セットアップ完了",
            font=('', 16, 'bold')
        ).pack(pady=(0, 20))

        # デバイス表示
        device_display = self.settings.get('device', 'auto')
        for label, value in DEVICE_OPTIONS:
            if value == device_display:
                device_display = label
                break

        # 設定サマリー
        summary_text = f"""
以下の設定でセットアップを完了します:

モデル: {self.settings.get('model_name', 'large-v3-turbo')}
言語: {self.settings.get('language', 'ja')}
デバイス: {device_display}
自動貼り付け: {'有効' if self.settings.get('auto_paste') else '無効'}
効果音: {'有効' if self.settings.get('sound_enabled') else '無効'}

使い方:
1. Ctrl+Alt+Shift+J を押して録音開始
2. 話し終わったらもう一度同じキーを押す
3. 認識結果がクリップボードにコピーされます

※ 初回起動時はモデルのダウンロードに数分かかります。
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
