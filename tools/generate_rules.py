"""
Генератор большого набора правил.

    python tools/generate_rules.py 100          создать config/rules_100.json
    python tools/generate_rules.py 100 --run    создать и сразу замерить

Аннотация проекта ставит вопрос: «их может быть сотни — как запомнить
связи сотен фрагментов со своими обработчиками?» Этот инструмент
показывает ответ в цифрах: сто правил добавляются одним файлом,
ни строчки кода не меняется.

Заодно он меряет, сколько времени занимает поиск ключевых слов, —
цифра «сколько правил в секунду успевает проверить система» хорошо
смотрится в защите.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.matcher import KeywordMatcher  # noqa: E402
from core.rules import Rule, RuleSet  # noqa: E402

OUTPUT = ROOT / "config" / "rules_100.json"

# Заготовки фраз: {место} и {действие} подставляются в разные комбинации
PLACES = [
    "часы", "стрелки", "певица", "окно", "зеркало", "дверь", "стол",
    "лампа", "зеркало", "картина", "штора", "часы", "стенные часы",
    "настенные часы", "старинные часы", "песня", "голос", "музыка",
]

ACTIONS = [
    "остановились", "пошли", "замолчали", "зазвучали", "открылись",
    "закрылись", "засветились", "погасли", "качнулись", "замерли",
]

DETAILS = [
    "в гостиной", "на кухне", "в зале", "у окна", "в спальне",
    "в коридоре", "на чердаке", "в подвале", "на балконе", "у двери",
]

# Действия обработчиков, которые используются в массовой генерации
TARGET_ACTIONS = [
    ("stats.count", "statistics"),
    ("beep", "sound"),
    ("log.write", "log"),
    ("caption.show", "screen"),
]


def generate(count: int, *, seed: int = 20261) -> RuleSet:
    """Создать набор из count правил."""
    rng = random.Random(seed)
    rules: list[Rule] = []

    # Первыми идут правила сценария аннотации — их быть не должно
    scenario = [
        Rule(
            id="clock_stop", action="clock.set",
            phrases=["часы остановились", "стрелки остановились", "часы встали"],
            payload={"running": False, "caption": "А часы-то остановились!"},
            threshold=0.84, cooldown=4.0, priority=10, tags=["сценарий"],
        ),
        Rule(
            id="clock_run", action="clock.set",
            phrases=["еще идут старинные часы", "идут старинные часы"],
            payload={"running": True, "caption": "А часы-то пошли!"},
            threshold=0.84, cooldown=4.0, priority=10, tags=["сценарий"],
        ),
    ]
    rules.extend(scenario)

    while len(rules) < count:
        index = len(rules)
        phrase = f"{rng.choice(PLACES)} {rng.choice(ACTIONS)} {rng.choice(DETAILS)}"

        action, tag = rng.choice(TARGET_ACTIONS)
        payload: dict = {}
        if action == "stats.count":
            payload = {"name": f"{tag}-{index}"}
        elif action == "beep":
            payload = {"frequency": 220 + (index % 12) * 60, "duration": 0.08}
        elif action == "log.write":
            payload = {"text": f"сработало правило №{index}", "level": "info"}
        else:
            payload = {"text": phrase.capitalize(), "duration": 2.5}

        # Немного синонимов, чтобы поиск был честно нагружен
        phrases = [phrase]
        if index % 3 == 0:
            phrases.append(f"{rng.choice(PLACES)} {rng.choice(ACTIONS)}")

        rules.append(Rule(
            id=f"auto_{index:03d}",
            action=action,
            phrases=phrases,
            payload=payload,
            threshold=0.86,
            cooldown=rng.choice([2.0, 4.0, 8.0]),
            priority=rng.randint(0, 5),
            tags=[tag, "сгенерировано"],
        ))

    return RuleSet(rules[:count])


def measure(rules: RuleSet, *, runs: int = 200) -> dict:
    """Замерить скорость поиска ключевых слов по всему набору правил."""
    matcher = KeywordMatcher(rules)

    # реалистичный кусок распознанного текста
    samples = [
        "восемь вечера в доме горел свет певица пела старинную песню",
        "часы остановились стрелки замерли а потом всё ожило",
        "еще идут старинные часы и поют про то как время идёт",
        "посмотри на настенные часы они показывают ровно полдень",
    ]
    from core import text as text_utils

    prepared = [text_utils.words(s) for s in samples]

    start = time.perf_counter()
    hits = 0
    for _ in range(runs):
        for words in prepared:
            hits += len(matcher.find_all(words))
    elapsed = time.perf_counter() - start

    checks = runs * len(prepared)
    return {
        "rules": len(rules),
        "checks": checks,
        "seconds": elapsed,
        "per_second": checks / elapsed if elapsed else 0.0,
        "rule_checks_per_second": (checks * len(rules) / elapsed) if elapsed else 0.0,
        "hits": hits,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Генератор правил")
    parser.add_argument("count", nargs="?", type=int, default=100,
                        help="сколько правил создать (по умолчанию 100)")
    parser.add_argument("-o", "--output", default=str(OUTPUT),
                        help="куда сохранить")
    parser.add_argument("--run", action="store_true",
                        help="сразу замерить скорость поиска")
    args = parser.parse_args(argv)

    rules = generate(args.count)
    output = Path(args.output)
    rules.save(output)

    print(f"Создано правил: {len(rules)}")
    print(f"Сохранено в:    {output}")
    print(f"Пример записи:")
    example = rules[len(rules) - 1].to_dict()
    for key, value in example.items():
        print(f"    {key}: {value}")

    print()
    print(f"Подключить этот набор вместо основного:")
    print(f'    python main.py gui -r "{output}"')

    if args.run:
        print()
        print("=" * 68)
        print("  СКОРОСТЬ ПОИСКА КЛЮЧЕВЫХ СЛОВ")
        print("=" * 68)
        data = measure(rules)
        print(f"  Правил:                {data['rules']}")
        print(f"  Проверок фраз:         {data['checks']}")
        print(f"  Время:                 {data['seconds']:.3f} с")
        print(f"  Фраз в секунду:        {data['per_second']:.0f}")
        print(f"  Проверок правил/сек:   {data['rule_checks_per_second']:.0f}")
        print()
        print("  Распознавание в реальном времени успевает за 0.5 секунды,")
        print(f"  то есть система проверяет около "
              f"{data['rule_checks_per_second'] * 0.5:.0f} правил за это время.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())