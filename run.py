#!/usr/bin/env python3
"""BambuDiscordNotifier — entry point.

Connects to a Bambu Lab printer via MQTT and sends print events
to a Discord webhook.
"""

import os
import sys
import signal
import argparse
import logging
from typing import Any, Dict, Optional

from config import load_config, AppConfig
from logger import setup_logging
from bambu.client import BambuClient
from bambu.models import BambuState
from bambu.state_translator import StateTranslator
from notifier.discord import DiscordNotifier
from notifier.events import (
    PrintStarted, PrintProgress, PrintDone, PrintFailed,
    PrintPaused, PrintResumed, PrintError, FilamentChange,
)
from camera.picam import PiCamCapture
from camera.stream import MjpegStreamServer


class BambuDiscordApp:
    """Main application tying together MQTT client, state translator, camera, and Discord."""

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.Config = config
        self.Logger = logger
        self._shutdown = False

        # Camera (may be None if disabled)
        self.Camera: Optional[PiCamCapture] = None
        self.StreamServer: Optional[MjpegStreamServer] = None
        if config.camera.enabled:
            self.Camera = PiCamCapture(
                logger=logger,
                method=config.camera.method,
                resolution=config.camera.resolution,
                rotation=config.camera.rotation,
                flip_horizontal=config.camera.flip_horizontal,
                flip_vertical=config.camera.flip_vertical,
            )
            if config.camera.stream_enabled:
                self.StreamServer = MjpegStreamServer(
                    logger=logger,
                    camera=self.Camera,
                    port=config.camera.stream_port,
                    fps=config.camera.stream_fps,
                )

        # Discord notifier
        self.Discord = DiscordNotifier(
            logger=logger,
            webhook_url=config.discord.webhook_url,
            mention_role_id=config.discord.mention_role_id,
        )

        # Track last-reported progress to throttle
        self._last_progress_sent: Optional[int] = None

        # State translator with callbacks
        self.Translator = StateTranslator(
            logger=logger,
            on_started=self._on_started,
            on_done=self._on_done,
            on_failed=self._on_failed,
            on_paused=self._on_paused,
            on_resumed=self._on_resumed,
            on_progress=self._on_progress,
            on_error=self._on_error,
            on_filament_change=self._on_filament_change,
        )
        self.Translator.set_printer_name(config.printer.name)

        # MQTT client
        self.Client = BambuClient(
            logger=logger,
            ip=config.printer.ip,
            access_code=config.printer.access_code,
            serial_number=config.printer.serial_number,
            port=config.printer.port,
            on_state_update=self._on_state_update,
            on_connected=lambda: logger.info("✅ Connected to %s", config.printer.name),
            on_disconnected=lambda: self.Translator.reset_for_new_connection(),
        )

    def run(self) -> None:
        """Start the client and block until shutdown signal."""
        self.Logger.info("Starting BambuDiscordNotifier for '%s' at %s:%d",
                         self.Config.printer.name, self.Config.printer.ip, self.Config.printer.port)

        self.Client.start()

        # Start MJPEG stream server if enabled
        if self.StreamServer:
            self.StreamServer.start()

        # Block until SIGINT/SIGTERM
        stop_event = self.Client._stop_event
        try:
            while not stop_event.is_set():
                stop_event.wait(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.Logger.info("Shutting down ...")
        if self.StreamServer:
            self.StreamServer.stop()
        self.Client.stop()
        if self.Camera:
            self.Camera.close()
        self.Logger.info("Goodbye.")

    # ── MQTT callback ─────────────────────────────────────

    def _on_state_update(self, client: BambuClient, msg: Dict[str, Any], state: BambuState, is_first_full_sync: bool) -> None:
        self.Translator.on_mqtt_message(client, msg, state, is_first_full_sync)

    # ── event callbacks ───────────────────────────────────

    def _get_snapshot(self, event_name: str) -> Optional[bytes]:
        if self.Camera is None:
            return None
        if event_name not in self.Config.camera.include_on_events:
            return None
        return self.Camera.get_snapshot()

    def _on_started(self, printer_name: str, filename: Optional[str]) -> None:
        if not self.Config.discord.events.started:
            return
        self._last_progress_sent = None
        event = PrintStarted(printer_name=printer_name, filename=filename)
        snapshot = self._get_snapshot("started")
        self.Discord.send_started(event, snapshot)

    def _on_done(self, printer_name: str, filename: Optional[str], duration_sec: Optional[float]) -> None:
        if not self.Config.discord.events.done:
            return
        event = PrintDone(printer_name=printer_name, filename=filename, duration_sec=duration_sec)
        snapshot = self._get_snapshot("done")
        self.Discord.send_done(event, snapshot)

    def _on_failed(self, printer_name: str, filename: Optional[str], duration_sec: Optional[float], reason: str) -> None:
        if not self.Config.discord.events.failed:
            return
        event = PrintFailed(printer_name=printer_name, filename=filename, duration_sec=duration_sec, reason=reason)
        snapshot = self._get_snapshot("failed")
        self.Discord.send_failed(event, snapshot)

    def _on_paused(self, printer_name: str, filename: Optional[str], reason: Optional[str]) -> None:
        if not self.Config.discord.events.paused:
            return
        event = PrintPaused(printer_name=printer_name, filename=filename, reason=reason)
        snapshot = self._get_snapshot("paused")
        self.Discord.send_paused(event, snapshot)

    def _on_resumed(self, printer_name: str, filename: Optional[str]) -> None:
        if not self.Config.discord.events.resumed:
            return
        event = PrintResumed(printer_name=printer_name, filename=filename)
        self.Discord.send_resumed(event)

    def _on_progress(self, printer_name: str, percentage: int, state: BambuState) -> None:
        if not self.Config.discord.events.progress:
            return
        interval = self.Config.discord.events.progress_interval
        # Only send at configured interval boundaries
        threshold = (percentage // interval) * interval
        if threshold == 0 or threshold == 100:
            return
        if self._last_progress_sent is not None and threshold <= self._last_progress_sent:
            return
        self._last_progress_sent = threshold

        event = PrintProgress(
            printer_name=printer_name,
            percentage=percentage,
            layer=state.layer_num,
            total_layers=state.total_layer_num,
            time_remaining_sec=state.GetContinuousTimeRemainingSec(),
            filename=state.GetFileNameWithNoExtension(),
            nozzle_temp=state.nozzle_temper,
            bed_temp=state.bed_temper,
        )
        snapshot = self._get_snapshot("progress")
        self.Discord.send_progress(event, snapshot)

    def _on_error(self, printer_name: str, error_string: str) -> None:
        if not self.Config.discord.events.error:
            return
        event = PrintError(printer_name=printer_name, error_string=error_string)
        snapshot = self._get_snapshot("error")
        self.Discord.send_error(event, snapshot)

    def _on_filament_change(self, printer_name: str) -> None:
        event = FilamentChange(printer_name=printer_name)
        snapshot = self._get_snapshot("filament_change")
        self.Discord.send_filament_change(event, snapshot)


def main() -> None:
    parser = argparse.ArgumentParser(description="BambuDiscordNotifier")
    parser.add_argument(
        "-c", "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
        help="Path to config.yaml (default: ./config.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(level=config.logging.level, log_file=config.logging.file)

    app = BambuDiscordApp(config, logger)

    # Handle signals for graceful shutdown
    def handle_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %d", signum)
        app.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    app.run()


if __name__ == "__main__":
    main()
