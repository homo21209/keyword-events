"""
Шина событий и диспетчер.

AudioEvent — это факт «ключевые слова прозвучали, и по ним надо
выполнить обработчик». EventBus — точка, где обработчики
подписываются на события.

Важное требование: поток распознавания речи работает отдельно от
графического интерфейса, поэтому emit() кладёт событие в очередь, а
выполняют обработчики его уже в главном потоке (см. EventBus.process).
Это снимает классическую ошибку «Qt: операция из другого потока».
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .matcher import Match
from .rules import Rule

# Подписка на все события сразу
ANY = "*"


@dataclass(slots=True)
class AudioEvent:
    """Сработавшее ключевое слово."""

    rule_id: str
    action: str
    text: str                       # весь фрагмент, в котором нашлось совпадение
    matched: str                    # непосредственно совпавшие слова
    phrase: str                     # фраза-эталон из правила
    score: float                    # уверенность совпадения, 0..1
    timestamp: float = field(default_factory=time.monotonic)  # время срабатывания
    audio_time: float = 0.0         # позиция в аудиофайле, секунды
    is_final: bool = True           # False — событие по частичному результату
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_match(
        cls,
        match: Match,
        *,
        text: str,
        audio_time: float = 0.0,
        is_final: bool = True,
    ) -> "AudioEvent":
        """Собрать событие из результата сопоставления."""
        rule: Rule = match.rule
        return cls(
            rule_id=rule.id,
            action=rule.action,
            text=text,
            matched=match.matched,
            phrase=match.phrase,
            score=match.score,
            audio_time=audio_time,
            is_final=is_final,
            payload=dict(rule.payload),
            tags=list(rule.tags),
        )

    def describe(self) -> str:
        """Человекочитаемая строка для журнала."""
        state = "итог" if self.is_final else "черновик"
        return (
            f"[{self.audio_time:7.2f}s] {self.action:<14} "
            f"«{self.matched}» -> «{self.phrase}» {self.score:.0%} ({state})"
        )


Handler = Callable[[AudioEvent], None]


class EventBus:
    """Шинa событий: подписка обработчиков и потокобезопасная очередь."""

    def __init__(self, *, max_queue: int = 10000, history: int = 1000) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self._queue: deque[AudioEvent] = deque(maxlen=max_queue)
        self._history: deque[AudioEvent] = deque(maxlen=history)
        self._lock = threading.Lock()

    # ---------------- подписка ----------------

    def subscribe(self, action: str, handler: Handler) -> None:
        """Подписаться на события одного действия (или ANY — на все)."""
        with self._lock:
            self._subs.setdefault(action, []).append(handler)

    def unsubscribe(self, action: str, handler: Handler) -> None:
        """Отписаться. Ошибок при повторной отписке не возникает."""
        with self._lock:
            handlers = self._subs.get(action)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def handlers_for(self, event: AudioEvent) -> list[Handler]:
        """Все обработчики, подписанные на данное событие."""
        with self._lock:
            return list(self._subs.get(event.action, ())) + list(self._subs.get(ANY, ()))

    @property
    def actions(self) -> list[str]:
        with self._lock:
            return sorted(self._subs)

    # ---------------- передача событий ----------------

    def emit(self, event: AudioEvent) -> None:
        """Поставить событие в очередь. Безопасно вызывать из любого потока."""
        with self._lock:
            self._queue.append(event)
            self._history.append(event)

    def process(self, limit: int | None = None) -> int:
        """Выполнить накопленные события в текущем потоке.

        Возвращает количество обработанных событий.
        Ошибка в одном обработчике не должна ронять остальные —
        она перехватывается и возвращается вызывающему через on_error.
        """
        processed = 0
        while limit is None or processed < limit:
            with self._lock:
                if not self._queue:
                    break
                event = self._queue.popleft()

            for handler in self.handlers_for(event):
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001 - обработчик не должен ронять систему
                    self.on_error(event, handler, exc)
            processed += 1
        return processed

    @staticmethod
    def on_error(event: AudioEvent, handler: Handler, exc: Exception) -> None:
        """Обработчик ошибки по умолчанию — печать в консоль."""
        name = getattr(handler, "__qualname__", repr(handler))
        print(f"[event-bus] ОШИБКА в обработчике {name} для {event.action}: {exc}")

    # ---------------- журнал ----------------

    def history(self, limit: int | None = None) -> list[AudioEvent]:
        """Последние сработавшие события (для журнала и графика)."""
        with self._lock:
            items = list(self._history)
        return items[-limit:] if limit else items

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        """Сбросить очередь и журнал (кнопка «Очистить» в интерфейсе)."""
        with self._lock:
            self._queue.clear()
            self._history.clear()


__all__ = ["AudioEvent", "EventBus", "ANY", "Handler"]