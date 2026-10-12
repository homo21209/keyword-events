"""
Обработчики событий.

Обработчик — это то, что реально происходит, когда прозвучали ключевые
слова. В аннотации их два: остановить стрелки часов и вывести текст.
Здесь реализовано больше, чтобы было видно, что схема универсальна.

Все обработчики устроены одинаково:
    * не знают ни о каком распознавании речи — на вход только AudioEvent;
    * хранят своё состояние (что сейчас на экране);
    * умеют сообщить об изменении через колбэк on_change — так их
      подключает графический интерфейс, не вмешиваясь в логику.

Реестр позволяет расширять систему новыми обработчиками без правки ядра:
достаточно добавить функцию с декоратором @handler("имя.действия").
"""

from .base import Handler, handler, create_handler, registered_actions
from .builtin import (
    CaptionHandler,
    ClockHandler,
    LogHandler,
    SceneHandler,
    StatsHandler,
    beep,
    default_handlers,
)

__all__ = [
    "Handler",
    "handler",
    "create_handler",
    "registered_actions",
    "CaptionHandler",
    "ClockHandler",
    "LogHandler",
    "SceneHandler",
    "StatsHandler",
    "beep",
    "default_handlers",
]