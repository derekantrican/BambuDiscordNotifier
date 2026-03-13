"""Bambu Lab printer state models.

Extracted and simplified from OctoEverywhere's bambumodels.py.
Tracks printer state via MQTT partial updates.
"""

import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from .errors import BAMBU_PRINT_ERROR_STRINGS


class BambuPrintErrors(Enum):
    """Known printer error types."""
    Unknown = 1
    FilamentRunOut = 2
    PrintFailureDetected = 3
    PrintFilePauseCommand = 4
    PausedByUser = 5
    PausedUnknownReason = 6


class BambuState:
    """Locally-cached printer state kept in sync via MQTT partial updates."""

    def __init__(self) -> None:
        self.stg_cur: Optional[int] = None
        self.gcode_state: Optional[str] = None
        self.layer_num: Optional[int] = None
        self.total_layer_num: Optional[int] = None
        self.subtask_name: Optional[str] = None
        self.mc_percent: Optional[int] = None
        self.nozzle_temper: Optional[float] = None
        self.nozzle_target_temper: Optional[float] = None
        self.bed_temper: Optional[float] = None
        self.bed_target_temper: Optional[float] = None
        self.mc_remaining_time: Optional[int] = None
        self.project_id: Optional[str] = None
        self.task_id: Optional[str] = None
        self.print_error: Optional[int] = None
        self.rtsp_url: Optional[str] = None
        self.chamber_light: Optional[bool] = None
        self.LastTimeRemainingWallClock: Optional[float] = None

    def OnUpdate(self, msg: Dict[str, Any]) -> None:
        """Apply a partial MQTT update to our cached state."""
        self.stg_cur = msg.get("stg_cur", self.stg_cur)
        self.gcode_state = msg.get("gcode_state", self.gcode_state)
        self.layer_num = msg.get("layer_num", self.layer_num)
        self.total_layer_num = msg.get("total_layer_num", self.total_layer_num)
        self.subtask_name = msg.get("subtask_name", self.subtask_name)
        self.project_id = msg.get("project_id", self.project_id)
        self.task_id = msg.get("task_id", self.task_id)
        self.mc_percent = msg.get("mc_percent", self.mc_percent)
        self.nozzle_temper = msg.get("nozzle_temper", self.nozzle_temper)
        self.nozzle_target_temper = msg.get("nozzle_target_temper", self.nozzle_target_temper)
        self.bed_temper = msg.get("bed_temper", self.bed_temper)
        self.bed_target_temper = msg.get("bed_target_temper", self.bed_target_temper)
        self.print_error = msg.get("print_error", self.print_error)

        ipCam = msg.get("ipcam", None)
        if ipCam is not None:
            self.rtsp_url = ipCam.get("rtsp_url", self.rtsp_url)

        lightsReport: Optional[List[Dict[str, Any]]] = msg.get("lights_report", None)
        if lightsReport is not None and isinstance(lightsReport, list):
            for light in lightsReport:
                if isinstance(light, dict) and light.get("node") == "chamber_light":
                    mode = light.get("mode")
                    if mode is not None:
                        self.chamber_light = mode.lower() != "off"
                    break

        old_mc_remaining_time = self.mc_remaining_time
        self.mc_remaining_time = msg.get("mc_remaining_time", self.mc_remaining_time)
        if old_mc_remaining_time != self.mc_remaining_time:
            self.LastTimeRemainingWallClock = time.time()

    def GetContinuousTimeRemainingSec(self) -> Optional[int]:
        """Returns time remaining counting down in seconds (not just minutes)."""
        if self.mc_remaining_time is None or self.LastTimeRemainingWallClock is None:
            return None
        if self.IsPrepareOrSlicing():
            self.LastTimeRemainingWallClock = time.time()
            return int(self.mc_remaining_time * 60)
        return int(max(0, (self.mc_remaining_time * 60) - (time.time() - self.LastTimeRemainingWallClock)))

    def IsPrinting(self, includePausedAsPrinting: bool) -> bool:
        return BambuState.IsPrintingState(self.gcode_state, includePausedAsPrinting)

    @staticmethod
    def IsPrintingState(state: Optional[str], includePausedAsPrinting: bool) -> bool:
        if state is None:
            return False
        if state == "PAUSE" and includePausedAsPrinting:
            return True
        return state == "RUNNING" or BambuState.IsPrepareOrSlicingState(state)

    def IsPrepareOrSlicing(self) -> bool:
        return BambuState.IsPrepareOrSlicingState(self.gcode_state)

    @staticmethod
    def IsPrepareOrSlicingState(state: Optional[str]) -> bool:
        if state is None:
            return False
        return state == "SLICING" or state == "PREPARE"

    def IsPaused(self) -> bool:
        if self.gcode_state is None:
            return False
        return self.gcode_state == "PAUSE"

    def GetFileNameWithNoExtension(self) -> Optional[str]:
        if self.subtask_name is None:
            return None
        pos = self.subtask_name.rfind(".")
        if pos == -1:
            return self.subtask_name
        return self.subtask_name[:pos]

    def GetPrintCookie(self) -> Optional[str]:
        """Returns a unique string for this print, or None if no active print."""
        if (self.project_id is None or len(self.project_id) == 0
                or self.task_id is None or len(self.task_id) == 0
                or self.subtask_name is None or len(self.subtask_name) == 0):
            return None
        return f"{self.project_id}-{self.task_id}-{self.GetFileNameWithNoExtension()}"

    def GetPrinterErrorType(self) -> Optional[BambuPrintErrors]:
        """Returns the error type if in an error state, otherwise None."""
        if self.print_error is None or self.print_error == 0:
            return None
        # Known non-error codes
        if self.print_error in (83918896, 50364434, 83935249, 134184967):
            return None
        h = hex(self.print_error)[2:].rjust(8, '0')
        errorMap = {
            "07008011": BambuPrintErrors.FilamentRunOut,
            "07018011": BambuPrintErrors.FilamentRunOut,
            "07028011": BambuPrintErrors.FilamentRunOut,
            "07038011": BambuPrintErrors.FilamentRunOut,
            "07FF8011": BambuPrintErrors.FilamentRunOut,
            "03008003": BambuPrintErrors.PrintFailureDetected,
            "03008013": BambuPrintErrors.PrintFilePauseCommand,
            "03008001": BambuPrintErrors.PausedByUser,
            "03008000": BambuPrintErrors.PausedUnknownReason,
        }
        return errorMap.get(h, BambuPrintErrors.Unknown)

    def GetDetailedPrinterErrorStr(self) -> Optional[str]:
        if self.print_error is None or self.print_error == 0:
            return None
        h = hex(self.print_error)[2:].rjust(8, '0')
        errorStr = BAMBU_PRINT_ERROR_STRINGS.get(h, "Error")
        return errorStr.replace("\\\"", "\"")


