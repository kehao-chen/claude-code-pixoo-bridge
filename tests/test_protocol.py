from __future__ import annotations

import unittest

from pixoo_bridge.pixoo_protocol import PixooMaxProtocolAdapter
from pixoo_bridge.rendering import RenderedFrame, RenderedScene


class PixooMaxProtocolAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PixooMaxProtocolAdapter()

    def test_single_frame_encodes_set_image_message(self) -> None:
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

        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet.command_name, "set image")
        self.assertEqual(packet.command_id, 0x44)
        self.assertEqual(packet.message[0], 0x01)
        self.assertEqual(packet.message[-1], 0x02)
        self.assertEqual(packet.payload[2], 0x44)

    def test_multi_frame_encodes_animation_packets(self) -> None:
        rendered_scene = RenderedScene(
            width=32,
            height=32,
            frames=[
                RenderedFrame(
                    palette=["#000000", "#ffffff", "#ff0000", "#00ff00"],
                    rows=["0123" * 8 for _ in range(32)],
                    duration_ms=100,
                ),
                RenderedFrame(
                    palette=["#000000", "#ffffff", "#0000ff", "#00ff00"],
                    rows=["3210" * 8 for _ in range(32)],
                    duration_ms=100,
                ),
            ],
        )

        packets = self.adapter.encode_rendered_scene(rendered_scene)

        self.assertGreaterEqual(len(packets), 1)
        self.assertTrue(
            all(packet.command_name == "set animation frame" for packet in packets)
        )
        self.assertTrue(all(packet.command_id == 0x49 for packet in packets))
        self.assertTrue(all(packet.message[0] == 0x01 for packet in packets))
        self.assertTrue(all(packet.message[-1] == 0x02 for packet in packets))

    def test_encode_brightness_message(self) -> None:
        packet = self.adapter.encode_brightness(5)

        self.assertEqual(packet.command_name, "set brightness")
        self.assertEqual(packet.command_id, 0x74)
        self.assertEqual(packet.payload[2], 0x74)
        self.assertEqual(packet.payload[-1], 5)
        self.assertEqual(packet.message[0], 0x01)
        self.assertEqual(packet.message[-1], 0x02)

    def test_encode_brightness_rejects_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            self.adapter.encode_brightness(101)
