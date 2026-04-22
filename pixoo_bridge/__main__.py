from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .bridge import (
    BridgeService,
    CompositePixooTransport,
    LoggingPixooTransport,
    PixooTransport,
    TCPProxyTransport,
    build_macos_bluetooth_transport,
)
from .rendering import SimplePixooRenderer
from .runtime_config import (
    BridgeRuntimeConfig,
    default_config_path,
    load_runtime_config,
)


def build_transport(config: BridgeRuntimeConfig) -> PixooTransport:
    logging_transport = LoggingPixooTransport()
    if config.transport == "log":
        return logging_transport

    if config.transport == "macos-bluetooth":
        assert config.device_mac is not None
        return CompositePixooTransport(
            (
                logging_transport,
                build_macos_bluetooth_transport(
                    device_mac=config.device_mac,
                    device_channel=config.device_channel,
                    packet_gap_ms=config.bluetooth_packet_gap_ms,
                    settle_ms=config.bluetooth_settle_ms,
                    brightness_percent=config.brightness_percent,
                ),
            )
        )

    return CompositePixooTransport(
        (
            logging_transport,
            TCPProxyTransport(
                host=config.proxy_host,
                port=config.proxy_port,
                brightness_percent=config.brightness_percent,
                connect_timeout=config.proxy_connect_timeout,
                ack_timeout=config.proxy_ack_timeout,
            ),
        )
    )


def build_renderer(config: BridgeRuntimeConfig) -> SimplePixooRenderer:
    return SimplePixooRenderer(
        usage_label=config.usage_label,
        mascot_asset_path=config.mascot_asset_path,
        status_dot_enabled=config.status_dot_enabled,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local bridge that turns Claude Code session state into "
            "Pixoo scene selections."
        )
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to a TOML config file. Defaults to "
            f"{default_config_path()}."
        ),
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--transport",
        default=None,
        choices=["log", "tcp-proxy", "macos-bluetooth"],
        help=(
            "How to present the selected scene. When omitted, the bridge uses "
            "macos-bluetooth if device_mac is configured, otherwise log."
        ),
    )
    parser.add_argument(
        "--proxy-host",
        default=None,
        help="Debug TCP proxy host used when --transport=tcp-proxy.",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=None,
        help="Debug TCP proxy port used when --transport=tcp-proxy.",
    )
    parser.add_argument(
        "--proxy-connect-timeout",
        type=float,
        default=None,
        help="Debug TCP proxy connect timeout in seconds.",
    )
    parser.add_argument(
        "--proxy-ack-timeout",
        type=float,
        default=None,
        help="Debug TCP proxy acknowledgement timeout in seconds.",
    )
    parser.add_argument(
        "--device-mac",
        default=None,
        help="Pixoo Max MAC address used when --transport=macos-bluetooth.",
    )
    parser.add_argument(
        "--device-channel",
        type=int,
        default=None,
        help="RFCOMM channel used when --transport=macos-bluetooth.",
    )
    parser.add_argument(
        "--bluetooth-packet-gap-ms",
        type=int,
        default=None,
        help="Delay between Bluetooth packet writes in milliseconds.",
    )
    parser.add_argument(
        "--bluetooth-settle-ms",
        type=int,
        default=None,
        help="Delay after the final Bluetooth packet write in milliseconds.",
    )
    parser.add_argument(
        "--brightness-percent",
        type=int,
        default=None,
        help=(
            "Optional Pixoo brightness percentage (0-100) to apply whenever a "
            "scene is sent."
        ),
    )
    parser.add_argument(
        "--usage-label",
        default=None,
        choices=["S", "Sess"],
        help="Label prefix shown in the bottom usage band.",
    )
    parser.add_argument(
        "--mascot-asset-path",
        default=None,
        help="Optional local GIF / PNG / sprite asset path for the mascot area.",
    )
    parser.add_argument(
        "--status-dot-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show or hide the top-right animated status dot.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["critical", "error", "warning", "info", "debug"],
    )
    args = parser.parse_args()

    try:
        config = load_runtime_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = BridgeService(
        transport=build_transport(config),
        renderer=build_renderer(config),
    )

    uvicorn.run(
        create_app(service),
        host=config.host,
        port=config.port,
        log_level=config.log_level,
    )


if __name__ == "__main__":
    main()
