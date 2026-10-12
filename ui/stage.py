"""
Сцена — то, что видно «на экране MediaPlayer».

Аннотация требует, чтобы наряду с изображением певицы выводилось
изображение настенных часов, а по ключевым словам стрелки на них
останавливались. Виджет сцены как раз и рисует эту картинку.

Настоящие картинки (assets/images/singer.*, assets/images/clock.*)
подхватываются автоматически, если они есть. Если их нет, сцена
рисуется программно — чтобы приложение всегда запускалось и выглядело
прилично без подготовки графики.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .clock_widget import ClockWidget

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "images"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

BG_TOP = QColor(28, 30, 46)
BG_BOTTOM = QColor(58, 40, 62)
ACCENT = QColor(236, 196, 108)


def _load_image(name: str) -> QPixmap | None:
    """Найти картинку сцены в assets/images."""
    for suffix in IMAGE_SUFFIXES:
        path = ASSETS / f"{name}{suffix}"
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
    return None


class SceneView(QWidget):
    """Экран сцены: певица, часы, подписи."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(320)

        self._scene = "both"
        self._caption = ""
        self._highlight = ""
        self._highlight_until = 0.0

        self._singer_pixmap = _load_image("singer")
        self._clock_pixmap = _load_image("clock")

        # Живые часы поверх сцены — именно их останавливают события
        self.clock = ClockWidget(self)
        self.clock.set_running(True)

        self._timer_pulse()
        self.clock.runningChanged.connect(lambda _: self.update())

    # ---------------- состояние ----------------

    @property
    def scene(self) -> str:
        """Текущая сцена."""
        return self._scene

    @property
    def caption(self) -> str:
        """Текущая подпись."""
        return self._caption

    def set_scene(self, scene: str) -> None:
        """Переключить сцену: singer / clock / both / black."""
        if scene != self._scene:
            self._scene = scene
            self.clock.setVisible(scene in ("clock", "both"))
            self.clock.setGeometry(self._clock_rect())
            self.update()

    def set_caption(self, text: str) -> None:
        """Подпись внизу экрана («А часы-то остановились!»)."""
        if text != self._caption:
            self._caption = text
            self.update()

    def flash(self, text: str) -> None:
        """Мигнуть подсветкой ключевого слова в углу экрана."""
        self._highlight = text
        self._highlight_until = time_now() + 2.0
        self.update()

    def _timer_pulse(self) -> None:
        """Обновлять подсветку, пока она горит."""
        from PySide6.QtCore import QTimer

        self._pulse = QTimer(self)
        self._pulse.timeout.connect(self._on_pulse)
        self._pulse.start(120)

    def _on_pulse(self) -> None:
        if self._highlight and time_now() > self._highlight_until:
            self._highlight = ""
            self.update()

    # ---------------- геометрия ----------------

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self.clock.setGeometry(self._clock_rect())

    def _clock_rect(self) -> QRect:
        """Положение часов на сцене."""
        side = int(min(self.width(), self.height()) * 0.46)
        left = self.width() - side - 24
        top = (self.height() - side) // 2
        return QRect(left, top, side, side)

    # ---------------- отрисовка ----------------

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._scene == "black":
            painter.fillRect(self.rect(), QColor(8, 8, 12))
        else:
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0, BG_TOP)
            gradient.setColorAt(1, BG_BOTTOM)
            painter.fillRect(self.rect(), QBrush(gradient))

            show_singer = self._scene in ("singer", "both")
            show_clock_pic = self._scene in ("clock", "both")

            if show_singer:
                self._draw_singer(painter)
            if show_clock_pic and self._clock_pixmap is not None:
                self._draw_clock_picture(painter)
            elif self._scene == "clock":
                self._draw_clock_placeholder(painter)

        self._draw_caption(painter)
        self._draw_highlight(painter)
        painter.end()

    def _draw_singer(self, painter: QPainter) -> None:
        """Певица: своя картинка или рисованная силуэтом."""
        area = self._half_rect()

        if self._singer_pixmap is not None:
            self._draw_pixmap_cover(painter, self._singer_pixmap, area)
            return

        # Процедурный силуэт певицы с микрофоном
        painter.save()
        painter.setClipRect(area)

        glow = QLinearGradient(area.topLeft(), area.bottomRight())
        glow.setColorAt(0, QColor(236, 196, 108, 60))
        glow.setColorAt(1, QColor(236, 196, 108, 0))
        painter.fillRect(area, QBrush(glow))

        cx = area.center().x()
        base = area.bottom() - area.height() * 0.06
        head_r = area.height() * 0.10
        head_y = area.top() + area.height() * 0.26

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 18, 26, 230))
        painter.drawEllipse(QPointF(cx, head_y), head_r, head_r * 1.12)      # голова
        body = QRectF(
            cx - area.width() * 0.17, head_y + head_r * 0.75,
            area.width() * 0.34, base - (head_y + head_r * 0.75),
        )
        painter.drawRoundedRect(body, 28, 28)                                # плечи

        # микрофон
        mic_w = max(6.0, area.width() * 0.022)
        painter.setBrush(QColor(196, 196, 206))
        mic = QRectF(cx + area.width() * 0.13, head_y - head_r * 0.2, mic_w, mic_w * 2.6)
        painter.drawRoundedRect(mic, mic_w / 2, mic_w / 2)
        painter.setBrush(QColor(120, 120, 132))
        painter.drawRect(QRectF(cx + area.width() * 0.13 + mic_w * 0.35,
                                mic.bottom(), mic_w * 0.3, area.height() * 0.16))

        painter.restore()

        font = QFont()
        font.setPointSizeF(max(8.0, area.height() * 0.055))
        font.setItalic(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 150))
        painter.drawText(area.adjusted(0, 0, 0, -int(area.height() * 0.08)),
                         Qt.AlignHCenter | Qt.AlignBottom, "певица")

    def _draw_clock_picture(self, painter: QPainter) -> None:
        """Настенные часы, если есть картинка; иначе — их отрисует виджет."""
        if self._clock_pixmap is None:
            return
        self._draw_pixmap_cover(painter, self._clock_pixmap, self._half_rect())

    def _draw_clock_placeholder(self, painter: QPainter) -> None:
        """Подложка на месте часов, пока не подгрузился виджет."""
        area = QRectF(self._clock_rect())
        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(area, 10, 10)

    def _half_rect(self) -> QRectF:
        """Левая половина сцены — место для певицы."""
        return QRectF(0, 0, self.width() * 0.52, self.height())

    @staticmethod
    def _draw_pixmap_cover(painter: QPainter, pixmap: QPixmap, area: QRectF) -> None:
        """Вписать картинку в прямоугольник без искажения пропорций."""
        if pixmap.isNull() or area.width() <= 0 or area.height() <= 0:
            return

        scale = max(area.width() / pixmap.width(), area.height() / pixmap.height())
        size = pixmap.size() * scale
        x = area.center().x() - size.width() / 2
        y = area.center().y() - size.height() / 2

        painter.save()
        painter.setClipRect(area)
        painter.drawPixmap(QRectF(x, y, size.width(), size.height()), pixmap,
                           QRectF(pixmap.rect()))
        painter.restore()

    def _draw_caption(self, painter: QPainter) -> None:
        """Подпись по нижнему краю сцены."""
        if not self._caption:
            return

        font = QFont()
        font.setPointSizeF(max(11.0, self.height() * 0.055))
        font.setBold(True)

        painter.setFont(font)
        box = QRectF(24, self.height() - 74, self.width() - 48, 52)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(box, 10, 10)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, Qt.AlignCenter, self._caption)

    def _draw_highlight(self, painter: QPainter) -> None:
        """Вспышка с найденным ключевым словом."""
        if not self._highlight:
            return

        font = QFont()
        font.setPointSizeF(max(10.0, self.height() * 0.045))
        font.setBold(True)

        painter.setFont(font)
        box = QRectF(24, 20, min(self.width() - 48, 420), 44)

        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(QColor(20, 20, 26, 200))
        painter.drawRoundedRect(box, 8, 8)

        painter.setPen(ACCENT)
        painter.drawText(box, Qt.AlignCenter, f"«{self._highlight}»")


def time_now() -> float:
    """Текущее время (отдельная функция, чтобы не тянуть импорт вверх)."""
    import time

    return time.monotonic()


__all__ = ["SceneView"]