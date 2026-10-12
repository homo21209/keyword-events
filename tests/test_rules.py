"""Проверки загрузки правил и разбора конфигурации."""

import json
import tempfile
import unittest
from pathlib import Path

from core.rules import Rule, RuleError, RuleSet


class TestRule(unittest.TestCase):
    def test_minimal(self):
        rule = Rule(id="a", action="clock.set", phrases=["часы"])
        self.assertEqual(rule.main_phrase, "часы")
        self.assertTrue(rule.enabled)
        self.assertEqual(rule.threshold, Rule.__dataclass_fields__["threshold"].default)

    def test_precomputed_normalized_phrases(self):
        rule = Rule(id="a", action="x", phrases=["Часы Остановились!"])
        phrase, words = rule.normalized_phrases[0]
        self.assertEqual(phrase, "часы остановились")
        self.assertEqual(words, ("часы", "остановились"))

    def test_empty_phrase_rejected(self):
        with self.assertRaises(RuleError):
            Rule(id="a", action="x", phrases=["   "])

    def test_roundtrip_dict(self):
        rule = Rule(id="a", action="clock.set", phrases=["часы"],
                    payload={"running": False}, threshold=0.9, cooldown=3.0,
                    once=True, require_final=True, tags=["t"])
        again = Rule.from_dict(rule.to_dict())
        self.assertEqual(again.to_dict(), rule.to_dict())


class TestRuleFromDict(unittest.TestCase):
    def test_action_required(self):
        with self.assertRaises(RuleError):
            Rule.from_dict({"id": "a", "phrases": ["часы"]})

    def test_phrases_required(self):
        with self.assertRaises(RuleError):
            Rule.from_dict({"id": "a", "action": "x", "phrases": []})

    def test_threshold_bounds(self):
        with self.assertRaises(RuleError):
            Rule.from_dict({"id": "a", "action": "x", "phrases": ["y"], "threshold": 1.5})

    def test_single_phrase_string_allowed(self):
        rule = Rule.from_dict({"id": "a", "action": "x", "phrase": "часы"})
        self.assertEqual(rule.phrases, ["часы"])

    def test_defaults_filled(self):
        rule = Rule.from_dict({"id": "a", "action": "x", "phrases": ["часы"]})
        self.assertEqual(rule.id, "a")
        self.assertFalse(rule.once)
        self.assertFalse(rule.require_final)


class TestRuleSet(unittest.TestCase):
    def test_add_and_get(self):
        rs = RuleSet()
        rule = Rule(id="a", action="x", phrases=["часы"])
        rs.add(rule)
        self.assertEqual(len(rs), 1)
        self.assertIs(rs.get("a"), rule)
        self.assertIsNone(rs.get("нет"))

    def test_add_replaces_same_id(self):
        rs = RuleSet()
        rs.add(Rule(id="a", action="x", phrases=["раз"]))
        rs.add(Rule(id="a", action="x", phrases=["два"]))
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs["a"].main_phrase, "два")

    def test_active_sorted_by_priority(self):
        rs = RuleSet([
            Rule(id="low", action="x", phrases=["а"], priority=1),
            Rule(id="high", action="x", phrases=["б"], priority=10),
            Rule(id="off", action="x", phrases=["в"], enabled=False, priority=99),
        ])
        self.assertEqual([r.id for r in rs.active()], ["high", "low"])

    def test_actions(self):
        rs = RuleSet([Rule(id="a", action="clock.set", phrases=["а"]),
                      Rule(id="b", action="beep", phrases=["б"])])
        self.assertEqual(rs.actions(), {"clock.set", "beep"})


class TestRuleSetIO(unittest.TestCase):
    def test_load_missing_file_is_empty(self):
        self.assertEqual(len(RuleSet.load("нет/такого/файла.json")), 0)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            original = RuleSet([
                Rule(id="a", action="clock.set", phrases=["часы остановились"],
                     payload={"running": False}, tags=["сценарий"]),
            ])
            original.save(path)

            restored = RuleSet.load(path)
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored["a"].payload, {"running": False})
            self.assertEqual(restored["a"].tags, ["сценарий"])

    def test_load_list_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps(
                [{"id": "a", "action": "x", "phrases": ["часы"]}], ensure_ascii=False),
                encoding="utf-8")
            self.assertEqual(len(RuleSet.load(path)), 1)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps([
                {"id": "a", "action": "x", "phrases": ["раз"]},
                {"id": "a", "action": "x", "phrases": ["два"]},
            ], ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(RuleError):
                RuleSet.load(path)

    def test_project_rules_load(self):
        """Штатный файл правил проекта должен быть корректным."""
        path = Path(__file__).resolve().parent.parent / "config" / "rules.json"
        rules = RuleSet.load(path)
        self.assertGreaterEqual(len(rules), 4, "в config/rules.json слишком мало правил")

        clock = rules.get("clock_stop")
        self.assertIsNotNone(clock, "нет правила clock_stop из сценария аннотации")
        self.assertEqual(clock.action, "clock.set")
        self.assertFalse(clock.payload["running"])


if __name__ == "__main__":
    unittest.main()