from __future__ import annotations

import json
import socketserver
import threading
import unittest
from datetime import datetime, timezone

from pixoo_bridge.bridge import (
    PacketSenderTransport,
    SceneKind,
    ScreenScene,
    TCPProxyTransport,
    TransportError,
)
from pixoo_bridge.rendering import SimplePixooRenderer


class RecordingPacketSender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[dict[str, object]] = []

    def send_packets(self, packets, *, scene=None) -> dict[str, object]:
        if self.error is not None:
            raise self.error
        request = {
            "packets": list(packets),
            "scene": scene,
        }
        self.requests.append(request)
        return {
            "sender": "recording",
            "packet_count": len(request["packets"]),
        }


class ThreadedTestProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(
        self, server_address: tuple[str, int], ack_payload: dict[str, object]
    ) -> None:
        super().__init__(server_address, TestProxyHandler)
        self.ack_payload = ack_payload
        self.messages: list[dict[str, object]] = []


class TestProxyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(65536)
        if not line:
            return

        payload = json.loads(line)
        self.server.messages.append(payload)  # type: ignore[attr-defined]
        self.wfile.write(
            json.dumps(
                self.server.ack_payload,  # type: ignore[attr-defined]
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )


class TCPProxyTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = SimplePixooRenderer()

    def start_proxy(
        self, ack_payload: dict[str, object]
    ) -> tuple[ThreadedTestProxy, threading.Thread]:
        proxy = ThreadedTestProxy(("127.0.0.1", 0), ack_payload)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        return proxy, thread

    def stop_proxy(self, proxy: ThreadedTestProxy, thread: threading.Thread) -> None:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)

    def test_tcp_proxy_transport_sends_scene(self) -> None:
        proxy, thread = self.start_proxy({"ok": True})
        host, port = proxy.server_address
        transport = TCPProxyTransport(host=host, port=port, brightness_percent=5)
        scene = ScreenScene(
            kind=SceneKind.RUNNING,
            detail="18",
            footer="5H 32.5%",
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
        rendered_scene = self.renderer.render(scene)

        try:
            emitted = transport.present(scene, rendered_scene)
        finally:
            self.stop_proxy(proxy, thread)

        self.assertTrue(emitted)
        self.assertEqual(len(proxy.messages), 1)
        request = proxy.messages[0]
        self.assertEqual(request["type"], "present_scene")
        self.assertEqual(request["schema_version"], 1)
        self.assertEqual(request["brightness_percent"], 5)
        self.assertEqual(request["scene"]["detail"], "18")
        self.assertNotIn("session_id", request["scene"])
        self.assertNotIn("headline", request["scene"])
        self.assertEqual(request["rendering"]["width"], 32)
        self.assertEqual(len(request["rendering"]["frames"]), 8)

    def test_tcp_proxy_transport_raises_on_rejected_ack(self) -> None:
        proxy, thread = self.start_proxy({"ok": False, "error": "proxy rejected"})
        host, port = proxy.server_address
        transport = TCPProxyTransport(host=host, port=port)
        scene = ScreenScene(
            kind=SceneKind.FAILURE,
            detail="demo",
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
        rendered_scene = self.renderer.render(scene)

        try:
            with self.assertRaises(TransportError):
                transport.present(scene, rendered_scene)
        finally:
            self.stop_proxy(proxy, thread)


class PacketSenderTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = SimplePixooRenderer()

    def test_packet_sender_transport_encodes_and_sends_scene(self) -> None:
        sender = RecordingPacketSender()
        transport = PacketSenderTransport(sender=sender, brightness_percent=5)
        scene = ScreenScene(
            kind=SceneKind.RUNNING,
            detail="18",
            footer="5H 32.5%",
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
        rendered_scene = self.renderer.render(scene)

        emitted = transport.present(scene, rendered_scene)

        self.assertTrue(emitted)
        self.assertEqual(len(sender.requests), 1)
        request = sender.requests[0]
        packets = request["packets"]
        assert isinstance(packets, list)
        self.assertGreater(len(packets), 1)
        self.assertEqual(packets[0].command_name, "set brightness")
        self.assertIn(
            "set animation frame",
            [packet.command_name for packet in packets],
        )
        self.assertEqual(request["scene"]["detail"], "18")
        self.assertNotIn("session_id", request["scene"])
        self.assertNotIn("headline", request["scene"])

    def test_packet_sender_transport_wraps_sender_errors(self) -> None:
        sender = RecordingPacketSender(error=RuntimeError("helper failed"))
        transport = PacketSenderTransport(sender=sender)
        scene = ScreenScene(
            kind=SceneKind.FAILURE,
            detail="demo",
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
        rendered_scene = self.renderer.render(scene)

        with self.assertRaisesRegex(TransportError, "helper failed"):
            transport.present(scene, rendered_scene)
