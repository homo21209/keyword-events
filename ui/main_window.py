"""
Главное окно программы.

Распознавание речи — тяжёлая операция, выполнять её в главном потоке
нельзя: окно перестанет отвечать. Поэтому звук обрабатывается в отдельном
потоке (RecognitionWorker), а графическая часть живёт в своём.

Связь между потоками — через сигналы Qt:

    RecognitionWorker --(textReady, progress, state)--> MainWindow
    MainWindow         --(события из шины)-------------> обработчики -> виджеты

Шина событий разбирается в главном потоке по таймеру. Поэтому обработчики,
которые меняют состояние часов и подписей, тоже работают в главном потоке
и могут спокойно трогать виджеты.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import Application
from core.audio import FileSource, MicSource
from core.stt import STTError

from .panels import EventLogPanel, RulesPanel, StatsPanel, TranscriptPanel
from .stage import SceneView

AUDIO_FILTER = "Аудио (*.wav *.mp3 *.ogg *.flac);;Все файлы (*.*)"

# Поддерживаемые форматы — их умеет читать soundfile
AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac")


def format_time(seconds: float) -> str:
    """Секунды -> «мм:сс»."""
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


# ----------------------------------------------------------------------
#  Рабочий поток
# ----------------------------------------------------------------------

class RecognitionWorker(QObject):
    """Обрабатывает звук вне главного потока."""

    textReady = Signal(str, bool, float)
    progress = Signal(float, float)
    stateChanged = Signal(str)
    runFinished = Signal(dict)
    failed = Signal(str)

    def __init__(self, app: Application) -> None:
        super().__init__()
        self._app = app
        self._mode = "file"
        self._path: Path | None = None
        self.stop_requested = False
        self.pause_requested = False
        self._source = None

    def configure(self, mode: str, path: Path | None = None) -> None:
        """Что именно обрабатывать. Вызывается до запуска потока."""
        self._mode = mode
        self._path = path
        self.stop_requested = False
        self.pause_requested = False
        self._source = None

    def on_control(self, event) -> None:
        """Реакция на событие управления воспроизведением.

        Обработчик вызывается из главного потока (там разбирается шина),
        поэтому здесь только меняются флаги — их рабочий поток читает
        на каждой порции.
        """
        control = event.payload.get("_control")
        if control == "pause":
            self.pause_requested = True
        elif control == "resume":
            self.pause_requested = False
        elif control == "stop":
            self.stop_requested = True

        source = self._source
        if source is None:
            return

        if control == "stop":
            source.stop()
        else:
            source.set_paused(control == "pause")

    @Slot()
    def run(self) -> None:
        """Точка входа потока."""
        try:
            if self._mode == "mic":
                self._run_microphone()
            else:
                self._run_file()
        except STTError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - сообщаем в интерфейс
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def _make_engine(self):
        return self._app.make_engine(
            on_text=lambda text, is_final, t0: self.textReady.emit(text, is_final, t0)
        )

    def _run_file(self) -> None:
        """Прогон аудиофайла со звуком, в темпе записи."""
        if self._path is None:
            return

        engine = self._make_engine()
        source = FileSource(self._path, real_time=True)
        self._source = source
        total = source.duration

        self.stateChanged.emit(f"Файл: {self._path.name}  ({format_time(total)})")

        try:
            with source:
                for frame in source:
                    if self.stop_requested or source.stopped:
                        self.stateChanged.emit("Остановлено")
                        break

                    # пауза держит и звук, и поток распознавания:
                    # иначе система услышала бы то, что ещё не прозвучало
                    if self.pause_requested:
                        source.set_paused(True)

                    engine.process_frame(frame)
                    self.progress.emit(frame.t0, total)

            engine.finish()
        finally:
            self._source = None

        self.runFinished.emit(engine.stats.as_dict())

    def _run_microphone(self) -> None:
        """Работа с микрофоном до остановки."""
        engine = self._make_engine()
        source = MicSource()
        self._source = source
        started = time.monotonic()

        self.stateChanged.emit("Микрофон включён — говорите")

        try:
            with source:
                for frame in source:
                    if self.stop_requested:
                        self.stateChanged.emit("Остановлено")
                        break

                    engine.process_frame(frame)
                    elapsed = time.monotonic() - started
                    self.progress.emit(elapsed, 0.0)

            engine.finish()
        finally:
            self._source = None

        self.runFinished.emit(engine.stats.as_dict())


# ----------------------------------------------------------------------
#  Главное окно
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Окно программы."""

    def __init__(self, app: Application, parent=None) -> None:
        super().__init__(parent)
        self.app = app

        self._thread: QThread | None = None
        self._worker: RecognitionWorker | None = None
        self._file: Path | None = None
        self._mic_running = False
        self._clock_seen = True

        self.setWindowTitle("Управление событиями на основе ключевых слов")
        self.resize(1180, 760)

        self._build_ui()
        self._build_toolbar()

        # каждое событие попадает в журнал (этот обработчик тоже
        # вызывается из главного потока, при разборе шины по таймеру)
        self.app.bus.subscribe("*", self._on_event)

        # разбор накопившихся событий — в главном потоке
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start(50)

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(500)

        self._update_actions()
        self._refresh_stats()

    # ---------------- интерфейс ----------------

    def _build_ui(self) -> None:
        central = QWidget(self)

        # ---- сцена ----
        self.stage = SceneView(central)

        # ---- правая колонка ----
        self.transcript = TranscriptPanel(central)
        self.event_log = EventLogPanel(central)
        self.rules_panel = RulesPanel(self.app.rules, central)
        self.stats_panel = StatsPanel(central)

        self.tabs = QTabWidget(central)
        self.tabs.addTab(self.transcript, "Распознанный текст")
        self.tabs.addTab(self.event_log, "Журнал событий")
        self.tabs.addTab(self.rules_panel, f"Правила ({len(self.app.rules)})")
        self.tabs.addTab(self.stats_panel, "Статистика")

        splitter = QSplitter(Qt.Horizontal, central)
        splitter.addWidget(self.stage)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 500])

        # ---- низ: ход воспроизведения ----
        self.slider = QProgressBar(central)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setTextVisible(False)

        self.time_label = QLabel("00:00 / 00:00", central)
        self.time_label.setMinimumWidth(120)
        self.time_label.setAlignment(Qt.AlignCenter)

        self.speed_label = QLabel("", central)
        self.speed_label.setMinimumWidth(160)

        bottom = QHBoxLayout()
        bottom.addWidget(self.slider, 1)
        bottom.addWidget(self.time_label)
        bottom.addWidget(self.speed_label)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

        self.setCentralWidget(central)

        self.statusBar().showMessage(
            f"Модель распознавания: {Path(self.app.model_path).name}"
            if self.app.model_path else "Модель распознавания не загружена"
        )

    def _build_toolbar(self) -> None:
        bar = QToolBar("Управление", self)
        bar.setMovable(False)
        self.addToolBar(bar)

        self.action_open = QAction("Открыть файл…", self)
        self.action_open.setShortcut(QKeySequence.Open)
        self.action_open.triggered.connect(self.on_open_file)
        bar.addAction(self.action_open)

        self.action_mic = QAction("Микрофон", self)
        self.action_mic.setCheckable(True)
        self.action_mic.triggered.connect(self.on_toggle_microphone)
        bar.addAction(self.action_mic)

        bar.addSeparator()

        self.action_play = QAction("Старт", self)
        self.action_play.setShortcut(QKeySequence("Ctrl+Return"))
        self.action_play.triggered.connect(self.on_toggle_run)
        bar.addAction(self.action_play)

        self.action_stop = QAction("Стоп", self)
        self.action_stop.triggered.connect(self.on_stop)
        bar.addAction(self.action_stop)

        bar.addSeparator()

        label = QLabel("  Режим: ", bar)
        bar.addWidget(label)

        self.scene_select = QComboBox(bar)
        self.scene_select.addItems(["singer", "clock", "both", "black"])
        self.scene_select.setCurrentText("both")
        self.scene_select.currentTextChanged.connect(self.stage.set_scene)
        bar.addWidget(self.scene_select)

        bar.addSeparator()

        self.action_fullscreen = QAction("Во весь экран", self)
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.triggered.connect(self.on_fullscreen)
        bar.addAction(self.action_fullscreen)

    def _update_actions(self) -> None:
        """Разрешить/запретить кнопки по текущему состоянию."""
        running = self._thread is not None and self._thread.isRunning()

        self.action_play.setEnabled(running or self._file is not None)
        self.action_stop.setEnabled(running)
        self.action_open.setEnabled(not running)
        self.action_mic.setEnabled(not running)

    # ---------------- команды ----------------

    @Slot()
    def on_open_file(self) -> None:
        """Выбрать аудиофайл."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите аудиофайл", str(Path.home()), AUDIO_FILTER
        )
        if not path:
            return

        self._file = Path(path)
        self.slider.setValue(0)

        try:
            duration = FileSource(self._file, real_time=False).duration
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Не удалось открыть файл", str(exc))
            return

        self.time_label.setText(f"{format_time(0)} / {format_time(duration)}")
        self.statusBar().showMessage(f"Файл: {self._file.name}")
        self._update_actions()

    @Slot()
    def on_toggle_run(self) -> None:
        """Запустить или прервать обработку файла."""
        if self._thread is not None and self._thread.isRunning():
            self.on_stop()
            return

        if self._file is None:
            self.on_open_file()
            if self._file is None:
                return

        self._start("file", self._file)

    @Slot()
    def on_toggle_microphone(self, checked: bool) -> None:
        """Включить или выключить микрофон."""
        if checked:
            self._start("mic", None)
        else:
            self.on_stop()

    @Slot()
    def on_stop(self) -> None:
        """Попросить рабочий поток остановиться."""
        if self._worker is not None:
            self._worker.stop_requested = True
            self.statusBar().showMessage("Останавливаю…")

    @Slot()
    def on_fullscreen(self, checked: bool) -> None:
        """Развернуть окно на весь экран (удобно для демонстрации)."""
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    # ---------------- запуск потока ----------------

    def _start(self, mode: str, path: Path | None) -> None:
        """Поднять рабочий поток."""
        self.transcript.clear()
        self.event_log.clear()
        self.stage.set_caption("")
        self._clock_seen = None

        self._thread = QThread(self)
        self._worker = RecognitionWorker(self.app)
        self._worker.configure(mode, path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.textReady.connect(self._on_text)
        self._worker.progress.connect(self._on_progress)
        self._worker.stateChanged.connect(self.statusBar().showMessage)
        self._worker.runFinished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_failed)

        # события управления воспроизведением -> рабочий поток
        for action in ("media.pause", "media.resume", "media.stop"):
            self.app.bus.subscribe(action, self._worker.on_control)

        self._thread.start()

        self._update_actions()

    @Slot(str, bool, float)
    def _on_text(self, text: str, is_final: bool, audio_time: float) -> None:
        """Новый результат распознавания из рабочего потока."""
        self.transcript.add_text(text, is_final, audio_time)

    @Slot(float, float)
    def _on_progress(self, position: float, total: float) -> None:
        """Ход воспроизведения."""
        if total > 0:
            self.slider.setValue(int(position / total * 1000))
            self.time_label.setText(f"{format_time(position)} / {format_time(total)}")
        else:
            self.time_label.setText(f"{format_time(position)} / ∞")

    @Slot(dict)
    def _on_run_finished(self, data: dict) -> None:
        """Обработка закончилась."""
        self._teardown_thread()
        self.statusBar().showMessage(
            f"Готово. Событий: {data.get('events', 0)}, "
            f"обработано {data.get('seconds', 0)} с"
        )

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        """В рабочем потоке произошла ошибка."""
        self._teardown_thread()
        QMessageBox.critical(self, "Ошибка", message)

    def _teardown_thread(self) -> None:
        """Остановить и удалить рабочий поток."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread.deleteLater()
            self._thread = None
            self._worker = None

        self.action_mic.setChecked(False)
        self._update_actions()

    # ---------------- разбор событий ----------------

    def _pump(self) -> None:
        """Разобрать накопившиеся события и обновить виджеты.

        Вызывается по таймеру в главном потоке — поэтому обработчики
        могут менять состояние виджетов.
        """
        self.app.bus.process()
        self._sync_handlers()

    def _sync_handlers(self) -> None:
        """Перенести состояние обработчиков на экран."""
        clock = self.app.handlers.get("clock.set")
        if clock is not None:
            if getattr(self, "_clock_seen", None) != clock.running:
                self._clock_seen = clock.running
                self.stage.clock.set_running(clock.running)

        caption = self.app.handlers.get("caption.show")
        if caption is not None:
            caption.tick()
            if self.stage.caption != caption.text:
                self.stage.set_caption(caption.text)

        scene = self.app.handlers.get("scene.set")
        if scene is not None and self.stage.scene != scene.scene:
            self.stage.set_scene(scene.scene)

    def _on_event(self, event) -> None:
        """Любое событие попадает в журнал и подсветку текста."""
        self.event_log.add_event(event)
        self.transcript.mark_hit(event.matched)
        self.stage.flash(event.matched)

    def _refresh_stats(self) -> None:
        """Обновить вкладку со статистикой и показатель скорости."""
        engine = self.app.engine
        counters = getattr(self.app.handlers.get("stats.count"), "counters", {})
        self.stats_panel.update_stats(engine.stats if engine else None, counters or {})

        if engine is not None and engine.stats.seconds > 0.5:
            self.speed_label.setText(
                f"x{engine.stats.real_time_factor:.2f} от реального времени"
            )

    # ---------------- закрытие ----------------

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.on_stop()
        self._teardown_thread()
        super().closeEvent(event)


__all__ = ["MainWindow", "RecognitionWorker", "format_time"]