"""
Запуск графического интерфейса.

    python main.py gui
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import DEFAULT_RULES, Application  # noqa: E402


def launch(rules_path: str | Path = DEFAULT_RULES,
           model: str | Path | None = None) -> int:
    """Открыть окно программы.

    Возвращает код выхода для main().
    """
    QApplication.setAttribute(Qt.AA_DontShowIconsInMenus, False)

    qt_app = QApplication.instance() or QApplication(sys.argv)
    qt_app.setApplicationName("Ключевые слова")
    qt_app.setOrganizationName("KeyWordsDemo")

    # Загрузка модели распознавания занимает несколько секунд, поэтому
    # делаем её до появления окна — иначе приложение «зависнет» при старте.
    try:
        app = Application(rules_path, model=model, silent=True)
    except Exception as exc:  # noqa: BLE001 - показываем понятное сообщение
        QMessageBox.critical(
            None,
            "Не удалось запустить систему",
            f"{exc}\n\n"
            "Подсказка: скачать модель распознавания можно командой\n"
            "    python tools/download_model.py",
        )
        return 1

    from ui.main_window import MainWindow  # noqa: PLC0415 - импорт после QApplication

    window = MainWindow(app)
    window.show()

    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(launch())