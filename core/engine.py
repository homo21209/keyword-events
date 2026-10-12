"""
Конвейер: звук -> текст -> ключевые слова -> события.

Здесь собираются вместе все ранее написанные модули:

    Frame -> SpeechRecognizer -> текст -> KeywordMatcher -> AudioEvent -> EventBus

Три тонкости, ради которых модуль существует:

1. ЧЕРНОВИК И ИТОГ.
   Распознаватель отдаёт текст дважды: сначала как «черновик» (partial),
   потом как итоговую фразу. Обработать оба результата напрямую нельзя —
   одно и то же ключевое слово сработает дважды. Черновик обрабатывается
   сразу (чтобы не ждать конца фразы), а повтор по той же позиции
   подавляется.

2. ВРЕМЕННОЕ ОКНО.
   Ключевая фраза может быть разорвана границей между двумя фразами,
   поэтому проверять нужно не только последнее слово, но и несколько
   предыдущих. Но если брать просто «последние N слов», слова «часы» и
   «идут», прозвучавшие с интервалом в десять секунд, склеятся в одну
   фразу. Поэтому окно ограничено ещё и по времени.

3. ФРАЗА НА СТЫКЕ.
   Последние слова предыдущей фразы могут оказаться началом ключевой.
   Поэтому сканирование начинается не с нуля, а с глубины
   MAX_PHRASE_WORDS - 1 слов назад.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import text as text_utils
from .audio import Frame
from .events import AudioEvent, EventBus
from .matcher import KeywordMatcher, Match
from .rules import RuleSet
from .stt import SpeechRecognizer

# Максимальная длина ключевой фразы в словах. Определяет, насколько
# глубоко назад пересматривается буфер при появлении новых слов.
MAX_PHRASE_WORDS = 12

# Сколько секунд назад слова ещё считаются относящимися к текущей фразе.
# Больше этого — пауза, и слова не должны склеиваться.
CONTEXT_SECONDS = 8.0

# Предохранитель от роста памяти при долгой работе.
MAX_BUFFER_WORDS = 400

# Сигнатура обратного вызова: (текст, является_ли_итоговым, время)
TextCallback = Callable[[str, bool, float], None]


@dataclass
class Stats:
    """Счётчики для отчёта о работе системы."""

    frames: int = 0
    seconds: float = 0.0      # длительность обработанного звука
    wall: float = 0.0         # реальное время работы программы
    phrases: int = 0
    events: int = 0
    events_partial: int = 0
    events_final: int = 0
    suppressed: int = 0       # подавлено повторов и срабатываний по cooldown
    by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def real_time_factor(self) -> float:
        """Во сколько раз обработка отстаёт от реального времени.

        1.0 — ровно в темпе звука, больше — медленнее (не успевает),
        меньше — быстрее (пакетный прогон файла).
        """
        return (self.wall / self.seconds) if self.seconds else 0.0

    def as_dict(self) -> dict:
        return {
            "frames": self.frames,
            "seconds": round(self.seconds, 2),
            "wall": round(self.wall, 2),
            "rtf": round(self.real_time_factor, 3),
            "phrases": self.phrases,
            "events": self.events,
            "events_partial": self.events_partial,
            "events_final": self.events_final,
            "suppressed": self.suppressed,
            "by_rule": dict(self.by_rule),
        }


class EventEngine:
    """Обрабатывает поток звука и порождает события."""

    def __init__(
        self,
        rules: RuleSet,
        recognizer: SpeechRecognizer,
        bus: EventBus,
        *,
        emit_partial: bool = True,
        on_text: TextCallback | None = None,
        context_seconds: float = CONTEXT_SECONDS,
    ) -> None:
        self._rules = rules
        self._stt = recognizer
        self._bus = bus
        self._matcher = KeywordMatcher(rules)
        self._emit_partial = emit_partial
        self._on_text = on_text
        self._context = context_seconds

        # Буфер слов. Подтверждённые фразы и текущий черновик хранятся
        # раздельно, но в каждом слове есть сквозной номер — по нему
        # определяется, то ли это место в тексте или уже обработанное.
        # Номер переживает превращение черновика в итоговую фразу,
        # поэтому событие по одному месту срабатывает ровно один раз.
        self._committed: list[tuple[str, float, int]] = []
        self._preview: list[tuple[str, float, int]] = []
        self._word_counter = 0

        # Состояние подавления повторов
        self._fired_at: dict[str, float] = {}     # правило -> время последнего срабатывания
        self._fired_pos: dict[str, int] = {}      # правило -> сквозной номер слова
        self._once_done: set[str] = set()

        self.stats = Stats()
        self._started = time.monotonic()

    # ---------------- свойства ----------------

    @property
    def rules(self) -> RuleSet:
        return self._rules

    @property
    def matcher(self) -> KeywordMatcher:
        return self._matcher

    @property
    def transcript(self) -> str:
        """Весь подтверждённый текст сессии."""
        return " ".join(word for word, _, _ in self._committed)

    # ---------------- основной цикл ----------------

    def process_frame(self, frame: Frame) -> None:
        """Обработать одну порцию звука."""
        self.stats.frames += 1

        for utterance in self._stt.accept(frame.data):
            self._handle_utterance(utterance, frame.t0)

        # сколько звука реально обработано (не время работы программы!)
        self.stats.seconds = frame.t0 + frame.duration
        self.stats.wall = time.monotonic() - self._started

    def process_frames(self, frames: Iterable[Frame], *, on_progress=None) -> Stats:
        """Обработать поток порций до конца."""
        for frame in frames:
            self.process_frame(frame)
            if on_progress is not None:
                on_progress(frame)
            # события накоплены в шине; в консольном режиме их разбираем сразу
            self._bus.process()

        self.finish()
        self.stats.wall = time.monotonic() - self._started
        return self.stats

    def finish(self) -> None:
        """Забрать последнюю фразу при остановке потока."""
        last = self._stt.final()
        if last is not None:
            self._handle_utterance(last, self._current_time())
            self._bus.process()

    # ---------------- внутренняя логика ----------------

    @property
    def _buffer(self) -> list[tuple[str, float, int]]:
        """Подтверждённые слова и черновик как один список."""
        return self._committed + self._preview

    def _current_time(self) -> float:
        """Время последнего слова в буфере."""
        buffer = self._buffer
        return buffer[-1][1] if buffer else 0.0

    def _new_index(self) -> int:
        self._word_counter += 1
        return self._word_counter

    def _handle_utterance(self, utterance, t0: float) -> None:
        """Разобрать очередной результат распознавания."""
        if not utterance.is_final and not self._emit_partial:
            return

        words = text_utils.words(utterance.text)

        if utterance.is_final:
            self.stats.phrases += 1
            self._commit(words, t0)
        else:
            # Пустой черновик не трогает буфер
            if not words:
                return
            self._apply_preview(words, t0)

        if self._on_text is not None:
            self._on_text(utterance.text, utterance.is_final, t0)

        self._scan(t0, utterance.is_final)

    def _apply_preview(self, words: list[str], t0: float) -> None:
        """Обновить черновик.

        Черновик каждый раз приходит целиком заново, поэтому старые
        слова заменяются, а не дописываются. Номера уже известных слов
        сохраняются: если черновик длиннее, новые слова получают новые
        номера. Именно это позволяет потом узнать тот же самый текст,
        когда распознаватель пришлёт итоговую фразу.
        """
        old = self._preview
        preview: list[tuple[str, float, int]] = []

        for i, word in enumerate(words):
            if i < len(old):
                preview.append((word, t0, old[i][2]))
            else:
                preview.append((word, t0, self._new_index()))

        self._preview = preview

    def _commit(self, words: list[str], t0: float) -> None:
        """Перевести черновик в подтверждённые слова.

        Общий префикс с черновиком сохраняет свои номера — иначе
        итоговая фраза выглядела бы новым местом в тексте, и событие
        сработало бы второй раз.
        """
        old = self._preview

        # сколько слов в начале совпадают с черновиком
        common = 0
        limit = min(len(words), len(old))
        while common < limit and old[common][0] == words[common]:
            common += 1

        for i, word in enumerate(words):
            if i < common:
                self._committed.append((word, old[i][1], old[i][2]))
            else:
                self._committed.append((word, t0, self._new_index()))

        self._preview = []

    def _trim(self, now: float) -> None:
        """Убрать устаревшие слова.

        Важно: после обрезки список слов становится короче, поэтому
        индексы в нём сдвигаются. Сквозные номера слов при этом
        сохраняются — по ним событие по одному месту срабатывает
        ровно один раз.
        """
        # Длинная пауза — слова перестают относиться к текущей фразе
        while (self._committed
               and (now - self._committed[0][1]) > self._context):
            self._committed.pop(0)

        # Предохранитель от роста памяти при долгой работе
        overflow = len(self._committed) - MAX_BUFFER_WORDS
        if overflow > 0:
            del self._committed[:overflow]

    def _scan(self, t0: float, is_final: bool) -> None:
        """Найти ключевые слова в буфере и породить события."""
        self._trim(t0)
        buffer = self._buffer
        if not buffer:
            return

        # Возвращаемся на MAX_PHRASE_WORDS - 1 слов назад от границы
        # «подтверждённое / черновик»: фраза могла начаться в конце
        # предыдущей и целиком видна только сейчас.
        scan_from = max(0, len(self._committed) - (MAX_PHRASE_WORDS - 1))

        window = buffer[scan_from:]
        if not window:
            return

        words = [word for word, _, _ in window]
        for match in self._matcher.find_all(words):
            absolute_start = window[match.start][2]
            self._try_fire(match, is_final, t0, absolute_start,
                           self._context_around(words, match))

    @staticmethod
    def _context_around(words: list[str], match, radius: int = 6) -> str:
        """Короткий фрагмент текста вокруг совпадения — для журнала.

        Весь буфер не годится: он обрезается по времени, и к моменту
        события начало фразы уже могло исчезнуть.
        """
        length = len(match.matched.split())
        left = max(0, match.start - radius)
        right = min(len(words), match.start + length + radius)
        return " ".join(words[left:right])

    def _try_fire(self, match: Match, is_final: bool, t0: float,
                  absolute_start: int, context: str = "") -> None:
        """Проверить ограничения и отправить событие в шину."""
        rule = match.rule

        if not rule.enabled:
            return
        if rule.once and rule.id in self._once_done:
            return
        if not is_final and rule.require_final:
            return

        # Повтор по той же позиции — это тот же самый черновик/фраза
        if self._fired_pos.get(rule.id) == absolute_start:
            self.stats.suppressed += 1
            return

        # Защита от «залипания» ключевого слова в длинном припеве
        if rule.cooldown > 0:
            last = self._fired_at.get(rule.id)
            if last is not None and (t0 - last) < rule.cooldown:
                self.stats.suppressed += 1
                return

        event = AudioEvent.from_match(
            match, text=context, audio_time=t0, is_final=is_final
        )
        self._bus.emit(event)

        self._fired_at[rule.id] = t0
        self._fired_pos[rule.id] = absolute_start
        self.stats.events += 1
        self.stats.by_rule[rule.id] = self.stats.by_rule.get(rule.id, 0) + 1
        if is_final:
            self.stats.events_final += 1
        else:
            self.stats.events_partial += 1
        if rule.once:
            self._once_done.add(rule.id)

    def reset(self) -> None:
        """Очистить состояние перед новым запуском."""
        self._committed.clear()
        self._preview.clear()
        self._word_counter = 0
        self._fired_at.clear()
        self._fired_pos.clear()
        self._once_done.clear()
        self.stats = Stats()
        self._started = time.monotonic()
        self._stt.reset()


__all__ = ["EventEngine", "Stats", "MAX_PHRASE_WORDS", "CONTEXT_SECONDS", "TextCallback"]