class BambuPrinters(Enum):
    Unknown = 1
    X1C = 2
    X1E = 3
    P1P = 10
    P1S = 11
    P2S = 12
    H2D = 30
    H2S = 31
    A1  = 20
    A1Mini = 21


class BambuVersion:
    """Tracks printer hardware/software version info from MQTT."""

    def __init__(self, logger: logging.Logger) -> None:
        self.Logger = logger
        self.HasLoggedPrinterVersion = False
        self.SoftwareVersion: Optional[str] = None
        self.HardwareVersion: Optional[str] = None
        self.SerialNumber: Optional[str] = None
        self.ProjectName: Optional[str] = None
        self.PrinterName: Optional[BambuPrinters] = None

    def OnUpdate(self, msg: Dict[str, Any]) -> None:
        module = msg.get("module", None)
        if module is None:
            return
        productNamesLower: List[str] = []
        for m in module:
            pName = m.get("product_name", None)
            if pName is not None:
                productNamesLower.append(pName.lower())
            name = m.get("name", None)
            if name is None:
                continue
            if name == "ota":
                self.SoftwareVersion = m.get("sw_ver", self.SoftwareVersion)
            elif name == "mc":
                self.SerialNumber = m.get("sn", self.SerialNumber)
            elif name == "esp32":
                self.HardwareVersion = m.get("hw_ver", self.HardwareVersion)
                self.ProjectName = m.get("project_name", self.ProjectName)
            elif name in ("rv1126", "ap"):
                self.HardwareVersion = m.get("hw_ver", self.HardwareVersion)
                self.ProjectName = m.get("project_name", self.ProjectName)

        # Detect model from product name
        modelFromProductName = self._DetectModelFromProductName(module)
        if modelFromProductName is not None:
            self.PrinterName = modelFromProductName
        elif self.PrinterName is None:
            # Fallback string matching
            for pNameLower in productNamesLower:
                if "x1 carbon" in pNameLower:
                    self.PrinterName = BambuPrinters.X1C
                elif "x1e" in pNameLower:
                    self.PrinterName = BambuPrinters.X1E
                elif "p1p" in pNameLower:
                    self.PrinterName = BambuPrinters.P1P
                elif "a1 mini" in pNameLower:
                    self.PrinterName = BambuPrinters.A1Mini
                elif "a1" in pNameLower:
                    self.PrinterName = BambuPrinters.A1
                elif "p1s" in pNameLower:
                    self.PrinterName = BambuPrinters.P1S
                elif "p2s" in pNameLower:
                    self.PrinterName = BambuPrinters.P2S
                elif "h2d" in pNameLower:
                    self.PrinterName = BambuPrinters.H2D
                elif "h2s" in pNameLower:
                    self.PrinterName = BambuPrinters.H2S

            if self.PrinterName is None:
                self.PrinterName = BambuPrinters.Unknown

        if not self.HasLoggedPrinterVersion:
            self.HasLoggedPrinterVersion = True
            self.Logger.info(
                "Printer Version: %s, Hardware: %s, Software: %s, Serial: %s",
                self.PrinterName, self.HardwareVersion, self.SoftwareVersion, self.SerialNumber
            )

    @staticmethod
    def _DetectModelFromProductName(module: List[Dict[str, Any]]) -> Optional[BambuPrinters]:
        name_map = {
            "Bambu Lab A1": BambuPrinters.A1,
            "Bambu Lab A1 mini": BambuPrinters.A1Mini,
            "Bambu Lab A1 Mini": BambuPrinters.A1Mini,
            "Bambu Lab P1S": BambuPrinters.P1S,
            "Bambu Lab P2S": BambuPrinters.P2S,
            "Bambu Lab P1P": BambuPrinters.P1P,
            "Bambu Lab H2D": BambuPrinters.H2D,
            "Bambu Lab H2S": BambuPrinters.H2S,
        }
        if not module:
            return None
        for m in module:
            model = name_map.get(m.get("product_name", ""))
            if model:
                return model
        return None
