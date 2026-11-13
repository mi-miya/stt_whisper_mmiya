"""効果音

winsound.Beep は音量を変えられず、イヤホンでは大きすぎるため、
正弦波の WAV をその場で作り、音量を掛けてから PlaySound で鳴らす。
"""
import io
import math
import threading
import time
import wave
import winsound
from array import array

from .settings import current_settings
from .logger import logger

SAMPLE_RATE = 22050
FADE_SECONDS = 0.005  # 音の頭と終わりのプツッという音を防ぐ

# (周波数 Hz, 長さ 秒) の並び
# 1000Hz を超えるとイヤホンで耳に刺さるため、低めの音にしている。
# Bluetooth の通話用のモードは 300Hz くらいより低い音を通さないので、それより下げない
#
# 録音開始の音は鳴らさない。Bluetooth イヤホンは、マイクを開くと音楽用から通話用のモードに
# 切り替わり、マイクの音はすぐ届き始めるが、イヤホンへ音を出す側の切り替えはそれより遅れて終わる。
# そのため録音開始の音は、話し始めてよくなってから 1 秒近く遅れてしか鳴らせず、合図にならない。
# 録音中かどうかはアイコンの色で示す
FINISH_TONES = [(660, 0.1), (880, 0.1)]  # 上がる音
CANCEL_TONES = [(523, 0.1), (392, 0.1)]  # 下がる音

# 試し聞きでマイクを開いたとき、これ以上かかったら音楽用からの切り替えが起きたと見て、
# 音を出す側の切り替えが終わるまで待ってから鳴らす（待たないと音が消える）
MODE_SWITCH_OPEN_SECONDS = 0.3
DELAY_AFTER_MODE_SWITCH = 0.8


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


def play_finish():
    _play_in_background(FINISH_TONES, "finish")


def play_cancel():
    _play_in_background(CANCEL_TONES, "cancel")


def _open_mic_for_preview():
    """試し聞きの間だけマイクを開く。開けなければ None"""
    try:
        import sounddevice as sd
        stream = sd.InputStream(
            samplerate=current_settings.sample_rate,
            channels=1,
            dtype='int16',
            device=current_settings.audio_device,
            callback=lambda *args: None,  # 音は使わない
        )
        stream.start()
        return stream
    except Exception as e:
        logger.warning(f"Failed to open microphone for sound preview: {e}")
        return None


def _preview(volume: int):
    # 本番の効果音は、マイクを開いている間（またはその直後）に鳴る。
    # Bluetooth イヤホンは、マイクを開くと通話用のモードに切り替わり、
    # 音楽用のモードとは聞こえる大きさが変わるため、試し聞きでも同じようにマイクを開いて鳴らす
    opening = time.monotonic()
    stream = _open_mic_for_preview()
    if stream is not None and time.monotonic() - opening >= MODE_SWITCH_OPEN_SECONDS:
        time.sleep(DELAY_AFTER_MODE_SWITCH)
    try:
        # 文字にできたときの音、少し間を空けて、取り消したときの音
        _play(FINISH_TONES + [(0, 0.4)] + CANCEL_TONES, volume, "preview")
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                logger.warning(f"Failed to close preview microphone: {e}")


def preview(volume: int):
    """設定画面の「試し聞き」用。効果音をオフにしていても、指定の音量で鳴らす"""
    threading.Thread(target=_preview, args=(int(volume),), daemon=True).start()
