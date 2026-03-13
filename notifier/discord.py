"""Discord webhook notifier — sends rich embed notifications."""

import io
import json
import time
import logging
import threading
from typing import Optional

import requests

from .events import (
    PrintStarted, PrintProgress, PrintDone, PrintFailed,
    PrintPaused, PrintResumed, PrintError, FilamentChange,
)


# Embed colors
COLOR_GREEN  = 0x2ECC71  # done
COLOR_RED    = 0xE74C3C  # failed / error
COLOR_BLUE   = 0x3498DB  # started
COLOR_YELLOW = 0xF1C40F  # paused
COLOR_ORANGE = 0xE67E22  # filament change
COLOR_TEAL   = 0x1ABC9C  # resumed
COLOR_GREY   = 0x95A5A6  # progress


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


class DiscordNotifier:
    """Sends print event notifications to a Discord webhook."""

    def __init__(
        self,
        logger: logging.Logger,
        webhook_url: str,
        mention_role_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> None:
        self.Logger = logger
        self.WebhookUrl = webhook_url
        self.MentionRoleId = mention_role_id
        self.MaxRetries = max_retries

    # ── public API ──────────────────────────────────────────

    def send_started(self, event: PrintStarted, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "🚀 Print Started", COLOR_BLUE)
        if event.filename:
            embed["fields"] = [{"name": "File", "value": event.filename, "inline": True}]
        self._send_async(embed, snapshot)

    def send_progress(self, event: PrintProgress, snapshot: Optional[bytes] = None) -> None:
        bar = self._progress_bar(event.percentage)
        embed = self._base_embed(event.printer_name, f"📊 Progress — {event.percentage}%", COLOR_GREY)
        fields = [{"name": "Progress", "value": bar, "inline": False}]
        if event.filename:
            fields.append({"name": "File", "value": event.filename, "inline": True})
        if event.layer is not None and event.total_layers is not None and event.total_layers > 0:
            fields.append({"name": "Layer", "value": f"{event.layer}/{event.total_layers}", "inline": True})
        if event.time_remaining_sec is not None:
            fields.append({"name": "ETA", "value": _format_duration(event.time_remaining_sec), "inline": True})
        if event.nozzle_temp is not None:
            fields.append({"name": "Nozzle", "value": f"{event.nozzle_temp:.0f}°C", "inline": True})
        if event.bed_temp is not None:
            fields.append({"name": "Bed", "value": f"{event.bed_temp:.0f}°C", "inline": True})
        embed["fields"] = fields
        self._send_async(embed, snapshot)

    def send_done(self, event: PrintDone, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "✅ Print Complete!", COLOR_GREEN)
        fields = []
        if event.filename:
            fields.append({"name": "File", "value": event.filename, "inline": True})
        if event.duration_sec is not None:
            fields.append({"name": "Duration", "value": _format_duration(event.duration_sec), "inline": True})
        if fields:
            embed["fields"] = fields
        self._send_async(embed, snapshot)

    def send_failed(self, event: PrintFailed, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "❌ Print Failed", COLOR_RED)
        fields = []
        if event.filename:
            fields.append({"name": "File", "value": event.filename, "inline": True})
        if event.reason:
            fields.append({"name": "Reason", "value": event.reason, "inline": True})
        if event.duration_sec is not None:
            fields.append({"name": "Duration", "value": _format_duration(event.duration_sec), "inline": True})
        if fields:
            embed["fields"] = fields
        mention = self._mention_text()
        self._send_async(embed, snapshot, content=mention)

    def send_paused(self, event: PrintPaused, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "⏸️ Print Paused", COLOR_YELLOW)
        fields = []
        if event.filename:
            fields.append({"name": "File", "value": event.filename, "inline": True})
        if event.reason:
            fields.append({"name": "Reason", "value": event.reason, "inline": True})
        if fields:
            embed["fields"] = fields
        self._send_async(embed, snapshot)

    def send_resumed(self, event: PrintResumed, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "▶️ Print Resumed", COLOR_TEAL)
        if event.filename:
            embed["fields"] = [{"name": "File", "value": event.filename, "inline": True}]
        self._send_async(embed, snapshot)

    def send_error(self, event: PrintError, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "🔴 Printer Error", COLOR_RED)
        embed["fields"] = [{"name": "Error", "value": event.error_string or "Unknown error", "inline": False}]
        mention = self._mention_text()
        self._send_async(embed, snapshot, content=mention)

    def send_filament_change(self, event: FilamentChange, snapshot: Optional[bytes] = None) -> None:
        embed = self._base_embed(event.printer_name, "🔄 Filament Change Required", COLOR_ORANGE)
        embed["description"] = "The printer needs filament attention. Please check the printer."
        mention = self._mention_text()
        self._send_async(embed, snapshot, content=mention)

    # ── internal ────────────────────────────────────────────

    @staticmethod
    def _base_embed(printer_name: str, title: str, color: int) -> dict:
        return {
            "title": title,
            "color": color,
            "footer": {"text": printer_name},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @staticmethod
    def _progress_bar(percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"`{bar}` {percent}%"

    def _mention_text(self) -> Optional[str]:
        if self.MentionRoleId:
            return f"<@&{self.MentionRoleId}>"
        return None

    def _send_async(self, embed: dict, snapshot: Optional[bytes] = None, content: Optional[str] = None) -> None:
        """Fire-and-forget send in a background thread."""
        threading.Thread(
            target=self._send_with_retry,
            args=(embed, snapshot, content),
            daemon=True,
        ).start()

    def _send_with_retry(self, embed: dict, snapshot: Optional[bytes] = None, content: Optional[str] = None) -> None:
        payload: dict = {"embeds": [embed]}
        if content:
            payload["content"] = content

        for attempt in range(self.MaxRetries):
            try:
                if snapshot:
                    # Reference the uploaded file in the embed image
                    embed["image"] = {"url": "attachment://snapshot.jpg"}
                    # Discord requires an attachments array mapping file index to metadata
                    payload["attachments"] = [{"id": 0, "filename": "snapshot.jpg"}]
                    resp = requests.post(
                        self.WebhookUrl,
                        data={"payload_json": json.dumps(payload)},
                        files={
                            # Discord expects the multipart field name to be "files[n]"
                            "files[0]": ("snapshot.jpg", io.BytesIO(snapshot), "image/jpeg"),
                        },
                        timeout=30,
                    )
                else:
                    resp = requests.post(
                        self.WebhookUrl,
                        json=payload,
                        timeout=30,
                    )

                if resp.status_code == 429:
                    # Rate limited — respect retry_after
                    retry_after = resp.json().get("retry_after", 5)
                    self.Logger.warning("Discord rate limited. Waiting %.1fs ...", retry_after)
                    time.sleep(retry_after)
                    continue

                if resp.status_code >= 400:
                    self.Logger.error("Discord webhook error %d: %s", resp.status_code, resp.text[:200])
                    if attempt < self.MaxRetries - 1:
                        time.sleep(2 ** attempt)
                    continue

                self.Logger.debug("Discord notification sent successfully.")
                return

            except Exception as e:
                self.Logger.error("Discord send failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MaxRetries - 1:
                    time.sleep(2 ** attempt)

        self.Logger.error("Failed to send Discord notification after %d attempts.", self.MaxRetries)
