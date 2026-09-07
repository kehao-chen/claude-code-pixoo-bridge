"""Read Claude quota usage from the local `openusage` binary.

This is the only place in the bridge that shells out to an external tool. It
turns `openusage`'s JSON report into a small reading, and offers a background
poller that hands fresh readings to a callback on a fixed interval.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

PROVIDER_ID = "claude"

DEFAULT_SEARCH_PATHS: tuple[str, ...] = (
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "~/.local/bin",
    "~/.local/share/mise/shims",
    "/Applications/OpenUsage.app/Contents/Helpers",
)

DEFAULT_READ_TIMEOUT_SECONDS = 8.0
DEFAULT_POLL_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class OpenUsageReading:
    session_pct: float | None = None
    weekly_pct: float | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_pct": self.session_pct,
            "weekly_pct": self.weekly_pct,
            "stale": self.stale,
        }


def find_openusage_binary(
    explicit: str | None = None,
    *,
    search_paths: Sequence[str] = DEFAULT_SEARCH_PATHS,
    env_path: str | None = None,
) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        return str(candidate) if os.access(candidate, os.X_OK) else None

    entries = [str(Path(path).expanduser()) for path in search_paths]
    if env_path is None:
        env_path = os.environ.get("PATH", "")
    if env_path:
        entries.append(env_path)
    if not entries:
        return None
    return shutil.which("openusage", path=os.pathsep.join(entries))


def read_openusage(
    binary: str,
    *,
    timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> OpenUsageReading | None:
    try:
        completed = subprocess.run(
            [binary],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("openusage invocation failed: %s", exc)
        return None

    if completed.returncode != 0:
        logger.debug(
            "openusage exited with %s: %s",
            completed.returncode,
            completed.stderr.strip(),
        )
        return None

    return parse_openusage_output(completed.stdout)


def parse_openusage_output(raw: str) -> OpenUsageReading | None:
    payload = _decode_json_object(raw)
    if payload is None:
        return None

    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None
    provider = providers.get(PROVIDER_ID)
    if not isinstance(provider, dict):
        return None

    resources = provider.get("resources")
    if not isinstance(resources, dict):
        resources = {}

    return OpenUsageReading(
        session_pct=_resource_percentage(resources.get("session")),
        weekly_pct=_resource_percentage(resources.get("weekly")),
        stale=bool(provider.get("stale", False)),
    )


def _decode_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _resource_percentage(resource: Any) -> float | None:
    if not isinstance(resource, dict):
        return None
    used = _number(resource.get("used"))
    if used is None:
        return None
    limit = _number(resource.get("limit"))
    if limit is None or limit == 100:
        return used
    if limit <= 0:
        return None
    return used / limit * 100


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class OpenUsagePoller:
    """Calls `reader` on an interval and forwards each reading it produces."""

    def __init__(
        self,
        *,
        reader: Callable[[], OpenUsageReading | None],
        on_reading: Callable[[OpenUsageReading], Any],
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._reader = reader
        self._on_reading = on_reading
        self._interval_seconds = interval_seconds
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="openusage-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_requested.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_requested.is_set():
            self._poll_once()
            self._stop_requested.wait(self._interval_seconds)

    def _poll_once(self) -> None:
        try:
            reading = self._reader()
        except Exception:
            logger.warning("openusage poll failed", exc_info=True)
            return
        if reading is None:
            return
        try:
            self._on_reading(reading)
        except Exception:
            logger.warning("openusage reading could not be applied", exc_info=True)
