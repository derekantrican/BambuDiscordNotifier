"""YAML configuration loader for BambuDiscordNotifier."""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class PrinterConfig:
    name: str = "Bambu Lab Printer"
    ip: str = ""
    access_code: str = ""
    serial_number: str = ""
    port: int = 8883


@dataclass
class DiscordEventsConfig:
    started: bool = True
    progress: bool = True
    progress_interval: int = 25  # Send every N%
    done: bool = True
    failed: bool = True
    paused: bool = True
    resumed: bool = True
    error: bool = True


@dataclass
class DiscordConfig:
    webhook_url: str = ""
    mention_role_id: Optional[str] = None
    events: DiscordEventsConfig = field(default_factory=DiscordEventsConfig)


@dataclass
class CameraConfig:
    enabled: bool = True
    method: str = "libcamera"  # "picamera2" or "libcamera"
    resolution: List[int] = field(default_factory=lambda: [1280, 720])
    include_on_events: List[str] = field(default_factory=lambda: ["done", "failed", "progress"])


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = None


@dataclass
class AppConfig:
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(path: str) -> AppConfig:
    """Load configuration from a YAML file."""
    if not os.path.exists(path):
        print(f"Error: Config file not found: {path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and fill in your settings.", file=sys.stderr)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = AppConfig()

    # Printer settings
    p = raw.get("printer", {})
    config.printer = PrinterConfig(
        name=p.get("name", config.printer.name),
        ip=p.get("ip", config.printer.ip),
        access_code=p.get("access_code", config.printer.access_code),
        serial_number=p.get("serial_number", config.printer.serial_number),
        port=int(p.get("port", config.printer.port)),
    )

    # Discord settings
    d = raw.get("discord", {})
    events_raw = d.get("events", {})
    events = DiscordEventsConfig(
        started=events_raw.get("started", True),
        progress=events_raw.get("progress", True),
        progress_interval=int(events_raw.get("progress_interval", 25)),
        done=events_raw.get("done", True),
        failed=events_raw.get("failed", True),
        paused=events_raw.get("paused", True),
        resumed=events_raw.get("resumed", True),
        error=events_raw.get("error", True),
    )
    config.discord = DiscordConfig(
        webhook_url=d.get("webhook_url", config.discord.webhook_url),
        mention_role_id=d.get("mention_role_id", None),
        events=events,
    )

    # Camera settings
    c = raw.get("camera", {})
    config.camera = CameraConfig(
        enabled=c.get("enabled", config.camera.enabled),
        method=c.get("method", config.camera.method),
        resolution=c.get("resolution", config.camera.resolution),
        include_on_events=c.get("include_on_events", config.camera.include_on_events),
    )

    # Logging settings
    lg = raw.get("logging", {})
    config.logging = LoggingConfig(
        level=lg.get("level", config.logging.level),
        file=lg.get("file", None),
    )

    # Validation
    if not config.printer.ip:
        print("Error: printer.ip is required in config.yaml", file=sys.stderr)
        sys.exit(1)
    if not config.printer.access_code:
        print("Error: printer.access_code is required in config.yaml", file=sys.stderr)
        sys.exit(1)
    if not config.printer.serial_number:
        print("Error: printer.serial_number is required in config.yaml", file=sys.stderr)
        sys.exit(1)
    if not config.discord.webhook_url:
        print("Error: discord.webhook_url is required in config.yaml", file=sys.stderr)
        sys.exit(1)

    return config
