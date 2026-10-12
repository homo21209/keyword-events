"""
Точка входа. Консольный интерфейс системы.

    python main.py                       прогон config/rules.json без аудио (самопроверка)
    python main.py run  ПЕСНЯ.mp3        прогон аудиофайла
    python main.py play ПЕСНЯ.mp3        прогон со звуком в реальном времени
    python main.py mic                   работа с микрофоном
    python main.py gui                   графический интерфейс
    python main.py rules                 список правил
    python main.py check ПЕСНЯ.mp3       оценка точности и скорости
    python main.py test                  модульные проверки без звука
    python main.py models                состояние моделей распознавания
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import DEFAULT_RULES, ROOT, Application
from core import text as text_utils
from core.matcher import KeywordMatcher


# ----------------------------------------------------------------------
#  Вспомогательный вывод
# ----------------------------------------------------------------------

def _print_banner(title: str) -> None:
    print()
    print("=" * 68)
    print(f"  {title}")
    print("=" * 68)


# ----------------------------------------------------------------------
#  Команды
# ----------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """Прогон аудиофайла (без звука, максимально быстро)."""
    app = Application(args.rules, model=args.model, emit_partial=not args.final_only)
    stats = app.run_file(args.file, play=False, on_text=lambda t, f, t0: _print_text(t, f))
    print(app.report(stats))
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """Прогон аудиофайла со звуком, в темпе записи."""
    app = Application(args.rules, model=args.model, emit_partial=not args.final_only)
    stats = app.run_file(args.file, play=True, on_text=lambda t, f, t0: _print_text(t, f))
    print(app.report(stats))
    return 0


def cmd_mic(args: argparse.Namespace) -> int:
    """Работа с микрофоном."""
    app = Application(args.rules, model=args.model, emit_partial=not args.final_only)
    stats = app.run_microphone(duration=args.seconds, on_text=lambda t, f, t0: _print_text(t, f))
    print(app.report(stats))
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Графический интерфейс."""
    try:
        from ui.main import launch
    except ImportError as exc:
        print(f"Не удалось загрузить графический интерфейс: {exc}\n"
              "Установите его командой:  pip install PySide6")
        return 1

    return launch(rules_path=args.rules, model=args.model)


