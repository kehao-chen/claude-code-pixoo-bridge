from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable, Protocol, Sequence

from .pixoo_protocol import PixooMaxProtocolAdapter, normalize_brightness_percent
from .proxy_sender import MacOSBluetoothPacketSender, PixooPacketSender
from .rendering import PixooRenderer, RenderedScene, SimplePixooRenderer

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidPayloadError(ValueError):
    pass


class TransportError(RuntimeError):
    pass


class LifecycleState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class ActivityState(StrEnum):
    WAITING = "waiting"
    THINKING = "thinking"
    WORKING = "working"


class SceneKind(StrEnum):
    ATTENTION = "attention"
    FAILURE = "failure"
    RUNNING = "running"
    THINKING = "thinking"
    WAITING = "waiting"
    UNATTENDED_WARNING = "unattended-warning"
    UNATTENDED_CRITICAL = "unattended-critical"
    STOPPED = "stopped"
    IDLE = "idle"


WAITING_SIGNAL_EVENTS = {
    "SessionStart",
}

THINKING_SIGNAL_EVENTS = {
    "UserPromptSubmit",
    "PostToolUse",
    "SubagentStop",
    "TaskCompleted",
    "PostCompact",
}

WORKING_SIGNAL_EVENTS = {
    "PreToolUse",
    "SubagentStart",
    "TaskCreated",
    "WorktreeCreate",
}

ATTENTION_NOTIFICATION_TYPES = {
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
}

UNATTENDED_WARNING_AFTER = timedelta(seconds=30)
UNATTENDED_CRITICAL_AFTER = timedelta(seconds=60)

SCENE_PRIORITY = {
    SceneKind.ATTENTION: 70,
    SceneKind.FAILURE: 60,
    SceneKind.UNATTENDED_CRITICAL: 50,
    SceneKind.UNATTENDED_WARNING: 40,
    SceneKind.THINKING: 30,
    SceneKind.RUNNING: 20,
    SceneKind.WAITING: 10,
    SceneKind.STOPPED: 10,
    SceneKind.IDLE: 0,
}

EVENT_NAME_ALIASES = {
    "sessionstart": "SessionStart",
    "userpromptsubmit": "UserPromptSubmit",
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "posttoolusefailure": "PostToolUseFailure",
    "permissionrequest": "PermissionRequest",
    "permissiondenied": "PermissionDenied",
    "notification": "Notification",
    "subagentstart": "SubagentStart",
    "subagentstop": "SubagentStop",
    "taskcreated": "TaskCreated",
    "taskcompleted": "TaskCompleted",
    "stop": "Stop",
    "stopfailure": "StopFailure",
    "sessionend": "SessionEnd",
    "worktreecreate": "WorktreeCreate",
    "worktreeremove": "WorktreeRemove",
    "postcompact": "PostCompact",
}


@dataclass(slots=True)
class HookEvent:
    session_id: str
    event_name: str
    cwd: str | None = None
    permission_mode: str | None = None
    notification_type: str | None = None
    message: str | None = None
    title: str | None = None
    error: str | None = None
    error_details: str | None = None
    last_assistant_message: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    is_interrupt: bool = False


@dataclass(slots=True)
class StatusSnapshot:
    session_id: str
    cwd: str | None = None
    session_name: str | None = None
    model_display_name: str | None = None
    context_used_pct: float | None = None
    five_hour_pct: float | None = None
    seven_day_pct: float | None = None
    total_cost_usd: float | None = None


@dataclass(slots=True)
class SessionState:
    session_id: str
    session_name: str | None = None
    cwd: str | None = None
    model_display_name: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.IDLE
    activity_state: ActivityState = ActivityState.WAITING
    attention_needed: bool = False
    failure: bool = False
    last_event: str | None = None
    notification_type: str | None = None
    last_message: str | None = None
    error_type: str | None = None
    error_details: str | None = None
    tool_name: str | None = None
    context_used_pct: float | None = None
    five_hour_pct: float | None = None
    seven_day_pct: float | None = None
    total_cost_usd: float | None = None
    updated_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "cwd": self.cwd,
            "model_display_name": self.model_display_name,
            "lifecycle_state": self.lifecycle_state.value,
            "activity_state": self.activity_state.value,
            "attention_needed": self.attention_needed,
            "failure": self.failure,
            "last_event": self.last_event,
            "notification_type": self.notification_type,
            "last_message": self.last_message,
            "error_type": self.error_type,
            "error_details": self.error_details,
            "tool_name": self.tool_name,
            "context_used_pct": self.context_used_pct,
            "five_hour_pct": self.five_hour_pct,
            "seven_day_pct": self.seven_day_pct,
            "total_cost_usd": self.total_cost_usd,
            "updated_at": self.updated_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@dataclass(slots=True)
