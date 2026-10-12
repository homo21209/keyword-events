"""
База обработчиков и реестр действий.

Правило в rules.json ссылается на обработчик именем вида "clock.set".
Это имя — общая точка сопоставления между конфигурацией и кодом.

    {
      "id": "clock_stop",
      "action": "clock.set",
      "phrases": ["часы остановились"],
      "payload": {"running": false}
    }
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # только для подсказок типов, без циклической зависимости
    from core.events import AudioEvent

# Колбэк изменения состояния: (обработчик, событие)
ChangeCallback = Callable[["Handler", "AudioEvent | None"], None]


class Handler:
    """Общий интерфейс обработчика."""

    # Имя действия, на которое подписывается обработчик.
    action: str = "noop"

    def __init__(self, on_change: ChangeCallback | None = None) -> None:
        self.on_change = on_change
        self.triggered = 0

    # ---- обязательный интерфейс ----

    def handle(self, event: "AudioEvent") -> None:
        """Обработать событие. Переопределяется в наследниках."""
        self.triggered += 1
        self._changed(event)

    # ---- вспомогательное ----

    def _changed(self, event: "AudioEvent | None" = None) -> None:
        """Сообщить подписчикам об изменении состояния."""
        if self.on_change is not None:
            self.on_change(self, event)

    def snapshot(self) -> dict[str, Any]:
        """Текущее состояние — для интерфейса и отчёта."""
        return {"action": self.action, "triggered": self.triggered}

    def __repr__(self) -> str:  # pragma: no cover - служебное
        return f"<{type(self).__name__} action={self.action!r} fired={self.triggered}>"


# ----------------------------------------------------------------------
#  Реестр
# ----------------------------------------------------------------------

_REGISTRY: dict[str, type[Handler]] = {}


def handler(action: str) -> Callable[[type[Handler]], type[Handler]]:
    """Декоратор, регистрирующий класс обработчика под именем действия.

        @handler("clock.set")
        class ClockHandler(Handler):
            ...
    """

    def decorator(cls: type[Handler]) -> type[Handler]:
        cls.action = action
        _REGISTRY[action] = cls
        return cls

    return decorator


def register(action: str, cls: type[Handler]) -> None:
    """Зарегистрировать класс обработчика вручную."""
    if not issubclass(cls, Handler):
        raise TypeError(f"{cls!r} не является подклассом Handler")
    cls.action = action
    _REGISTRY[action] = cls


def registered_actions() -> list[str]:
    """Все зарегистрированные действия, по алфавиту."""
    return sorted(_REGISTRY)


def create_handler(action: str, **kwargs: Any) -> Handler | None:
    """Создать обработчик по имени действия.

    Возвращает None, если действие не зарегистрировано: правило может
    ссылаться на обработчик, которого пока нет, и это не повод
    останавливать всю систему — событие просто не выполнится.
    """
    cls = _REGISTRY.get(action)
    if cls is None:
        return None
    return cls(**kwargs)


__all__ = ["Handler", "handler", "register", "create_handler", "registered_actions",
           "ChangeCallback"]