def cmd_rules(args: argparse.Namespace) -> int:
    """Показать загруженные правила."""
    from core.rules import RuleSet

    rules = RuleSet.load(args.rules)
    if not len(rules):
        print(f"Правил нет: {args.rules}")
        return 1

    _print_banner(f"ПРАВИЛА ({len(rules)}) — {args.rules}")
    for rule in rules:
        state = "вкл " if rule.enabled else "выкл"
        print(f"\n  [{state}] {rule.id}  ->  {rule.action}")
        print(f"         порог {rule.threshold:.2f}   повтор не раньше чем через {rule.cooldown:g} с"
              + ("   только один раз" if rule.once else "")
              + ("   ждать подтверждения" if rule.require_final else ""))
        print(f"         ключевые слова: {', '.join(rule.phrases)}")
        if rule.payload:
            print(f"         параметры: {rule.payload}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Оценка точности распознавания ключевых слов и скорости работы."""
    app = Application(args.rules, model=args.model, silent=True)

    print(f"Анализ файла: {Path(args.file).name}")
    print("(сначала распознаём речь целиком, затем проверяем, "
          "найдены ли ключевые слова)")

    transcript: list[str] = []
    stats = app.run_file(args.file, play=False,
                         on_text=lambda t, f, t0: transcript.append(t) if f else None)

    full_text = " ".join(transcript)

    _print_banner("ТОЧНОСТЬ ПОИСКА КЛЮЧЕВЫХ СЛОВ")
    print(f"  Распознанный текст ({len(transcript)} фраз):")
    _print_wrapped(full_text, indent=4)

    print()
    matcher = KeywordMatcher(app.rules)
    words = text_utils.words(full_text)
    matches = matcher.find_all(words)

    print(f"  Найдено совпадений: {len(matches)} (из {len(app.rules)} правил)")
    for match in matches:
        print(f"    {match.score:5.0%}  «{match.matched}»  ->  {match.rule_id}")

    missed = [r.id for r in app.rules if r.id not in {m.rule_id for m in matches}]
    if missed:
        print(f"\n  Не сработали: {', '.join(missed)}")

    _print_banner("СКОРОСТЬ")
    print(f"  Звука обработано:   {stats.seconds:.1f} с")
    print(f"  Время обработки:   {stats.wall:.1f} с")
    print(f"  Скорость:          x{stats.real_time_factor:.2f} от реального времени")
    if stats.real_time_factor < 1.0:
        print("  -> распознавание успевает в реальном времени")
    else:
        print("  -> для работы в реальном времени нужен более быстрый компьютер")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Модульные проверки (без звуковой карты и модели)."""
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def cmd_models(args: argparse.Namespace) -> int:
    """Показать состояние моделей распознавания."""
    sys.path.insert(0, str(ROOT / "tools"))
    import download_model

    download_model.main(["list"])
    return 0


# ----------------------------------------------------------------------
#  Вывод распознанного текста
# ----------------------------------------------------------------------

_last_partial = ""


def _print_text(text: str, is_final: bool) -> None:
    """Живой вывод распознанной речи.

    Распознаватель присылает черновиок каждые 30 мс, и текст в нём
    чаще всего не меняется. Поэтому печатаем черновик только когда он
    действительно изменился — иначе консоль засоряется тысячами
    одинаковых строк.
    """
    global _last_partial

    if not text.strip():
        return

    if is_final:
        print(f"  [речь] {text}")
    elif text != _last_partial:
        _last_partial = text
        sys.stdout.write(f"\r  [черновик] {text[:70]}")
        sys.stdout.flush()


def _print_wrapped(text: str, indent: int = 4, width: int = 62) -> None:
    import textwrap

    prefix = " " * indent
    for line in textwrap.wrap(text, width=width) or ["(пусто)"]:
        print(prefix + line)


# ----------------------------------------------------------------------
#  Разбор аргументов
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Управление событиями на основе ключевых слов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-r", "--rules", default=str(DEFAULT_RULES),
                        help="файл с правилами (по умолчанию config/rules.json)")
    parser.add_argument("-m", "--model", default=None,
                        help="путь к модели распознавания (по умолчанию ищется сама)")
    parser.add_argument("--final-only", action="store_true",
                        help="реагировать только на подтверждённые фразы, "
                             "игнорируя промежуточные")

    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="прогон аудиофайла без звука")
    p_run.add_argument("file", help="путь к аудиофайлу")
    p_run.set_defaults(func=cmd_run)

    p_play = sub.add_parser("play", help="прогон аудиофайла со звуком")
    p_play.add_argument("file", help="путь к аудиофайлу")
    p_play.set_defaults(func=cmd_play)

    p_mic = sub.add_parser("mic", help="работа с микрофоном")
    p_mic.add_argument("-s", "--seconds", type=float, default=None,
                       help="сколько секунд слушать (по умолчанию до Ctrl+C)")
    p_mic.set_defaults(func=cmd_mic)

    p_gui = sub.add_parser("gui", help="графический интерфейс")
    p_gui.set_defaults(func=cmd_gui)

    p_rules = sub.add_parser("rules", help="показать правила")
    p_rules.set_defaults(func=cmd_rules)

    p_check = sub.add_parser("check", help="оценка точности и скорости")
    p_check.add_argument("file", help="путь к аудиофайлу")
    p_check.set_defaults(func=cmd_check)

    sub.add_parser("test", help="модульные проверки").set_defaults(func=cmd_test)
    sub.add_parser("models", help="состояние моделей").set_defaults(func=cmd_models)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"Ошибка: файл не найден — {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - пользователю нужен понятный текст
        print(f"\nОшибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())