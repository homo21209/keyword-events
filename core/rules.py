"""
Реестр правил: связь «ключевые слова -> событие -> обработчик».

Аннотация проекта ставит вопрос: как запомнить связи СОТЕН фрагментов
с их обработчиками? Ответ — вынести все связи во внешний файл
(config/rules.json), а в коде держать только универсальный загрузчик.

Правило описывает одно событие:

    {
      "id": "clock_stop",
      "action": "clock.set",
      "phrases": ["часы остановились", "стрелки встали"],
      "payload": { "running": false, "caption": "А часы-то остановились!" },
      "threshold": 0.85,
      "cooldown": 5.0
    }

Добавление сотого правила не требует ни одной строки кода.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import text as text_utils

# Значения по умолчанию для необязательных полей правила
DEFAULT_THRESHOLD = 0.82
DEFAULT_COOLDOWN = 5.0


class RuleError(ValueError):
    """Некорректно описанное правило в конфигурационном файле."""


@dataclass(slots=True)
class Rule:
    """Одно событие системы.

    Атрибуты:
        id:        уникальное имя правила;
        action:    имя обработчика, который будет вызван;
        phrases:   список ключевых слов (первое — основное, остальные — синонимы);
        payload:   произвольные параметры, передаваемые обработчику;
        threshold: минимальная схожесть (0..1) для срабатывания;
        cooldown:  минимальный интервал между срабатываниями, секунды;
        once:      если True, правило срабатывает только один раз;
        enabled:   можно временно отключить, не удаляя из файла;
        priority:  приоритет при конфликте (больше — важнее);
        require_final: ждать подтверждённой фразы, игнорируя черновик.
    """

    id: str
    action: str
    phrases: list[str]
    payload: dict[str, Any] = field(default_factory=dict)
    threshold: float = DEFAULT_THRESHOLD
    cooldown: float = DEFAULT_COOLDOWN
    once: bool = False
    enabled: bool = True
    priority: int = 0
    require_final: bool = False
    tags: list[str] = field(default_factory=list)

    # Предвычисленные нормализованные варианты фраз.
    # Заполняются в __post_init__ — экономят пересчёт в горячем цикле.
    _phrases_norm: list[tuple[str, tuple[str, ...]]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._phrases_norm = []
        for phrase in self.phrases:
            norm = text_utils.normalize(phrase)
            if not norm:
                raise RuleError(f"Правило {self.id!r}: пустая фраза")
            self._phrases_norm.append((norm, tuple(norm.split(" "))))

    @property
    def main_phrase(self) -> str:
        """Основная фраза правила (первая в списке)."""
        return self.phrases[0]

    @property
    def normalized_phrases(self) -> list[tuple[str, tuple[str, ...]]]:
        """Список (нормализованная фраза, кортеж слов) по всем вариантам."""
        return self._phrases_norm

    def to_dict(self) -> dict[str, Any]:
        """Обратное преобразование в словарь — для сохранения в JSON."""
        return {
            "id": self.id,
            "action": self.action,
            "phrases": list(self.phrases),
            "payload": self.payload,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
            "once": self.once,
            "enabled": self.enabled,
            "priority": self.priority,
            "require_final": self.require_final,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, index: int = 0) -> "Rule":
        """Создать правило из словаря конфигурации с проверкой полей."""
        if not isinstance(raw, dict):
            raise RuleError(f"Правило #{index}: ожидался объект, получено {type(raw).__name__}")

        rule_id = str(raw.get("id") or f"rule_{index}").strip()
        action = str(raw.get("action") or "").strip()
        if not action:
            raise RuleError(f"Правило {rule_id!r}: не задано поле 'action'")

        phrases_raw = raw.get("phrases") or raw.get("phrase") or []
        if isinstance(phrases_raw, str):
            phrases_raw = [phrases_raw]
        phrases = [str(p).strip() for p in phrases_raw if str(p).strip()]
        if not phrases:
            raise RuleError(f"Правило {rule_id!r}: не задано ни одной фразы")

        threshold = float(raw.get("threshold", DEFAULT_THRESHOLD))
        if not 0.0 <= threshold <= 1.0:
            raise RuleError(f"Правило {rule_id!r}: threshold должен быть в диапазоне 0..1")

        payload = raw.get("payload") or {}
        if not isinstance(payload, dict):
            raise RuleError(f"Правило {rule_id!r}: 'payload' должен быть объектом")

        return cls(
            id=rule_id,
            action=action,
            phrases=phrases,
            payload=payload,
            threshold=threshold,
            cooldown=float(raw.get("cooldown", DEFAULT_COOLDOWN)),
            once=bool(raw.get("once", False)),
            enabled=bool(raw.get("enabled", True)),
            priority=int(raw.get("priority", 0)),
            require_final=bool(raw.get("require_final", False)),
            tags=[str(t) for t in (raw.get("tags") or [])],
        )


class RuleSet:
    """Коллекция правил с быстрым доступом по id."""

    def __init__(self, rules: Iterable[Rule] | None = None) -> None:
        self._rules: list[Rule] = list(rules or ())

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def __getitem__(self, key: int | str) -> Rule:
        """Правило по номеру в списке или по id."""
        if isinstance(key, int):
            return self._rules[key]
        for rule in self._rules:
            if rule.id == key:
                return rule
        raise KeyError(key)

    def get(self, rule_id: str) -> Rule | None:
        """Вернуть правило по id или None, если такого нет."""
        try:
            return self[rule_id]
        except KeyError:
            return None

    def add(self, rule: Rule) -> None:
        """Добавить правило, заменив одноимённое."""
        existing = self.get(rule.id)
        if existing is not None:
            self._rules[self._rules.index(existing)] = rule
        else:
            self._rules.append(rule)

    def active(self) -> list[Rule]:
        """Только включённые правила, отсортированные по приоритету."""
        return sorted((r for r in self._rules if r.enabled), key=lambda r: -r.priority)

    def actions(self) -> set[str]:
        """Все имена обработчиков, на которые ссылаются правила."""
        return {r.action for r in self._rules}

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._rules]

    def save(self, path: str | Path) -> None:
        """Сохранить набор правил в JSON (с отступами, для читаемости)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_list(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "RuleSet":
        """Загрузить правила из JSON-файла.

        Поддерживаются два формата:
            [ {...}, {...} ]                       — простой список
            { "rules": [ {...} ] }                 — список с метаданными

        Возвращает пустой набор, если файл отсутствует: система обязана
        стартовать даже без конфигурации.
        """
        path = Path(path)
        if not path.exists():
            return cls()

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rules", [])
        if not isinstance(data, list):
            raise RuleError(f"{path.name}: ожидался список правил")

        rules: list[Rule] = []
        seen: set[str] = set()
        for i, raw in enumerate(data):
            rule = Rule.from_dict(raw, index=i)
            if rule.id in seen:
                raise RuleError(f"Дублирующийся id правила: {rule.id!r}")
            seen.add(rule.id)
            rules.append(rule)

        return cls(rules)


__all__ = ["Rule", "RuleSet", "RuleError", "DEFAULT_THRESHOLD", "DEFAULT_COOLDOWN"]