"""文字起こしの調整・デバッグ用に、録音とその結果を一定期間残す

logs/history/ に、1回の録音ごとに同じ名前の2つのファイルを置く。
- <日時>.wav  : 前処理をかける前の、マイクから入ったままの音声
                （前処理やモデルの設定を変えて、同じ音声で比べ直せるようにする）
- <日時>.json : 文字起こし結果・セグメントごとの信頼度・処理時間・そのときの設定

無音と判定して捨てた録音も、閾値の調整に使えるよう理由つきで残す。
録音を取り消した場合（Esc）は残さない。
"""
import json
import time
from datetime import datetime
from pathlib import Path

from .logger import logger
from .settings import current_settings

HISTORY_DIR = Path("logs") / "history"
# 日数とは別に、件数でも上限を設けてディスクを使いすぎないようにする
# （16kHz モノラルで 1 分の録音が約 2MB）
MAX_ENTRIES = 1000

# 結果を読むときに必要になる設定だけを記録する
_RECORDED_SETTINGS = (
    "model_name", "device", "compute_type", "language", "beam_size", "temperature",
    "vad_filter", "highpass_hz", "normalize_audio", "initial_prompt",
    "sample_rate", "silence_threshold", "noise_floor",
)


def _entry_stem(started_at: float) -> str:
    dt = datetime.fromtimestamp(started_at)
    return dt.strftime("%Y%m%d-%H%M%S-") + f"{dt.microsecond // 1000:03d}"


def record(capture, result, stopped_at: float, finished_at: float) -> None:
    """1回分の録音と結果を保存する。失敗しても本来の処理は止めない"""
    if not current_settings.debug_history_enabled:
        return
    try:
        _write_entry(capture, result, stopped_at, finished_at)
        cleanup()
    except Exception as e:
        logger.warning(f"Failed to save debug history: {e}")


def _write_entry(capture, result, stopped_at: float, finished_at: float) -> None:
    from .recorder import _lazy_import
    _sd, _np, wav = _lazy_import()

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stem = _entry_stem(capture.started_at)

    wav.write(str(HISTORY_DIR / f"{stem}.wav"), capture.sample_rate, capture.raw)

    entry = {
        "recorded_at": datetime.fromtimestamp(capture.started_at).isoformat(timespec="milliseconds"),
        "audio_file": f"{stem}.wav",
        "audio_seconds": round(len(capture.raw) / capture.sample_rate, 2),
        "rms": round(capture.rms, 1),
        "max_amp": round(capture.max_amp, 1),
        "skipped_reason": capture.skipped_reason,
        "preprocess": capture.preprocess_info,
        "text": result.text if result else None,
        "error": result.error if result else None,
        "device_used": result.device if result else None,
        "duration_after_vad": result.duration_after_vad if result else None,
        "timing": {
            # ホットキーで止めてから貼り付けが終わるまでの、体感の待ち時間
            "total_wait": round(finished_at - stopped_at, 3),
            "transcribe": round(result.elapsed, 3) if result else None,
        },
        "segments": result.segments if result else [],
        "settings": {key: getattr(current_settings, key, None) for key in _RECORDED_SETTINGS},
    }

    with open(HISTORY_DIR / f"{stem}.json", "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)


def cleanup() -> None:
    """保存期間を過ぎたもの・件数の上限を超えた古いものを消す"""
    if not HISTORY_DIR.exists():
        return

    cutoff = time.time() - current_settings.debug_history_days * 86400
    # ファイル名が日時なので、名前順に並べれば古い順になる
    stems = sorted({p.stem for p in HISTORY_DIR.iterdir() if p.suffix in (".wav", ".json")})
    excess = max(len(stems) - MAX_ENTRIES, 0)

    for index, stem in enumerate(stems):
        try:
            started_at = datetime.strptime(stem[:15], "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            # 自分が作ったものではないファイルには触らない
            continue
        if index < excess or started_at < cutoff:
            for suffix in (".wav", ".json"):
                (HISTORY_DIR / f"{stem}{suffix}").unlink(missing_ok=True)
