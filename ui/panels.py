"""
Журнал распознанного текста и список сработавших событий.

Эти два виджета — «приборная панель» системы: они показывают, что именно
распознано и на какие ключевые слова система отреагировала.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

TEXT_COLOR = QColor(226, 230, 238)
PARTIAL_COLOR = QColor(150, 158, 175)
HIT_COLOR = QColor(255, 196, 84)
DRAFT_TAG = "черновик"


class TranscriptPanel(QWidget):
    """Распознанный текст с подсветкой найденных ключевых слов."""

    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._final_text = ""
        self._partial_text = ""
        self._hits: list[tuple[int, int]] = []   # диапазоны подсветки в final_text

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 11))
        self.view.setStyleSheet(
            f"QTextEdit {{ background:#14161d; color:{TEXT_COLOR.name()};"
            " border:1px solid #2a2e3a; border-radius:6px; padding:8px; }"
        )

        button = QPushButton("Очистить")
        button.clicked.connect(self.clear)
        button.setFixedWidth(100)

        header = QHBoxLayout()
        header.addWidget(button)
        header.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.view)

        self._render()

    # ---------------- ввод данных ----------------

    def add_text(self, text: str, is_final: bool, audio_time: float) -> None:
        """Новый результат распознавания.

        Итоговые фразы накапливаются, черновик показывается отдельным
        курсивом и при следующем обновлении исчезает.
        """
        self._partial_text = "" if is_final else text

        if is_final and text.strip():
            self._final_text += (" " if self._final_text else "") + text.strip()

        self._render()

    def mark_hit(self, matched: str) -> None:
        """Подсветить место в тексте, где найдено ключевое слово."""
        normalized = " ".join(matched.lower().split())
        haystack = " ".join(self._final_text.lower().split())
        if not normalized:
            return

        index = haystack.rfind(normalized)
        if index >= 0:
            # переводим позицию из нормализованного текста в исходный
            original = haystack[:index].count(" ") + 1
            self._hits.append((original, original + len(normalized.split())))

    def clear(self) -> None:
        self._final_text = ""
        self._partial_text = ""
        self._hits.clear()
        self._render()
        self.cleared.emit()

    # ---------------- отрисовка ----------------

    def _render(self) -> None:
        """Перерисовать текст целиком: подсветка меняется по ходу работы."""
        parts: list[str] = []

        cursor_position = 0
        for start, end in sorted(self._hits):
            start = max(cursor_position, min(start, len(self._final_text)))
            end = max(start, min(end, len(self._final_text)))
            parts.append(html.escape(self._final_text[cursor_position:start]))
            parts.append(
                f'<span style="background:#b8860b33;color:{HIT_COLOR.name()};'
                f'font-weight:600">{html.escape(self._final_text[start:end])}</span>'
            )
            cursor_position = end
        parts.append(html.escape(self._final_text[cursor_position:]))

        body = "".join(parts) or '<span style="color:#5b6272">— текст появится здесь —</span>'

        if self._partial_text:
            body += (
                f'<br><span style="color:{PARTIAL_COLOR.name()};font-style:italic">'
                f"{DRAFT_TAG}: {html.escape(self._partial_text)}</span>"
            )

        self.view.setHtml(body)

        # прокрутка вниз, чтобы был виден последний результат
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())


class EventLogPanel(QWidget):
    """Список сработавших событий — по сути, журнал системы."""

    cleared = Signal()

    COLUMNS = ["Время", "Действие", "Найдено", "Совпадение", "Уверенность"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { background:#14161d; }")

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        button = QPushButton("Очистить журнал")
        button.clicked.connect(self.clear)
        button.setFixedWidth(150)

        top = QHBoxLayout()
        top.addWidget(button)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)

    def add_event(self, event) -> None:
        """Добавить строку журнала."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        cells = [
            f"{event.audio_time:.2f} с",
            event.action,
            event.matched,
            f"«{event.phrase}»",
            f"{event.score:.0%}" + ("" if event.is_final else f"  ({DRAFT_TAG})"),
        ]

        for column, value in enumerate(cells):
            item = QTableWidgetItem(value)
            if column == 4:
                item.setForeground(QBrush(QColor(150, 158, 175)))
            if not event.is_final:
                item.setForeground(QBrush(QColor(PARTIAL_COLOR)))
            item.setToolTip(event.describe())
            self.table.setItem(row, column, item)

        self.table.scrollToBottom()

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.cleared.emit()


