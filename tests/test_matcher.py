"""Проверки нечёткого сопоставления ключевых слов."""

import unittest

from core import text
from core.matcher import KeywordMatcher
from core.rules import Rule, RuleSet


def make_matcher(*phrases, threshold=0.82, slack=1):
    rule = Rule(id="test", action="noop", phrases=list(phrases), threshold=threshold)
    return KeywordMatcher(RuleSet([rule]))


class TestExactMatch(unittest.TestCase):
    def test_exact_phrase(self):
        m = make_matcher("часы остановились")
        match = m.match_rule(m.rules[0], text.words("вот часы остановились да"))
        self.assertIsNotNone(match)
        self.assertEqual(match.matched, "часы остановились")
        self.assertAlmostEqual(match.score, 1.0, places=2)

    def test_case_and_punctuation_ignored(self):
        m = make_matcher("Часы остановились!")
        match = m.match_rule(m.rules[0], text.words("ЧАСЫ ОСТАНОВИЛИСЬ!!!"))
        self.assertIsNotNone(match)

    def test_yo_equivalent(self):
        m = make_matcher("еще идут часы")
        self.assertIsNotNone(m.match_rule(m.rules[0], text.words("ещё идут часы")))


class TestFuzzyMatch(unittest.TestCase):
    def test_typo_in_last_word(self):
        """Распознаватель «съел» букву — событие всё равно должно сработать."""
        m = make_matcher("часы остановились")
        match = m.match_rule(m.rules[0], text.words("часы остановилис"))
        self.assertIsNotNone(match)
        self.assertGreater(match.score, 0.9)

    def test_filler_word_inside(self):
        """Между словами ключевой фразы вклинилось лишнее слово."""
        m = make_matcher("часы остановились")
        match = m.match_rule(m.rules[0], text.words("часы же остановились"))
        self.assertIsNotNone(match)

    def test_synonym_via_second_phrase(self):
        rule = Rule(id="test", action="noop",
                    phrases=["еще идут старинные часы", "часы пошли"], threshold=0.82)
        matcher = KeywordMatcher(RuleSet([rule]))
        match = matcher.match_rule(rule, text.words("и вот часы пошли уж"))
        self.assertIsNotNone(match)
        self.assertEqual(match.phrase, "часы пошли")

    def test_unrelated_text_no_match(self):
        m = make_matcher("часы остановились")
        self.assertIsNone(m.match_rule(m.rules[0], text.words("мы пели про весну")))

    def test_anchor_protects_tail_only(self):
        """Одно слово-«хвост» фразы не должно считаться совпадением."""
        m = make_matcher("часы остановились")
        self.assertIsNone(m.match_rule(m.rules[0], text.words("певцы остановились")))


class TestThreshold(unittest.TestCase):
    def test_high_threshold_rejects_rough_match(self):
        rule = Rule(id="t", action="noop", phrases=["часы остановились"], threshold=0.98)
        matcher = KeywordMatcher(RuleSet([rule]))
        self.assertIsNone(matcher.match_rule(rule, text.words("часы остановилис")))

    def test_low_threshold_accepts_it(self):
        rule = Rule(id="t", action="noop", phrases=["часы остановились"], threshold=0.85)
        matcher = KeywordMatcher(RuleSet([rule]))
        self.assertIsNotNone(matcher.match_rule(rule, text.words("часы остановилис")))


class TestFindAll(unittest.TestCase):
    def test_multiple_rules_sorted_by_score(self):
        rules = RuleSet([
            Rule(id="exact", action="a", phrases=["часы остановились"], threshold=0.8),
            Rule(id="rough", action="b", phrases=["погода хорошая"], threshold=0.5),
        ])
        matcher = KeywordMatcher(rules)
        found = matcher.find_all(text.words("часы остановились и погода хороша"))
        self.assertEqual([m.rule_id for m in found][0], "exact")
        self.assertEqual(len(found), 2)

    def test_disabled_rules_skipped(self):
        rules = RuleSet([
            Rule(id="off", action="a", phrases=["часы"], enabled=False),
        ])
        matcher = KeywordMatcher(rules)
        self.assertEqual(matcher.find_all(text.words("часы пошли")), [])

    def test_empty_text(self):
        m = make_matcher("часы остановились")
        self.assertEqual(m.find_all([]), [])


if __name__ == "__main__":
    unittest.main()