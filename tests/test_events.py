"""Проверки шины событий."""

import unittest

from core.events import ANY, AudioEvent, EventBus


def make_event(action="clock.set", rule_id="clock_stop", **kwargs) -> AudioEvent:
    data = {
        "rule_id": rule_id,
        "action": action,
        "text": "часы остановились",
        "matched": "часы остановились",
        "phrase": "часы остановились",
        "score": 0.95,
    }
    data.update(kwargs)
    return AudioEvent(**data)


class TestSubscription(unittest.TestCase):
    def test_handler_receives_matching_action(self):
        bus = EventBus()
        got = []
        bus.subscribe("clock.set", got.append)

        bus.emit(make_event())
        bus.process()

        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].rule_id, "clock_stop")

    def test_wildcard_receives_all(self):
        bus = EventBus()
        got = []
        bus.subscribe(ANY, got.append)

        bus.emit(make_event(action="clock.set"))
        bus.emit(make_event(action="beep", rule_id="b"))
        bus.process()

        self.assertEqual(len(got), 2)

    def test_other_action_ignored(self):
        bus = EventBus()
        got = []
        bus.subscribe("beep", got.append)

        bus.emit(make_event(action="clock.set"))
        bus.process()

        self.assertEqual(got, [])

    def test_unsubscribe(self):
        bus = EventBus()
        got = []

        def handler(ev):
            got.append(ev)

        bus.subscribe("clock.set", handler)
        bus.unsubscribe("clock.set", handler)
        bus.emit(make_event())
        bus.process()

        self.assertEqual(got, [])

    def test_unsubscribe_twice_is_safe(self):
        bus = EventBus()
        handler = lambda ev: None  # noqa: E731
        bus.subscribe("x", handler)
        bus.unsubscribe("x", handler)
        bus.unsubscribe("x", handler)


class TestQueue(unittest.TestCase):
    def test_queue_preserves_order(self):
        bus = EventBus()
        got = []
        bus.subscribe(ANY, got.append)

        for i in range(5):
            bus.emit(make_event(rule_id=f"r{i}"))
        bus.process()

        self.assertEqual([e.rule_id for e in got], ["r0", "r1", "r2", "r3", "r4"])

    def test_process_limit(self):
        bus = EventBus()
        got = []
        bus.subscribe(ANY, got.append)

        for i in range(5):
            bus.emit(make_event(rule_id=f"r{i}"))
        bus.process(limit=2)

        self.assertEqual(len(got), 2)
        self.assertEqual(bus.pending, 3)

    def test_history_records_events(self):
        bus = EventBus()
        bus.emit(make_event(rule_id="a"))
        bus.process()
        self.assertEqual([e.rule_id for e in bus.history()], ["a"])

    def test_clear(self):
        bus = EventBus()
        bus.emit(make_event())
        bus.clear()
        self.assertEqual(bus.pending, 0)
        self.assertEqual(bus.history(), [])

    def test_failing_handler_does_not_break_others(self):
        """Ошибка в одном обработчике не должна останавливать систему."""
        bus = EventBus()
        got = []

        def broken(ev):
            raise RuntimeError("обработчик сломался")

        bus.subscribe(ANY, broken)
        bus.subscribe(ANY, got.append)

        bus.emit(make_event())
        bus.process()  # исключение перехватывается внутри

        self.assertEqual(len(got), 1, "второй обработчик должен был отработать")


class TestAudioEvent(unittest.TestCase):
    def test_describe_contains_key_info(self):
        text = make_event().describe()
        self.assertIn("clock.set", text)
        self.assertIn("часы остановились", text)

    def test_from_match(self):
        from core.matcher import Match
        from core.rules import Rule

        rule = Rule(id="r", action="a", phrases=["фраза"], payload={"k": 1})
        event = AudioEvent.from_match(
            Match(rule=rule, phrase="фраза", matched="фраза", start=0, score=1.0),
            text="контекст фраза",
            audio_time=3.5,
            is_final=False,
        )
        self.assertEqual(event.rule_id, "r")
        self.assertEqual(event.payload, {"k": 1})
        self.assertEqual(event.audio_time, 3.5)
        self.assertFalse(event.is_final)


if __name__ == "__main__":
    unittest.main()