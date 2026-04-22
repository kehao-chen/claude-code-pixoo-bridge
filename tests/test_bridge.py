from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pixoo_bridge.bridge import BridgeService


class RecordingTransport:
    def __init__(self) -> None:
        self.scenes: list[dict[str, str | None]] = []
        self.rendered_scenes: list[dict[str, object]] = []

    def present(self, scene, rendered_scene) -> bool:
        self.scenes.append(scene.to_dict())
        self.rendered_scenes.append(rendered_scene.to_dict())
        return True


class StaticClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class BridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = StaticClock(datetime(2026, 4, 21, 2, 0, tzinfo=timezone.utc))
        self.transport = RecordingTransport()
        self.service = BridgeService(
            transport=self.transport,
            clock=self.clock,
            ended_session_retention=timedelta(seconds=30),
        )

    def test_status_snapshot_bootstraps_waiting_scene(self) -> None:
        result = self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "model": {"display_name": "Sonnet"},
                "context_window": {"used_percentage": 18},
                "rate_limits": {"five_hour": {"used_percentage": 32.5}},
            }
        )

        self.assertEqual(result["session"]["lifecycle_state"], "running")
        self.assertEqual(result["session"]["activity_state"], "waiting")
        self.assertEqual(result["selected_scene"]["kind"], "waiting")
        self.assertEqual(result["selected_scene"]["detail"], "32")
        self.assertEqual(result["selected_scene"]["footer"], "CTX 18%")
        self.assertNotIn("session_id", result["selected_scene"])
        self.assertNotIn("headline", result["selected_scene"])

    def test_usage_display_drops_fractional_part_instead_of_rounding(self) -> None:
        result = self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "context_window": {"used_percentage": 5.9},
            }
        )

        self.assertEqual(result["selected_scene"]["detail"], "5")
        self.assertEqual(result["selected_scene"]["footer"], "CTX 5.9%")

    def test_usage_display_prefers_five_hour_quota_by_default(self) -> None:
        result = self.service.ingest_status(
            {
                "session_id": "pixoo-test",
                "session_name": "Pixoo Test",
                "cwd": "/tmp/pixoo",
                "context_window": {"used_percentage": 21},
                "rate_limits": {"five_hour": {"used_percentage": 16.1}},
            }
        )

        self.assertEqual(result["selected_scene"]["detail"], "16")

    def test_fractional_usage_change_that_keeps_same_integer_does_not_emit(
        self,
    ) -> None:
        first = self.service.ingest_status(
            {
                "session_id": "quota-session",
                "session_name": "quota",
                "cwd": "/tmp/quota",
                "rate_limits": {"five_hour": {"used_percentage": 48.1}},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        second = self.service.ingest_status(
            {
                "session_id": "quota-session",
                "session_name": "quota",
                "cwd": "/tmp/quota",
                "rate_limits": {"five_hour": {"used_percentage": 48.2}},
            }
        )

        self.assertTrue(first["scene_emitted"])
        self.assertFalse(second["scene_emitted"])
        self.assertEqual(second["selected_scene"]["detail"], "48")
        self.assertEqual(len(self.transport.scenes), 1)

    def test_single_zero_five_hour_update_is_ignored_until_same_source_repeats_it(
        self,
    ) -> None:
        first = self.service.ingest_status(
            {
                "session_id": "quota-session",
                "session_name": "quota",
                "cwd": "/tmp/quota",
                "rate_limits": {"five_hour": {"used_percentage": 33}},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        suspicious = self.service.ingest_status(
            {
                "session_id": "quota-session",
                "session_name": "quota",
                "cwd": "/tmp/quota",
                "context_window": {"used_percentage": 3},
                "rate_limits": {"five_hour": {"used_percentage": 0}},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        confirmed = self.service.ingest_status(
            {
                "session_id": "quota-session",
                "session_name": "quota",
                "cwd": "/tmp/quota",
                "context_window": {"used_percentage": 3},
                "rate_limits": {"five_hour": {"used_percentage": 0}},
            }
        )

        self.assertEqual(first["selected_scene"]["detail"], "33")
        self.assertEqual(suspicious["session"]["five_hour_pct"], 33)
        self.assertEqual(suspicious["selected_scene"]["detail"], "33")
        self.assertEqual(confirmed["session"]["five_hour_pct"], 0.0)
        self.assertEqual(confirmed["selected_scene"]["detail"], "0")

    def test_source_switch_that_keeps_same_render_does_not_emit(self) -> None:
        self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "repo-a",
                "cwd": "/tmp/repo-a",
                "rate_limits": {"five_hour": {"used_percentage": 35}},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        first_attention = self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/repo-a",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
            }
        )
        self.clock.advance(timedelta(seconds=1))
        self.service.ingest_status(
            {
                "session_id": "sess-2",
                "session_name": "repo-b",
                "cwd": "/tmp/repo-b",
                "rate_limits": {"five_hour": {"used_percentage": 35.8}},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        second_attention = self.service.ingest_hook(
            {
                "session_id": "sess-2",
                "cwd": "/tmp/repo-b",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Edit",
            }
        )

        self.assertTrue(first_attention["scene_emitted"])
        self.assertFalse(second_attention["scene_emitted"])
        self.assertEqual(second_attention["selected_scene"]["kind"], "attention")
        self.assertEqual(second_attention["selected_scene"]["detail"], "35")
        self.assertEqual(len(self.transport.scenes), 2)

    def test_attention_scene_beats_running_session(self) -> None:
        self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "repo-a",
                "cwd": "/tmp/repo-a",
                "context_window": {"used_percentage": 20},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        self.service.ingest_status(
            {
                "session_id": "sess-2",
                "session_name": "repo-b",
                "cwd": "/tmp/repo-b",
                "context_window": {"used_percentage": 35},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        result = self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/repo-a",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "git push"},
            }
        )

        self.assertEqual(result["selected_scene"]["kind"], "attention")
        self.assertEqual(result["selected_scene"]["detail"], "35")
        self.assertEqual(result["selected_scene"]["footer"], "Bash")

    def test_failure_scene_beats_running_session(self) -> None:
        self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "repo-a",
                "cwd": "/tmp/repo-a",
                "context_window": {"used_percentage": 20},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        self.service.ingest_status(
            {
                "session_id": "sess-2",
                "session_name": "repo-b",
                "cwd": "/tmp/repo-b",
                "context_window": {"used_percentage": 35},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        result = self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/repo-a",
                "hook_event_name": "StopFailure",
                "error": "rate_limit",
                "error_details": "429 Too Many Requests",
            }
        )

        self.assertEqual(result["selected_scene"]["kind"], "failure")
        self.assertEqual(result["session"]["failure"], True)
        self.assertEqual(result["selected_scene"]["detail"], "35")
        self.assertEqual(result["selected_scene"]["footer"], "rate_limit")

    def test_usage_display_follows_latest_status_even_when_another_session_wins_scene(
        self,
    ) -> None:
        self.service.ingest_status(
            {
                "session_id": "older-session",
                "session_name": "older",
                "cwd": "/tmp/older",
                "context_window": {"used_percentage": 7},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        self.service.ingest_hook(
            {
                "session_id": "older-session",
                "cwd": "/tmp/older",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
            }
        )
        self.clock.advance(timedelta(seconds=1))

        result = self.service.ingest_status(
            {
                "session_id": "latest-session",
                "session_name": "latest",
                "cwd": "/tmp/latest",
                "rate_limits": {"five_hour": {"used_percentage": 16.1}},
            }
        )

        self.assertEqual(result["selected_scene"]["kind"], "attention")
        self.assertEqual(result["selected_scene"]["detail"], "16")

    def test_ended_sessions_expire_after_grace_period(self) -> None:
        self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/repo-a",
                "hook_event_name": "SessionEnd",
            }
        )

        self.clock.advance(timedelta(seconds=31))
        snapshot = self.service.snapshot()

        self.assertEqual(snapshot["session_count"], 0)
        self.assertEqual(snapshot["selected_scene"]["kind"], "idle")

    def test_duplicate_scene_is_not_emitted_twice(self) -> None:
        first = self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "context_window": {"used_percentage": 18},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        second = self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "context_window": {"used_percentage": 18},
            }
        )

        self.assertTrue(first["scene_emitted"])
        self.assertFalse(second["scene_emitted"])
        self.assertEqual(len(self.transport.scenes), 1)
        self.assertEqual(len(self.transport.rendered_scenes), 1)

    def test_user_prompt_submit_switches_scene_to_thinking(self) -> None:
        self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "context_window": {"used_percentage": 18},
            }
        )
        self.clock.advance(timedelta(seconds=1))

        result = self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/bridge",
                "hook_event_name": "UserPromptSubmit",
            }
        )

        self.assertEqual(result["session"]["activity_state"], "thinking")
        self.assertEqual(result["selected_scene"]["kind"], "thinking")
        self.assertEqual(result["selected_scene"]["detail"], "18")

    def test_unattended_session_escalates_after_thresholds(self) -> None:
        self.service.ingest_status(
            {
                "session_id": "sess-1",
                "session_name": "bridge",
                "cwd": "/tmp/bridge",
                "context_window": {"used_percentage": 18},
            }
        )
        self.clock.advance(timedelta(seconds=1))
        self.service.ingest_hook(
            {
                "session_id": "sess-1",
                "cwd": "/tmp/bridge",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
            }
        )

        self.clock.advance(timedelta(seconds=31))
        warning_snapshot = self.service.snapshot()
        self.assertEqual(
            warning_snapshot["selected_scene"]["kind"],
            "unattended-warning",
        )

        self.clock.advance(timedelta(seconds=31))
        critical_snapshot = self.service.snapshot()
        self.assertEqual(
            critical_snapshot["selected_scene"]["kind"],
            "unattended-critical",
        )


if __name__ == "__main__":
    unittest.main()
