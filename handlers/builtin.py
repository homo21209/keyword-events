"""
Встроенные обработчики.

Каждый из них соответствует одной строке из аннотации проекта:

    clock.set      -> остановить/запустить стрелки часов
    caption.show   -> вывести текст «А часы-то остановились!»
    scene.set      -> сменить изображение на экране
    log.write      -> записать произвольное сообщение
    stats.count    -> увеличить счётчик (для наглядной статистики)
    beep           -> звуковой отклик

Дополнительно добавлены действия для управления воспроизведением
(media.pause / media.resume / media.stop) и переход между сценами —
они показывают, что схема не завязана на конкретный пример.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from .base import ChangeCallback, Handler, handler

if TYPE_CHECKING:
    from core.events import AudioEvent

    ChangeCallback = ChangeCallback


# ----------------------------------------------------------------------
#  Часы — прямой сюжет аннотации
# ----------------------------------------------------------------------

@handler("clock.set")
class ClockHandler(Handler):
    """Управление стрелками часов.

    payload:
        running  (bool)   — идут стрелки или стоят;
        caption  (str)    — подпись, которую покажет интерфейс.
    """

    def __init__(self, on_change: ChangeCallback | None = None) -> None:
        super().__init__(on_change)
        self.running = True
        self.caption = ""
        self.stopped_at: float | None = None
        self.started_at: float | None = None

    def handle(self, event: "AudioEvent") -> None:
        payload = event.payload
        running = payload.get("running")
        if running is None:
            running = not self.running  # без параметра — переключение

        changed = bool(running) != self.running
        self.running = bool(running)
        if changed:
            # Запоминаем, когда часы последний раз вставали и когда
            # пошли. Значения не сбрасываются — это история, а не
            # текущее состояние.
            if self.running:
                self.started_at = event.audio_time
            else:
                self.stopped_at = event.audio_time

        if "caption" in payload:
            self.caption = str(payload["caption"])

        super().handle(event)

    def snapshot(self) -> dict[str, Any]:
        return {
            **super().snapshot(),
            "running": self.running,
            "caption": self.caption,
            "stopped_at": self.stopped_at,
            "started_at": self.started_at,
        }


# ----------------------------------------------------------------------
#  Текстовая подпись на экране
# ----------------------------------------------------------------------

@handler("caption.show")
class CaptionHandler(Handler):
    """Вывод текста поверх изображения.

    payload:
        text      (str)  — что показать;
        duration  (float, сек) — сколько держать, 0 = до следующего события;
        clear     (bool) — очистить подпись, игнорируя text.
    """

    def __init__(self, on_change: ChangeCallback | None = None) -> None:
        super().__init__(on_change)
        self.text = ""
        self.shown_at: float | None = None
        self.duration = 0.0

    def handle(self, event: "AudioEvent") -> None:
        payload = event.payload

        if payload.get("clear"):
            self.text = ""
            self.shown_at = None
        else:
            self.text = str(payload.get("text") or event.payload.get("caption") or event.phrase)
            self.duration = float(payload.get("duration", 0.0))
            self.shown_at = time.monotonic()

        super().handle(event)

    def expired(self) -> bool:
        """Подпись должна исчезнуть (если задан срок показа)."""
        return bool(
            self.duration > 0
            and self.shown_at is not None
            and (time.monotonic() - self.shown_at) > self.duration
        )

    def tick(self) -> None:
        """Проверить, не пора ли убрать подпись."""
        if self.expired():
            self.text = ""
            self._changed()

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "text": self.text, "duration": self.duration}


# ----------------------------------------------------------------------
#  Смена изображения / сцены
# ----------------------------------------------------------------------

@handler("scene.set")
class SceneHandler(Handler):
    """Переключение видимой картинки.

    payload:
        scene (str) — ключ сцены, например "singer", "clock", "both".
    """

    SCENES = ("singer", "clock", "both", "black")

    def __init__(self, on_change: ChangeCallback | None = None,
                 initial: str = "both") -> None:
        super().__init__(on_change)
        self.scene = initial
        self.history: list[tuple[float, str]] = []

    def handle(self, event: "AudioEvent") -> None:
        scene = str(event.payload.get("scene") or self.scene)
        if scene not in self.SCENES:
            # Неизвестную сцену лучше показать как есть, чем молча проигнорировать
            pass
        self.scene = scene
        self.history.append((event.audio_time, scene))
        super().handle(event)

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "scene": self.scene,
                "switches": len(self.history)}


# ----------------------------------------------------------------------
#  Журнал
# ----------------------------------------------------------------------

@handler("log.write")
class LogHandler(Handler):
    """Запись произвольного сообщения.

    payload:
        text (str)  — сообщение;
        level (str) — info | warn | error.
    """

    def __init__(self, on_change: ChangeCallback | None = None) -> None:
        super().__init__(on_change)
        self.lines: list[str] = []

    def handle(self, event: "AudioEvent") -> None:
        level = str(event.payload.get("level", "info")).upper()
        message = str(event.payload.get("text") or event.matched)
        self.lines.append(f"[{event.audio_time:7.2f}s] {level:<5} {message}")
        super().handle(event)

    def tail(self, count: int = 50) -> list[str]:
        """Последние записи журнала."""
        return self.lines[-count:]

    def clear(self) -> None:
        self.lines.clear()

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "lines": list(self.lines)}


# ----------------------------------------------------------------------
#  Счётчики
# ----------------------------------------------------------------------

@handler("stats.count")
class StatsHandler(Handler):
    """Увеличить именованный счётчик.

    payload:
        name  (str)   — имя счётчика;
        delta (float) — на сколько увеличить (по умолчанию 1).
    """

    def __init__(self, on_change: ChangeCallback | None = None) -> None:
        super().__init__(on_change)
        self.counters: dict[str, float] = {}

    def handle(self, event: "AudioEvent") -> None:
        name = str(event.payload.get("name") or event.rule_id)
        delta = float(event.payload.get("delta", 1))
        self.counters[name] = self.counters.get(name, 0.0) + delta
        super().handle(event)

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "counters": dict(self.counters)}


# ----------------------------------------------------------------------
#  Звуковой отклик
# ----------------------------------------------------------------------

def beep(frequency: float = 880.0, duration: float = 0.12, volume: float = 0.25) -> None:
    """Короткий звуковой сигнал (синус) через устройство вывода."""
    try:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, duration, int(sd.query_devices(kind="output")["default_samplerate"] * duration),
                        endpoint=False, dtype=np.float32)
        tone = (volume * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
        sd.play(tone, blocking=False)
    except Exception:  # noqa: BLE001 - звук не критичен
        pass


@handler("beep")
class BeepHandler(Handler):
    """Звуковой отклик на событие.

    payload:
        frequency (float) — частота в герцах;
        duration  (float) — длительность, сек;
        silent    (bool)  — не звучать (для тихих прогонов).
    """

    def __init__(self, on_change: ChangeCallback | None = None, *, silent: bool = False) -> None:
        super().__init__(on_change)
        self.silent = silent

    def handle(self, event: "AudioEvent") -> None:
        if not self.silent:
            beep(
                float(event.payload.get("frequency", 880.0)),
                float(event.payload.get("duration", 0.12)),
            )
        super().handle(event)


# ----------------------------------------------------------------------
#  Управление воспроизведением
# ----------------------------------------------------------------------

@handler("media.pause")
class MediaPauseHandler(Handler):
    """Поставить воспроизведение на паузу (например, по слову «тишина»)."""

    def handle(self, event: "AudioEvent") -> None:
        event.payload.setdefault("_control", "pause")
        super().handle(event)


@handler("media.resume")
class MediaResumeHandler(Handler):
    """Продолжить воспроизведение."""

    def handle(self, event: "AudioEvent") -> None:
        event.payload.setdefault("_control", "resume")
        super().handle(event)


@handler("media.stop")
class MediaStopHandler(Handler):
    """Остановить воспроизведение."""

    def handle(self, event: "AudioEvent") -> None:
        event.payload.setdefault("_control", "stop")
        super().handle(event)


# ----------------------------------------------------------------------
#  Набор по умолчанию
# ----------------------------------------------------------------------

def default_handlers(on_change: ChangeCallback | None = None,
                     *, silent: bool = False) -> dict[str, Handler]:
    """Создать стандартный набор обработчиков.

    Возвращает словарь «имя действия -> обработчик», готовый
    к подписке на шине событий.
    """
    instances = [
        ClockHandler(on_change),
        CaptionHandler(on_change),
        SceneHandler(on_change),
        LogHandler(on_change),
        StatsHandler(on_change),
        BeepHandler(on_change, silent=silent),
        MediaPauseHandler(on_change),
        MediaResumeHandler(on_change),
        MediaStopHandler(on_change),
    ]
    return {h.action: h for h in instances}


__all__ = [
    "ClockHandler",
    "CaptionHandler",
    "SceneHandler",
    "LogHandler",
    "StatsHandler",
    "BeepHandler",
    "MediaPauseHandler",
    "MediaResumeHandler",
    "MediaStopHandler",
    "default_handlers",
    "beep",
]