class RulesPanel(QWidget):
    """Таблица правил с возможностью включать и выключать их.

    Это и есть ответ на вопрос аннотации про сотни обработчиков:
    новые ключевые слова добавляются файлом config/rules.json, а здесь
    их можно посмотреть и отключить, не перезапуская программу.
    """

    changed = Signal()

    def __init__(self, rules, parent=None) -> None:
        super().__init__(parent)
        self._rules = rules

        COLUMNS = ["Вкл", "Правило", "Действие", "Ключевые слова", "Порог", "Повтор, с"]
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { background:#14161d; }")
        self.table.itemChanged.connect(self._on_item_changed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        save = QPushButton("Сохранить в JSON")
        save.clicked.connect(self._on_save)

        reload_btn = QPushButton("Перечитать файл")
        reload_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.addWidget(save)
        top.addWidget(reload_btn)
        top.addStretch(1)

        hint = QTextEdit(self)
        hint.setReadOnly(True)
        hint.setMaximumHeight(74)
        hint.setPlainText(
            "Правила хранятся в config/rules.json. Чтобы добавить новое "
            "ключевое слово, достаточно дописать запись в этот файл — "
            "код менять не нужно."
        )
        hint.setStyleSheet("QTextEdit { background:#1b1e27; border:1px solid #2a2e3a;"
                            " border-radius:6px; padding:6px; }")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(hint)

        self.status = QPushButton("")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        self.reload()

    def reload(self) -> None:
        """Перечитать правила и заполнить таблицу."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for rule in self._rules:
            row = self.table.rowCount()
            self.table.insertRow(row)

            enabled = QTableWidgetItem("")
            enabled.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            enabled.setCheckState(Qt.Checked if rule.enabled else Qt.Unchecked)
            enabled.setData(Qt.UserRole, rule.id)
            self.table.setItem(row, 0, enabled)

            for column, value in enumerate(
                [rule.id, rule.action, ", ".join(rule.phrases),
                 f"{rule.threshold:.2f}", f"{rule.cooldown:g}"],
                start=1,
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))

        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Включение или выключение правила галочкой."""
        if item.column() != 0:
            return

        rule = self._rules.get(item.data(Qt.UserRole))
        if rule is not None:
            rule.enabled = (item.checkState() == Qt.Checked)
            self.changed.emit()

    def _on_save(self) -> None:
        """Записать текущее состояние правил обратно в файл."""
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "config" / "rules.json"
        try:
            self._rules.save(path)
        except OSError as exc:
            self._show_status(f"Не удалось сохранить: {exc}", error=True)
            return

        self._show_status(f"Сохранено в {path.name}")

    def _show_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setVisible(True)
        self.status.setStyleSheet(
            "QPushButton { background:#7a2b2b; color:#ffd7d7; }" if error
            else "QPushButton { background:#1f4d2b; color:#c9f0d4; }"
        )


class StatsPanel(QWidget):
    """Сводка: счётчики событий и скорость работы системы."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setStyleSheet(
            "QTextEdit { background:#14161d; border:1px solid #2a2e3a;"
            " border-radius:6px; padding:10px; }"
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        self.update_stats(None, {})

    def update_stats(self, engine_stats, counters: dict) -> None:
        """Перерисовать сводку."""
        lines: list[str] = []

        if engine_stats is None:
            lines.append("<b>Работа ещё не началась.</b>")
            lines.append("Запустите воспроизведение файла или микрофон.")
        else:
            data = engine_stats.as_dict()
            lines.append("<b>Работа системы</b>")
            lines.append("")
            lines.append(f"Обработано звука:&nbsp;&nbsp;<b>{data['seconds']}</b> с")
            lines.append(f"Время обработки:&nbsp;&nbsp;<b>{data['wall']}</b> с")
            lines.append(
                "Скорость:&nbsp;&nbsp;"
                f"<b>x{data['rtf']}</b> от реального времени"
                + ("&nbsp;&nbsp;(успевает)" if data["rtf"] and data["rtf"] < 1.0 else "")
            )
            lines.append("")
            lines.append(f"Распознано фраз:&nbsp;&nbsp;<b>{data['phrases']}</b>")
            lines.append(f"Событий:&nbsp;&nbsp;<b>{data['events']}</b>")
            lines.append(f"&nbsp;&nbsp;по черновику:&nbsp;&nbsp;{data['events_partial']}")
            lines.append(f"&nbsp;&nbsp;подтверждено:&nbsp;&nbsp;{data['events_final']}")
            lines.append(f"Подавлено повторов:&nbsp;&nbsp;{data['suppressed']}")

            if data["by_rule"]:
                lines.append("")
                lines.append("<b>По правилам</b>")
                lines.append("")
                for rule_id, count in sorted(data["by_rule"].items(),
                                             key=lambda kv: -kv[1]):
                    lines.append(f"{html.escape(rule_id):<26} {count:>4} раз(а)")

        if counters:
            lines.append("")
            lines.append("<b>Счётчики из правил</b>")
            lines.append("")
            for name, value in sorted(counters.items()):
                lines.append(f"{html.escape(name):<26} {value:g}")

        self.view.setHtml("<br>".join(lines))
        self.view.moveCursor(QTextCursor.Start)


__all__ = ["TranscriptPanel", "EventLogPanel", "RulesPanel", "StatsPanel"]