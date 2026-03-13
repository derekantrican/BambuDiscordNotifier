"""State translator — maps MQTT state transitions to notification events.

Extracted and simplified from OctoEverywhere's bambustatetranslater.py.
Instead of implementing complex interfaces, fires simple event callbacks.
"""

import time
import logging
from typing import Any, Callable, Dict, Optional

from .models import BambuState, BambuPrintErrors
from .client import BambuClient


class StateTranslator:
    """Watches Bambu MQTT messages and emits event callbacks on state transitions."""

    def __init__(
        self,
        logger: logging.Logger,
        on_started: Optional[Callable[[str, Optional[str]], None]] = None,
        on_done: Optional[Callable[[str, Optional[str], Optional[float]], None]] = None,
        on_failed: Optional[Callable[[str, Optional[str], Optional[float], str], None]] = None,
        on_paused: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_resumed: Optional[Callable[[str, Optional[str]], None]] = None,
        on_progress: Optional[Callable[[str, int, BambuState], None]] = None,
        on_error: Optional[Callable[[str, str], None]] = None,
        on_filament_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        All callbacks receive printer_name as the first arg.
        on_started(printer_name, filename)
        on_done(printer_name, filename, duration_sec)
        on_failed(printer_name, filename, duration_sec, reason)
        on_paused(printer_name, filename, reason)
        on_resumed(printer_name, filename)
        on_progress(printer_name, percentage, bambu_state)
        on_error(printer_name, error_string)
        on_filament_change(printer_name)
        """
        self.Logger = logger
        self.LastState: Optional[str] = None
        self._is_tracking_print = False
        self._print_start_time: Optional[float] = None
        self._last_progress_reported: Optional[int] = None

        self._on_started = on_started
        self._on_done = on_done
        self._on_failed = on_failed
        self._on_paused = on_paused
        self._on_resumed = on_resumed
        self._on_progress = on_progress
        self._on_error = on_error
        self._on_filament_change = on_filament_change

        self._printer_name = "Printer"

    def set_printer_name(self, name: str) -> None:
        self._printer_name = name

    def reset_for_new_connection(self) -> None:
        self.LastState = None

    def on_mqtt_message(self, client: BambuClient, msg: Dict[str, Any], state: BambuState, is_first_full_sync: bool) -> None:
        """Called by BambuClient for every MQTT message after state is updated."""

        # On first sync, recover tracking state
        if is_first_full_sync:
            if state.IsPrinting(False) or state.IsPaused():
                if not self._is_tracking_print:
                    self._is_tracking_print = True
                    self._print_start_time = time.time()
                    self._last_progress_reported = None
                    self.Logger.info("Recovered in-progress print: %s", state.GetFileNameWithNoExtension())

        # Detect state transitions
        if self.LastState != state.gcode_state:
            self.Logger.debug("State change: %s -> %s", self.LastState, state.gcode_state)

            if self.LastState is not None:
                if state.IsPrinting(False):
                    if self.LastState == "PAUSE":
                        self._fire_resumed(state)
                    elif not BambuState.IsPrintingState(self.LastState, False):
                        self._fire_started(state)
                elif state.IsPaused():
                    self._fire_pause_or_error(state)
                elif state.gcode_state == "FAILED":
                    self._fire_failed(state)
                elif state.gcode_state == "FINISH":
                    self._fire_done(state)

            self.LastState = state.gcode_state

        # Progress updates — only while actively tracking a print
        if not is_first_full_sync and self._is_tracking_print:
            printMsg = msg.get("print", None)
            if printMsg is not None and "mc_percent" in printMsg:
                if not state.IsPrepareOrSlicing():
                    self._fire_progress(state)

        # Finalize print duration when no longer printing
        if not state.IsPrinting(True) and self._is_tracking_print:
            if state.gcode_state in ("IDLE", "FINISH", "FAILED"):
                self._is_tracking_print = False

    def _get_duration(self) -> Optional[float]:
        if self._print_start_time is None:
            return None
        return time.time() - self._print_start_time

    def _fire_started(self, state: BambuState) -> None:
        self._is_tracking_print = True
        self._print_start_time = time.time()
        self._last_progress_reported = None
        filename = state.GetFileNameWithNoExtension()
        self.Logger.info("Print started: %s", filename)
        if self._on_started:
            self._on_started(self._printer_name, filename)

    def _fire_done(self, state: BambuState) -> None:
        filename = state.GetFileNameWithNoExtension()
        duration = self._get_duration()
        self.Logger.info("Print done: %s (%.0fs)", filename, duration or 0)
        self._is_tracking_print = False
        if self._on_done:
            self._on_done(self._printer_name, filename, duration)

    def _fire_failed(self, state: BambuState) -> None:
        filename = state.GetFileNameWithNoExtension()
        duration = self._get_duration()
        self.Logger.info("Print failed/cancelled: %s", filename)
        self._is_tracking_print = False
        if self._on_failed:
            self._on_failed(self._printer_name, filename, duration, "cancelled")

    def _fire_pause_or_error(self, state: BambuState) -> None:
        err = state.GetPrinterErrorType()
        filename = state.GetFileNameWithNoExtension()

        if err is None or err in (BambuPrintErrors.PausedByUser, BambuPrintErrors.PausedUnknownReason, BambuPrintErrors.PrintFilePauseCommand):
            self.Logger.info("Print paused: %s", filename)
            if self._on_paused:
                self._on_paused(self._printer_name, filename, None)
            return

        if err == BambuPrintErrors.FilamentRunOut:
            self.Logger.info("Filament change required")
            if self._on_filament_change:
                self._on_filament_change(self._printer_name)
            return

        errorStr = state.GetDetailedPrinterErrorStr() or "General Error"
        self.Logger.info("Printer error: %s", errorStr)
        if self._on_error:
            self._on_error(self._printer_name, errorStr)

    def _fire_resumed(self, state: BambuState) -> None:
        filename = state.GetFileNameWithNoExtension()
        self.Logger.info("Print resumed: %s", filename)
        if self._on_resumed:
            self._on_resumed(self._printer_name, filename)

    def _fire_progress(self, state: BambuState) -> None:
        percent = state.mc_percent
        if percent is None:
            return
        if self._on_progress:
            self._on_progress(self._printer_name, int(percent), state)
