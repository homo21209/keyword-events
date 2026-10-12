"""
Загрузка модели распознавания речи Vosk.

    python tools/download_model.py                # маленькая модель (~44 МБ)
    python tools/download_model.py large          # большая модель (~1.3 ГБ)
    python tools/download_model.py list          # что уже скачано

Модели лежат в assets/models и не входят в систему контроля версий:
они слишком велики для репозитория.

Почему загрузка такая сложная
-----------------------------
Сервер моделей отдаёт файл маленькими порциями (примерно по 8 КБ) и
часто замолкает посреди ответа. Одно соединение даёт около 15 КБ/с,
то есть модель качалась бы несколько часов. Несколько соединений
сразу работают почти в суммарной скорости, поэтому файл скачивается
блоками в несколько потоков, а каждый блок пишется на своё место
в заранее выделенный файл.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://alphacephei.com/vosk/models"

MODELS = {
    "small": {
        "name": "vosk-model-small-ru-0.22",
        "size": "44 МБ",
        "note": "быстрая, подходит для демонстрации в реальном времени",
    },
    "large": {
        "name": "vosk-model-ru-0.10",
        "size": "1.3 ГБ",
        "note": "точнее распознаёт шёпот и шум, требует больше памяти",
    },
}

# Размер одного блока. Сервер отдаёт примерно по 8 КБ за раз, и блок
# большего размера он может не дослать.
BLOCK_SIZE = 8 * 1024

# Сколько блоков качать одновременно
WORKERS = 16

# Сколько секунд ждать данные
READ_TIMEOUT = 25

# Сколько раз повторить неудачный блок
BLOCK_ATTEMPTS = 12


def models_dir() -> Path:
    """Каталог для моделей — assets/models рядом с корнем проекта."""
    return Path(__file__).resolve().parent.parent / "assets" / "models"


def already_present(name: str) -> Path | None:
    """Путь к модели, если она уже скачана."""
    path = models_dir() / name
    return path if (path / "am").exists() else None


def list_installed() -> list[tuple[str, Path]]:
    """Все установленные модели."""
    result = []
    if models_dir().exists():
        for child in sorted(models_dir().iterdir()):
            if (child / "am").exists():
                result.append((child.name, child))
    return result


# ----------------------------------------------------------------------
#  Загрузка
# ----------------------------------------------------------------------

def _remote_size(url: str) -> int:
    """Узнать размер файла на сервере (без скачивания целиком)."""
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:  # noqa: S310
        if response.status != 206:
            # сервер не умеет докачку — отдаст Content-Length целиком
            return int(response.headers.get("Content-Length") or 0)
        # Content-Range имеет вид: bytes 0-0/46236750
        return int(response.headers.get("Content-Range", "").rsplit("/", 1)[-1])


def _fetch_block(url: str, start: int, end: int) -> bytes:
    """Скачать диапазон [start; end] целиком, при необходимости повторяя."""
    wanted = end - start + 1

    for attempt in range(BLOCK_ATTEMPTS):
        try:
            request = urllib.request.Request(
                url, headers={"Range": f"bytes={start}-{end}"}
            )
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:  # noqa: S310
                parts: list[bytes] = []
                got = 0
                while got < wanted:
                    # read1 отдаёт данные сразу, не дожидаясь заполнения буфера
                    piece = response.read1(BLOCK_SIZE)
                    if not piece:
                        break
                    parts.append(piece)
                    got += len(piece)

            data = b"".join(parts)
            if len(data) == wanted:
                return data
        except Exception:  # noqa: BLE001 - попробуем ещё раз
            pass
        time.sleep(0.5)

    raise RuntimeError(f"блок {start}-{end} не скачан за {BLOCK_ATTEMPTS} попыток")


def _download_file(url: str, destination: Path, *, progress: bool = True) -> None:
    """Скачать файл блоками в несколько потоков."""
    try:
        total = _remote_size(url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Не удалось получить сведения о файле {url}: {exc}") from exc

    count = (total + BLOCK_SIZE - 1) // BLOCK_SIZE
    if progress:
        print(f"  размер {total / 1_048_576:.1f} МБ, блоков {count}, "
              f"потоков {WORKERS}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    # файл заранее растягивается на полный размер: каждый поток потом
    # пишет свой блок по своему смещению, порядок не важен
    with destination.open("wb") as out:
        out.truncate(total)

    blocks = [(i * BLOCK_SIZE, min((i + 1) * BLOCK_SIZE, total) - 1)
              for i in range(count)]
    queues: list[list[tuple[int, int]]] = [[] for _ in range(WORKERS)]
    for i, block in enumerate(blocks):
        queues[i % WORKERS].append(block)

    done = 0
    lock = threading.Lock()
    last_percent = -1
    errors: list[str] = []

    def worker(assigned: list[tuple[int, int]]) -> None:
        nonlocal done, last_percent

        with destination.open("r+b") as out:
            for start, end in assigned:
                try:
                    data = _fetch_block(url, start, end)
                except RuntimeError as exc:
                    errors.append(str(exc))
                    continue

                out.seek(start)
                out.write(data)

                with lock:
                    done += 1
                    percent = done * 100 // count
                    if progress and percent != last_percent:
                        last_percent = percent
                        bar = "#" * (percent // 2)
                        sys.stdout.write(
                            f"\r  [{bar:<50}] {percent:3d}%  "
                            f"({done * BLOCK_SIZE / 1_048_576:.1f} МБ)"
                        )
                        sys.stdout.flush()

    threads = [threading.Thread(target=worker, args=(q,), daemon=True)
               for q in queues if q]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if progress:
        print()

    if errors:
        raise RuntimeError(
            f"Не удалось скачать {len(errors)} из {count} блоков.\n"
            f"Пример: {errors[0]}\n"
            "Повторите загрузку — она попробует заново только их."
        )


def download(name: str, *, force: bool = False, progress: bool = True) -> Path:
    """Скачать и распаковать модель."""
    target = models_dir() / name
    models_dir().mkdir(parents=True, exist_ok=True)

    if already_present(name) and not force:
        print(f"Модель уже установлена: {target}")
        return target

    archive = models_dir() / f".{name}.zip.part"

    print(f"Загрузка модели {name}")
    print(f"  адрес: {BASE_URL}/{name}.zip")

    _download_file(f"{BASE_URL}/{name}.zip", archive, progress=progress)
    print(f"  распаковка...")

    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target.parent)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Архив повреждён: {exc}\n"
            "Удалите файл и повторите загрузку:\n"
            f"    del \"{archive}\""
        ) from exc

    if not (target / "am").exists():
        raise RuntimeError(f"В архиве нет ожидаемой папки модели: {name}")

    archive.unlink(missing_ok=True)
    print(f"Готово: {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    args = [str(a).lower() for a in raw]

    if not args or args[0] in ("small", "ru-small"):
        download(MODELS["small"]["name"])
        return 0

    if args[0] in ("large", "ru-large", "big"):
        download(MODELS["large"]["name"])
        return 0

    if args[0] == "list":
        installed = list_installed()
        if not installed:
            print("Ни одной модели не установлено.")
        for name, path in installed:
            print(f"  {name}  ->  {path}")
        print("\nДоступные модели:")
        for key, info in MODELS.items():
            state = "установлена" if already_present(info["name"]) else "не скачана"
            print(f"  {key:6} {info['name']:28} {info['size']:>8}  "
                  f"[{state}]  {info['note']}")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())