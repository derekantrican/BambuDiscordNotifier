"""Bambu Lab MQTT client — local LAN connection only.

Extracted and simplified from OctoEverywhere's bambuclient.py.
Connects to the printer over MQTT/TLS on port 8883, subscribes to
state updates, and dispatches them to the StateTranslator.
"""

import ssl
import time
import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

import paho.mqtt.client as mqtt

from .models import BambuState, BambuVersion


class BambuClient:
    """Manages the MQTT connection to a Bambu Lab printer on the local network."""

    def __init__(
        self,
        logger: logging.Logger,
        ip: str,
        access_code: str,
        serial_number: str,
        port: int = 8883,
        on_state_update: Optional[Callable[["BambuClient", Dict[str, Any], BambuState, bool], None]] = None,
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[], None]] = None,
    ) -> None:
        self.Logger = logger
        self.IpOrHostname = ip
        self.AccessCode = access_code
        self.PrinterSn = serial_number
        self.Port = port

        # Callbacks
        self._on_state_update = on_state_update
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        # Printer state
        self.State: Optional[BambuState] = None
        self.Version: Optional[BambuVersion] = None
        self.HasDoneFirstFullStateSync = False
        self.ReportSubscribeMid: Optional[int] = None
        self.IsPendingSubscribe = False

        self.SleepEvent = threading.Event()
        self._stop_event = threading.Event()
        self.Client: Optional[mqtt.Client] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the MQTT connection thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._client_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the MQTT connection thread to stop."""
        self._stop_event.set()
        self.SleepEvent.set()
        c = self.Client
        if c is not None:
            try:
                c.disconnect()
            except Exception:
                pass

    def get_state(self) -> Optional[BambuState]:
        if self.State is None:
            self.SleepEvent.set()
        return self.State

    def send_pause(self) -> bool:
        return self._publish({"print": {"sequence_id": "0", "command": "pause"}})

    def send_resume(self) -> bool:
        return self._publish({"print": {"sequence_id": "0", "command": "resume"}})

    def send_cancel(self) -> bool:
        return self._publish({"print": {"sequence_id": "0", "command": "stop"}})

    def send_set_chamber_light(self, on: bool) -> bool:
        mode = "on" if on else "off"
        return self._publish({
            "system": {
                "sequence_id": "0", "command": "ledctrl",
                "led_node": "chamber_light", "led_mode": mode,
                "led_on_time": 500, "led_off_time": 500,
                "loop_times": 0, "interval_time": 0,
            }
        })

    # ── internal ──────────────────────────────────────────────

    def _cleanup_state(self) -> None:
        self.State = None
        self.Version = None
        self.HasDoneFirstFullStateSync = False
        self.ReportSubscribeMid = None
        self.IsPendingSubscribe = False
        try:
            c = self.Client
            if c is not None:
                c.disconnect()
        except Exception:
            pass

    def _client_worker(self) -> None:
        backoff = 0
        while not self._stop_event.is_set():
            try:
                self._cleanup_state()

                self.Client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                self.Client.reconnect_delay_set(min_delay=1, max_delay=5)

                self.Client.on_connect = self._on_connect
                self.Client.on_message = self._on_message
                self.Client.on_disconnect = self._on_disconnect
                self.Client.on_subscribe = self._on_subscribe

                # Local TLS — printer uses self-signed certs
                self.Client.tls_set(tls_version=ssl.PROTOCOL_TLS, cert_reqs=ssl.CERT_NONE)
                self.Client.tls_insecure_set(True)
                self.Client.username_pw_set("bblp", self.AccessCode)

                self.Logger.info("Connecting to printer at %s:%s ...", self.IpOrHostname, self.Port)
                backoff += 1
                self.Client.connect(self.IpOrHostname, self.Port, keepalive=5)

                backoff = 0
                self.Client.loop_forever()

            except Exception as e:
                self.Logger.warning("Connection failed (%s:%s): %s", self.IpOrHostname, self.Port, e)

            if self._on_disconnected:
                try:
                    self._on_disconnected()
                except Exception:
                    pass

            if self._stop_event.is_set():
                break

            backoff = min(backoff, 60)
            delay = 5.0 * backoff
            self.Logger.info("Retrying in %.0fs ...", delay)
            self.SleepEvent.wait(delay)
            self.SleepEvent.clear()

    def _force_state_sync_async(self) -> None:
        def worker():
            try:
                self.Logger.info("Requesting full state sync ...")
                if not self._publish({"info": {"sequence_id": "0", "command": "get_version"}}):
                    raise Exception("Failed to publish get_version")
                if not self._publish({"pushing": {"sequence_id": "0", "command": "pushall"}}):
                    raise Exception("Failed to publish pushall")
            except Exception as e:
                self.Logger.error("Full state sync failed: %s", e)
                c = self.Client
                if c is not None:
                    c.disconnect()

        threading.Thread(target=worker, daemon=True).start()

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        self.Logger.info("Connected! Subscribing to printer reports ...")
        c = self.Client
        if c is None:
            return
        self.IsPendingSubscribe = True
        (result, self.ReportSubscribeMid) = c.subscribe(f"device/{self.PrinterSn}/report")
        if result != mqtt.MQTT_ERR_SUCCESS or self.ReportSubscribeMid is None:
            self.Logger.warning("Subscribe failed (result=%s). Disconnecting.", result)
            c.disconnect()

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        if self.IsPendingSubscribe:
            self.Logger.error(
                "Disconnected while subscribing — check your ACCESS CODE ('%s') and SERIAL NUMBER ('%s').",
                self.AccessCode, self.PrinterSn,
            )
        else:
            self.Logger.warning("Printer connection lost. Will reconnect ...")
        self._cleanup_state()

    def _on_subscribe(self, client: Any, userdata: Any, mid: Any, reason_code_list: List[mqtt.ReasonCode], properties: Any) -> None:
        if self.ReportSubscribeMid is not None and self.ReportSubscribeMid == mid:
            for r in reason_code_list:
                if r.is_failure:
                    self.Logger.error("Subscription failure: %s", r)
                    c = self.Client
                    if c is not None:
                        c.disconnect()
                    return

            self.Logger.info("Subscribed to printer reports successfully.")
            if self._on_connected:
                try:
                    self._on_connected()
                except Exception:
                    pass
            self._force_state_sync_async()

    def _on_message(self, client: Any, userdata: Any, mqttMsg: mqtt.MQTTMessage) -> None:
        try:
            msg = json.loads(mqttMsg.payload)
            if msg is None:
                return

            is_first_full_sync = False

            if "print" in msg:
                printMsg = msg["print"]
                if self.State is None:
                    s = BambuState()
                    s.OnUpdate(printMsg)
                    self.State = s
                else:
                    self.State.OnUpdate(printMsg)

                if not self.HasDoneFirstFullStateSync:
                    cmd = printMsg.get("command", None)
                    if cmd is not None and cmd == "push_status":
                        if len(printMsg) > 40:
                            is_first_full_sync = True
                            self.HasDoneFirstFullStateSync = True

            if "info" in msg:
                if self.Version is None:
                    v = BambuVersion(self.Logger)
                    v.OnUpdate(msg["info"])
                    self.Version = v
                else:
                    self.Version.OnUpdate(msg["info"])

            if self.State is not None and self._on_state_update:
                self._on_state_update(self, msg, self.State, is_first_full_sync)

        except Exception as e:
            self.Logger.error("Error handling MQTT message: %s", e)

    def _publish(self, msg: Dict[str, Any]) -> bool:
        try:
            if self.Client is None or not self.Client.is_connected():
                self.Logger.debug("Cannot publish — not connected.")
                self.SleepEvent.set()
                return False
            state = self.Client.publish(f"device/{self.PrinterSn}/request", json.dumps(msg))
            state.wait_for_publish(20)
            return True
        except Exception as e:
            self.Logger.error("Publish failed: %s", e)
        return False
