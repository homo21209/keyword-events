"""
Создание демонстрационной дорожки.

    python tools/make_demo_audio.py

Собирает wav-файл, в котором звучат ключевые слова из сценария
аннотации. Нужен для проверки системы без оригинальной записи песни:
произносит речь встроенный синтезатор Windows.

Готовый файл складывается в assets/demo/демо.wav и запускается так:

    python main.py play assets/demo/демо.wav
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "assets" / "demo"
OUT_FILE = OUT_DIR / "демо.wav"

# Куски демонстрации: (что говорить, пауза после в секундах)
# Паузы нужны, чтобы проверить, что система не «склеивает» слова
# из разных фраз, звучащих далеко друг от друга.
SCRIPT: list[tuple[str, float]] = [
    ("восемь вечера. в доме горел свет.", 1.2),
    ("певица пела старинную песню про часы.", 1.0),
    ("посмотри на часы.", 1.2),
    ("часы остановились.", 2.0),
    ("в доме стало тихо.", 1.5),
    ("а потом всё ожило.", 1.2),
    ("ещё идут старинные часы.", 2.0),
    ("часы остановились.", 1.5),
    ("но и это не помогло.", 1.2),
    ("ещё идут старинные часы.", 2.0),
    ("тишина.", 0.5),
]


def build_script() -> str:
    """Собрать текст программы для PowerShell."""
    lines = []
    for text, pause in SCRIPT:
        escaped = text.replace("'", "''")
        lines.append(
            f"    $synth.Speak('{escaped}')",
        )
        lines.append(f"    Start-Sleep -Milliseconds {int(pause * 1000)}")

    body = "\n".join(lines)
    return f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile('{OUT_FILE}')
{body}
$synth.SetOutputToNull()
$synth.Dispose()
Write-Output 'ok'
"""


def make(target: Path = OUT_FILE) -> Path:
    """Создать демонстрационный файл."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    script = build_script()

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    if result.returncode != 0 or not target.exists():
        raise RuntimeError(
            "Не удалось создать демонстрационный файл.\n"
            f"{result.stdout}\n{result.stderr}"
        )

    # Пересчитываем в 16 кГц моно — так, как ждёт модель распознавания
    convert_to_16k(target)

    with wave.open(str(target), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()

    print(f"Готово: {target}")
    print(f"Длительность: {duration:.1f} с")
    return target


def convert_to_16k(path: Path, rate: int = 16000) -> None:
    """Привести файл к 16 кГц / моно / 16 бит."""
    import soundfile as sf

    from core.audio import resample

    data, current = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)

    if current != rate:
        data = resample(data, current, rate)

    sf.write(str(path), data, rate, subtype="PCM_16")


def main() -> int:
    try:
        make()
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка: {exc}")
        return 1

    print()
    print("Проверить работу системы можно так:")
    print(f'    python main.py play "{OUT_FILE}"')
    print(f'    python main.py check "{OUT_FILE}"')
    print(f'    python main.py gui')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())