class ScreenScene:
    kind: SceneKind
    detail: str
    footer: str = ""
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "footer": self.footer,
            "updated_at": self.updated_at.isoformat(),
        }


def parse_hook_payload(payload: dict[str, Any]) -> HookEvent:
    if not isinstance(payload, dict):
        raise InvalidPayloadError("hook payload must be a JSON object")

    session_id = require_string(payload.get("session_id"), "session_id")
    raw_event_name = first_string(
        payload.get("hook_event_name"),
        payload.get("event_name"),
        payload.get("event"),
        payload.get("type"),
    )
    if not raw_event_name:
        raise InvalidPayloadError("hook payload is missing hook_event_name")

    return HookEvent(
        session_id=session_id,
        event_name=normalize_event_name(raw_event_name),
        cwd=optional_string(payload.get("cwd")),
        permission_mode=optional_string(payload.get("permission_mode")),
        notification_type=normalize_notification_type(
            optional_string(payload.get("notification_type"))
        ),
        message=optional_string(payload.get("message")),
        title=optional_string(payload.get("title")),
        error=optional_string(payload.get("error")),
        error_details=optional_string(payload.get("error_details")),
        last_assistant_message=optional_string(payload.get("last_assistant_message")),
        tool_name=optional_string(payload.get("tool_name")),
        tool_input=payload.get("tool_input")
        if isinstance(payload.get("tool_input"), dict)
        else None,
        is_interrupt=bool(payload.get("is_interrupt", False)),
    )


def parse_status_payload(payload: dict[str, Any]) -> StatusSnapshot:
    if not isinstance(payload, dict):
        raise InvalidPayloadError("status payload must be a JSON object")

    session_id = require_string(payload.get("session_id"), "session_id")
    model_value = payload.get("model")
    model_display_name = None
    if isinstance(model_value, dict):
        model_display_name = optional_string(model_value.get("display_name"))
    elif isinstance(model_value, str):
        model_display_name = model_value

    workspace_dir = nested_value(payload, "workspace", "current_dir")
    if not isinstance(workspace_dir, str):
        workspace_dir = nested_value(payload, "workspace", "project_dir")

    return StatusSnapshot(
        session_id=session_id,
        cwd=optional_string(workspace_dir) or optional_string(payload.get("cwd")),
        session_name=optional_string(payload.get("session_name")),
        model_display_name=model_display_name,
        context_used_pct=optional_number(
            nested_value(payload, "context_window", "used_percentage")
        ),
        five_hour_pct=optional_number(
            nested_value(payload, "rate_limits", "five_hour", "used_percentage")
        ),
        seven_day_pct=optional_number(
            nested_value(payload, "rate_limits", "seven_day", "used_percentage")
        ),
        total_cost_usd=optional_number(
            nested_value(payload, "cost", "total_cost_usd")
        ),
    )


def normalize_event_name(raw_event_name: str) -> str:
    normalized = raw_event_name.strip()
    alias_key = normalized.replace("-", "").replace("_", "").lower()
    return EVENT_NAME_ALIASES.get(alias_key, normalized)


