# 計画書：Local Whisper Hotkey Dictation（Windows常駐）

## 0. ゴール

* 任意のホットキーで録音を **トグル（開始／停止）**
* 停止後にローカルで文字起こし → **クリップボード格納** →（任意）**自動貼り付け（Ctrl+V）**
* 録音音声（WAV）は **必ず削除**して残さない
* 整形、無音検知、ストリーミングはやらない（MVP）

---

## 1. 既定ホットキー（被りにくい案）

* **Ctrl + Alt + Shift + H** を初期値にする（設定で変更可能）
* 予約キー（Winキー系）は使わない

---

## 2. 非目標（今回やらない）

* 句読点や文体の自動整形
* 無音検知（VAD）による自動停止
* リアルタイム逐次字幕
* 多言語の自動判定強化（必要なら設定で切替）

---

## 3. 採用技術（推奨）

### 3.1 文字起こしエンジン

* `whisper.cpp` の **`whisper-cli.exe`** を同梱して subprocess 実行 ([GitHub][2])

  * 利点：Python側の重い依存を避けられる／軽量モデルで快適に回せる
  * 注意：Windows環境によっては **VC++ 再頒布可能パッケージ**が必要な場合がある（同梱EXE運用の現実対策） ([Medium][3])

### 3.2 常駐／トレイ

* `pystray` でタスクトレイ常駐（メニュー：Start/Stop/Settings/Exit） ([Pystray][4])

### 3.3 ホットキー

