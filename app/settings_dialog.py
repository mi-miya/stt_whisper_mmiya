"""設定ダイアログ - GUIから設定を変更できるようにする"""
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable
from .logger import logger
from .settings import AVAILABLE_MODELS, LANGUAGES, DEVICE_OPTIONS, COMPUTE_TYPE_OPTIONS


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

    SAMPLE_RATES = [16000, 22050, 44100]

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
        self.window.geometry("550x550")
        self.window.resizable(True, True)

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

        # sound_enabledの初期化
        sound_enabled_value = s.get('sound_enabled', True)
        logger.info(f"Initializing sound_enabled with value: {sound_enabled_value}")
        self.var_sound_enabled = tk.BooleanVar(value=sound_enabled_value)

        # 詳細設定
        self.var_device = tk.StringVar(value=s.get('device', 'auto'))
        self.var_compute_type = tk.StringVar(value=s.get('compute_type', 'float16'))
        self.var_sample_rate = tk.IntVar(value=s.get('sample_rate', 16000))
        self.var_silence_threshold = tk.IntVar(value=s.get('silence_threshold', 500))
        self.var_noise_floor = tk.IntVar(value=s.get('noise_floor', 200))

        # 認識精度
        self.var_initial_prompt = tk.StringVar(value=s.get('initial_prompt', ''))
        self.var_beam_size = tk.IntVar(value=s.get('beam_size', 1))
        self.var_temperature = tk.DoubleVar(value=s.get('temperature', 0.0))

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

        self.model_combo = ttk.Combobox(model_frame, width=40)
        self.model_combo['values'] = [f"{model_id} ({desc})" for model_id, desc, _ in AVAILABLE_MODELS]
        # 現在のモデルを設定
        current_model = self.settings.get('model_name', 'large-v3-turbo')
        self._set_model_combo(current_model)
        self.model_combo.pack(side='left', fill='x', expand=True)

        row += 1

        # 言語選択
        ttk.Label(tab, text="言語:").grid(row=row, column=0, sticky='w', pady=5)

        self.lang_combo = ttk.Combobox(tab, state='readonly', width=20)
        self.lang_combo['values'] = [f"{name} ({code})" for name, code in LANGUAGES]
        current_lang = self.var_language.get()
        for name, code in LANGUAGES:
            if code == current_lang or f"{name} ({code})" == current_lang:
                self.lang_combo.set(f"{name} ({code})")
                self.var_language.set(code)
                break
        self.lang_combo.bind('<<ComboboxSelected>>', lambda e: self._on_language_select())
        self.lang_combo.grid(row=row, column=1, sticky='w', pady=5)

        row += 1

        # チェックボックス
        ttk.Checkbutton(tab, text="自動貼り付け (Ctrl+V を自動実行)", variable=self.var_auto_paste).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
        row += 1

        # ビープ音のチェックボックス
        self.sound_checkbox = ttk.Checkbutton(
            tab,
            text="効果音を鳴らす",
            variable=self.var_sound_enabled
        )
        self.sound_checkbox.grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=15)
        row += 1

        info_text = "モデルが大きいほど精度が上がりますが、\n処理速度は遅くなり、GPUメモリも多く使用します。\n\nカスタムモデルIDを直接入力することもできます。\nモデルは初回使用時に自動でダウンロードされます。"
        ttk.Label(tab, text=info_text, foreground='gray').grid(row=row, column=0, columnspan=2, sticky='w')

        tab.columnconfigure(1, weight=1)

    def _set_model_combo(self, model_name: str):
        """モデルコンボボックスの値を設定"""
        for model_id, desc, _ in AVAILABLE_MODELS:
            if model_id == model_name:
                self.model_combo.set(f"{model_id} ({desc})")
                return
        self.model_combo.set(model_name)

    def _get_model_name(self) -> str:
        """モデルコンボボックスからモデルIDを取得"""
        selected = self.model_combo.get()
        for model_id, desc, _ in AVAILABLE_MODELS:
            if selected == f"{model_id} ({desc})":
                return model_id
        return selected.strip()

    def _build_advanced_tab(self):
        """詳細設定タブを構築"""
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="詳細")

        row = 0

        # デバイス選択
        ttk.Label(tab, text="デバイス:").grid(row=row, column=0, sticky='w', pady=5)

        device_frame = ttk.Frame(tab)
        device_frame.grid(row=row, column=1, sticky='w', pady=5)

        self.device_combo = ttk.Combobox(device_frame, state='readonly', width=20)
        self.device_combo['values'] = [label for label, _ in DEVICE_OPTIONS]
        # 現在値を設定
        current_device = self.var_device.get()
        for label, value in DEVICE_OPTIONS:
            if value == current_device:
                self.device_combo.set(label)
                break
        else:
            self.device_combo.set(DEVICE_OPTIONS[0][0])
        self.device_combo.bind('<<ComboboxSelected>>', lambda e: self._on_device_select())
        self.device_combo.pack(side='left')

        row += 1

        # 計算精度
        ttk.Label(tab, text="計算精度:").grid(row=row, column=0, sticky='w', pady=5)

        compute_frame = ttk.Frame(tab)
        compute_frame.grid(row=row, column=1, sticky='w', pady=5)

        self.compute_combo = ttk.Combobox(compute_frame, state='readonly', width=20)
        self.compute_combo['values'] = [label for label, _ in COMPUTE_TYPE_OPTIONS]
        current_compute = self.var_compute_type.get()
        for label, value in COMPUTE_TYPE_OPTIONS:
            if value == current_compute:
                self.compute_combo.set(label)
                break
        else:
            self.compute_combo.set(COMPUTE_TYPE_OPTIONS[0][0])
        self.compute_combo.bind('<<ComboboxSelected>>', lambda e: self._on_compute_select())
        self.compute_combo.pack(side='left')

        ttk.Label(compute_frame, text="※ float16はGPU推奨", foreground='gray').pack(side='left', padx=(10, 0))

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

        # ノイズフロア（最小音量閾値）
        ttk.Label(tab, text="ノイズフロア:").grid(row=row, column=0, sticky='w', pady=5)

        noise_frame = ttk.Frame(tab)
        noise_frame.grid(row=row, column=1, sticky='ew', pady=5)

        self.noise_scale = ttk.Scale(
            noise_frame,
            from_=0,
            to=1000,
            variable=self.var_noise_floor,
            orient='horizontal',
            length=200
        )
        self.noise_scale.pack(side='left')

        self.noise_label = ttk.Label(noise_frame, text=str(self.var_noise_floor.get()), width=6)
        self.noise_label.pack(side='left', padx=(10, 0))

        self.var_noise_floor.trace_add('write', lambda *args: self.noise_label.config(text=str(int(self.var_noise_floor.get()))))

        row += 1

        # 発話判定閾値
        ttk.Label(tab, text="発話判定の閾値:").grid(row=row, column=0, sticky='w', pady=5)

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

        info_text = "ノイズフロア: この値以下の音は無視（環境ノイズ除去）\n発話判定閾値: この値を超えたら発話として認識"
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

        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
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

        # ホットキー
        ttk.Label(tab, text="録音ホットキー:", font=('', 9, 'bold')).grid(row=row, column=0, sticky='w', pady=(5, 2))
        row += 1
        ttk.Label(tab, text="(押すと録音開始、もう一度押すと停止)", foreground='gray').grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        # ホットキーキャプチャウィジェット
        self.hotkey_capture = HotkeyCapture(tab, self.settings.get('hotkey', '<ctrl>+<alt>+<shift>+j'))
        self.hotkey_capture.grid(row=row, column=0, sticky='ew', pady=5)
        row += 1

        # 説明
        ttk.Separator(tab, orient='horizontal').grid(row=row, column=0, sticky='ew', pady=20)
        row += 1

        info_text = "「変更...」をクリックしてから、\n新しいショートカットキーを押してください。\n\n修飾キー (Ctrl/Alt/Shift) + 通常キーの\n組み合わせが必要です。\n\nEscキーでキャンセルできます。"
        ttk.Label(tab, text=info_text, foreground='gray', justify='left').grid(row=row, column=0, sticky='w')

        tab.columnconfigure(0, weight=1)

    def _on_language_select(self):
        """言語選択時のハンドラ"""
        selected = self.lang_combo.get()
        for name, code in LANGUAGES:
            if f"{name} ({code})" == selected:
                self.var_language.set(code)
                break

    def _on_device_select(self):
        """デバイス選択時のハンドラ"""
        selected = self.device_combo.get()
        for label, value in DEVICE_OPTIONS:
            if label == selected:
                self.var_device.set(value)
                break

    def _on_compute_select(self):
        """計算精度選択時のハンドラ"""
        selected = self.compute_combo.get()
        for label, value in COMPUTE_TYPE_OPTIONS:
            if label == selected:
                self.var_compute_type.set(value)
                break

    def _on_sr_select(self):
        """サンプルレート選択時のハンドラ"""
        selected = self.sr_combo.get()
        for sr in self.SAMPLE_RATES:
            if str(sr) in selected:
                self.var_sample_rate.set(sr)
                break

    def _on_save(self):
        """保存ボタンのハンドラ"""
        logger.info("=== Save button clicked ===")
        try:
            # 設定を収集
            new_settings = self.settings.copy()

            # モデル名
            model_name = self._get_model_name()
            if model_name:
                new_settings['model_name'] = model_name

            # 基本設定
            new_settings['language'] = self.var_language.get()
            new_settings['auto_paste'] = self.var_auto_paste.get()

            # sound_enabledの保存
            sound_enabled_save = self.var_sound_enabled.get()
            logger.info(f"Saving sound_enabled with value: {sound_enabled_save}")
            new_settings['sound_enabled'] = sound_enabled_save

            # 詳細設定
            new_settings['device'] = self.var_device.get()
            new_settings['compute_type'] = self.var_compute_type.get()
            new_settings['sample_rate'] = self.var_sample_rate.get()
            new_settings['silence_threshold'] = int(self.var_silence_threshold.get())
            new_settings['noise_floor'] = int(self.var_noise_floor.get())

            # 認識精度
            new_settings['initial_prompt'] = self.prompt_text.get('1.0', 'end-1c')
            new_settings['beam_size'] = self.var_beam_size.get()
            new_settings['temperature'] = self.var_temperature.get()

            # ホットキー
            hotkey = self.hotkey_capture.get()
            if hotkey:
                new_settings['hotkey'] = hotkey

            # コールバックを呼び出し
            logger.info("Calling save callback...")
            self.on_save(new_settings)
            logger.info("Settings saved successfully, closing dialog...")
            self.window.destroy()

        except Exception as e:
            logger.error(f"設定の保存に失敗: {e}")
            messagebox.showerror("エラー", f"設定の保存に失敗しました:\n{e}")

    def _on_close(self):
        """ウィンドウを閉じる"""
        self.window.destroy()
