"""ポータブル配布パッケージを作成するスクリプト

使用方法:
1. Python embeddable版をダウンロード（https://www.python.org/downloads/windows/）
2. このスクリプトを実行
3. dist/ フォルダに配布用パッケージが作成される

注意:
- Python embeddable版は別途ダウンロードが必要です
- whisper-cli.exe と models/ は含まれません（別途配布）
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
import zipfile
import urllib.request


# 設定
PYTHON_VERSION = "3.11.7"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
OUTPUT_NAME = "stt_whisper_portable"


def download_python_embed(dest_dir: Path) -> bool:
    """Python embeddable版をダウンロード"""
    zip_path = dest_dir / "python-embed.zip"

    print(f"Downloading Python {PYTHON_VERSION} embeddable...")
    try:
        urllib.request.urlretrieve(PYTHON_EMBED_URL, zip_path)

        print("Extracting...")
        python_dir = dest_dir / "python"
        python_dir.mkdir(exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(python_dir)

        # _pth ファイルを修正して import を有効にする
        pth_files = list(python_dir.glob("*._pth"))
        if pth_files:
            pth_file = pth_files[0]
            content = pth_file.read_text()
            # import site のコメントを解除
            content = content.replace("#import site", "import site")
            # Lib フォルダを追加
            content += "\n../Lib\n../Lib/site-packages\n"
            pth_file.write_text(content)

        zip_path.unlink()
        print(f"Python extracted to {python_dir}")
        return True

    except Exception as e:
        print(f"Error downloading Python: {e}")
        return False


def install_dependencies(dest_dir: Path) -> bool:
    """依存ライブラリをインストール"""
    python_exe = dest_dir / "python" / "python.exe"
    lib_dir = dest_dir / "Lib" / "site-packages"
    lib_dir.mkdir(parents=True, exist_ok=True)

    print("Installing dependencies...")

    # pip をインストール
    print("Installing pip...")
    try:
        subprocess.run(
            [str(python_exe), "-m", "ensurepip", "--upgrade"],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError:
        # get-pip.py を使用
        print("Using get-pip.py...")
        getpip_path = dest_dir / "get-pip.py"
        urllib.request.urlretrieve(
            "https://bootstrap.pypa.io/get-pip.py",
            getpip_path
        )
        subprocess.run([str(python_exe), str(getpip_path)], check=True)
        getpip_path.unlink()

    # requirements.txt から依存ライブラリをインストール
    project_root = Path(__file__).parent.parent
    requirements_file = project_root / "requirements.txt"

    if requirements_file.exists():
        print(f"Installing from {requirements_file}...")
        subprocess.run([
            str(python_exe), "-m", "pip", "install",
            "-r", str(requirements_file),
            "--target", str(lib_dir),
            "--no-warn-script-location"
        ], check=True)

    print("Dependencies installed")
    return True


def copy_app_files(dest_dir: Path) -> bool:
    """アプリケーションファイルをコピー"""
    project_root = Path(__file__).parent.parent
    app_src = project_root / "app"
    app_dest = dest_dir / "app"

    print("Copying application files...")

    # app/ フォルダをコピー
    if app_dest.exists():
        shutil.rmtree(app_dest)
    shutil.copytree(app_src, app_dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    # config.default.json をコピー
    default_config = project_root / "config.default.json"
    if default_config.exists():
        shutil.copy(default_config, dest_dir / "config.default.json")

    # bin/ フォルダを作成（空）
    bin_dir = dest_dir / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "README.txt").write_text(
        "このフォルダに whisper-cli.exe と関連DLLを配置してください。\n\n"
        "ダウンロード先:\n"
        "https://github.com/ggerganov/whisper.cpp/releases\n"
    )

    # models/ フォルダを作成（空）
    models_dir = dest_dir / "models"
    models_dir.mkdir(exist_ok=True)
    (models_dir / "README.txt").write_text(
        "このフォルダにWhisperモデルファイル (*.bin) を配置してください。\n\n"
        "ダウンロード先:\n"
        "https://huggingface.co/ggerganov/whisper.cpp/tree/main\n\n"
        "推奨モデル:\n"
        "- ggml-small.bin (466MB) - 高速・軽量\n"
        "- ggml-medium.bin (1.5GB) - バランス型\n"
        "- ggml-large-v3-turbo.bin (1.5GB) - 高精度・推奨\n"
    )

    # logs/ フォルダを作成
    logs_dir = dest_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    print("Application files copied")
    return True


def create_start_scripts(dest_dir: Path) -> bool:
    """起動スクリプトを作成"""
    print("Creating start scripts...")

    # start.bat - 通常起動
    start_bat = dest_dir / "start.bat"
    start_bat.write_text(
        '@echo off\n'
        'cd /d "%~dp0"\n'
        'python\\pythonw.exe -m app.main\n',
        encoding='utf-8'
    )

    # debug.bat - デバッグ起動
    debug_bat = dest_dir / "debug.bat"
    debug_bat.write_text(
        '@echo off\n'
        'cd /d "%~dp0"\n'
        'python\\python.exe -m app.main\n'
        'pause\n',
        encoding='utf-8'
    )

    # README.txt
    readme = dest_dir / "README.txt"
    readme.write_text(
        '============================================\n'
        '  ローカルWhisper 音声入力ツール\n'
        '============================================\n\n'
        '【初回セットアップ】\n'
        '1. bin/ フォルダに whisper-cli.exe を配置\n'
        '2. models/ フォルダにモデルファイル (*.bin) を配置\n'
        '3. start.bat をダブルクリックして起動\n\n'
        '【使い方】\n'
        '1. Ctrl+Alt+Shift+J を押して録音開始\n'
        '2. 話し終わったらもう一度同じキーを押す\n'
        '3. 認識結果がクリップボードにコピーされます\n\n'
        '【設定変更】\n'
        '画面左上のマイクアイコンを右クリック → 設定\n\n'
        '【トラブルシューティング】\n'
        '・ログは logs/app.log に出力されます\n'
        '・debug.bat で起動するとコンソールにログが表示されます\n',
        encoding='utf-8'
    )

    print("Start scripts created")
    return True


def create_zip(dest_dir: Path, output_path: Path) -> bool:
    """配布用ZIPファイルを作成"""
    print(f"Creating ZIP file: {output_path}")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in dest_dir.rglob('*'):
            if file_path.is_file():
                arcname = file_path.relative_to(dest_dir.parent)
                zf.write(file_path, arcname)

    print(f"ZIP file created: {output_path}")
    return True


def main():
    """メイン処理"""
    project_root = Path(__file__).parent.parent
    dist_dir = project_root / "dist"
    output_dir = dist_dir / OUTPUT_NAME

    print("=" * 50)
    print("ポータブル配布パッケージ作成ツール")
    print("=" * 50)
    print()

    # 出力ディレクトリをクリア
    if output_dir.exists():
        print(f"Removing existing {output_dir}...")
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True)

    # 各ステップを実行
    steps = [
        ("Python embeddable版をダウンロード", lambda: download_python_embed(output_dir)),
        ("依存ライブラリをインストール", lambda: install_dependencies(output_dir)),
        ("アプリケーションファイルをコピー", lambda: copy_app_files(output_dir)),
        ("起動スクリプトを作成", lambda: create_start_scripts(output_dir)),
    ]

    for step_name, step_func in steps:
        print()
        print(f"[Step] {step_name}...")
        if not step_func():
            print(f"Error in step: {step_name}")
            return 1

    # ZIPファイルを作成
    print()
    zip_path = dist_dir / f"{OUTPUT_NAME}.zip"
    create_zip(output_dir, zip_path)

    print()
    print("=" * 50)
    print("完了!")
    print("=" * 50)
    print()
    print(f"配布フォルダ: {output_dir}")
    print(f"配布ZIP: {zip_path}")
    print()
    print("注意: bin/ と models/ フォルダは空です。")
    print("whisper-cli.exe とモデルファイルを別途配布してください。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
