"""
Источники звука: аудиофайл и микрофон.

Оба источника отдают одинаковые объекты Frame — порции оцифрованного
звука в формате 16 кГц / 16 бит / моно (требование движка распознавания).

Frame.t0 — время начала порции в секундах от начала сессии. Оно нужно
для двух вещей: синхронизации с воспроизведением и отметок в журнале
(«на 12.4 секунде прозвучало "часы остановились"»).

Для аудиофайла есть два режима:
    real_time=True  — звук идёт в колонки, распознавание синхронизировано
                      с реальным временем (демонстрация «как в MediaPlayer»);
    real_time=False — файл читается максимально быстро (тесты, пакетный
                      прогон, замер точности).
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

# Параметры, требуемые моделью распознавания
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # байт на отсчёт (int16)
FRAME_MS = 30      # длина порции, мс
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 отсчётов
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH

INPUT_DEVICE: str | int | None = None   # None — устройство по умолчанию
OUTPUT_DEVICE: str | int | None = None


class AudioError(RuntimeError):
    """Ошибка чтения или воспроизведения звука."""


@dataclass(slots=True)
class Frame:
    """Порция звука для распознавания."""

    data: bytes                 # 16 бит, little-endian, моно
    index: int = 0              # номер порции с начала сессии
    t0: float = 0.0             # время начала порции, секунды

    @property
    def duration(self) -> float:
        """Длительность порции в секундах."""
        return len(self.data) / 2 / SAMPLE_RATE


# ----------------------------------------------------------------------
#  Вспомогательные функции
# ----------------------------------------------------------------------

def resample(data: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Пересэмплировать одноканальный массив float32 в нужную частоту.

    Используется линейная интерполяция — для задачи распознавания речи
    этого достаточно, а внешние зависимости (scipy) не требуются.
    """
    if src_rate == dst_rate or data.size == 0:
        return data.astype(np.float32, copy=False)

    duration = data.size / src_rate
    dst_size = max(1, int(round(duration * dst_rate)))
    src_positions = np.arange(data.size, dtype=np.float64) / src_rate
    dst_positions = np.arange(dst_size, dtype=np.float64) / dst_rate

    return np.interp(dst_positions, src_positions, data).astype(np.float32)


