from __future__ import annotations

import unittest

from pixoo_bridge.proxy import PixooProxyApplication


class RecordingSender:
    def __init__(self) -> None:
        self.requests = []
        self.packets = []
        self.scene = None

    def send_packets(self, packets, *, scene=None) -> dict[str, object]:
        self.packets = list(packets)
        self.scene = scene
        self.requests.append({"packets": list(packets), "scene": scene})
        return {
            "packet_count": len(self.packets),
            "commands": [packet.command_name for packet in self.packets],
            "sender": "recording",
        }


class PixooProxyApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sender = RecordingSender()
        self.application = PixooProxyApplication(sender=self.sender)

    def test_handle_payload_returns_packet_summary(self) -> None:
        response = self.application.handle_payload(
            {
                "type": "present_scene",
                "scene": {
                    "kind": "running",
                    "detail": "18",
                    "footer": "5H 32.5%",
                },
                "rendering": {
                    "width": 32,
                    "height": 32,
                    "frames": [
                        {
                            "duration_ms": 0,
                            "palette": ["#000000", "#ffffff", "#ff0000", "#00ff00"],
                            "rows": ["0123" * 8 for _ in range(32)],
                        }
                    ],
                },
            }
        )

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["packet_count"], 1)
        self.assertEqual(response["commands"], ["set image"])
        self.assertEqual(response["sender"], "recording")
        self.assertEqual(self.sender.scene["kind"], "running")

    def test_handle_payload_includes_brightness_when_requested(self) -> None:
        response = self.application.handle_payload(
            {
                "type": "present_scene",
                "brightness_percent": 5,
                "scene": {
                    "kind": "running",
                    "detail": "18",
                    "footer": "5H 32.5%",
                },
                "rendering": {
                    "width": 32,
                    "height": 32,
                    "frames": [
                        {
                            "duration_ms": 0,
                            "palette": ["#000000", "#ffffff", "#ff0000", "#00ff00"],
                            "rows": ["0123" * 8 for _ in range(32)],
                        }
                    ],
                },
            }
        )

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["packet_count"], 2)
        self.assertEqual(response["commands"], ["set brightness", "set image"])
        self.assertEqual(
            self.sender.requests[0]["packets"][0].command_name,
            "set brightness",
        )

    def test_handle_payload_rejects_unknown_type(self) -> None:
        response = self.application.handle_payload({"type": "other"})

        self.assertEqual(response, {"ok": False, "error": "unsupported_request_type"})
