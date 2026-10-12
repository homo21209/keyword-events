"""
Сборка приложения: правила + обработчики + шина + распознаватель.

Модуль связывает воедино всё написанное и используется одинаково
из консольного режима и из графического интерфейса — чтобы поведение
системы не зависело от способа запуска.

    app = Application("config/rules.json")
    app.open_recognizer()
    app.run_file("песня.mp3")
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

from core.audio import FileSource, MicSource
from core.engine import EventEngine, Stats
from core.events import ANY, AudioEvent, EventBus
from core.rules import RuleSet
from core.stt import SpeechRecognizer, find_model

from handlers import default_handlers

# Корень проекта — отсюда берутся пути по умолчанию
ROOT = Path(__file__).resolve().parent
DEFAULT_RULES = ROOT / "config" / "rules.json"


class Application:
    """Готовая к работе система."""

    def __init__(
        self,
        rules_path: str | Path = DEFAULT_RULES,
        *,
        model: str | Path | None = None,
        silent: bool = False,
        emit_partial: bool = True,
        auto_open: bool = True,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.rules = RuleSet.load(self.rules_path)
        self.bus = EventBus()
        self.handlers: dict[str, Any] = {}
        self.silent = silent
        self.emit_partial = emit_partial

        # колбэки на изменение состояния обработчиков (их слушает интерфейс)
        self._listeners: list[Callable[[Any, AudioEvent | None], None]] = []
        self._handlers_built = False

        self.recognizer: SpeechRecognizer | None = None
        self.engine: EventEngine | None = None
        self.model_path = model

        if auto_open:
            self.open_recognizer()

    # ---------------- подписка на изменения ----------------

    def add_listener(self, callback: Callable[[Any, AudioEvent | None], None]) -> None:
        """Слушать изменения состояния любого обработчика."""
        self._listeners.append(callback)

    def _on_handler_change(self, handler_obj, event: AudioEvent | None) -> None:
        for callback in self._listeners:
            callback(handler_obj, event)

    # ---------------- сборка ----------------

    def build_handlers(self) -> dict[str, Any]:
        """Создать обработчики и подписать их на шину событий."""
        self.handlers = default_handlers(self._on_handler_change, silent=self.silent)

        for action, instance in self.handlers.items():
            self.bus.subscribe(action, instance.handle)

        # Любое событие пишем в общий журнал — удобно для отчёта
        self.bus.subscribe(ANY, self._default_log)
        self._handlers_built = True
        return self.handlers

    def _default_log(self, event: AudioEvent) -> None:
        """Печать события в консоль (в графическом режиме отключается)."""
        if self.silent:
            return
        mark = "✔" if event.is_final else "·"
        print(f"  {mark} {event.describe()}")

    def open_recognizer(self, model: str | Path | None = None) -> SpeechRecognizer:
        """Загрузить модель распознавания речи."""
        target = model or self.model_path
        path = Path(target) if target else find_model()
        if path is None:
            from core.stt import require_model

            path = require_model()

        self.model_path = path
        self.recognizer = SpeechRecognizer(path).open()
        return self.recognizer

    def make_engine(self, on_text=None) -> EventEngine:
        """Создать конвейер, не трогая обработчики.

        Нужно графическому интерфейсу: обработчики создаются один раз
        в главном потоке (там же живут их виджеты), а конвейер
        создаётся заново при каждом запуске — в том потоке, где идёт
        звук.
        """
        if self.recognizer is None:
            self.open_recognizer()
        if not self._handlers_built:
            self.build_handlers()

        self.engine = EventEngine(
            self.rules,
            self.recognizer,
            self.bus,
            emit_partial=self.emit_partial,
            on_text=on_text,
        )
        return self.engine

    # Оставить прежнее имя как понятный синоним
    build_engine = make_engine

    # ---------------- запуск ----------------

    def run_file(
        self,
        path: str | Path,
        *,
        play: bool = False,
        on_text=None,
        on_progress=None,
        progress_label: bool = True,
    ) -> Stats:
        """Обработать аудиофайл.

        play=True — воспроизводить файл в колонки в реальном времени
        (демонстрация), иначе — обработать максимально быстро (тесты).
        """
        engine = self.build_engine(on_text=on_text)

        source = FileSource(path, real_time=play)
        if progress_label and not self.silent:
            print(f"Обработка: {Path(path).name} ({source.duration:.1f} с, "
                  f"{'со звуком' if play else 'ускоренно'})")

        try:
            with source:
                engine.process_frames(iter(source), on_progress=on_progress)
        except KeyboardInterrupt:
            print("\nПрервано пользователем.")
            engine.finish()
            self.bus.process()
        finally:
            if self.recognizer is not None:
                self.recognizer.close()

        return engine.stats

    def run_microphone(self, *, duration: float | None = None, on_text=None) -> Stats:
        """Слушать микрофон и реагировать на ключевые слова."""
        engine = self.build_engine(on_text=on_text)
        source = MicSource()

        if not self.silent:
            print("Слушаю микрофон. Остановить — Ctrl+C.\n")

        try:
            with source:
                started = time.monotonic()
                for frame in source:
                    engine.process_frame(frame)
                    self.bus.process()
                    if duration and (time.monotonic() - started) > duration:
                        break
        except KeyboardInterrupt:
            pass
        finally:
            engine.finish()
            self.bus.process()
            if self.recognizer is not None:
                self.recognizer.close()

        return engine.stats

    def replay(self, frames: Iterable[Frame]) -> Stats:
        """Прогнать уже подготовленный поток порций через конвейер."""
        engine = self.build_engine()
        return engine.process_frames(frames)

    # ---------------- отчёт ----------------

    def report(self, stats: Stats) -> str:
        """Краткий отчёт по результатам работы."""
        lines = [
            "",
            "=" * 68,
            "  ИТОГИ",
            "=" * 68,
            f"  Обработано звука:      {stats.seconds:.2f} с "
            f"({stats.frames} порций по 30 мс)",
            f"  Время обработки:       {stats.wall:.2f} с "
            f"(скорость x{stats.real_time_factor:.2f} от реального)",
            f"  Распознано фраз:       {stats.phrases}",
            f"  Событий:               {stats.events} "
            f"(черновик {stats.events_partial}, подтверждено {stats.events_final})",
            f"  Подавлено повторов:    {stats.suppressed}",
        ]

        if stats.by_rule:
            lines.append("")
            lines.append("  Сработавшие правила:")
            for rule_id, count in sorted(stats.by_rule.items(), key=lambda kv: -kv[1]):
                rule = self.rules.get(rule_id)
                phrase = f"  «{rule.main_phrase}»" if rule else ""
                lines.append(f"    {rule_id:<24} {count:>3} раз(а){phrase}")

        counters = self.handlers.get("stats.count")
        if counters and counters.counters:
            lines.append("")
            lines.append("  Счётчики:")
            for name, value in sorted(counters.counters.items()):
                lines.append(f"    {name:<24} {value:g}")

        lines.append("=" * 68)
        return "\n".join(lines)


def build_app(
    rules_path: str | Path = DEFAULT_RULES,
    *,
    model: str | Path | None = None,
    silent: bool = False,
    emit_partial: bool = True,
) -> Application:
    """Собрать приложение — короткая запись для скриптов и тестов."""
    return Application(
        rules_path, model=model, silent=silent, emit_partial=emit_partial
    )


__all__ = ["Application", "build_app", "DEFAULT_RULES", "ROOT"]