def to_pcm16(data: np.ndarray) -> bytes:
    """Перевести float32 [-1..1] в little-endian int16 PCM."""
    return (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def from_pcm16(data: bytes) -> np.ndarray:
    """Обратное преобразование: bytes -> float32 в диапазоне [-1..1]."""
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Прочитать аудиофайл.

    Возвращает (данные float32 моно, частота дискретизации исходника).
    Поддерживаются форматы, которые умеет читать libsndfile
    (в том числе mp3 и wav).
    """
    import soundfile as sf

    path = Path(path)
    if not path.exists():
        raise AudioError(f"Аудиофайл не найден: {path}")

    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    except Exception as exc:  # noqa: BLE001 - приводим к понятному сообщению
        raise AudioError(
            f"Не удалось прочитать «{path.name}»: {exc}\n"
            "Поддерживаемые форматы: wav, flac, ogg, mp3.\n"
            "Если файл в другом формате — перекодируйте его в wav."
        ) from exc

    if data.ndim > 1:  # стерео -> моно (берём среднее)
        data = data.mean(axis=1)

    return np.ascontiguousarray(data, dtype=np.float32), int(rate)


def probe_duration(path: str | Path) -> float:
    """Длительность аудиофайла в секундах (без чтения данных)."""
    import soundfile as sf

    try:
        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate)
    except Exception as exc:  # noqa: BLE001
        raise AudioError(f"Не удалось получить сведения о «{Path(path).name}»: {exc}") from exc


# ----------------------------------------------------------------------
#  Источник: аудиофайл
# ----------------------------------------------------------------------

class FileSource:
    """Чтение аудиофайла порциями по 30 мс.

    При real_time=True одновременно воспроизводит файл через
    динамики и замедляет чтение до реального времени — так
    демонстрация выглядит естественно, а события приходят вовремя.
    """

    def __init__(self, path: str | Path, *, real_time: bool = False,
                 start: float = 0.0, until: float | None = None) -> None:
        self.path = Path(path)
        self.real_time = real_time
        self.start = max(0.0, start)
        self.until = until
        self._stopped = False

        self._data, self._rate = load_audio(self.path)
        self._resampled = resample(self._data, self._rate)
        self._player: AudioPlayer | None = None
        self._position = 0.0

    @property
    def duration(self) -> float:
        """Длительность файла в секундах."""
        return self._resampled.size / SAMPLE_RATE

    @property
    def position(self) -> float:
        """Текущая позиция воспроизведения в секундах."""
        if self._player is not None:
            return self._player.position
        return self._position

    @property
    def paused(self) -> bool:
        """Воспроизведение приостановлено."""
        return bool(self._player and self._player.paused)

    @property
    def stopped(self) -> bool:
        """Воспроизведение остановлено по команде."""
        return self._stopped

    def set_paused(self, paused: bool) -> None:
        """Поставить воспроизведение на паузу или продолжить."""
        if self._player is not None:
            self._player.set_paused(paused)

    def stop(self) -> None:
        """Прервать воспроизведение."""
        self._stopped = True

    def open(self) -> "FileSource":
        """Открыть устройство вывода, если включён реальный режим."""
        if self.real_time:
            self._player = AudioPlayer(self._data, self._rate)
            self._player.start()
            if self.start:
                self._player.seek(self.start)
        return self

    def close(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player = None

    def __enter__(self) -> "FileSource":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __iter__(self) -> Iterator[Frame]:
        """Отдавать порции одну за другой, пока не кончится файл."""
        begin = int(self.start * SAMPLE_RATE)
        end = self._resampled.size
        if self.until is not None:
            end = min(end, int(self.until * SAMPLE_RATE))

        position = begin
        index = 0
        while position + FRAME_SAMPLES <= end and not self._stopped:
            chunk = self._resampled[position:position + FRAME_SAMPLES]
            self._position = position / SAMPLE_RATE

            yield Frame(data=to_pcm16(chunk), index=index, t0=self._position)
            index += 1
            position += FRAME_SAMPLES

            if self.real_time:
                self._wait_for_playback(self._position)
            elif index % 200 == 0:
                time.sleep(0)  # дать другим потокам подышать

        self._position = position / SAMPLE_RATE

    def _wait_for_playback(self, t0: float) -> None:
        """Не отдавать порцию раньше, чем она прозвучит в колонках.

        Ориентируемся на часы звукового устройства, а не на системное
        время: иначе рассинхронизация накапливалась бы от порции к порции.
        На паузе ждём, пока воспроизведение не продолжится.
        """
        player = self._player
        if player is None:
            return

        while player.playing and (player.paused or player.position < t0):
            time.sleep(0.005)


class AudioPlayer:
    """Воспроизведение массива звука через динамики.

    Позиция отсчитывается по часам звукового устройства, а не по
    системному времени — поэтому рассинхронизация не накапливается.
    """

    def __init__(self, data: np.ndarray, sample_rate: int,
                 device: str | int | None = None, volume: float = 1.0) -> None:
        import sounddevice as sd

        self._data = np.ascontiguousarray(data * volume, dtype=np.float32)
        self._rate = sample_rate
        self._pos = 0
        self._finished = False
        self._paused = False
        self._out = sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=device if device is not None else OUTPUT_DEVICE,
            callback=self._callback,
        )

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        if self._paused or self._finished:
            # на паузе буфер заполняем тишиной и позицию не двигаем
            outdata[:] = 0.0
            return

        start = self._pos
        end = min(start + frames, self._data.size)

        if end > start:
            outdata[: end - start, 0] = self._data[start:end]
        if end - start < frames:  # файл закончился — тишина
            outdata[end - start:, 0] = 0.0
            self._finished = True
        self._pos = end

    def start(self) -> None:
        self._out.start()

    def set_paused(self, paused: bool) -> None:
        """Пауза / продолжение.

        Останавливается поток звукового устройства, поэтому позиция
        перестаёт меняться и отсчёт идёт ровно с места остановки.
        """
        paused = bool(paused)
        if paused == self._paused:
            return

        self._paused = paused
        try:
            if paused:
                self._out.stop()
            else:
                self._out.start()
        except Exception:  # noqa: BLE001 - устройство могло закрыться
            self._paused = False

    def stop(self) -> None:
        try:
            self._out.stop()
        except Exception:  # noqa: BLE001 - устройство могло уже закрыться
            pass
        finally:
            self._out.close()

    def seek(self, seconds: float) -> None:
        self._pos = max(0, min(self._data.size, int(seconds * self._rate)))
        self._finished = False

    @property
    def position(self) -> float:
        return self._pos / self._rate

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def playing(self) -> bool:
        return not self._finished


# ----------------------------------------------------------------------
#  Источник: микрофон
# ----------------------------------------------------------------------

class MicSource:
    """Захват звука с микрофона в реальном времени.

    Звуковое устройство работает в своём потоке и складывает порции
    в очередь; основной поток забирает их оттуда. Это развязывает
    захват и распознавание — распознавание не может «съесть» звук.
    """

    def __init__(self, *, device: str | int | None = None,
                 max_queue: int = 100, on_error=None) -> None:
        import sounddevice as sd

        self._queue: deque[Frame] = deque(maxlen=max_queue)
        self._lock = threading.Lock()
        self._count = 0
        self._t0 = time.monotonic()
        self._running = False
        self._dropped = 0
        self.on_error = on_error
        self._device = device

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=device if device is not None else INPUT_DEVICE,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:  # переполнение буфера — считаем, но продолжаем
            self._dropped += 1
        if not self._running:
            return

        raw = bytes(indata)
        with self._lock:
            was_full = len(self._queue) == self._queue.maxlen
            self._queue.append(
                Frame(
                    data=raw,
                    index=self._count,
                    t0=(time.monotonic() - self._t0),
                )
            )
            if was_full:
                self._dropped += 1
            self._count += 1

    def open(self) -> "MicSource":
        self._running = True
        self._t0 = time.monotonic()
        self._stream.start()
        return self

    def close(self) -> None:
        self._running = False
        try:
            self._stream.stop()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._stream.close()

    def __enter__(self) -> "MicSource":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __iter__(self) -> Iterator[Frame]:
        """Бесконечный поток порций. Останавливается только close()."""
        while True:
            with self._lock:
                frame = self._queue.popleft() if self._queue else None
            if frame is None:
                if not self._running:
                    break
                time.sleep(0.005)
                continue
            yield frame

    @property
    def elapsed(self) -> float:
        """Сколько секунд идёт запись."""
        return time.monotonic() - self._t0

    @property
    def dropped(self) -> int:
        """Сколько порций потеряно из-за переполнения очереди."""
        return self._dropped


def list_devices(kind: str = "input") -> list[dict]:
    """Список аудиоустройств — для выбора микрофона в интерфейсе.

    kind: "input" (микрофоны) или "output" (динамики).
    """
    import sounddevice as sd

    if kind not in ("input", "output"):
        raise ValueError("kind должен быть 'input' или 'output'")

    wants_in = (kind == "input")
    channels_key = "max_input_channels" if wants_in else "max_output_channels"
    default_key = "default_" + kind

    result = []
    for index, device in enumerate(sd.query_devices()):
        if device[channels_key] > 0:
            result.append({
                "index": index,
                "name": device["name"],
                "channels": device[channels_key],
                "default_samplerate": int(device["default_samplerate"]),
                "is_default": device.get(default_key) is not None,
            })
    return result


def default_device_name(kind: str = "input") -> str:
    """Имя устройства по умолчанию — для показа в журнале."""
    try:
        import sounddevice as sd

        key = "default_" + ("input" if kind == "input" else "output")
        device = sd.query_devices(kind=key)
        return device["name"]
    except Exception:  # noqa: BLE001
        return "неизвестно"


__all__ = [
    "Frame",
    "FileSource",
    "MicSource",
    "AudioPlayer",
    "AudioError",
    "SAMPLE_RATE",
    "FRAME_MS",
    "FRAME_SAMPLES",
    "FRAME_BYTES",
    "INPUT_DEVICE",
    "OUTPUT_DEVICE",
    "resample",
    "to_pcm16",
    "from_pcm16",
    "load_audio",
    "probe_duration",
    "list_devices",
    "default_device_name",
]