def normalize_notification_type(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip().lower()
    return value or None


def nested_value(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def require_string(value: Any, field_name: str) -> str:
    string_value = optional_string(value)
    if string_value is None:
        raise InvalidPayloadError(f"{field_name} must be a non-empty string")
    return string_value


def optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def first_string(*values: Any) -> str | None:
    for value in values:
        text = optional_string(value)
        if text is not None:
            return text
    return None


def optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return f"{text[: limit - 1]}+"


def format_percentage(label: str, value: float | None) -> str:
    if value is None:
        return ""
    rounded = float(round(value, 1))
    if rounded.is_integer():
        return f"{label} {int(rounded)}%"
    return f"{label} {rounded:.1f}%"


def format_usage_number(value: float | None) -> str:
    if value is None:
        return "--"
    bounded = max(0.0, min(100.0, value))
    return str(int(bounded))


def preferred_usage_value(
    *,
    five_hour_pct: float | None,
    context_used_pct: float | None,
    seven_day_pct: float | None,
) -> float | None:
    for value in (five_hour_pct, context_used_pct, seven_day_pct):
        if value is not None:
            return value
    return None


def rendered_scene_signature(rendered_scene: RenderedScene) -> str:
    return json.dumps(
        rendered_scene.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )


class LoggingPixooTransport:
    def __init__(self, transport_logger: logging.Logger | None = None) -> None:
        self._logger = transport_logger or logging.getLogger("pixoo_bridge.transport")

    def present(self, scene: ScreenScene, rendered_scene: RenderedScene) -> bool:
        render_summary = {
            "width": rendered_scene.width,
            "height": rendered_scene.height,
            "frame_count": len(rendered_scene.frames),
        }
        self._logger.info(
            "selected_scene=%s render_summary=%s",
            json.dumps(scene.to_dict(), sort_keys=True, separators=(",", ":")),
            json.dumps(render_summary, sort_keys=True, separators=(",", ":")),
        )
        return True


class PixooTransport(Protocol):
    def present(self, scene: ScreenScene, rendered_scene: RenderedScene) -> bool:
        ...


class CompositePixooTransport:
    def __init__(self, transports: Sequence[PixooTransport]) -> None:
        if not transports:
            raise ValueError("at least one transport is required")
        self._transports = tuple(transports)

    def present(self, scene: ScreenScene, rendered_scene: RenderedScene) -> bool:
        presented = False
        for transport in self._transports:
            presented = transport.present(scene, rendered_scene) or presented
        return presented


class TCPProxyTransport:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        brightness_percent: int | None = None,
        connect_timeout: float = 2.0,
        ack_timeout: float = 2.0,
        proxy_logger: logging.Logger | None = None,
    ) -> None:
        self._host = require_string(host, "host")
        self._port = port
        self._brightness_percent = (
            normalize_brightness_percent(brightness_percent)
            if brightness_percent is not None
            else None
        )
        self._connect_timeout = connect_timeout
        self._ack_timeout = ack_timeout
        self._logger = proxy_logger or logging.getLogger("pixoo_bridge.transport")

    def present(self, scene: ScreenScene, rendered_scene: RenderedScene) -> bool:
        request = {
            "schema_version": 1,
            "type": "present_scene",
            "sent_at": utc_now().isoformat(),
            "scene": scene.to_dict(),
            "rendering": rendered_scene.to_dict(),
        }
        if self._brightness_percent is not None:
            request["brightness_percent"] = self._brightness_percent
        payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"

        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            ) as sock:
                sock.settimeout(self._ack_timeout)
                with sock.makefile("rwb") as proxy_stream:
                    proxy_stream.write(payload)
                    proxy_stream.flush()
                    ack_line = proxy_stream.readline(65536)
        except OSError as exc:
            raise TransportError(
                f"pixoo proxy {self._host}:{self._port} connection failed: {exc}"
            ) from exc

        if not ack_line:
            raise TransportError(
                f"pixoo proxy {self._host}:{self._port} closed without acknowledgement"
            )

        try:
            acknowledgement = json.loads(ack_line)
        except json.JSONDecodeError as exc:
            raise TransportError(
                f"pixoo proxy {self._host}:{self._port} returned invalid JSON"
            ) from exc

        if not isinstance(acknowledgement, dict):
            raise TransportError(
                "pixoo proxy "
                f"{self._host}:{self._port} acknowledgement must be an object"
            )

        if acknowledgement.get("ok") is not True:
            error_message = first_string(
                acknowledgement.get("error"), acknowledgement.get("message")
            )
            raise TransportError(
                error_message
                or f"pixoo proxy {self._host}:{self._port} rejected the scene"
            )

        self._logger.info(
            "proxy_ack=%s",
            json.dumps(acknowledgement, sort_keys=True, separators=(",", ":")),
        )
        return True


