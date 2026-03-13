"""Event dataclasses for print notifications."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PrintEvent:
    """Base class for all print events."""
    printer_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PrintStarted(PrintEvent):
    filename: Optional[str] = None


@dataclass
class PrintProgress(PrintEvent):
    percentage: int = 0
    layer: Optional[int] = None
    total_layers: Optional[int] = None
    time_remaining_sec: Optional[int] = None
    filename: Optional[str] = None
    nozzle_temp: Optional[float] = None
    bed_temp: Optional[float] = None


@dataclass
class PrintDone(PrintEvent):
    filename: Optional[str] = None
    duration_sec: Optional[float] = None


@dataclass
class PrintFailed(PrintEvent):
    filename: Optional[str] = None
    duration_sec: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class PrintPaused(PrintEvent):
    filename: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class PrintResumed(PrintEvent):
    filename: Optional[str] = None


@dataclass
class PrintError(PrintEvent):
    error_string: str = ""


@dataclass
class FilamentChange(PrintEvent):
    pass
