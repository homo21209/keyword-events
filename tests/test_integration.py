"""
Сквозная проверка: правила -> конвейер -> шина -> обработчики.

Берутся настоящие правила из config/rules.json и подставляется
распознаватель-заглушка с заранее заданными фразами. Так проверяется
весь путь целиком, включая работу обработчиков, — без звуковой карты
и без модели распознавания.

Порции звука идут по 30 мс, поэтому порция 200 — это 6 секунд записи.
"""

import unittest

from app import ROOT
from core.audio import Frame
from core.engine import EventEngine
from core.events import EventBus
from core.stt import Utterance
from handlers import default_handlers

RULES_PATH = ROOT / "config" / "rules.json"

FRAME_SECONDS = 0.03


def say(text, is_final=True):
    return Utterance(text=text, is_final=is_final)


def at(seconds: float, text: str, is_final: bool = True):
    """Фраза, произнесённая на указанной секунде записи."""
    return (int(seconds / FRAME_SECONDS), say(text, is_final))


class ScriptedRecognizer:
    """Распознаватель, выдающий фразы по расписанию порций."""

    def __init__(self, script):
        self.schedule: dict[int, Utterance] = {}
        self.calls = 0

        for index, item in enumerate(script):
            if isinstance(item, tuple):
                frame, utterance = item
            else:
                frame, utterance = index, item
            self.schedule[frame] = utterance

    def accept(self, data):
        self.calls += 1
        utterance = self.schedule.get(self.calls - 1)
        return [utterance] if utterance else []

    def final(self):
        return None

    def reset(self):
        self.calls = 0


class TestFullPipeline(unittest.TestCase):
    """Сценарий аннотации целиком."""

    def setUp(self):
        from core.rules import RuleSet

        self.rules = RuleSet.load(RULES_PATH)
        self.bus = EventBus()
        self.handlers = default_handlers(silent=True)
        for action, instance in self.handlers.items():
            self.bus.subscribe(action, instance.handle)

        self.clock = self.handlers["clock.set"]
        self.counters = self.handlers["stats.count"]
        self.log = self.handlers["log.write"]

    def run_script(self, script, frames=800):
        engine = EventEngine(self.rules, ScriptedRecognizer(script), self.bus)
        for i in range(frames):
            engine.process_frame(Frame(data=b"\x00\x00" * 480, index=i, t0=i * FRAME_SECONDS))
            self.bus.process()
        engine.finish()
        self.bus.process()
        return engine

    # ---------------- сценарий аннотации ----------------

    def test_annotation_scenario(self):
        """Проигрывание песни: часы встают, потом снова идут."""
        engine = self.run_script([
            at(0.0, "восемь вечера в доме горел свет"),
            at(3.0, "певица пела старинную песню"),
            at(6.0, "часы остановились"),
            at(9.0, "в доме стало тихо"),
            at(15.0, "ещё идут старинные часы"),
            at(18.0, "певица пела старинную песню про часы"),
        ])

        # ключевые слова привели к остановке и запуску часов
        self.assertEqual(self.clock.triggered, 2)
        self.assertAlmostEqual(self.clock.stopped_at, 6.0, places=2)
        self.assertAlmostEqual(self.clock.started_at, 15.0, places=2)
        self.assertTrue(self.clock.running, "после «ещё идут старинные часы» стрелки идут")

        # подписи и счётчики отработали
        self.assertEqual(self.counters.counters.get("остановок часов"), 1)
        self.assertEqual(self.counters.counters.get("запусков часов"), 1)
        self.assertEqual(len(self.log.lines), 2)
        self.assertIn("СТОП", self.log.lines[0])

        self.assertGreaterEqual(engine.stats.events, 6)

    def test_clock_toggles_on_alternation(self):
        """Чередование «остановились / идут» переключает часы каждый раз."""
        script = [
            at(0.0, "часы остановились"),
            at(6.0, "ещё идут старинные часы"),
            at(12.0, "часы остановились"),
            at(18.0, "ещё идут старинные часы"),
        ]
        self.run_script(script, frames=800)
        self.assertEqual(self.clock.triggered, 4)

    def test_silence_leaves_clock_alone(self):
        """Посторонняя речь не должна двигать состояние часов."""
        self.run_script([
            at(0.0, "мы пели про весну"),
            at(5.0, "а потом ушли домой спать"),
        ])
        self.assertEqual(self.clock.triggered, 0)
        self.assertTrue(self.clock.running)

    def test_scene_switches(self):
        self.run_script([
            at(0.0, "посмотри на часы"),
            at(9.0, "а вот и певица поёт"),
        ])
        self.assertEqual(self.handlers["scene.set"].triggered, 2)

    # ---------------- устойчивость ----------------

    def test_repeated_keyword_in_chorus(self):
        """Ключевое слово в припеве много раз -> событие одно."""
        script = [at(0.0, "часы остановились") for _ in range(12)]
        self.run_script(script, frames=900)
        self.assertEqual(self.clock.triggered, 1)

    def test_cooldown_lets_repeat_through_after_a_while(self):
        """Тот же припев, но пауза больше времени ожидания."""
        script = [at(float(i * 6), "часы остановились") for i in range(5)]
        self.run_script(script, frames=1200)
        self.assertEqual(self.clock.triggered, 5)

    def test_recognition_error_does_not_break_event(self):
        """Ошибка в распознанном слове не мешает событию."""
        self.run_script([at(0.0, "часы остановилис")])
        self.assertEqual(self.clock.triggered, 1)

    def test_partial_result_triggers_fast(self):
        """Событие срабатывает по черновику, не дожидаясь итога."""
        self.run_script([(0, say("часы остановили", is_final=False))], frames=60)
        self.assertEqual(self.clock.triggered, 1)

    def test_far_apart_words_not_joined(self):
        """«часы» и «идут» через полминуты не дают фразу «часы идут»."""
        from core.rules import Rule

        rule = Rule(id="join_test", action="stats.count",
                    phrases=["часы идут"], payload={"name": "склейка"})
        self.rules.add(rule)

        self.run_script([
            at(0.0, "вот и часы"),
            at(20.0, "идут по улице"),
        ])

        self.assertNotIn("склейка", self.counters.counters)


if __name__ == "__main__":
    unittest.main()