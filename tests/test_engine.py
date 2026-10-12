"""
Проверки конвейера: повторы, cooldown, режим «только один раз».

Для тестов подставляется заглушка распознавателя: реальная модель
не нужна, важна логика обработки событий.

Заглушка выдаёт фразы по расписанию: ключ — номер порции звука,
значение — фраза. Номер порции задаёт и время t0, поэтому расстояние
между фразами можно задавать точно.
"""

import unittest

from core.audio import Frame
from core.engine import EventEngine
from core.events import EventBus
from core.rules import Rule, RuleSet
from core.stt import Utterance


class FakeRecognizer:
    """Распознаватель, отдающий фразы по расписанию.

    Расписание: {номер_порции: Utterance}. Например
        {0: Utterance("часы остановились"),
         200: Utterance("часы остановились")}
    означает: фраза на 0-й порции и снова на 200-й (через 6 секунд).
    """

    def __init__(self, schedule: dict[int, Utterance] | None = None) -> None:
        self.schedule = dict(schedule or {})
        self.calls = 0
        self.reset_called = 0
        self._final: Utterance | None = None

    def set_final(self, utterance: Utterance | None) -> None:
        """Что выдать, когда поток закончится."""
        self._final = utterance

    def accept(self, data):
        self.calls += 1
        utterance = self.schedule.get(self.calls - 1)
        return [utterance] if utterance else []

    def final(self):
        return self._final

    def reset(self):
        self.reset_called += 1
        self.calls = 0


def say(text, is_final=True) -> Utterance:
    return Utterance(text=text, is_final=is_final)


class EngineFixture:
    """Движок с собранной шиной и списком собранных событий."""

    def __init__(self, rules: RuleSet, schedule=None, *, emit_partial=True) -> None:
        self.bus = EventBus()
        self.collected = []
        self.bus.subscribe("*", self.collected.append)
        self.recognizer = FakeRecognizer(schedule)
        self.engine = EventEngine(rules, self.recognizer, self.bus,
                                  emit_partial=emit_partial)

    def run(self, frames: int = 200) -> None:
        """Прогнать указанное число порций, разбирая очередь шины."""
        for i in range(frames):
            self.engine.process_frame(
                Frame(data=b"\x00\x00" * 480, index=i, t0=i * 0.03)
            )
            self.bus.process()
        self.engine.finish()
        self.bus.process()

    @property
    def stats(self):
        return self.engine.stats


def rule(**kwargs) -> Rule:
    params = {
        "id": "r1",
        "action": "x",
        "phrases": ["часы остановились"],
        "cooldown": 0,
    }
    params.update(kwargs)
    return Rule(**params)


class TestBasicFlow(unittest.TestCase):
    def test_final_phrase_produces_event(self):
        fixture = EngineFixture(
            RuleSet([rule()]), {0: say("вот часы остановились да")})
        fixture.run()

        self.assertEqual(len(fixture.collected), 1)
        event = fixture.collected[0]
        self.assertEqual(event.rule_id, "r1")
        self.assertEqual(event.matched, "часы остановились")
        self.assertTrue(event.is_final)

    def test_no_phrase_no_event(self):
        fixture = EngineFixture(
            RuleSet([rule()]), {0: say("мы пели про весну")})
        fixture.run()
        self.assertEqual(fixture.collected, [])

    def test_stats_counted(self):
        fixture = EngineFixture(RuleSet([rule()]), {0: say("часы остановились")})
        fixture.run()

        self.assertEqual(fixture.stats.events, 1)
        self.assertEqual(fixture.stats.by_rule["r1"], 1)
        self.assertEqual(fixture.stats.phrases, 1)

    def test_audio_time_recorded(self):
        fixture = EngineFixture(RuleSet([rule()]), {10: say("часы остановились")})
        fixture.run()
        self.assertAlmostEqual(fixture.collected[0].audio_time, 10 * 0.03, places=3)


class TestDuplicateSuppression(unittest.TestCase):
    def test_partial_then_final_fires_once(self):
        """Одна и та же фраза приходит дважды: черновик и итог.
        Событие должно быть одно, иначе обработчик сработает дважды."""
        fixture = EngineFixture(RuleSet([rule()]), {
            0: say("часы остановили", is_final=False),
            1: say("часы остановились", is_final=True),
        })
        fixture.run()

        self.assertEqual(len(fixture.collected), 1)
        self.assertGreaterEqual(fixture.stats.suppressed, 1)

    def test_require_final_ignores_partial(self):
        fixture = EngineFixture(
            RuleSet([rule(require_final=True)]),
            {0: say("часы остановились", is_final=False)},
        )
        fixture.run()
        self.assertEqual(fixture.collected, [])

    def test_emit_partial_disabled_globally(self):
        fixture = EngineFixture(RuleSet([rule()]), {
            0: say("часы остановились", is_final=False),
            1: say("часы остановились", is_final=True),
        }, emit_partial=False)
        fixture.run()
        self.assertEqual(len(fixture.collected), 1)


