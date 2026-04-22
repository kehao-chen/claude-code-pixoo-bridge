from __future__ import annotations

import atexit
import json
import logging
import socket
import threading
from typing import Protocol, Sequence

from .macos_bluetooth_helper import (
    MacOSBluetoothHelperBuilder,
    MacOSBluetoothHelperBundleProvider,
    MacOSBluetoothHelperRunner,
    SubprocessMacOSBluetoothHelperRunner,
)
from .pixoo_protocol import PixooPacket


class PixooPacketSender(Protocol):
    def send_packets(
        self,
        packets: Sequence[PixooPacket],
        *,
        scene: dict[str, object] | None = None,
    ) -> dict[str, object]:
        ...


def summarize_commands(packets: Sequence[PixooPacket]) -> list[str]:
    commands: list[str] = []
    for packet in packets:
        if packet.command_name not in commands:
            commands.append(packet.command_name)
    return commands


def parse_mac_address(value: str) -> bytes:
    normalized = value.strip().replace(":", "").replace("-", "")
    if len(normalized) != 12:
        raise ValueError("device MAC must contain exactly 12 hex characters")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("device MAC must be hexadecimal") from exc


def normalize_mac_address(value: str) -> str:
    return ":".join(f"{octet:02X}" for octet in parse_mac_address(value))


class PrintingPacketSender:
    def __init__(self, sender_logger: logging.Logger | None = None) -> None:
        self._logger = sender_logger or logging.getLogger("pixoo_bridge.proxy")

    def send_packets(
        self,
        packets: Sequence[PixooPacket],
        *,
        scene: dict[str, object] | None = None,
    ) -> dict[str, object]:
        commands = summarize_commands(packets)
        print(
            json.dumps(
                {
                    "scene": scene,
                    "packet_count": len(packets),
                    "commands": commands,
                    "packets": [packet.to_dict() for packet in packets],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        summary = {
            "packet_count": len(packets),
            "commands": commands,
            "sender": "print",
        }
        self._logger.info(
            "packet_summary=%s",
            json.dumps(summary, sort_keys=True, separators=(",", ":")),
        )
        return summary


class MacOSBluetoothPacketSender:
    def __init__(
        self,
        *,
        device_mac: str,
        channel_id: int = 1,
        packet_gap_ms: int = 30,
        settle_ms: int = 500,
        sender_logger: logging.Logger | None = None,
        helper_builder: MacOSBluetoothHelperBundleProvider | None = None,
        helper_runner: MacOSBluetoothHelperRunner | None = None,
    ) -> None:
        if not 0 < channel_id < 256:
            raise ValueError("RFCOMM channel must be between 1 and 255")
        if packet_gap_ms < 0:
            raise ValueError("Bluetooth packet gap must be zero or greater")
        if settle_ms < 0:
            raise ValueError("Bluetooth settle time must be zero or greater")

        self._device_mac = normalize_mac_address(device_mac)
        self._channel_id = channel_id
        self._packet_gap_ms = packet_gap_ms
        self._settle_ms = settle_ms
        self._logger = sender_logger or logging.getLogger("pixoo_bridge.proxy")
        self._helper_builder = helper_builder or MacOSBluetoothHelperBuilder()
        self._helper_runner = helper_runner or SubprocessMacOSBluetoothHelperRunner()

    def send_packets(
        self,
        packets: Sequence[PixooPacket],
        *,
        scene: dict[str, object] | None = None,
    ) -> dict[str, object]:
        commands = summarize_commands(packets)
        packet_payloads: list[str] = []
        for packet in packets:
            if len(packet.message) > 0xFFFF:
                raise ValueError("Pixoo packet exceeds RFCOMM writeSync length limit")
            packet_payloads.append(packet.message.hex())

        helper = self._helper_builder.ensure_built()
        response = self._helper_runner.run(
            helper,
            {
                "device_mac": self._device_mac,
                "channel_id": self._channel_id,
                "packets": packet_payloads,
                "packet_gap_ms": self._packet_gap_ms,
                "settle_ms": self._settle_ms,
            },
        )
        if response.get("ok") is False:
            error = response.get("error")
            if isinstance(error, str) and error:
                raise RuntimeError(error)
            raise RuntimeError(
                "macOS Bluetooth helper returned an unsuccessful response"
            )

        summary = {
            "packet_count": len(packets),
            "commands": commands,
            "sender": "macos-bluetooth",
            "device_mac": self._device_mac,
            "device_channel": self._channel_id,
            "helper_app": str(helper.app_path),
        }
        if isinstance(response.get("bytes_sent"), int):
            summary["bytes_sent"] = response["bytes_sent"]
        self._logger.info(
            "packet_summary=%s",
            json.dumps(summary, sort_keys=True, separators=(",", ":")),
        )
        return summary

    def close(self) -> None:
        return


class DivoomProxyPacketSender:
    def __init__(
        self,
        *,
        host: str,
        device_mac: str,
        device_port: int = 1,
        upstream_port: int = 7777,
        socket_timeout: float = 3.0,
        sender_logger: logging.Logger | None = None,
    ) -> None:
        normalized_host = host.strip()
        if not normalized_host:
            raise ValueError("upstream host must be a non-empty string")
        if not 0 < device_port < 256:
            raise ValueError("device port must be between 1 and 255")
        if not 0 < upstream_port < 65536:
            raise ValueError("upstream port must be between 1 and 65535")

        self._host = normalized_host
        self._device_mac = parse_mac_address(device_mac)
        self._device_port = device_port
        self._upstream_port = upstream_port
        self._socket_timeout = socket_timeout
        self._logger = sender_logger or logging.getLogger("pixoo_bridge.proxy")
        self._lock = threading.RLock()
        self._socket: socket.socket | None = None
        atexit.register(self.close)

    def send_packets(
        self,
        packets: Sequence[PixooPacket],
        *,
        scene: dict[str, object] | None = None,
    ) -> dict[str, object]:
        commands = summarize_commands(packets)
        with self._lock:
            self._ensure_connected_locked()
            try:
                assert self._socket is not None
                for packet in packets:
                    self._socket.sendall(packet.message)
            except OSError as exc:
                self._close_locked()
                raise RuntimeError(
                    "failed sending Pixoo packets to "
                    f"{self._host}:{self._upstream_port}: {exc}"
                ) from exc

        summary = {
            "packet_count": len(packets),
            "commands": commands,
            "sender": "divoom-proxy",
            "upstream": f"{self._host}:{self._upstream_port}",
        }
        self._logger.info(
            "packet_summary=%s",
            json.dumps(summary, sort_keys=True, separators=(",", ":")),
        )
        return summary

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _ensure_connected_locked(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection(
                (self._host, self._upstream_port), timeout=self._socket_timeout
            )
            self._socket.settimeout(self._socket_timeout)
            self._socket.sendall(
                bytes([0x69]) + self._device_mac + bytes([self._device_port])
            )
        except OSError as exc:
            self._close_locked()
            raise RuntimeError(
                "failed connecting to Divoom proxy "
                f"{self._host}:{self._upstream_port}: {exc}"
            ) from exc

    def _close_locked(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.sendall(bytes([0x96]) + self._device_mac)
        except OSError:
            pass
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()
        self._socket = None
