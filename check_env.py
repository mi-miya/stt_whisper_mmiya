import sys
import os
from pathlib import Path
import platform

def check():
    print("Checking environment for Local Whisper Dictation...")

    # 1. Check Python
    print(f"[OK] Python {sys.version}")

    # 2. Check imports
    missing_pkg = []
    try:
        import sounddevice
        print(f"[OK] sounddevice {sounddevice.__version__ if hasattr(sounddevice, '__version__') else ''}")
    except ImportError:
        missing_pkg.append("sounddevice")

    try:
        import numpy
        print(f"[OK] numpy {numpy.__version__}")
    except ImportError:
        missing_pkg.append("numpy")

    try:
        import pystray
        print("[OK] pystray")
    except ImportError:
        missing_pkg.append("pystray")

    try:
        import PIL
        print(f"[OK] Pillow {PIL.__version__}")
    except ImportError:
        missing_pkg.append("Pillow")

    if missing_pkg:
        print(f"[FAIL] Missing Python packages: {', '.join(missing_pkg)}")
        print("Run: pip install -r requirements.txt")
    else:
        print("[OK] All Python packages installed")

    # 3. Check External Dependencies
    bin_path = Path("bin/whisper-cli.exe")
    if bin_path.exists():
        print(f"[OK] whisper-cli.exe found at {bin_path}")
    else:
        print(f"[FAIL] whisper-cli.exe NOT found at {bin_path}")
        print("  -> Download 'main' or 'stream' binary from https://github.com/ggml-org/whisper.cpp/releases")
        print("  -> Rename it to whisper-cli.exe and place in bin/")

    model_path = Path("models/ggml-small.bin")
    # Check any bin file in models
    models = list(Path("models").glob("ggml-*.bin"))
    if models:
        print(f"[OK] Model(s) found: {[m.name for m in models]}")
        if not model_path.exists():
            print(f"[WARN] Default model {model_path} not found, but others exist. Update config.json if needed.")
    else:
        print(f"[FAIL] No models found in models/")
        print("  -> Download models (e.g. ggml-small.bin) from https://huggingface.co/ggerganov/whisper.cpp")

    # 4. Check Config
    if Path("config.json").exists():
        print("[OK] config.json exists")
    else:
        print("[WARN] config.json missing (will use defaults)")

    if not missing_pkg and bin_path.exists() and models:
        print("\n[SUCCESS] Environment looks ready!")
    else:
        print("\n[INCOMPLETE] Please fix the issues above before running.")

if __name__ == "__main__":
    check()
