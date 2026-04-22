from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pixoo_protocol import normalize_brightness_percent

VALID_LOG_LEVELS = {"critical", "error", "warning", "info", "debug"}
VALID_TRANSPORTS = {"log", "tcp-proxy", "macos-bluetooth"}


@dataclass(frozen=True)
class BridgeRuntimeConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "log"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 9001
    proxy_connect_timeout: float = 2.0
    proxy_ack_timeout: float = 2.0
    device_mac: str | None = None
    device_channel: int = 1
    bluetooth_packet_gap_ms: int = 30
    bluetooth_settle_ms: int = 500
    brightness_percent: int | None = None
    usage_label: str = "S"
    mascot_asset_path: str | None = None
    status_dot_enabled: bool = True
    log_level: str = "info"
    config_path: Path | None = None


def default_config_path() -> Path:
    configured = os.environ.get("PIXOO_BRIDGE_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "claude-code-pixoo-bridge" / "config.toml"


def load_runtime_config(args: Any) -> BridgeRuntimeConfig:
    requested_path = getattr(args, "config", None)
    config_path = (
        Path(requested_path).expanduser()
        if requested_path
        else default_config_path()
    )
    config_exists = config_path.exists()
    config_data = _load_config_file(
        config_path,
        required=bool(requested_path),
    )

    host = _resolve_value(args, config_data, "host", "127.0.0.1")
    port = _require_int(_resolve_value(args, config_data, "port", 8765), "port")
    device_mac = _optional_string(_resolve_value(args, config_data, "device_mac", None))
    transport = _resolve_transport(args, config_data, device_mac=device_mac)
    log_level = _require_choice(
        _resolve_value(args, config_data, "log_level", "info"),
        "log_level",
        VALID_LOG_LEVELS,
    )
    proxy_host = _resolve_value(args, config_data, "proxy_host", "127.0.0.1")
    proxy_port = _require_int(
        _resolve_value(args, config_data, "proxy_port", 9001),
        "proxy_port",
    )
    proxy_connect_timeout = _require_float(
        _resolve_value(args, config_data, "proxy_connect_timeout", 2.0),
        "proxy_connect_timeout",
    )
    proxy_ack_timeout = _require_float(
        _resolve_value(args, config_data, "proxy_ack_timeout", 2.0),
        "proxy_ack_timeout",
    )
    device_channel = _require_int(
        _resolve_value(args, config_data, "device_channel", 1),
        "device_channel",
    )
    bluetooth_packet_gap_ms = _require_int(
        _resolve_value(args, config_data, "bluetooth_packet_gap_ms", 30),
        "bluetooth_packet_gap_ms",
    )
    bluetooth_settle_ms = _require_int(
        _resolve_value(args, config_data, "bluetooth_settle_ms", 500),
        "bluetooth_settle_ms",
    )
    brightness_percent = _optional_brightness_percent(
        _resolve_value(args, config_data, "brightness_percent", None),
        "brightness_percent",
    )
    usage_label = _require_choice(
        _resolve_value(args, config_data, "usage_label", "S"),
        "usage_label",
        {"S", "Sess"},
    )
    mascot_asset_path = _optional_string(
        _resolve_value(args, config_data, "mascot_asset_path", None)
    )
    status_dot_enabled = _require_bool(
        _resolve_value(args, config_data, "status_dot_enabled", True),
        "status_dot_enabled",
    )

    if transport == "macos-bluetooth" and device_mac is None:
        raise ValueError(
            "device_mac must be set in the config file or via --device-mac when "
            "--transport=macos-bluetooth"
        )

    return BridgeRuntimeConfig(
        host=_require_string(host, "host"),
        port=port,
        transport=transport,
        proxy_host=_require_string(proxy_host, "proxy_host"),
        proxy_port=proxy_port,
        proxy_connect_timeout=proxy_connect_timeout,
        proxy_ack_timeout=proxy_ack_timeout,
        device_mac=device_mac,
        device_channel=device_channel,
        bluetooth_packet_gap_ms=bluetooth_packet_gap_ms,
        bluetooth_settle_ms=bluetooth_settle_ms,
        brightness_percent=brightness_percent,
        usage_label=usage_label,
        mascot_asset_path=mascot_asset_path,
        status_dot_enabled=status_dot_enabled,
        log_level=log_level,
        config_path=config_path if config_exists or requested_path else None,
    )


def _load_config_file(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ValueError(f"config file does not exist: {path}")
        return {}
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"failed reading config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in config file {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"config file must contain a TOML table: {path}")
    return parsed


def _resolve_transport(
    args: Any,
    config_data: dict[str, Any],
    *,
    device_mac: str | None,
) -> str:
    transport = _resolve_value(args, config_data, "transport", None)
    transport_text = _optional_string(transport)
    if transport_text is None:
        return "macos-bluetooth" if device_mac else "log"
    return _require_choice(transport_text, "transport", VALID_TRANSPORTS)


def _resolve_value(
    args: Any,
    config_data: dict[str, Any],
    name: str,
    default: Any,
) -> Any:
    cli_value = getattr(args, name, None)
    if cli_value is not None:
        return cli_value
    return config_data.get(name, default)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("string configuration values must be strings")
    stripped = value.strip()
    return stripped or None


def _require_string(value: Any, name: str) -> str:
    resolved = _optional_string(value)
    if resolved is None:
        raise ValueError(f"{name} must be a non-empty string")
    return resolved


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_brightness_percent(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    try:
        return normalize_brightness_percent(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("brightness_percent", name)) from exc


def _require_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{name} must be a number")


def _require_choice(value: Any, name: str, choices: set[str]) -> str:
    resolved = _require_string(value, name)
    if resolved not in choices:
        expected = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {expected}")
    return resolved


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value
