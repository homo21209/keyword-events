"""
Проверка графического интерфейса без модели распознавания.

    python tools/gui_smoke_test.py

Собирает окно на «пустышке» вместо распознавателя, наполняет журнал
и сцена правдоподобными данными и сохраняет снимок в
assets/preview/окно.png — им удобно проверять внешний вид, ничего не
запуская. Полезно и как проверка: если виджет падает при отрисовке,
скрипт упадёт здесь.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Отрисовка без экрана — нужно на сервере и в автоматических проверках
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.events import AudioEvent, EventBus  # noqa: E402
from core.rules import RuleSet  # noqa: E402
from handlers import default_handlers  # noqa: E402

OUT_DIR = ROOT / "assets" / "preview"


class FakeApplication:
    """Заглушка приложения: всё, что нужно окну, кроме распознавания."""

    def __init__(self) -> None:
        self.rules = RuleSet.load(ROOT / "config" / "rules.json")
        self.bus = EventBus()
        self.handlers = default_handlers(silent=True)

        for action, instance in self.handlers.items():
            self.bus.subscribe(action, instance.handle)

        self.model_path = "vosk-model-small-ru-0.22"
        self.engine = None


def make_event(rule_id, action, matched, phrase, audio_time, payload, score=0.96):
    return AudioEvent(
        rule_id=rule_id, action=action, text=matched, matched=matched,
        phrase=phrase, score=score, audio_time=audio_time, is_final=True,
        payload=payload,
    )


def fill(window) -> None:
    """Наполнить интерфейс правдоподобными данными."""
    transcript = window.transcript
    lines = [
        ("восемь вечера в доме горел свет", True),
        ("певица пела старинную песню про часы", True),
        ("посмотри на часы они показывают полдень", True),
        ("часы остановилис", True),
        ("в доме стало тихо и певица замолчала", True),
        ("а потом всё ожило и зазвучали старые часы", True),
        ("ещё идут старинные часы", True),
        ("певица пела старинную песню про часы", True),
        ("часы остановились", True),
    ]
    for text, is_final in lines:
        transcript.add_text(text, is_final, 0.0)

    events = [
        make_event("scene_show_clock", "scene.set", "посмотри на часы",
                   "посмотри на часы", 5.1, {"scene": "clock"}),
        make_event("clock_stop", "clock.set", "часы остановилис",
                   "часы остановились", 11.4,
                   {"running": False, "caption": "А часы-то остановились!"}, 0.94),
        make_event("clock_caption_stop", "caption.show", "часы остановилис",
                   "часы остановились", 11.4,
                   {"text": "А часы-то остановились!", "duration": 6.0}),
        make_event("count_stop", "stats.count", "часы остановилис",
                   "часы остановились", 11.4, {"name": "остановок часов"}),
        make_event("clock_run", "clock.set", "идут старинные часы",
                   "идут старинные часы", 19.8,
                   {"running": True, "caption": "А часы-то пошли!"}),
        make_event("clock_caption_run", "caption.show", "идут старинные часы",
                   "идут старинные часы", 19.8,
                   {"text": "А часы-то пошли!", "duration": 6.0}),
        make_event("count_run", "stats.count", "идут старинные часы",
                   "идут старинные часы", 19.8, {"name": "запусков часов"}),
        make_event("clock_stop", "clock.set", "часы остановились",
                   "часы остановились", 27.3,
                   {"running": False, "caption": "А часы-то остановились!"}),
    ]

    for event in events:
        window._on_event(event)  # noqa: SLF001 - это же окно
        window.app.bus.emit(event)

    window.app.bus.process()
    window._sync_handlers()  # noqa: SLF001
    window._refresh_stats()  # noqa: SLF001
    window.time_label.setText("00:28 / 00:29")
    window.slider.setValue(970)
    window.speed_label.setText("x0.31 от реального времени")
    window.statusBar().showMessage(
        "Файл: демо.wav  (00:29)  —  ключевые слова сработали вовремя"
    )


def main() -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)

    from ui.main_window import MainWindow

    window = MainWindow(FakeApplication())
    window.resize(1180, 760)
    window.show()

    fill(window)
    qt_app.processEvents()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot = OUT_DIR / "окно.png"
    window.grab().save(str(shot))

    print(f"Снимок сохранён: {shot}")
    print(f"Правил в таблице: {len(window.app.rules)}")
    print(f"Строк в журнале:   {window.event_log.table.rowCount()}")

    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())