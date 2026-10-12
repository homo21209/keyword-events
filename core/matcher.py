"""
Нечёткий поиск ключевых слов в распознанном тексте.

Почему нельзя просто искать подстроку `if phrase in text`:

    1. Движок распознавания речи ошибается. Вместо «часы остановились»
       он вполне может выдать «часы остановилис» или «час ы остановились».
    2. Между словами могут вклиниться слова-паразиты:
       «часы то ли остановились».
    3. Ключевая фраза может быть произнесена с ошибкой в одном слове
       из пяти — человек волновался, шум, акцент.

Поэтому сравнение ведётся по близости (Levenshtein) через rapidfuzz,
с перебором скользящих окон и якорем по первому слову.

Алгоритм match_rule():
    для каждого варианта фразы правила:
        для каждой длины окна len(фраза) и len(фраза)+1:
            для каждой позиции окна в тексте:
                score = fuzz.ratio(окно, фраза)
                если score выше лучшего — запоминаем
    якорь: первое слово окна должно быть похоже на первое слово фразы,
    иначе фраза «часы остановились» совпала бы со словом «остановились».

Возвращается лучшее совпадение, либо None если порог не достигнут.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from .rules import Rule, RuleSet

# Насколько первое слово окна должно быть похоже на первое слово фразы.
# Защищает от «хвостовых» ложных срабатываний.
ANCHOR_THRESHOLD = 55.0

# Сколько лишних слов допускаем внутри окна (слова-паразиты).
WINDOW_SLACK = 1


@dataclass(slots=True)
class Match:
    """Результат сопоставления одного правила с текстом."""

    rule: Rule
    phrase: str          # какая фраза (основная или синоним) совпала
    matched: str         # что именно нашлось в тексте
    start: int           # индекс начала совпадения в списке слов
    score: float         # 0..1, насколько уверенно

    @property
    def rule_id(self) -> str:
        return self.rule.id

    def __str__(self) -> str:
        return f"{self.rule_id}: «{self.matched}» ~ {self.rule.phrase} ({self.score:.0%})"


class KeywordMatcher:
    """Ищет ключевые слова правил в последовательности слов."""

    def __init__(self, rules: RuleSet, *, window_slack: int = WINDOW_SLACK) -> None:
        self._rules = rules
        self._slack = max(0, window_slack)

    @property
    def rules(self) -> RuleSet:
        return self._rules

    def match_rule(self, rule: Rule, text_words: list[str]) -> Match | None:
        """Найти лучшее совпадение одного правила в тексте.

        text_words — уже нормализованный список слов (см. core.text.words).
        """
        if not text_words:
            return None

        best: Match | None = None

        for phrase, phrase_words in rule.normalized_phrases:
            p_len = len(phrase_words)
            if p_len == 0:
                continue

            # Насколько первое слово фразы похоже на первое слово окна
            anchor_word = phrase_words[0]

            for win_len in range(p_len, p_len + self._slack + 1):
                if win_len > len(text_words):
                    continue

                for start in range(0, len(text_words) - win_len + 1):
                    window = text_words[start:start + win_len]
                    window_str = " ".join(window)

                    score = fuzz.ratio(window_str, phrase) / 100.0

                    # Якорь по первому слову: без него фраза из трёх слов
                    # совпала бы с одним её «хвостом» и дала 100%.
                    if score < 0.999:
                        anchor = fuzz.ratio(window[0], anchor_word) / 100.0
                        if anchor < ANCHOR_THRESHOLD / 100.0:
                            continue

                    # При равных оценках побеждает ПОЗДНЕЕ вхождение.
                    # Буфер текста каждый раз сканируется заново, и старое
                    # совпадение в начале иначе всегда «побеждало бы» новое,
                    # из-за чего повтор фразы в припеве не распознавался.
                    if best is None or score >= best.score:
                        best = Match(
                            rule=rule,
                            phrase=phrase,
                            matched=window_str,
                            start=start,
                            score=score,
                        )

        if best is None or best.score < rule.threshold:
            return None
        return best

    def find_all(self, text_words: list[str], *, only_enabled: bool = True) -> list[Match]:
        """Проверить все правила и вернуть сработавшие, отсортированные
        по убыванию уверенности."""
        pool = self._rules.active() if only_enabled else list(self._rules)

        found: list[Match] = []
        for rule in pool:
            match = self.match_rule(rule, text_words)
            if match is not None:
                found.append(match)

        found.sort(key=lambda m: (-m.score, -m.rule.priority))
        return found

    def find_overlapping(self, text_words: list[str]) -> list[Match]:
        """Как find_all, но без повторов: если два совпадения
        перекрываются по позициям, оставляется более уверенное."""
        matches = self.find_all(text_words)

        result: list[Match] = []
        for match in sorted(matches, key=lambda m: m.score, reverse=True):
            length = len(match.matched.split(" "))
            span = range(match.start, match.start + length)
            if any(
                span and set(range(o.start, o.start + len(o.matched.split(" ")))) & set(span)
                for o in result
            ):
                continue
            result.append(match)

        return result


__all__ = ["KeywordMatcher", "Match", "ANCHOR_THRESHOLD", "WINDOW_SLACK"]