class TestCooldown(unittest.TestCase):
    def test_repeat_within_cooldown_blocked(self):
        """Припев повторяет ключевые слова через полсекунды."""
        fixture = EngineFixture(RuleSet([rule(cooldown=10)]), {
            0: say("часы остановились"),
            5: say("часы остановились"),   # +0.15 с — слишком рано
        })
        fixture.run()

        self.assertEqual(len(fixture.collected), 1)
        self.assertGreaterEqual(fixture.stats.suppressed, 1)

    def test_repeat_after_cooldown_allowed(self):
        """Тот же повтор, но уже через 6 секунд при cooldown 5 с."""
        fixture = EngineFixture(RuleSet([rule(cooldown=5)]), {
            0: say("часы остановились"),
            200: say("часы остановились"),  # +6.0 с — можно
        })
        fixture.run(frames=260)

        self.assertEqual(len(fixture.collected), 2)

    def test_cooldown_zero_allows_back_to_back(self):
        fixture = EngineFixture(RuleSet([rule(cooldown=0)]), {
            0: say("часы остановились"),
            10: say("вот и опять часы остановились"),
        })
        fixture.run()
        self.assertEqual(len(fixture.collected), 2)


class TestOnce(unittest.TestCase):
    def test_once_rule_fires_single_time(self):
        fixture = EngineFixture(RuleSet([rule(once=True)]), {
            0: say("часы остановились"),
            50: say("полностью часы остановились"),
        })
        fixture.run()
        self.assertEqual(len(fixture.collected), 1)

    def test_once_other_rules_still_fire(self):
        rules = RuleSet([rule(id="once_rule", once=True),
                         rule(id="repeat_rule")])
        fixture = EngineFixture(rules, {
            0: say("часы остановились"),
            50: say("часы остановились"),
        })
        fixture.run()

        by_rule = [e.rule_id for e in fixture.collected]
        self.assertEqual(by_rule.count("once_rule"), 1)
        self.assertEqual(by_rule.count("repeat_rule"), 2)


class TestDisabledRule(unittest.TestCase):
    def test_disabled_rule_never_fires(self):
        fixture = EngineFixture(RuleSet([rule(enabled=False)]),
                                {0: say("часы остановились")})
        fixture.run()
        self.assertEqual(fixture.collected, [])


class TestCrossPhraseBoundary(unittest.TestCase):
    def test_phrase_split_across_two_finals(self):
        """Ключевая фраза может оказаться разорвана границей между фразами."""
        fixture = EngineFixture(RuleSet([rule()]), {
            0: say("посмотри часы"),
            1: say("остановились"),
        })
        fixture.run()

        self.assertEqual(len(fixture.collected), 1, "фраза на стыке должна находиться")

    def test_words_far_apart_not_joined(self):
        """Два слова из разных фраз не должны склеиться в ключевую фразу."""
        fixture = EngineFixture(RuleSet([rule(phrases=["часы идут"])]), {
            0: say("часы"),
            300: say("идут"),   # +9 секунд — это разные события
        })
        fixture.run(frames=400)
        self.assertEqual(fixture.collected, [])


class TestTextCallback(unittest.TestCase):
    def test_callback_receives_all_text(self):
        seen = []
        bus = EventBus()
        engine = EventEngine(
            RuleSet([rule()]),
            FakeRecognizer({0: say("черновик", is_final=False), 1: say("итог")}),
            bus,
            on_text=lambda t, f, t0: seen.append((t, f)),
        )
        for i in range(10):
            engine.process_frame(Frame(data=b"\x00\x00" * 480, index=i, t0=i * 0.03))

        self.assertEqual(seen, [("черновик", False), ("итог", True)])

    def test_final_at_stream_end(self):
        fixture = EngineFixture(RuleSet([rule()]), {})
        fixture.recognizer.set_final(say("часы остановились"))
        fixture.run()
        self.assertEqual(len(fixture.collected), 1)


class TestReset(unittest.TestCase):
    def test_reset_clears_state(self):
        fixture = EngineFixture(RuleSet([rule()]), {0: say("часы остановились")})
        fixture.run()
        self.assertEqual(fixture.stats.events, 1)

        fixture.engine.reset()

        self.assertEqual(fixture.stats.events, 0)
        self.assertEqual(fixture.recognizer.reset_called, 1)

        # после сброса тот же текст должен снова вызвать событие
        fixture.recognizer.schedule = {0: say("часы остановились")}
        fixture.run()
        self.assertEqual(len(fixture.collected), 2)


if __name__ == "__main__":
    unittest.main()