class PacketSenderTransport:
    def __init__(
        self,
        *,
        sender: PixooPacketSender,
        protocol_adapter: PixooMaxProtocolAdapter | None = None,
        brightness_percent: int | None = None,
        transport_logger: logging.Logger | None = None,
    ) -> None:
        self._sender = sender
        self._protocol = protocol_adapter or PixooMaxProtocolAdapter()
        self._brightness_percent = (
            normalize_brightness_percent(brightness_percent)
            if brightness_percent is not None
            else None
        )
        self._logger = transport_logger or logging.getLogger("pixoo_bridge.transport")

    def present(self, scene: ScreenScene, rendered_scene: RenderedScene) -> bool:
        try:
            packets = []
            if self._brightness_percent is not None:
                packets.append(
                    self._protocol.encode_brightness(self._brightness_percent)
                )
            packets.extend(self._protocol.encode_rendered_scene(rendered_scene))
            summary = self._sender.send_packets(packets, scene=scene.to_dict())
        except (RuntimeError, ValueError) as exc:
            raise TransportError(f"pixoo packet sender failed: {exc}") from exc

        self._logger.info(
            "packet_sender_ack=%s",
            json.dumps(summary, sort_keys=True, separators=(",", ":")),
        )
        return True


def build_macos_bluetooth_transport(
    *,
    device_mac: str,
    device_channel: int = 1,
    packet_gap_ms: int = 30,
    settle_ms: int = 500,
    brightness_percent: int | None = None,
) -> PacketSenderTransport:
    return PacketSenderTransport(
        sender=MacOSBluetoothPacketSender(
            device_mac=device_mac,
            channel_id=device_channel,
            packet_gap_ms=packet_gap_ms,
            settle_ms=settle_ms,
            sender_logger=logging.getLogger("pixoo_bridge.transport"),
        ),
        brightness_percent=brightness_percent,
        transport_logger=logging.getLogger("pixoo_bridge.transport"),
    )


