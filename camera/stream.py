"""MJPEG HTTP streaming server for the Pi Camera.

Serves a live MJPEG feed at http://<pi-ip>:<port>/stream and a single
snapshot at /snapshot. Uses the existing PiCamCapture for image capture,
so rotation/flip/resolution settings are automatically applied.

Designed to be lightweight enough for a Pi Zero.
"""

import io
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from .picam import PiCamCapture


class _StreamHandler(BaseHTTPRequestHandler):
    """HTTP handler serving MJPEG stream and single snapshots."""

    # Set by MjpegStreamServer before the server starts
    camera: Optional[PiCamCapture] = None
    frame_interval: float = 0.5
    logger: Optional[logging.Logger] = None

    def do_GET(self) -> None:
        if self.logger:
            self.logger.info("Stream server request: %s from %s", self.path, self.client_address[0])
        if self.path == "/stream":
            self._handle_stream()
        elif self.path == "/snapshot":
            self._handle_snapshot()
        elif self.path == "/":
            self._handle_index()
        else:
            self.send_error(404)

    def _handle_index(self) -> None:
        """Simple HTML page embedding the stream."""
        html = (
            '<!DOCTYPE html><html><head><title>Pi Camera</title></head>'
            '<body style="margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh">'
            '<img src="/stream" style="max-width:100%;max-height:100vh" />'
            '</body></html>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _handle_snapshot(self) -> None:
        """Serve a single JPEG frame."""
        if self.camera is None:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Camera not available")
            return
        frame = self.camera.get_snapshot()
        if frame is None:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Failed to capture snapshot")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(frame)

    def _handle_stream(self) -> None:
        """Serve an MJPEG stream (multipart/x-mixed-replace)."""
        if self.camera is None:
            self.send_error(503, "Camera not available")
            return

        BOUNDARY = b"--frameboundary"
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frameboundary")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

        try:
            while True:
                frame = self.camera.get_snapshot()
                if frame is None:
                    time.sleep(self.frame_interval)
                    continue

                self.wfile.write(BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n".encode())
                self.wfile.write(b"\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()

                time.sleep(self.frame_interval)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected

    def log_message(self, format: str, *args) -> None:
        """Suppress default stderr logging; use our logger instead."""
        if self.logger:
            self.logger.debug("Stream server: %s", format % args)


class MjpegStreamServer:
    """Runs an MJPEG HTTP server in a background thread."""

    def __init__(
        self,
        logger: logging.Logger,
        camera: PiCamCapture,
        host: str = "0.0.0.0",
        port: int = 8080,
        fps: float = 2.0,
    ) -> None:
        self.Logger = logger
        self.Camera = camera
        self.Host = host
        self.Port = port
        self.Fps = fps
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the MJPEG HTTP server in a daemon thread."""
        # Inject dependencies into the handler class
        _StreamHandler.camera = self.Camera
        _StreamHandler.frame_interval = 1.0 / max(self.Fps, 0.1)
        _StreamHandler.logger = self.Logger

        try:
            self._server = HTTPServer((self.Host, self.Port), _StreamHandler)
        except OSError as e:
            self.Logger.error("Failed to start MJPEG stream server on port %d: %s", self.Port, e)
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.Logger.info(
            "📷 MJPEG stream available at http://%s:%d/stream  (%.1f fps)",
            self.Host if self.Host != "0.0.0.0" else "<pi-ip>",
            self.Port,
            self.Fps,
        )

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self.Logger.info("MJPEG stream server stopped.")
