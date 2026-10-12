"""
Распознавание речи: обёртка над офлайн-моделью Vosk.

Vosk — офлайновая система распознавания речи с русской моделью.
Работает без интернета и без ключей API, что важно для демонстрации:
не нужно ничего настраивать перед защитой.

Модель выдаёт два вида результатов:
    частичный (partial) — текст, который распознан «на лету» и ещё может
        измениться. Нужен, чтобы сработать сразу, не дожидаясь конца фразы;
    финальный (final)   — текст целой фразы, уже не изменится.

Для событий важен момент: частичный результат позволяет среагировать
за доли секунды после того, как ключевые слова прозвучали.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .audio import SAMPLE_RATE

# Модель по умолчанию: маленькая русская (~40 МБ), быстрая и достаточная
DEFAULT_MODEL = "vosk-model-small-ru-0.22"

# Пакетная модель (большая, точнее): vosk-model-ru-0.10
LARGE_MODEL = "vosk-model-ru-0.10"

# Поддерживаемые русские модели
KNOWN_MODELS = (DEFAULT_MODEL, LARGE_MODEL)


class STTError(RuntimeError):
    """Ошибка инициализации или работы движка распознавания."""


@dataclass(slots=True)
class Utterance:
    """Фрагмент распознанной речи."""

    text: str
    is_final: bool
    start: float = 0.0
    end: float = 0.0
    confidence: float = 1.0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def _is_ascii(path: Path) -> bool:
    """Можно ли записать путь однобайтовыми символами."""
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def ascii_safe_path(path: Path) -> Path:
    """Привести путь к модели к виду, понятному библиотеке Vosk.

    Vosk — это C++-библиотека, и на Windows она работает с файлами
    только по однобайтовым путям. Если проект лежит в папке с
    кириллицей (например «C:\\Users\\Иван\\...»), движок отвечает
    «does not contain model files», хотя модель на месте и цела.

    Обход простой: при первом запуске модель копируется в кэш с
    латинским путём, и дальше загружается уже оттуда.
    """
    path = Path(path)
    if _is_ascii(path):
        return path

    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    cache = Path(base) / "KeyWordsDemo" / path.name

    if not (cache / "am").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        staging = cache.with_name(cache.name + ".copying")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(path, staging)
        shutil.rmtree(cache, ignore_errors=True)
        staging.replace(cache)

    return cache


class SpeechRecognizer:
    """Потоковый распознаватель речи.

    Использование:
        rec = SpeechRecognizer("assets/models/vosk-model-small-ru-0.22")
        rec.open()
        for frame in frames:
            for phrase in rec.accept(frame.data):
                ...
        rec.close()
    """

    def __init__(self, model_path: str | Path, *, sample_rate: int = SAMPLE_RATE,
                 log_level: int = -1) -> None:
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        self.log_level = log_level
        self._model = None
        self._recognizer = None

    # ---------------- открытие / закрытие ----------------

    def open(self) -> "SpeechRecognizer":
        """Загрузить модель. Вызывается один раз в начале работы."""
        if self._recognizer is not None:
            return self

        if not self.model_path.exists():
            raise STTError(
                f"Модель распознавания не найдена: {self.model_path}\n"
                f"Загрузите её командой:\n"
                f"    python tools/download_model.py {DEFAULT_MODEL}"
            )

        # движок на Windows не понимает кириллицу в пути
        model_path = ascii_safe_path(self.model_path)

        try:
            # Импорт внутри метода: модель грузится только когда реально нужна,
            # поэтому модуль можно импортировать и без установленного vosk.
            import vosk

            vosk.SetLogLevel(self.log_level)
            self._model = vosk.Model(str(model_path))
            self._recognizer = vosk.KaldiRecognizer(self._model, float(self.sample_rate))
            self._recognizer.SetWords(True)
        except ImportError as exc:
            raise STTError(
                "Не установлена библиотека vosk.\n"
                "Установите зависимости:  pip install -r requirements.txt"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise STTError(f"Не удалось загрузить модель распознавания: {exc}") from exc

        return self

    def close(self) -> None:
        """Освободить ресурсы модели."""
        self._recognizer = None
        self._model = None

    def __enter__(self) -> "SpeechRecognizer":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---------------- распознавание ----------------

    def accept(self, data: bytes) -> list[Utterance]:
        """Передать порцию звука и получить новые результаты.

        Возвращает список из 0, 1 или 2 элементов: обычно это один
        частичный результат, иногда — только что завершившаяся фраза.
        """
        if self._recognizer is None:
            raise STTError("Модель не открыта. Вызовите open() перед accept().")

        results: list[Utterance] = []

        if self._recognizer.AcceptWaveform(data):
            final = self._parse(self._recognizer.Result(), is_final=True)
            if not final.is_empty:
                results.append(final)
        else:
            partial = self._parse(self._recognizer.PartialResult(), is_final=False)
            if not partial.is_empty:
                results.append(partial)

        return results

    def final(self) -> Utterance | None:
        """Забрать последнюю фразу при остановке потока."""
        if self._recognizer is None:
            return None
        result = self._parse(self._recognizer.FinalResult(), is_final=True)
        return None if result.is_empty else result

    def reset(self) -> None:
        """Сбросить состояние (например, при остановке воспроизведения)."""
        if self._recognizer is not None:
            self._recognizer.Reset()

    @staticmethod
    def _parse(payload: str, *, is_final: bool) -> Utterance:
        """Разобрать JSON от движка распознавания."""
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return Utterance(text="", is_final=is_final)

        if is_final:
            text = str(data.get("text", ""))
            start, end = SpeechRecognizer._word_span(data.get("result", []))
        else:
            text = str(data.get("partial", ""))
            start, end = 0.0, 0.0

        return Utterance(text=text, is_final=is_final, start=start, end=end)

    @staticmethod
    def _word_span(words: list) -> tuple[float, float]:
        """Границы фразы по списку слов (в секундах аудио)."""
        if not words:
            return 0.0, 0.0
        try:
            start = float(words[0].get("start", 0.0))
            end = float(words[-1].get("end", 0.0))
        except (TypeError, ValueError):
            return 0.0, 0.0
        return start, end


# ----------------------------------------------------------------------
#  Поиск модели на диске
# ----------------------------------------------------------------------

def models_dir() -> Path:
    """Каталог, где хранятся модели (assets/models)."""
    return Path(__file__).resolve().parent.parent / "assets" / "models"


def find_model(name: str | None = None) -> Path | None:
    """Найти каталог модели.

    Порядок поиска:
        1. точное имя из аргумента;
        2. переменная окружения VOSK_MODEL;
        3. любая известная модель, скачанная в assets/models.
    """
    candidates: list[Path] = []

    env = os.environ.get("VOSK_MODEL")
    if env:
        candidates.append(Path(env))
    if name:
        candidates.append(Path(name))
        candidates.append(models_dir() / name)

    for known in KNOWN_MODELS:
        candidates.append(models_dir() / known)

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "am").exists():
            return candidate

    return None


def require_model(name: str | None = None) -> Path:
    """Найти модель или объяснить, как её скачать."""
    found = find_model(name)
    if found is not None:
        return found

    raise STTError(
        "Модель распознавания речи не найдена.\n"
        f"Ожидался каталог: {models_dir() / DEFAULT_MODEL}\n"
        "Скачать её можно командой:\n"
        f"    python tools/download_model.py {DEFAULT_MODEL}\n"
        "Либо укажите свой путь переменной окружения VOSK_MODEL."
    )


__all__ = [
    "SpeechRecognizer",
    "Utterance",
    "STTError",
    "find_model",
    "require_model",
    "models_dir",
    "DEFAULT_MODEL",
    "LARGE_MODEL",
    "KNOWN_MODELS",
]