class BridgeService:
    def __init__(
        self,
        *,
        transport: PixooTransport | None = None,
        renderer: PixooRenderer | None = None,
        clock: Callable[[], datetime] = utc_now,
        ended_session_retention: timedelta = timedelta(seconds=60),
    ) -> None:
        self._transport = transport or LoggingPixooTransport()
        self._renderer = renderer or SimplePixooRenderer()
        self._clock = clock
        self._ended_session_retention = ended_session_retention
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.RLock()
        self._last_render_signature: str | None = None
        self._latest_status_usage_pct: float | None = None
        self._five_hour_zero_streaks: dict[str, int] = {}

    def ingest_hook(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = parse_hook_payload(payload)
        with self._lock:
            now = self._clock()
            self._prune_ended_sessions(now)
            session = self._session_for(event.session_id, now)
            self._merge_common_hook_fields(session, event)
            self._reduce_hook(session, event, now)
            scene = self._select_scene(now)
            scene_emitted = self._emit_scene(scene)
            return {
                "accepted": True,
                "source": "hook",
                "session": session.to_dict(),
                "selected_scene": scene.to_dict(),
                "scene_emitted": scene_emitted,
            }

    def ingest_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = parse_status_payload(payload)
        with self._lock:
            now = self._clock()
            self._prune_ended_sessions(now)
            session = self._session_for(snapshot.session_id, now)
            if snapshot.session_name is not None:
                session.session_name = snapshot.session_name
            if snapshot.cwd is not None:
                session.cwd = snapshot.cwd
            if snapshot.model_display_name is not None:
                session.model_display_name = snapshot.model_display_name
            session.context_used_pct = snapshot.context_used_pct
            (
                trusted_five_hour_pct,
                suspicious_zero_five_hour,
            ) = self._resolve_trusted_five_hour_pct(
                session_id=snapshot.session_id,
                previous_value=session.five_hour_pct,
                incoming_value=snapshot.five_hour_pct,
            )
            session.five_hour_pct = trusted_five_hour_pct
            session.seven_day_pct = snapshot.seven_day_pct
            session.total_cost_usd = snapshot.total_cost_usd
            latest_usage_pct = None
            if not suspicious_zero_five_hour:
                latest_usage_pct = preferred_usage_value(
                    five_hour_pct=trusted_five_hour_pct,
                    context_used_pct=snapshot.context_used_pct,
                    seven_day_pct=snapshot.seven_day_pct,
                )
            if latest_usage_pct is not None:
                self._latest_status_usage_pct = latest_usage_pct
            if (
                session.last_event is None
                and session.lifecycle_state == LifecycleState.IDLE
            ):
                session.lifecycle_state = LifecycleState.RUNNING
                session.activity_state = ActivityState.WAITING
            session.updated_at = now
            scene = self._select_scene(now)
            scene_emitted = self._emit_scene(scene)
            return {
                "accepted": True,
                "source": "status",
                "session": session.to_dict(),
                "selected_scene": scene.to_dict(),
                "scene_emitted": scene_emitted,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._prune_ended_sessions(now)
            scene = self._select_scene(now)
            sessions = sorted(
                (session.to_dict() for session in self._sessions.values()),
                key=lambda item: item["updated_at"],
                reverse=True,
            )
            return {
                "session_count": len(sessions),
                "selected_scene": scene.to_dict(),
                "sessions": sessions,
            }

    def health(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "ok": True,
            "session_count": snapshot["session_count"],
            "selected_scene": snapshot["selected_scene"],
        }

    def _session_for(self, session_id: str, now: datetime) -> SessionState:
        session = self._sessions.get(session_id)
        if session is None:
            session = SessionState(session_id=session_id, updated_at=now)
            self._sessions[session_id] = session
        return session

    def _emit_scene(self, scene: ScreenScene) -> bool:
        rendered_scene = self._renderer.render(scene)
        signature = rendered_scene_signature(rendered_scene)
        if signature == self._last_render_signature:
            return False
        emitted = self._transport.present(scene, rendered_scene)
        self._last_render_signature = signature
        return emitted

    def _merge_common_hook_fields(
        self, session: SessionState, event: HookEvent
    ) -> None:
        if event.cwd is not None:
            session.cwd = event.cwd
        session.last_event = event.event_name
        session.notification_type = event.notification_type
        session.tool_name = event.tool_name
        session.last_message = first_string(
            event.title,
            event.message,
            event.last_assistant_message,
            event.error_details,
            event.error,
        )

    def _reduce_hook(
        self, session: SessionState, event: HookEvent, now: datetime
    ) -> None:
        if event.event_name in WORKING_SIGNAL_EVENTS:
            session.lifecycle_state = LifecycleState.RUNNING
            session.activity_state = ActivityState.WORKING
            session.attention_needed = False
            session.failure = False
            session.error_type = None
            session.error_details = None
            session.ended_at = None
        elif event.event_name in THINKING_SIGNAL_EVENTS:
            session.lifecycle_state = LifecycleState.RUNNING
            session.activity_state = ActivityState.THINKING
            session.attention_needed = False
            session.failure = False
            session.error_type = None
            session.error_details = None
            session.ended_at = None
        elif event.event_name in WAITING_SIGNAL_EVENTS:
            session.lifecycle_state = LifecycleState.RUNNING
            session.activity_state = ActivityState.WAITING
            session.attention_needed = False
            session.failure = False
            session.error_type = None
            session.error_details = None
            session.ended_at = None
        elif event.event_name == "PermissionRequest":
            session.lifecycle_state = LifecycleState.RUNNING
            session.activity_state = ActivityState.WAITING
            session.attention_needed = True
            session.failure = False
            session.error_type = None
            session.error_details = None
        elif event.event_name == "Notification":
            if self._is_attention_notification(event):
                session.attention_needed = True
            elif session.lifecycle_state == LifecycleState.IDLE:
                session.lifecycle_state = LifecycleState.RUNNING
                session.activity_state = ActivityState.THINKING
        elif event.event_name == "StopFailure":
            session.lifecycle_state = LifecycleState.FAILED
            session.activity_state = ActivityState.WAITING
            session.attention_needed = False
            session.failure = True
            session.error_type = event.error
            session.error_details = first_string(
                event.error_details, event.last_assistant_message
            )
        elif event.event_name == "PostToolUseFailure":
            if not event.is_interrupt:
                session.lifecycle_state = LifecycleState.FAILED
                session.activity_state = ActivityState.WAITING
                session.attention_needed = False
                session.failure = True
                session.error_type = event.tool_name or "tool_failure"
                session.error_details = first_string(event.error, event.error_details)
        elif event.event_name == "Stop":
            session.attention_needed = False
            if not session.failure:
                session.lifecycle_state = LifecycleState.STOPPED
                session.activity_state = ActivityState.WAITING
        elif event.event_name == "SessionEnd":
            session.attention_needed = False
            if not session.failure:
                session.lifecycle_state = LifecycleState.STOPPED
                session.activity_state = ActivityState.WAITING
            session.ended_at = now
        elif session.lifecycle_state == LifecycleState.IDLE:
            session.lifecycle_state = LifecycleState.RUNNING
            session.activity_state = ActivityState.WORKING

        session.updated_at = now

    def _is_attention_notification(self, event: HookEvent) -> bool:
        if event.notification_type in ATTENTION_NOTIFICATION_TYPES:
            return True
        summary = " ".join(
            part
            for part in (event.title, event.message, event.last_assistant_message)
            if part
        ).lower()
        return "needs your permission" in summary or "permission" in summary

    def _prune_ended_sessions(self, now: datetime) -> None:
        expired_session_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if session.ended_at is not None
            and now - session.ended_at > self._ended_session_retention
        ]
        for session_id in expired_session_ids:
            self._sessions.pop(session_id, None)
            self._five_hour_zero_streaks.pop(session_id, None)

    def _resolve_trusted_five_hour_pct(
        self,
        *,
        session_id: str,
        previous_value: float | None,
        incoming_value: float | None,
    ) -> tuple[float | None, bool]:
        if incoming_value is None:
            self._five_hour_zero_streaks.pop(session_id, None)
            return None, False
        if incoming_value != 0:
            self._five_hour_zero_streaks.pop(session_id, None)
            return incoming_value, False

        zero_streak = self._five_hour_zero_streaks.get(session_id, 0) + 1
        self._five_hour_zero_streaks[session_id] = zero_streak
        if zero_streak >= 2:
            return 0.0, False
        return previous_value, True

    def _select_scene(self, now: datetime) -> ScreenScene:
        session = self._choose_display_session(now)
        if session is None:
            return ScreenScene(
                kind=SceneKind.IDLE,
                detail="--",
                footer="No sessions",
                updated_at=now,
            )

        scene_kind = self._scene_kind_for_session(session, now)
        usage_text = format_usage_number(self._latest_status_usage_pct)
        context_text = format_percentage("CTX", session.context_used_pct)
        secondary_text = (
            format_percentage("5H", session.five_hour_pct)
            or format_percentage("7D", session.seven_day_pct)
            or (session.model_display_name or "")
        )

        if scene_kind == SceneKind.ATTENTION:
            footer = shorten(
                first_string(
                    session.tool_name,
                    session.notification_type,
                    session.last_message,
                )
                or context_text
                or "Approval",
                12,
            )
        elif scene_kind == SceneKind.FAILURE:
            footer = shorten(
                first_string(
                    session.error_type,
                    session.error_details,
                    session.last_message,
                )
                or context_text
                or "Error",
                12,
            )
        elif scene_kind == SceneKind.UNATTENDED_CRITICAL:
            footer = "AFK 60+"
        elif scene_kind == SceneKind.UNATTENDED_WARNING:
            footer = "AFK 30+"
        elif scene_kind == SceneKind.THINKING:
            footer = "Thinking"
        elif scene_kind == SceneKind.RUNNING:
            footer = shorten(secondary_text or "Working", 12)
        else:
            footer = shorten(context_text or secondary_text or "Waiting", 12)

        return ScreenScene(
            kind=scene_kind,
            detail=usage_text,
            footer=footer,
            updated_at=now,
        )

    def _choose_display_session(self, now: datetime) -> SessionState | None:
        sessions = list(self._sessions.values())
        if not sessions:
            return None

        return max(
            sessions,
            key=lambda session: (
                SCENE_PRIORITY[self._scene_kind_for_session(session, now)],
                session.updated_at,
            ),
        )

    def _scene_kind_for_session(
        self, session: SessionState, now: datetime
    ) -> SceneKind:
        if session.attention_needed:
            return SceneKind.ATTENTION
        if session.failure:
            return SceneKind.FAILURE
        if session.ended_at is None:
            inactivity = now - session.updated_at
            if inactivity > UNATTENDED_CRITICAL_AFTER:
                return SceneKind.UNATTENDED_CRITICAL
            if inactivity > UNATTENDED_WARNING_AFTER:
                return SceneKind.UNATTENDED_WARNING
        if session.lifecycle_state == LifecycleState.RUNNING:
            if session.activity_state == ActivityState.THINKING:
                return SceneKind.THINKING
            if session.activity_state == ActivityState.WAITING:
                return SceneKind.WAITING
            return SceneKind.RUNNING
        if session.lifecycle_state == LifecycleState.IDLE:
            return SceneKind.IDLE
        return SceneKind.WAITING
