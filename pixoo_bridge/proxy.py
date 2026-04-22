from __future__ import annotations

import argparse
import json
import socketserver
from datetime import datetime, timezone

from .pixoo_protocol import PixooMaxProtocolAdapter, normalize_brightness_percent
from .proxy_sender import (
    DivoomProxyPacketSender,
    MacOSBluetoothPacketSender,
    PixooPacketSender,
    PrintingPacketSender,
)
from .rendering import RenderedFrame, RenderedScene


class ThreadedProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class PixooProxyApplication:
    def __init__(
        self,
        sender: PixooPacketSender | None = None,
        *,
        default_brightness_percent: int | None = None,
    ) -> None:
        self._protocol = PixooMaxProtocolAdapter()
        self._sender = sender or PrintingPacketSender()
        self._default_brightness_percent = (
            normalize_brightness_percent(default_brightness_percent)
            if default_brightness_percent is not None
            else None
        )

    def handle_payload(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("type") != "present_scene":
            return {"ok": False, "error": "unsupported_request_type"}

        rendered_scene = self._parse_rendered_scene(payload.get("rendering"))
        packets = []
        brightness_percent = self._parse_brightness_percent(
            payload.get("brightness_percent")
        )
        if brightness_percent is None:
            brightness_percent = self._default_brightness_percent
        if brightness_percent is not None:
            packets.append(self._protocol.encode_brightness(brightness_percent))
        packets.extend(self._protocol.encode_rendered_scene(rendered_scene))
        summary = self._sender.send_packets(
            packets,
            scene=payload.get("scene")
            if isinstance(payload.get("scene"), dict)
            else None,
        )
        return {
            "ok": True,
            "received_at": datetime.now(timezone.utc).isoformat(),
            **summary,
        }

    def _parse_brightness_percent(self, payload: object) -> int | None:
        if payload is None:
            return None
        if isinstance(payload, bool) or not isinstance(payload, int):
            raise ValueError("brightness_percent must be an integer")
        return normalize_brightness_percent(payload)

    def _parse_rendered_scene(self, payload: object) -> RenderedScene:
        if not isinstance(payload, dict):
            raise ValueError("rendering payload must be an object")

        frames_payload = payload.get("frames")
        if not isinstance(frames_payload, list):
            raise ValueError("rendering frames must be an array")

        frames = []
        for frame_payload in frames_payload:
            if not isinstance(frame_payload, dict):
                raise ValueError("rendering frame must be an object")

            palette = frame_payload.get("palette")
            rows = frame_payload.get("rows")
            duration_ms = frame_payload.get("duration_ms", 0)

            if not isinstance(palette, list) or not all(
                isinstance(color, str) for color in palette
            ):
                raise ValueError("rendering frame palette must be a string array")
            if not isinstance(rows, list) or not all(
                isinstance(row, str) for row in rows
            ):
                raise ValueError("rendering frame rows must be a string array")
            if not isinstance(duration_ms, int):
                raise ValueError("rendering frame duration_ms must be an integer")

            frames.append(
                RenderedFrame(
                    palette=palette,
                    rows=rows,
                    duration_ms=duration_ms,
                )
            )

        width = payload.get("width")
        height = payload.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("rendering width and height must be integers")

        return RenderedScene(width=width, height=height, frames=frames)


class PixooProxyHandler(socketserver.StreamRequestHandler):
    application = PixooProxyApplication()

    def handle(self) -> None:
        line = self.rfile.readline(65536)
        if not line:
            return

        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                response = {"ok": False, "error": "payload_must_be_object"}
            else:
                response = self.application.handle_payload(payload)
        except json.JSONDecodeError:
            response = {"ok": False, "error": "invalid_json"}
        except ValueError as exc:
            response = {"ok": False, "error": str(exc)}

        self.wfile.write(
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the optional Pixoo debug TCP proxy that derives Pixoo Max "
            "packets and either prints them, sends them over macOS Bluetooth "
            "Classic, or forwards them to a Divoom-compatible proxy."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument(
        "--sender",
        default="print",
        choices=["print", "divoom-proxy", "macos-bluetooth"],
        help="How the debug proxy should deliver generated Pixoo packets.",
    )
    parser.add_argument(
        "--upstream-host",
        help="Divoom-compatible upstream host used when --sender=divoom-proxy.",
    )
    parser.add_argument(
        "--upstream-port",
        type=int,
        default=7777,
        help="Divoom-compatible upstream port used when --sender=divoom-proxy.",
    )
    parser.add_argument(
        "--device-mac",
        help=(
            "Target Pixoo MAC address used when --sender=divoom-proxy or "
            "--sender=macos-bluetooth."
        ),
    )
    parser.add_argument(
        "--device-port",
        "--device-channel",
        dest="device_port",
        type=int,
        default=1,
        help=(
            "Target Pixoo RFCOMM channel used when --sender=divoom-proxy or "
            "--sender=macos-bluetooth."
        ),
    )
    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=3.0,
        help="Upstream socket timeout in seconds.",
    )
    parser.add_argument(
        "--bluetooth-packet-gap-ms",
        type=int,
        default=30,
        help="Delay between Bluetooth packet writes when --sender=macos-bluetooth.",
    )
    parser.add_argument(
        "--bluetooth-settle-ms",
        type=int,
        default=500,
        help="Delay to keep Bluetooth connection open after the last write.",
    )
    parser.add_argument(
        "--brightness-percent",
        type=int,
        default=None,
        help=(
            "Optional Pixoo brightness percentage (0-100) applied to each "
            "present_scene request."
        ),
    )
    args = parser.parse_args()

    if args.sender == "divoom-proxy":
        if not args.upstream_host or not args.device_mac:
            parser.error(
                "--upstream-host and --device-mac are required when "
                "--sender=divoom-proxy"
            )
        sender: PixooPacketSender = DivoomProxyPacketSender(
            host=args.upstream_host,
            upstream_port=args.upstream_port,
            device_mac=args.device_mac,
            device_port=args.device_port,
            socket_timeout=args.socket_timeout,
        )
    elif args.sender == "macos-bluetooth":
        if not args.device_mac:
            parser.error("--device-mac is required when --sender=macos-bluetooth")
        sender = MacOSBluetoothPacketSender(
            device_mac=args.device_mac,
            channel_id=args.device_port,
            packet_gap_ms=args.bluetooth_packet_gap_ms,
            settle_ms=args.bluetooth_settle_ms,
        )
    else:
        sender = PrintingPacketSender()

    try:
        PixooProxyHandler.application = PixooProxyApplication(
            sender=sender,
            default_brightness_percent=args.brightness_percent,
        )
    except ValueError as exc:
        parser.error(str(exc))

    with ThreadedProxyServer((args.host, args.port), PixooProxyHandler) as server:
        print(f"Pixoo debug proxy listening on {args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
