"""Whisper に渡す前の、軽い音声前処理

どちらも数ミリ秒で終わる処理だけを置く。ノイズ除去のように音をゆがめる
処理は、雑音込みで学習した Whisper の精度をかえって下げやすいため入れない。
"""
import numpy as np

# 正規化後のピークの目標値。1.0 ぎりぎりまで上げると、後段の処理で
# クリップしやすいため少し余裕を残す
NORMALIZE_TARGET_PEAK = 0.9
# 小さすぎる録音でノイズまで大きくしすぎないよう、増幅の上限を決める (約 +20dB)
NORMALIZE_MAX_GAIN = 10.0


def highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    """cutoff_hz 以下の低音（空調・ファンのうなり、机の振動、吹かれ）を落とす"""
    from scipy.signal import butter, sosfilt

    # 2次なら、カットオフより上の声の帯域はほとんど変えずに済む
    sos = butter(2, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def normalize_peak(audio: np.ndarray) -> tuple:
    """ピークが NORMALIZE_TARGET_PEAK になるよう音量をそろえる

    Returns:
        (処理後の音声, かけた倍率)
    """
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 0.0:
        return audio, 1.0
    gain = min(NORMALIZE_TARGET_PEAK / peak, NORMALIZE_MAX_GAIN)
    return (audio * gain).astype(np.float32), gain


def preprocess(audio: np.ndarray, sample_rate: int, highpass_hz: float, normalize: bool) -> tuple:
    """設定に従って前処理をかける

    Args:
        audio: float32 / [-1.0, 1.0] / モノラルの音声
        highpass_hz: 0 以下ならハイパスをかけない

    Returns:
        (処理後の音声, デバッグ記録用の処理内容)
    """
    info = {"highpass_hz": highpass_hz if highpass_hz > 0 else None, "gain": None}

    # ナイキスト周波数以上のカットオフは butter() がエラーにするため除外する
    if 0 < highpass_hz < sample_rate / 2:
        audio = highpass(audio, sample_rate, highpass_hz)

    if normalize:
        audio, gain = normalize_peak(audio)
        info["gain"] = round(gain, 3)

    return audio, info