* WinAPI `RegisterHotKey`（ctypes もしくは pywin32）で **グローバルホットキー**を取得

  * 典型構成：RegisterHotKey → WM_HOTKEY → UnregisterHotKey ([Tim Golden's Stuff][5])

### 3.4 録音

* Pythonでマイク入力を取得し **16kHz / mono / PCM16** に整形してWAV保存

  * ライブラリ例：`sounddevice` + `numpy` + `wave`（または `scipy.io.wavfile`）

### 3.5 クリップボード／貼り付け

* まず **クリップボード格納**を成功条件にする
* 自動貼り付けはオプション（Ctrl+Vを `SendInput` 等で送る）

---

## 4. 仕様（ユーザー体験）

1. ホットキー押下

* 待機 → **録音開始**（状態表示：Recording）

2. 再度ホットキー押下

* **録音停止** → **Transcribing**（whisper-cli実行）
* 文字起こし結果をクリップボードへ格納（状態：Copied）
* `auto_paste=true` の場合のみ Ctrl+V送信

3. 例外時

* 失敗を通知（Error）
* **一時WAVは必ず削除**（成功/失敗に関わらず）

---

## 5. ファイル構成（提案）

```
local_dictation/
  app/
    main.py                # エントリ：トレイ＋ホットキー＋状態管理
    hotkey_win.py          # RegisterHotKey/メッセージループ
    recorder.py            # 録音 start/stop、WAV一時ファイル生成
    transcriber.py         # whisper-cli呼び出し、出力テキスト取得
    clipboard_win.py       # クリップボード格納、任意で自動貼り付け
    settings.py            # configロード/保存/検証
    logger.py              # ログ
  bin/
    whisper-cli.exe
  models/
    ggml-*.bin             # 例：small/tiny等（ユーザー配置でも可）
  assets/
    tray_idle.ico
    tray_rec.ico
    tray_work.ico
  config.json
  README.md
```

---

## 6. 設定（config.json 例）

```json
{
  "hotkey": "CTRL+ALT+SHIFT+H",
  "language": "ja",
  "model_path": "./models/ggml-small.bin",
  "whisper_cli_path": "./bin/whisper-cli.exe",
  "auto_paste": false,
  "keep_trailing_newline": false,
  "temp_dir": "%TEMP%\\local_dictation",
  "audio_device": null,
  "sample_rate": 16000
}
```

---

## 7. 実装タスク（マイルストーン＋受け入れ基準）

### M0：前提チェック（エンジン起動確認）

**タスク**

* `whisper-cli.exe -h` が動く
* モデルパスが正しい
* WAV入力で文字が出る

**受け入れ基準**

* 同梱CLIが単体で起動し、WAVから日本語文字起こしが成功する ([Hugging Face][1])

---

### M1：録音（start/stop）→一時WAV生成

**タスク**

* 録音開始でバッファリング開始
* 録音停止で **temp_dir** にWAV生成（16kHz/mono/PCM16）
* 失敗しても例外を握りつぶさずログ

**受け入れ基準**

* 5秒録音してWAVが生成され、再生できる

---

### M2：WAV → 文字起こし（subprocess）

**タスク**

* subprocessで whisper-cli 実行
* **出力取得方法は2段構え**にする

  1. txt出力オプション（例：`--output-txt` 等）を使えるならそれを採用 ([GitHub][6])
  2. 使えない／環境差がある場合は stdout を回収
* 起動前に `whisper-cli -h` を一度読んで、利用可能オプションを自動判定（環境差吸収）

**受け入れ基準**

* WAV→テキストが取得でき、エラー時は例外ログが残る

---

### M3：一時WAVの自動削除（溜まらない保証）

**タスク**

* `try: ... finally:` で **必ず削除**
* 連続実行してもtemp_dirが増えない

**受け入れ基準**

* 10回連続利用後、temp_dirにWAVが残っていない

---

### M4：ホットキー常駐（RegisterHotKey）

**タスク**

* 指定ホットキーを登録
* WM_HOTKEY受信で録音トグル
* アプリ終了時に UnregisterHotKey

**受け入れ基準**

* どのアプリが前面でもホットキーで録音トグルが動作する ([Tim Golden's Stuff][5])

---

### M5：クリップボード格納＋任意の自動貼り付け

**タスク**

* テキストをクリップボードへ
* `auto_paste=true` の場合のみ Ctrl+V送信
* 末尾改行の保持/除去を設定で制御

**受け入れ基準**

* メモ帳/ブラウザ/Slack等で貼り付けが実用レベルで動く（最低でもクリップボードは必ず入る）

---

### M6：トレイUI（pystray）

**タスク**

* トレイアイコン表示
* メニュー：Start/Stop（状態に応じて表示切替）/Exit
* 状態ごとにアイコンやtooltip変更（Idle/Recording/Working）

**受け入れ基準**

* バックグラウンド常駐し、UIなしでも操作可能（ホットキー＋トレイ終了） ([Pystray][4])

---

## 8. 状態管理（実装ガイド）

* 状態は最小でOK：`IDLE / RECORDING / TRANSCRIBING`
* 競合防止：`TRANSCRIBING` 中のホットキーは無視（またはキューに積まず無視）
* エラー時：IDLEに戻す（録音バッファ破棄、temp削除）

---

## 9. 実行・配布

### 開発実行

* `python -m venv .venv` → `pip install -r requirements.txt` → `python app/main.py`

### 依存（例）

* pystray, pillow（トレイアイコン用）
* sounddevice, numpy
* pywin32（またはctypesのみでWinAPI直叩き）

### 単体exe化（任意）

* PyInstallerで `main.py` を1ファイル化
* `bin/whisper-cli.exe` と `models/` を同梱
* VC++再頒布可能パッケージが必要になる環境がある点だけREADMEに明記 ([Medium][3])

---

## 10. 追加オプション（後回しでOK）

* 入力デバイス選択UI（設定ファイル編集でも可）
* 「最後の結果を再貼り付け」ホットキー
* モデル切替（tiny/base等）

---

この計画書どおりに実装すれば、「被らないホットキーで録音トグル → ローカル文字起こし → クリップボード（＋任意貼り付け） → WAVは絶対残さない」まで最短で到達できます。

[1]: https://huggingface.co/spaces/natasa365/whisper.cpp/blob/4c88a2785ff6ece8196fc54a8760b32060ca35cf/examples/cli/README.md?utm_source=chatgpt.com "examples/cli/README.md · natasa365/whisper.cpp at ..."
[2]: https://github.com/ggml-org/whisper.cpp?utm_source=chatgpt.com "ggml-org/whisper.cpp"
[3]: https://medium.com/%40WattsOnAI/run-whisper-on-windows-to-extract-text-from-audio-81a32b7a6452?utm_source=chatgpt.com "Run Whisper on Windows to Extract Text from Audio"
[4]: https://pystray.readthedocs.io/en/latest/usage.html?utm_source=chatgpt.com "Creating a system tray icon — pystray 0.19.5 documentation"
[5]: https://timgolden.me.uk/python/win32_how_do_i/catch_system_wide_hotkeys.html?utm_source=chatgpt.com "Catch system-wide hotkeys - Python Stuff - Tim Golden"
[6]: https://github.com/ggml-org/whisper.cpp/issues/1398?utm_source=chatgpt.com "Generating a .txt file · Issue #1398 · ggml-org/whisper.cpp"
