"""Pi Camera snapshot capture module.

Supports picamera2 (Python library) and libcamera-still (subprocess fallback).
Gracefully handles camera not available.
"""

import io
import logging
import subprocess
from typing import List, Optional

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class PiCamCapture:
    """Captures JPEG snapshots from a Raspberry Pi Camera."""

    def __init__(
        self,
        logger: logging.Logger,
        method: str = "libcamera",
        resolution: Optional[List[int]] = None,
        rotation: int = 0,
        flip_horizontal: bool = False,
        flip_vertical: bool = False,
    ) -> None:
        self.Logger = logger
        self.Method = method
        self.Resolution = resolution or [1280, 720]
        self.Rotation = rotation  # 0, 90, 180, 270
        self.FlipHorizontal = flip_horizontal
        self.FlipVertical = flip_vertical
        self._picam2 = None
        self._available: Optional[bool] = None

        if method == "picamera2":
            self._init_picamera2()

    def _init_picamera2(self) -> None:
        try:
            from picamera2 import Picamera2  # type: ignore
            self._picam2 = Picamera2()
            config = self._picam2.create_still_configuration(
                main={"size": (self.Resolution[0], self.Resolution[1])}
            )
            self._picam2.configure(config)
            self._picam2.start()
            self._available = True
            self.Logger.info("picamera2 initialized at %dx%d", self.Resolution[0], self.Resolution[1])
        except Exception as e:
            self.Logger.warning("picamera2 not available, will try libcamera fallback: %s", e)
            self._picam2 = None
            self.Method = "libcamera"

    def get_snapshot(self) -> Optional[bytes]:
        """Capture a JPEG snapshot. Returns bytes or None on failure."""
        try:
            if self.Method == "picamera2" and self._picam2 is not None:
                return self._capture_picamera2()
            else:
                return self._capture_libcamera()
        except Exception as e:
            self.Logger.error("Snapshot capture failed: %s", e)
            return None

    def _capture_picamera2(self) -> Optional[bytes]:
        if self._picam2 is None:
            return None
        buf = io.BytesIO()
        self._picam2.capture_file(buf, format="jpeg")
        data = buf.getvalue()
        data = self._apply_transforms(data)
        return self._resize_if_needed(data)

    def _capture_libcamera(self) -> Optional[bytes]:
        """Capture using libcamera-still subprocess."""
        try:
            result = subprocess.run(
                [
                    "libcamera-still",
                    "-o", "-",          # output to stdout
                    "--width", str(self.Resolution[0]),
                    "--height", str(self.Resolution[1]),
                    "-t", "500",        # 500ms warmup
                    "-n",               # no preview
                    "--encoding", "jpg",
                    "-q", "80",         # JPEG quality
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                # Only log first failure
                if self._available is not False:
                    self.Logger.warning("libcamera-still failed: %s", stderr[:200])
                    self._available = False
                return None

            self._available = True
            data = self._apply_transforms(result.stdout)
            return self._resize_if_needed(data)

        except FileNotFoundError:
            if self._available is not False:
                self.Logger.warning("libcamera-still not found. Camera snapshots disabled.")
                self._available = False
            return None
        except subprocess.TimeoutExpired:
            self.Logger.warning("libcamera-still timed out.")
            return None

    def _apply_transforms(self, data: bytes) -> bytes:
        """Apply rotation and flip transforms if configured."""
        if not HAS_PIL:
            if self.Rotation or self.FlipHorizontal or self.FlipVertical:
                self.Logger.warning("Pillow not installed — cannot apply rotation/flip transforms.")
            return data
        if not self.Rotation and not self.FlipHorizontal and not self.FlipVertical:
            return data

        img = Image.open(io.BytesIO(data))
        if self.FlipHorizontal:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if self.FlipVertical:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if self.Rotation:
            # PIL rotates counter-clockwise, so negate for clockwise
            img = img.rotate(-self.Rotation, expand=True)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def _resize_if_needed(self, data: bytes, max_bytes: int = 2 * 1024 * 1024) -> bytes:
        """Ensure the image is under max_bytes. Resize if needed."""
        if len(data) <= max_bytes:
            return data
        if not HAS_PIL:
            return data

        img = Image.open(io.BytesIO(data))
        quality = 70
        while quality > 20:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_bytes:
                return buf.getvalue()
            quality -= 10
        # Last resort: downscale
        img = img.resize((img.width // 2, img.height // 2))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue()

    def close(self) -> None:
        if self._picam2 is not None:
            try:
                self._picam2.stop()
            except Exception:
                pass
