from __future__ import annotations

import json
import os
import stat
import threading
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from pixoo_bridge.openusage import (
    OpenUsagePoller,
    OpenUsageReading,
    find_openusage_binary,
    parse_openusage_output,
    read_openusage,
)

SAMPLE_PAYLOAD = {
    "generatedAt": "2026-09-07T00:22:57.346Z",
    "providers": {
        "claude": {
            "displayName": "Claude",
            "plan": "Max 5x",
            "resources": {
                "session": {
                    "kind": "consumption",
                    "limit": 100,
                    "remaining": 92,
                    "unit": "percent",
                    "used": 8,
                    "windowSeconds": 18000,
                },
                "weekly": {
                    "kind": "consumption",
                    "limit": 100,
                    "remaining": 90,
                    "unit": "percent",
                    "used": 10.5,
                    "windowSeconds": 604800,
                },
            },
            "stale": False,
        },
        "codex": {"displayName": "Codex", "resources": {}, "stale": False},
    },
}


def sample_output(**provider_overrides: object) -> str:
    payload = json.loads(json.dumps(SAMPLE_PAYLOAD))
    payload["providers"]["claude"].update(provider_overrides)
    return json.dumps(payload)


def write_executable(directory: Path, name: str, body: str) -> Path:
    script = directory / name
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class ParseOpenUsageOutputTests(unittest.TestCase):
    def test_reads_claude_session_and_weekly_percentages(self) -> None:
        reading = parse_openusage_output(sample_output())

        self.assertEqual(
            reading,
            OpenUsageReading(session_pct=8.0, weekly_pct=10.5, stale=False),
        )

    def test_scales_used_against_limit_when_limit_is_not_one_hundred(self) -> None:
        payload = json.loads(sample_output())
        payload["providers"]["claude"]["resources"]["session"] = {
            "limit": 400,
            "used": 100,
        }

        reading = parse_openusage_output(json.dumps(payload))

        self.assertEqual(reading.session_pct, 25.0)

    def test_salvages_json_embedded_in_surrounding_noise(self) -> None:
        noisy = f"warning: keychain prompt\n{sample_output()}\ntrailing chatter\n"

        reading = parse_openusage_output(noisy)

        self.assertEqual(reading.session_pct, 8.0)

    def test_carries_the_stale_flag(self) -> None:
        reading = parse_openusage_output(sample_output(stale=True))

        self.assertTrue(reading.stale)

    def test_missing_session_resource_leaves_that_percentage_unset(self) -> None:
        payload = json.loads(sample_output())
        del payload["providers"]["claude"]["resources"]["session"]

        reading = parse_openusage_output(json.dumps(payload))

        self.assertIsNone(reading.session_pct)
        self.assertEqual(reading.weekly_pct, 10.5)

    def test_missing_claude_provider_yields_no_reading(self) -> None:
        payload = json.loads(sample_output())
        del payload["providers"]["claude"]

        self.assertIsNone(parse_openusage_output(json.dumps(payload)))

    def test_unparseable_output_yields_no_reading(self) -> None:
        self.assertIsNone(parse_openusage_output("openusage: not logged in"))
        self.assertIsNone(parse_openusage_output(""))


class FindOpenUsageBinaryTests(unittest.TestCase):
    def test_explicit_path_is_expanded_and_returned(self) -> None:
        with TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            (home / "bin").mkdir()
            expected = write_executable(
                home / "bin", "openusage", "#!/bin/sh\necho '{}'\n"
            )

            with unittest.mock.patch.dict(os.environ, {"HOME": str(home)}):
                found = find_openusage_binary(
                    "~/bin/openusage", search_paths=(), env_path=""
                )

        self.assertEqual(found, str(expected))

    def test_explicit_path_that_is_not_executable_is_rejected(self) -> None:
        with TemporaryDirectory() as tempdir:
            missing = Path(tempdir) / "openusage"

            found = find_openusage_binary(str(missing), search_paths=(), env_path="")

        self.assertIsNone(found)

    def test_discovers_executable_in_a_search_path(self) -> None:
        with TemporaryDirectory() as tempdir:
            expected = write_executable(
                Path(tempdir), "openusage", "#!/bin/sh\necho '{}'\n"
            )

            found = find_openusage_binary(search_paths=(tempdir,), env_path="")

        self.assertEqual(found, str(expected))

    def test_returns_none_when_nothing_is_installed(self) -> None:
        with TemporaryDirectory() as tempdir:
            found = find_openusage_binary(search_paths=(tempdir,), env_path="")

        self.assertIsNone(found)


class ReadOpenUsageTests(unittest.TestCase):
    def test_runs_the_binary_and_parses_its_output(self) -> None:
        with TemporaryDirectory() as tempdir:
            script = write_executable(
                Path(tempdir),
                "openusage",
                f"#!/bin/sh\ncat <<'JSON'\n{sample_output()}\nJSON\n",
            )

            reading = read_openusage(str(script))

        self.assertEqual(reading.session_pct, 8.0)

    def test_failing_binary_yields_no_reading(self) -> None:
        with TemporaryDirectory() as tempdir:
            script = write_executable(
                Path(tempdir), "openusage", "#!/bin/sh\nexit 1\n"
            )

            self.assertIsNone(read_openusage(str(script)))

    def test_missing_binary_yields_no_reading(self) -> None:
        with TemporaryDirectory() as tempdir:
            missing = os.path.join(tempdir, "openusage")

            self.assertIsNone(read_openusage(missing))


class OpenUsagePollerTests(unittest.TestCase):
    def test_delivers_readings_until_stopped(self) -> None:
        delivered: list[OpenUsageReading] = []
        seen_two = threading.Event()

        def on_reading(reading: OpenUsageReading) -> None:
            delivered.append(reading)
            if len(delivered) >= 2:
                seen_two.set()

        poller = OpenUsagePoller(
            reader=lambda: OpenUsageReading(session_pct=42.0),
            on_reading=on_reading,
            interval_seconds=0.01,
        )
        poller.start()
        try:
            self.assertTrue(seen_two.wait(timeout=5))
        finally:
            poller.stop()

        self.assertEqual(delivered[0].session_pct, 42.0)
        self.assertFalse(poller.is_running)

    def test_keeps_polling_after_a_reader_failure(self) -> None:
        attempts: list[int] = []
        delivered = threading.Event()

        def reader() -> OpenUsageReading:
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("openusage exploded")
            return OpenUsageReading(session_pct=7.0)

        poller = OpenUsagePoller(
            reader=reader,
            on_reading=lambda reading: delivered.set(),
            interval_seconds=0.01,
        )
        with self.assertLogs("pixoo_bridge.openusage", level="WARNING") as captured:
            poller.start()
            try:
                self.assertTrue(delivered.wait(timeout=5))
            finally:
                poller.stop()

        self.assertIn("openusage exploded", "\n".join(captured.output))

    def test_does_not_deliver_when_the_reader_has_no_reading(self) -> None:
        delivered: list[OpenUsageReading] = []
        attempts: list[int] = []
        polled_twice = threading.Event()

        def reader() -> OpenUsageReading | None:
            attempts.append(1)
            if len(attempts) >= 2:
                polled_twice.set()
            return None

        poller = OpenUsagePoller(
            reader=reader,
            on_reading=delivered.append,
            interval_seconds=0.01,
        )
        poller.start()
        try:
            self.assertTrue(polled_twice.wait(timeout=5))
        finally:
            poller.stop()

        self.assertEqual(delivered, [])


if __name__ == "__main__":
    unittest.main()
