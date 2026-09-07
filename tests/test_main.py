from __future__ import annotations

import json
import stat
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pixoo_bridge.__main__ import build_openusage_poller
from pixoo_bridge.bridge import BridgeService
from pixoo_bridge.runtime_config import BridgeRuntimeConfig

SAMPLE_OUTPUT = json.dumps(
    {
        "providers": {
            "claude": {
                "resources": {
                    "session": {"limit": 100, "used": 8},
                    "weekly": {"limit": 100, "used": 10.5},
                },
                "stale": False,
            }
        }
    }
)


class SilentTransport:
    def present(self, scene, rendered_scene) -> bool:
        return True


class BuildOpenUsagePollerTests(unittest.TestCase):
    def make_service(self) -> BridgeService:
        return BridgeService(transport=SilentTransport())

    def write_openusage(self, directory: Path) -> Path:
        script = directory / "openusage"
        script.write_text(
            f"#!/bin/sh\ncat <<'JSON'\n{SAMPLE_OUTPUT}\nJSON\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_returns_no_poller_when_openusage_is_disabled(self) -> None:
        config = BridgeRuntimeConfig(openusage_enabled=False)

        self.assertIsNone(build_openusage_poller(config, self.make_service()))

    def test_returns_no_poller_when_the_binary_is_missing(self) -> None:
        with TemporaryDirectory() as tempdir:
            config = BridgeRuntimeConfig(
                openusage_binary=str(Path(tempdir) / "openusage")
            )

            with self.assertLogs("pixoo_bridge.__main__", level="WARNING") as captured:
                poller = build_openusage_poller(config, self.make_service())

        self.assertIsNone(poller)
        self.assertIn("openusage", "\n".join(captured.output))

    def test_poller_feeds_readings_into_the_bridge(self) -> None:
        service = self.make_service()
        with TemporaryDirectory() as tempdir:
            script = self.write_openusage(Path(tempdir))
            config = BridgeRuntimeConfig(
                openusage_binary=str(script),
                openusage_poll_seconds=0.01,
            )

            poller = build_openusage_poller(config, service)
            self.assertIsNotNone(poller)
            poller.start()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if service.snapshot()["usage"]["session_pct"] is not None:
                        break
                    time.sleep(0.01)
            finally:
                poller.stop()

        self.assertEqual(
            service.snapshot()["usage"],
            {"session_pct": 8.0, "weekly_pct": 10.5, "stale": False},
        )


if __name__ == "__main__":
    unittest.main()
