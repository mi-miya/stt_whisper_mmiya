"""効果音

winsound.Beep は音量を変えられず、イヤホンでは大きすぎるため、
正弦波の WAV をその場で作り、音量を掛けてから PlaySound で鳴らす。
"""
import io
import math
import threading
import wave
import winsound
from array import array

from .settings import current_settings
from .logger import logger

SAMPLE_RATE = 22050
FADE_SECONDS = 0.005  # 音の頭と終わりのプツッという音を防ぐ

# (周波数 Hz, 長さ 秒) の並び
START_TONES = [(1000, 0.2)]
FINISH_TONES = [(1000, 0.1), (1500, 0.1)]  # 上がる音
CANCEL_TONES = [(800, 0.1), (500, 0.1)]  # 下がる音


def _amplitude(volume: int) -> float:
    """音量 (0〜100) を振幅 (0.0〜0.9) に直す。耳の感じ方に合わせて2乗で効かせる"""
    v = max(0, min(100, volume)) / 100
    return 0.9 * v * v


def _make_wav(tones, volume: int) -> bytes:
    amp = _amplitude(volume) * 32767
    fade = int(SAMPLE_RATE * FADE_SECONDS)
    samples = array('h')
    for freq, seconds in tones:
        n = int(SAMPLE_RATE * seconds)
        for i in range(n):
            gain = min(1.0, i / fade, (n - 1 - i) / fade)
            samples.append(int(amp * gain * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _play(tones, volume: int, name: str):
    if volume <= 0:
        return
    try:
        # SND_MEMORY は SND_ASYNC と併用できないため、呼び出し側でスレッドに逃がす
        winsound.PlaySound(_make_wav(tones, volume), winsound.SND_MEMORY)
    except Exception as e:
        logger.error(f"Failed to play {name} sound: {e}")


def _play_in_background(tones, name: str):
    if not current_settings.sound_enabled:
        return
    volume = current_settings.sound_volume
    threading.Thread(target=_play, args=(tones, volume, name), daemon=True).start()


def play_start():
    _play_in_background(START_TONES, "start")


def play_finish():
    _play_in_background(FINISH_TONES, "finish")


def play_cancel():
    _play_in_background(CANCEL_TONES, "cancel")


def preview(volume: int):
    """設定画面の「試し聞き」用。効果音をオフにしていても、指定の音量で鳴らす"""
    threading.Thread(target=_play, args=(FINISH_TONES, int(volume), "preview"), daemon=True).start()
