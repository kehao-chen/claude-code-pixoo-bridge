from __future__ import annotations

import socketserver
import threading
import unittest
from pathlib import Path

from pixoo_bridge.macos_bluetooth_helper import BuiltMacOSBluetoothHelper
from pixoo_bridge.pixoo_protocol import PixooMaxProtocolAdapter, PixooPacket
from pixoo_bridge.proxy_sender import (
    DivoomProxyPacketSender,
    MacOSBluetoothPacketSender,
    normalize_mac_address,
    parse_mac_address,
)
from pixoo_bridge.rendering import RenderedFrame, RenderedScene


class RecordingUpstreamServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, RecordingUpstreamHandler)
        self.received = b""


class RecordingUpstreamHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        chunks = []
        while True:
            data = self.request.recv(4096)
            if not data:
                break
            chunks.append(data)
        self.server.received = b"".join(chunks)  # type: ignore[attr-defined]


class FakeHelperBuilder:
    def __init__(self, helper: BuiltMacOSBluetoothHelper) -> None:
        self.helper = helper
        self.calls = 0

    def ensure_built(self) -> BuiltMacOSBluetoothHelper:
        self.calls += 1
        return self.helper


class RecordingHelperRunner:
    def __init__(
        self,
        *,
        response: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {"ok": True}
        self.error = error
        self.helpers: list[BuiltMacOSBluetoothHelper] = []
        self.requests: list[dict[str, object]] = []

    def run(
        self,
        helper: BuiltMacOSBluetoothHelper,
        request: dict[str, object],
    ) -> dict[str, object]:
        self.helpers.append(helper)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return dict(self.response)

    def bundle_info(self, helper: BuiltMacOSBluetoothHelper) -> dict[str, object]:
        self.helpers.append(helper)
        if self.error is not None:
            raise self.error
        return {
            "ok": True,
            "bundle_identifier": helper.bundle_identifier,
            "bundle_path": str(helper.app_path),
            "has_bluetooth_usage_description": True,
        }


class DivoomProxyPacketSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PixooMaxProtocolAdapter()

    def start_server(self) -> tuple[RecordingUpstreamServer, threading.Thread]:
        server = RecordingUpstreamServer(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def stop_server(
        self, server: RecordingUpstreamServer, thread: threading.Thread
    ) -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_parse_mac_address(self) -> None:
        self.assertEqual(
            parse_mac_address("AA:BB:CC:DD:EE:FF"),
            bytes.fromhex("AABBCCDDEEFF"),
        )
        self.assertEqual(
            normalize_mac_address("aa-bb-cc-dd-ee-ff"),
            "AA:BB:CC:DD:EE:FF",
        )

    def test_sender_forwards_packets_to_upstream(self) -> None:
        rendered_scene = RenderedScene(
            width=32,
            height=32,
            frames=[
                RenderedFrame(
                    palette=["#000000", "#ffffff", "#ff0000", "#00ff00"],
                    rows=["0123" * 8 for _ in range(32)],
                )
            ],
        )
        packets = self.adapter.encode_rendered_scene(rendered_scene)
        server, thread = self.start_server()
        host, port = server.server_address
        sender = DivoomProxyPacketSender(
            host=host,
            upstream_port=port,
            device_mac="AA:BB:CC:DD:EE:FF",
            device_port=1,
        )

        try:
            summary = sender.send_packets(packets)
            sender.close()
        finally:
            self.stop_server(server, thread)

        self.assertEqual(summary["sender"], "divoom-proxy")
        self.assertEqual(summary["packet_count"], 1)
        received = server.received
        connect_prefix = bytes([0x69]) + bytes.fromhex("AABBCCDDEEFF") + b"\x01"
        disconnect_suffix = bytes([0x96]) + bytes.fromhex("AABBCCDDEEFF")
        self.assertTrue(received.startswith(connect_prefix))
        self.assertIn(packets[0].message, received)
        self.assertTrue(received.endswith(disconnect_suffix))


class MacOSBluetoothPacketSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PixooMaxProtocolAdapter()
        self.helper = BuiltMacOSBluetoothHelper(
            app_path=Path("/tmp/PixooBluetoothHelper.app"),
            executable_path=Path("/tmp/PixooBluetoothHelper.app/Contents/MacOS/PixooBluetoothHelper"),
            bundle_identifier="io.github.copilot.claude-code-pixoo-bridge.bluetooth-helper",
            usage_description="Send Pixoo Max frames over Bluetooth.",
        )

    def rendered_packets(self) -> list[PixooPacket]:
        rendered_scene = RenderedScene(
            width=32,
            height=32,
            frames=[
                RenderedFrame(
                    palette=["#000000", "#ffffff", "#ff0000", "#00ff00"],
                    rows=["0123" * 8 for _ in range(32)],
                )
            ],
        )
        return self.adapter.encode_rendered_scene(rendered_scene)

    def test_sender_invokes_helper_with_packet_hex_payloads(self) -> None:
        packets = self.rendered_packets()
        builder = FakeHelperBuilder(self.helper)
        runner = RecordingHelperRunner(
            response={
                "ok": True,
                "bytes_sent": sum(len(packet.message) for packet in packets),
            }
        )
        sender = MacOSBluetoothPacketSender(
            device_mac="aa-bb-cc-dd-ee-ff",
            channel_id=1,
            helper_builder=builder,
            helper_runner=runner,
        )

        summary = sender.send_packets(packets)

        self.assertEqual(builder.calls, 1)
        self.assertEqual(runner.helpers, [self.helper])
        self.assertEqual(
            runner.requests,
            [
                {
                    "device_mac": "AA:BB:CC:DD:EE:FF",
                    "channel_id": 1,
                    "packets": [packet.message.hex() for packet in packets],
                    "packet_gap_ms": 30,
                    "settle_ms": 500,
                }
            ],
        )
        self.assertEqual(summary["sender"], "macos-bluetooth")
        self.assertEqual(summary["device_mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(summary["device_channel"], 1)
        self.assertEqual(summary["packet_count"], len(packets))
        self.assertEqual(
            summary["bytes_sent"],
            sum(len(packet.message) for packet in packets),
        )
        self.assertEqual(summary["helper_app"], str(self.helper.app_path))

    def test_sender_surfaces_helper_errors(self) -> None:
        packets = self.rendered_packets()
        sender = MacOSBluetoothPacketSender(
            device_mac="AA:BB:CC:DD:EE:FF",
            channel_id=1,
            helper_builder=FakeHelperBuilder(self.helper),
            helper_runner=RecordingHelperRunner(
                response={"ok": False, "error": "Bluetooth permission denied"}
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "Bluetooth permission denied"):
            sender.send_packets(packets)

    def test_sender_rejects_oversized_packets_before_invoking_helper(self) -> None:
        oversized_packet = PixooPacket(
            command_name="set image",
            command_id=0x44,
            payload=b"",
            message=b"\x00" * 65536,
        )
        builder = FakeHelperBuilder(self.helper)
        runner = RecordingHelperRunner()
        sender = MacOSBluetoothPacketSender(
            device_mac="AA:BB:CC:DD:EE:FF",
            channel_id=1,
            helper_builder=builder,
            helper_runner=runner,
        )

        with self.assertRaisesRegex(
            ValueError,
            "Pixoo packet exceeds RFCOMM writeSync length limit",
        ):
            sender.send_packets([oversized_packet])

        self.assertEqual(builder.calls, 0)
        self.assertEqual(runner.requests, [])
