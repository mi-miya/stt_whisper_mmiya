"""設定ダイアログ - GUIから設定を変更できるようにする"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import json
from typing import Callable, Optional
from .logger import logger


class HotkeyCapture:
    """ホットキーをキャプチャするためのウィジェット"""

    # キーコードから pynput形式への変換マップ
    MODIFIER_MAP = {
        'Control_L': '<ctrl>', 'Control_R': '<ctrl>',
        'Alt_L': '<alt>', 'Alt_R': '<alt>',
        'Shift_L': '<shift>', 'Shift_R': '<shift>',
        'Win_L': '<cmd>', 'Win_R': '<cmd>',
    }

    def __init__(self, parent, initial_value: str = ""):
        self.frame = ttk.Frame(parent)
        self.current_hotkey = initial_value
        self.capturing = False
        self.pressed_keys = set()
        self.main_key = None

        # 表示ラベル
        self.display_var = tk.StringVar(value=self._format_display(initial_value))
        self.display_label = ttk.Label(
            self.frame,
            textvariable=self.display_var,
            font=('Consolas', 10),
            width=30,
            anchor='center',
            relief='sunken',
            padding=5
        )
        self.display_label.pack(side='left', fill='x', expand=True)

        # キャプチャボタン
        self.capture_btn = ttk.Button(
            self.frame,
            text="変更...",
            command=self.start_capture,
            width=10
        )
        self.capture_btn.pack(side='left', padx=(5, 0))

    def _format_display(self, hotkey: str) -> str:
        """pynput形式のホットキーを表示用に変換"""
        if not hotkey:
            return "(未設定)"
        # <ctrl>+<alt>+<shift>+<j> -> Ctrl+Alt+Shift+J
        display = hotkey
        display = display.replace('<ctrl>', 'Ctrl')
        display = display.replace('<alt>', 'Alt')
        display = display.replace('<shift>', 'Shift')
        display = display.replace('<cmd>', 'Win')
        display = display.replace('<', '').replace('>', '')
        return display

    def start_capture(self):
        """キャプチャモードを開始"""
        self.capturing = True
        self.pressed_keys = set()
        self.main_key = None
        self.display_var.set("キーを押してください...")
        self.capture_btn.config(text="キャンセル", command=self.cancel_capture)

        # キーイベントをバインド
        self.frame.winfo_toplevel().bind('<KeyPress>', self._on_key_press)
        self.frame.winfo_toplevel().bind('<KeyRelease>', self._on_key_release)
        self.frame.winfo_toplevel().focus_set()

    def cancel_capture(self):
        """キャプチャをキャンセル"""
        self.capturing = False
        self.display_var.set(self._format_display(self.current_hotkey))
        self.capture_btn.config(text="変更...", command=self.start_capture)
        self._unbind_keys()

    def _unbind_keys(self):
        """キーイベントのバインドを解除"""
        try:
            self.frame.winfo_toplevel().unbind('<KeyPress>')
            self.frame.winfo_toplevel().unbind('<KeyRelease>')
        except:
            pass

    def _on_key_press(self, event):
        """キー押下時のハンドラ"""
        if not self.capturing:
            return

        key = event.keysym

        # 修飾キーの場合
        if key in self.MODIFIER_MAP:
            self.pressed_keys.add(self.MODIFIER_MAP[key])
        # Escapeでキャンセル
        elif key == 'Escape':
            self.cancel_capture()
            return
        # 通常キーの場合
        else:
            self.main_key = key.lower()

        # 修飾キー + 通常キーが揃ったら確定
        if self.pressed_keys and self.main_key:
            self._confirm_hotkey()

    def _on_key_release(self, event):
        """キーリリース時のハンドラ"""
        if not self.capturing:
            return

        key = event.keysym
        if key in self.MODIFIER_MAP:
            # 修飾キーのみの場合は表示を更新
            modifier = self.MODIFIER_MAP[key]
            if modifier in self.pressed_keys:
                self.pressed_keys.discard(modifier)

    def _confirm_hotkey(self):
        """ホットキーを確定"""
        # 修飾キーをソートして結合
        modifiers = sorted(self.pressed_keys, key=lambda x: ['<ctrl>', '<alt>', '<shift>', '<cmd>'].index(x) if x in ['<ctrl>', '<alt>', '<shift>', '<cmd>'] else 99)
        hotkey_parts = modifiers + [f'<{self.main_key}>']
        self.current_hotkey = '+'.join(hotkey_parts)

        self.capturing = False
        self.display_var.set(self._format_display(self.current_hotkey))
        self.capture_btn.config(text="変更...", command=self.start_capture)
        self._unbind_keys()

    def get(self) -> str:
        """現在のホットキー設定を取得"""
        return self.current_hotkey

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)


class SettingsDialog:
    """設定ダイアログ"""

    # 言語オプション
    LANGUAGES = [
        ("日本語", "ja"),
        ("英語", "en"),
        ("中国語", "zh"),
        ("韓国語", "ko"),
        ("自動検出", "auto"),
    ]

    # サンプルレートオプション
    SAMPLE_RATES = [16000, 22050, 44100]

    # GPUレイヤーオプション
    GPU_LAYERS = [0, 30, 60, 99]

    def __init__(self, parent: tk.Tk, current_settings: dict, on_save: Callable[[dict], None]):
        """
        Args:
            parent: 親ウィンドウ
            current_settings: 現在の設定（dict）
            on_save: 保存時のコールバック（新しい設定dictを受け取る）
        """
        self.parent = parent
        self.settings = current_settings.copy()
        self.on_save = on_save

        # ダイアログウィンドウ作成
        self.window = tk.Toplevel(parent)
        self.window.title("設定")
        self.window.geometry("520x480")
        self.window.resizable(False, False)

        # モーダルダイアログとして設定
        self.window.transient(parent)
        self.window.grab_set()

        # 中央に配置
        self._center_window()

        # 変数を初期化
        self._init_variables()

        # UIを構築
        self._build_ui()

        # 閉じるボタンの処理
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self):
        """ウィンドウを画面中央に配置"""
        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'+{x}+{y}')

    def _init_variables(self):
        """Tkinter変数を初期化"""
        s = self.settings

        # 基本設定
        self.var_language = tk.StringVar(value=s.get('language', 'ja'))
        self.var_auto_paste = tk.BooleanVar(value=s.get('auto_paste', False))
        self.var_sound_enabled = tk.BooleanVar(value=s.get('sound_enabled', True))

        # 詳細設定
        self.var_n_gpu_layers = tk.IntVar(value=s.get('n_gpu_layers', 0))
        self.var_sample_rate = tk.IntVar(value=s.get('sample_rate', 16000))
        self.var_silence_threshold = tk.IntVar(value=s.get('silence_threshold', 500))

        # 認識精度
        self.var_initial_prompt = tk.StringVar(value=s.get('initial_prompt', ''))
        self.var_best_of = tk.IntVar(value=s.get('best_of', 5))
        self.var_beam_size = tk.IntVar(value=s.get('beam_size', 5))
        self.var_temperature = tk.DoubleVar(value=s.get('temperature', 0.0))
        self.var_carry_initial_prompt = tk.BooleanVar(value=s.get('carry_initial_prompt', False))

    def _build_ui(self):
        """UIを構築"""
        # スタイル設定
        style = ttk.Style()
        style.configure('TNotebook.Tab', padding=[15, 5])

        # ノートブック（タブ）
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # 各タブを作成
        self._build_basic_tab()
        self._build_advanced_tab()
        self._build_accuracy_tab()
        self._build_hotkey_tab()

        # ボタンフレーム
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(fill='x', padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text="キャンセル", command=self._on_close).pack(side='right', padx=5)
        ttk.Button(btn_frame, text="保存して再起動", command=self._on_save).pack(side='right')

    def _build_basic_tab(self):
        """基本設定タブを構築"""
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="基本")

        row = 0

        # モデル選択
        ttk.Label(tab, text="モデル:").grid(row=row, column=0, sticky='w', pady=5)

        model_frame = ttk.Frame(tab)
        model_frame.grid(row=row, column=1, sticky='ew', pady=5)

        self.model_combo = ttk.Combobox(model_frame, state='readonly', width=35)
        self.model_combo.pack(side='left', fill='x', expand=True)
        self._populate_models()

        ttk.Button(model_frame, text="参照...", command=self._browse_model, width=8).pack(side='left', padx=(5, 0))

        row += 1

        # 言語選択
        ttk.Label(tab, text="言語:").grid(row=row, column=0, sticky='w', pady=5)

        self.lang_combo = ttk.Combobox(tab, state='readonly', width=20)
        self.lang_combo['values'] = [f"{name} ({code})" for name, code in self.LANGUAGES]
        # 現在の値を表示用に変換（"ja" と "日本語 (ja)" の両方に対応）
        current_lang = self.var_language.get()
        for name, code in self.LANGUAGES:
            if code == current_lang or f"{name} ({code})" == current_lang:
                self.lang_combo.set(f"{name} ({code})")
                self.var_language.set(code)  # 内部値はコードのみに正規化
                break
        self.lang_combo.bind('<<ComboboxSelected>>', lambda e: self._on_language_select())
        self.lang_combo.grid(row=row, column=1, sticky='w', pady=5)

        row += 1

        # チェックボックス
        ttk.Checkbutton(tab, text="自動貼り付け (Ctrl+V を自動実行)", variable=self.var_auto_paste).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
        row += 1

        ttk.Checkbutton(tab, text="効果音を鳴らす", variable=self.var_sound_enabled).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=15)
        row += 1

        info_text = "モデルが大きいほど精度が上がりますが、\n処理速度は遅くなります。"
        ttk.Label(tab, text=info_text, foreground='gray').grid(row=row, column=0, columnspan=2, sticky='w')

        tab.columnconfigure(1, weight=1)

    def _build_advanced_tab(self):
        """詳細設定タブを構築"""
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="詳細")

        row = 0

        # GPU レイヤー
        ttk.Label(tab, text="GPU使用 (レイヤー数):").grid(row=row, column=0, sticky='w', pady=5)

        gpu_frame = ttk.Frame(tab)
        gpu_frame.grid(row=row, column=1, sticky='w', pady=5)

        self.gpu_combo = ttk.Combobox(gpu_frame, state='readonly', width=10)
        self.gpu_combo['values'] = ['0 (CPUのみ)', '30', '60', '99 (最大)']
        # 現在値を設定
        current_gpu = self.var_n_gpu_layers.get()
        if current_gpu == 0:
            self.gpu_combo.set('0 (CPUのみ)')
        elif current_gpu >= 99:
            self.gpu_combo.set('99 (最大)')
        else:
            self.gpu_combo.set(str(current_gpu))
        self.gpu_combo.bind('<<ComboboxSelected>>', lambda e: self._on_gpu_select())
        self.gpu_combo.pack(side='left')

        ttk.Label(gpu_frame, text="※ NVIDIA GPU のみ", foreground='gray').pack(side='left', padx=(10, 0))

        row += 1

        # サンプルレート
        ttk.Label(tab, text="サンプルレート:").grid(row=row, column=0, sticky='w', pady=5)

        self.sr_combo = ttk.Combobox(tab, state='readonly', width=15)
        self.sr_combo['values'] = ['16000 (標準)', '22050', '44100 (高品質)']
        current_sr = self.var_sample_rate.get()
        if current_sr == 16000:
            self.sr_combo.set('16000 (標準)')
        elif current_sr == 44100:
            self.sr_combo.set('44100 (高品質)')
        else:
            self.sr_combo.set(str(current_sr))
        self.sr_combo.bind('<<ComboboxSelected>>', lambda e: self._on_sr_select())
        self.sr_combo.grid(row=row, column=1, sticky='w', pady=5)

        row += 1

        # 無音閾値
        ttk.Label(tab, text="無音判定の閾値:").grid(row=row, column=0, sticky='w', pady=5)

        silence_frame = ttk.Frame(tab)
        silence_frame.grid(row=row, column=1, sticky='ew', pady=5)

        self.silence_scale = ttk.Scale(
            silence_frame,
            from_=100,
            to=2000,
            variable=self.var_silence_threshold,
            orient='horizontal',
            length=200
        )
        self.silence_scale.pack(side='left')

        self.silence_label = ttk.Label(silence_frame, text=str(self.var_silence_threshold.get()), width=6)
        self.silence_label.pack(side='left', padx=(10, 0))

        self.var_silence_threshold.trace_add('write', lambda *args: self.silence_label.config(text=str(int(self.var_silence_threshold.get()))))

        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=15)
        row += 1

        info_text = "無音閾値: 値が小さいほど敏感に無音を検出します。\n環境音が多い場合は値を大きくしてください。"
        ttk.Label(tab, text=info_text, foreground='gray').grid(row=row, column=0, columnspan=2, sticky='w')

        tab.columnconfigure(1, weight=1)

    def _build_accuracy_tab(self):
        """認識精度タブを構築"""
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="認識精度")

        row = 0

        # 初期プロンプト
        ttk.Label(tab, text="初期プロンプト:").grid(row=row, column=0, sticky='nw', pady=5)

        self.prompt_text = tk.Text(tab, height=4, width=40, wrap='word')
        self.prompt_text.insert('1.0', self.var_initial_prompt.get())
        self.prompt_text.grid(row=row, column=1, sticky='ew', pady=5)

        row += 1

        ttk.Checkbutton(
            tab,
            text="複数セグメントで初期プロンプトを継続使用",
            variable=self.var_carry_initial_prompt
        ).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)

        row += 1

        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        row += 1

        # best_of
        ttk.Label(tab, text="候補数 (best_of):").grid(row=row, column=0, sticky='w', pady=5)
        ttk.Spinbox(tab, from_=1, to=10, textvariable=self.var_best_of, width=10).grid(row=row, column=1, sticky='w', pady=5)
        row += 1

        # beam_size
        ttk.Label(tab, text="ビームサイズ:").grid(row=row, column=0, sticky='w', pady=5)
        ttk.Spinbox(tab, from_=1, to=10, textvariable=self.var_beam_size, width=10).grid(row=row, column=1, sticky='w', pady=5)
        row += 1

        # temperature
        ttk.Label(tab, text="Temperature:").grid(row=row, column=0, sticky='w', pady=5)

        temp_frame = ttk.Frame(tab)
        temp_frame.grid(row=row, column=1, sticky='w', pady=5)

        ttk.Spinbox(temp_frame, from_=0.0, to=1.0, increment=0.1, textvariable=self.var_temperature, width=10).pack(side='left')
        ttk.Label(temp_frame, text="(0.0 = 決定論的)", foreground='gray').pack(side='left', padx=(10, 0))

        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        row += 1

        info_text = "初期プロンプト: 認識精度を向上させるヒントを入力します。\n例: 「日本語です。」「技術的な内容を話しています。」"
        ttk.Label(tab, text=info_text, foreground='gray').grid(row=row, column=0, columnspan=2, sticky='w')

        tab.columnconfigure(1, weight=1)

    def _build_hotkey_tab(self):
        """ホットキータブを構築"""
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="ホットキー")

        row = 0

        ttk.Label(tab, text="録音開始/停止のホットキー:").grid(row=row, column=0, sticky='w', pady=10)
        row += 1

        # ホットキーキャプチャウィジェット
        self.hotkey_capture = HotkeyCapture(tab, self.settings.get('hotkey', ''))
        self.hotkey_capture.grid(row=row, column=0, sticky='ew', pady=5)
        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, sticky='ew', pady=20)
        row += 1

        info_text = "「変更...」をクリックしてから、\n新しいショートカットキーを押してください。\n\n修飾キー (Ctrl/Alt/Shift) + 通常キーの\n組み合わせが必要です。\n\nEscキーでキャンセルできます。"
        ttk.Label(tab, text=info_text, foreground='gray', justify='left').grid(row=row, column=0, sticky='w')

        tab.columnconfigure(0, weight=1)

    def _populate_models(self):
        """models/フォルダ内のモデルをスキャン"""
        models_dir = Path.cwd() / 'models'
        models = []

        if models_dir.exists():
            for f in models_dir.glob('*.bin'):
                models.append(f.name)

        if not models:
            models = ['(モデルが見つかりません)']

        self.model_combo['values'] = models

        # 現在の設定を選択
        current_model = Path(self.settings.get('model_path', '')).name
        if current_model in models:
            self.model_combo.set(current_model)
        elif models:
            self.model_combo.set(models[0])

    def _browse_model(self):
        """モデルファイルを参照"""
        initial_dir = Path.cwd() / 'models'
        if not initial_dir.exists():
            initial_dir = Path.cwd()

        filepath = filedialog.askopenfilename(
            title="モデルファイルを選択",
            initialdir=initial_dir,
            filetypes=[("Whisperモデル", "*.bin"), ("すべてのファイル", "*.*")]
        )

        if filepath:
            # models/ フォルダ内なら相対パスで保存
            path = Path(filepath)
            models_dir = Path.cwd() / 'models'

            try:
                rel_path = path.relative_to(models_dir)
                self.model_combo.set(rel_path.name)
                self._populate_models()  # リストを更新
            except ValueError:
                # models/ 外のファイルの場合は絶対パスを使用
                self.model_combo.set(str(path))

    def _on_language_select(self):
        """言語選択時のハンドラ"""
        selected = self.lang_combo.get()
        for name, code in self.LANGUAGES:
            if f"{name} ({code})" == selected:
                self.var_language.set(code)
                break

    def _on_gpu_select(self):
        """GPU選択時のハンドラ"""
        selected = self.gpu_combo.get()
        if '0' in selected:
            self.var_n_gpu_layers.set(0)
        elif '99' in selected:
            self.var_n_gpu_layers.set(99)
        else:
            try:
                self.var_n_gpu_layers.set(int(selected))
            except:
                pass

    def _on_sr_select(self):
        """サンプルレート選択時のハンドラ"""
        selected = self.sr_combo.get()
        for sr in self.SAMPLE_RATES:
            if str(sr) in selected:
                self.var_sample_rate.set(sr)
                break

    def _on_save(self):
        """保存ボタンのハンドラ"""
        try:
            # 設定を収集
            new_settings = self.settings.copy()

            # モデルパス
            model_name = self.model_combo.get()
            if model_name and model_name != '(モデルが見つかりません)':
                if Path(model_name).is_absolute():
                    new_settings['model_path'] = model_name
                else:
                    new_settings['model_path'] = f"./models/{model_name}"

            # 基本設定
            new_settings['language'] = self.var_language.get()
            new_settings['auto_paste'] = self.var_auto_paste.get()
            new_settings['sound_enabled'] = self.var_sound_enabled.get()

            # 詳細設定
            new_settings['n_gpu_layers'] = self.var_n_gpu_layers.get()
            new_settings['sample_rate'] = self.var_sample_rate.get()
            new_settings['silence_threshold'] = int(self.var_silence_threshold.get())

            # 認識精度
            new_settings['initial_prompt'] = self.prompt_text.get('1.0', 'end-1c')
            new_settings['best_of'] = self.var_best_of.get()
            new_settings['beam_size'] = self.var_beam_size.get()
            new_settings['temperature'] = self.var_temperature.get()
            new_settings['carry_initial_prompt'] = self.var_carry_initial_prompt.get()

            # ホットキー
            hotkey = self.hotkey_capture.get()
            if hotkey:
                new_settings['hotkey'] = hotkey

            # コールバックを呼び出し
            self.on_save(new_settings)
            self.window.destroy()

        except Exception as e:
            logger.error(f"設定の保存に失敗: {e}")
            messagebox.showerror("エラー", f"設定の保存に失敗しました:\n{e}")

    def _on_close(self):
        """ウィンドウを閉じる"""
        self.window.destroy()
