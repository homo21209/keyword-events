"""
Виджет часов.

Ровно тот объект, который описан в аннотации: пока идёт песня, на экране
 alongside с певицей видны настенные часы, и стрелки на них двигаются.
Когда прозвучат ключевые слова «Часы остановились», стрелки замирают.

Виджет ничего не знает про распознавание речи: у него есть только
метод set_running(True/False). Управляет им обработчик ClockHandler.

Анимация сделана на QTimer с шагом 50 мс — так плавнее, чем обновлять
раз в секунду, и при этом почти не нагружает процессор.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

# Палитра
FACE_COLOR = QColor(250, 246, 236)
RIM_COLOR = QColor(122, 92, 58)
TICK_COLOR = QColor(58, 46, 34)
HAND_COLOR = QColor(38, 30, 24)
SECOND_COLOR = QColor(186, 62, 44)
STOP_COLOR = QColor(186, 62, 44)

# Шаг обновления, мс
TICK_MS = 50


class ClockWidget(QWidget):
    """Настенные часы с остановимымися стрелками."""

    runningChanged = Signal(bool)

    def __init__(self, parent=None, *, running: bool = True) -> None:
        super().__init__(parent)
        self._running = running
        self._frozen: datetime | None = None
        self._tick = 0

        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ---------------- состояние ----------------

    @property
    def running(self) -> bool:
        return self._running

    def set_running(self, running: bool) -> None:
        """Запустить или остановить стрелки.

        При остановке запоминается текущее время — стрелки «замерли»,
        при запуске часы снова идут от этого момента.
        """
        running = bool(running)
        if running == self._running:
            return

        self._running = running
        if not running:
            self._frozen = datetime.now()
        else:
            self._frozen = None

        self.update()
        self.runningChanged.emit(running)

    def _on_tick(self) -> None:
        """Обновлять картинку, пока часы идут."""
        self._tick += 1
        if self._running or self._tick % 20 == 0:  # при остановке — редко, ради подписи
            self.update()

    def current_time(self) -> datetime:
        """Время, которое показывают часы."""
        return self._frozen if self._frozen is not None else datetime.now()

    # ---------------- отрисовка ----------------

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - имя из Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        size = min(self.width(), self.height())
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = size / 2 - 8

        if radius <= 0:
            return

        # корпус
        painter.setPen(QPen(RIM_COLOR, max(3.0, size * 0.022)))
        painter.setBrush(QBrush(FACE_COLOR))
        painter.drawEllipse(center, radius, radius)

        self._draw_ticks(painter, center, radius)
        self._draw_numbers(painter, center, radius)
        self._draw_hands(painter, center, radius)
        self._draw_stop_badge(painter, center, radius)

        painter.end()

    def _draw_ticks(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Минутные и часовые деления."""
        for i in range(60):
            angle = i * 6
            is_hour = (i % 5 == 0)

            length = radius * (0.12 if is_hour else 0.055)
            width = 0.055 if is_hour else 0.02

            inner = self._point_on_circle(center, radius - radius * 0.11 - length, angle)
            outer = self._point_on_circle(center, radius - radius * 0.11, angle)

            pen = QPen(TICK_COLOR, max(1.0, radius * width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(inner, outer)

    def _draw_numbers(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Цифры на циферблате."""
        font = QFont()
        font.setPointSizeF(max(6.0, radius * 0.17))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(TICK_COLOR))

        for number in range(1, 13):
            angle = number * 30
            point = self._point_on_circle(center, radius * 0.76, angle)
            painter.drawText(
                QRectF(point.x() - radius * 0.12, point.y() - radius * 0.10,
                       radius * 0.24, radius * 0.20),
                Qt.AlignCenter,
                str(number),
            )

    def _draw_hands(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Часовая, минутная и секундная стрелки."""
        now = self.current_time()
        # начиная с Python 3.13 поля datetime — свойства, а не методы
        seconds = now.second + now.microsecond / 1_000_000
        minutes = now.minute + seconds / 60
        hours = (now.hour % 12) + minutes / 60

        # (угол, длина, толщина, цвет) — толщина уже в пикселях
        hands = [
            (hours * 30, radius * 0.50, radius * 0.055, HAND_COLOR),
            (minutes * 6, radius * 0.74, radius * 0.040, HAND_COLOR),
            (seconds * 6, radius * 0.80, radius * 0.018, SECOND_COLOR),
        ]

        for angle, length, width, color in hands:
            tip = self._point_on_circle(center, length, angle)
            tail = self._point_on_circle(center, length * 0.14, angle + 180)

            pen = QPen(color, max(1.5, width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(tail, tip)

        # центральная ось
        painter.setPen(QPen(HAND_COLOR, 1))
        painter.setBrush(QBrush(HAND_COLOR))
        painter.drawEllipse(center, radius * 0.045, radius * 0.045)

    def _draw_stop_badge(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """Пометка «СТОП», когда стрелки остановлены."""
        if self._running:
            return

        font = QFont()
        font.setPointSizeF(max(7.0, radius * 0.17))
        font.setBold(True)

        badge = QRectF(
            center.x() - radius * 0.34,
            center.y() + radius * 0.42,
            radius * 0.68,
            radius * 0.26,
        )

        painter.setFont(font)
        painter.setPen(QPen(STOP_COLOR, 2))
        painter.setBrush(QBrush(QColor(255, 236, 232)))
        painter.drawRoundedRect(badge, 6, 6)
        painter.drawText(badge, Qt.AlignCenter, "СТОП")

    @staticmethod
    def _point_on_circle(center: QPointF, radius: float, angle_deg: float) -> QPointF:
        """Точка на окружности под заданным углом (0° — верх, по часовой)."""
        import math

        angle = math.radians(angle_deg - 90)
        return QPointF(
            center.x() + radius * math.cos(angle),
            center.y() + radius * math.sin(angle),
        )


class StopwatchLabel(QWidget):
    """Цифровое время под часами — «замерло» или идёт."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(28)
        self._running = True
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(1000)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.update()

    def _on_tick(self) -> None:
        self._tick += 1
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        now = datetime.now()
        font = QFont()
        font.setPointSizeF(13)
        font.setBold(True)
        self.setFont(font)

        painter = QPainter(self)
        painter.setPen(QPen(HAND_COLOR if self._running else STOP_COLOR))
        painter.drawText(
            self.rect(),
            Qt.AlignCenter,
            now.strftime("%H:%M:%S") + ("" if self._running else "   (остановлены)"),
        )
        painter.end()


# Импортируется для полноты картинки; используется в отрисовке стрелок
_ = QPolygonF

__all__ = ["ClockWidget", "StopwatchLabel"]