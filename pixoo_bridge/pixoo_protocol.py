from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .rendering import RenderedFrame, RenderedScene


@dataclass(slots=True)
class PixooPacket:
    command_name: str
    command_id: int
    payload: bytes
    message: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "command_name": self.command_name,
            "command_id": self.command_id,
            "payload_hex": self.payload.hex(),
            "message_hex": self.message.hex(),
        }


def normalize_brightness_percent(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("brightness_percent must be an integer")
    if not 0 <= value <= 100:
        raise ValueError("brightness_percent must be between 0 and 100")
    return value


class PixooMaxProtocolAdapter:
    COMMANDS = {
        "set brightness": 0x74,
        "set image": 0x44,
        "set animation frame": 0x49,
    }

    def __init__(self, *, chunk_size: int = 200, escape_payload: bool = False) -> None:
        self._chunk_size = chunk_size
        self._escape_payload = escape_payload

    def encode_rendered_scene(self, rendered_scene: RenderedScene) -> list[PixooPacket]:
        if rendered_scene.width != 32 or rendered_scene.height != 32:
            raise ValueError("Pixoo Max protocol adapter expects 32x32 rendered scenes")
        if not rendered_scene.frames:
            raise ValueError("rendered scene must include at least one frame")

        frames_count = len(rendered_scene.frames)
        encoded_frames = [
            self._make_frame(
                self._encode_frame(
                    frame,
                    width=rendered_scene.width,
                    height=rendered_scene.height,
                    frames_count=frames_count,
                )
            )
            for frame in rendered_scene.frames
        ]

        if frames_count == 1:
            frame_bytes, frame_length = encoded_frames[0]
            frame_part = self._make_frame_part(frame_length, -1, frame_bytes)
            return [self._make_packet("set image", frame_part)]

        encoded_frames = self._make_animation_prefix_frames() + encoded_frames
        frame_parts: list[int] = []
        total_frame_size = 0
        for frame_bytes, frame_length in encoded_frames:
            frame_parts.extend(frame_bytes)
            total_frame_size += frame_length

        packets = []
        for index, frame_chunk in enumerate(
            self._chunk_values(frame_parts, self._chunk_size)
        ):
            frame_part = self._make_frame_part(total_frame_size, index, frame_chunk)
            packets.append(self._make_packet("set animation frame", frame_part))
        return packets

    def encode_brightness(self, brightness_percent: int) -> PixooPacket:
        brightness = normalize_brightness_percent(brightness_percent)
        return self._make_packet("set brightness", [brightness])

    def _make_animation_prefix_frames(self) -> list[tuple[list[int], int]]:
        return [
            self._make_frame([0x00, 0x00, 0x05, 0x00, 0x00]),
            self._make_frame([0x00, 0x00, 0x06, 0x00, 0x00, 0x00]),
        ]

    def _make_packet(self, command_name: str, args: list[int]) -> PixooPacket:
        command_id = self.COMMANDS[command_name]
        payload = bytes(self._command_payload(command_id, args))
        message = bytes(self._make_message(list(payload)))
        return PixooPacket(
            command_name=command_name,
            command_id=command_id,
            payload=payload,
            message=message,
        )

    def _command_payload(self, command_id: int, args: list[int]) -> list[int]:
        length = len(args) + 3
        return list(length.to_bytes(2, byteorder="little")) + [command_id] + args

    def _make_message(self, payload: list[int]) -> list[int]:
        checksum_payload = payload + self._checksum(payload)
        escaped_payload = self._escape(checksum_payload)
        return [0x01] + escaped_payload + [0x02]

    def _checksum(self, payload: list[int]) -> list[int]:
        total = sum(payload)
        checksum_size = 4 if total >= 65535 else 2
        return list(total.to_bytes(checksum_size, byteorder="little"))

    def _escape(self, payload: list[int]) -> list[int]:
        if not self._escape_payload:
            return payload
        escaped = []
        for value in payload:
            escaped.extend(
                [0x03, value + 0x03] if value in range(0x01, 0x04) else [value]
            )
        return escaped

    def _make_frame(self, frame: list[int]) -> tuple[list[int], int]:
        length = len(frame) + 3
        header = [0xAA] + list(length.to_bytes(2, byteorder="little"))
        return header + frame, length

    def _make_frame_part(
        self, length_sum: int, index: int, frame_part: list[int]
    ) -> list[int]:
        if index >= 0:
            header = list(length_sum.to_bytes(4, byteorder="little"))
            header += list(index.to_bytes(2, byteorder="little"))
            return header + frame_part
        return [0x00, 0x0A, 0x0A, 0x04] + frame_part

    def _encode_frame(
        self,
        frame: RenderedFrame,
        *,
        width: int,
        height: int,
        frames_count: int,
    ) -> list[int]:
        colors = [self._parse_hex_color(color) for color in frame.palette]
        if not colors:
            raise ValueError("rendered frame palette must not be empty")

        pixels = self._parse_rows(frame.rows, palette_size=len(colors))
        if len(pixels) != width * height:
            raise ValueError("rendered frame rows do not match declared dimensions")

        duration_ms = frame.duration_ms if frames_count > 1 else 0
        time_code = list(duration_ms.to_bytes(2, byteorder="little"))
        palette_flag = [0x03]
        color_count = len(colors)
        if color_count >= (width * height):
            color_count = 0

        payload = time_code + palette_flag + list(
            color_count.to_bytes(2, byteorder="little")
        )
        for color in colors:
            payload.extend(color)
        payload.extend(self._pack_pixels(pixels, color_count=len(colors)))
        return payload

    def _parse_rows(self, rows: list[str], *, palette_size: int) -> list[int]:
        pixels = []
        for row in rows:
            for char in row.strip():
                pixel = int(char, 36)
                if pixel >= palette_size:
                    raise ValueError(
                        "rendered frame row references palette index out of range"
                    )
                pixels.append(pixel)
        return pixels

    def _pack_pixels(self, pixels: list[int], *, color_count: int) -> list[int]:
        bits_per_pixel = math.ceil(math.log(color_count) / math.log(2))
        if bits_per_pixel == 0:
            bits_per_pixel = 1

        pixel_string = ""
        for pixel in pixels:
            pixel_bits = f"{pixel:b}".zfill(8)
            pixel_string += pixel_bits[::-1][:bits_per_pixel]

        result = []
        for chunk in self._chunk_bits(pixel_string, 8):
            result.append(int(chunk[::-1], 2))
        return result

    def _parse_hex_color(self, color: str) -> list[int]:
        normalized = color.strip().lstrip("#")
        if len(normalized) != 6:
            raise ValueError(f"invalid RGB hex color: {color}")
        return [int(normalized[index : index + 2], 16) for index in (0, 2, 4)]

    def _chunk_bits(self, values: str, size: int) -> list[str]:
        return [values[index : index + size] for index in range(0, len(values), size)]

    def _chunk_values(self, values: Iterable[int], size: int) -> list[list[int]]:
        values_list = list(values)
        return [
            values_list[index : index + size]
            for index in range(0, len(values_list), size)
        ]
