from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from pixoo_bridge.runtime_config import load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def make_args(self, **overrides: object) -> SimpleNamespace:
        values = {
            "config": None,
            "host": None,
            "port": None,
            "transport": None,
            "proxy_host": None,
            "proxy_port": None,
            "proxy_connect_timeout": None,
            "proxy_ack_timeout": None,
            "device_mac": None,
            "device_channel": None,
            "bluetooth_packet_gap_ms": None,
            "bluetooth_settle_ms": None,
            "brightness_percent": None,
            "usage_label": None,
            "mascot_asset_path": None,
            "status_dot_enabled": None,
            "log_level": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_device_mac_in_config_defaults_to_macos_bluetooth(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                'device_mac = "AA:BB:CC:DD:EE:FF"\n',
                encoding="utf-8",
            )

            config = load_runtime_config(self.make_args(config=str(config_path)))

        self.assertEqual(config.transport, "macos-bluetooth")
        self.assertEqual(config.device_mac, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(config.config_path, config_path)

    def test_cli_transport_overrides_config_transport(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'transport = "macos-bluetooth"',
                        'device_mac = "AA:BB:CC:DD:EE:FF"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_runtime_config(
                self.make_args(
                    config=str(config_path),
                    transport="tcp-proxy",
                )
            )

        self.assertEqual(config.transport, "tcp-proxy")

    def test_macos_bluetooth_requires_device_mac(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                'transport = "macos-bluetooth"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "device_mac must be set"):
                load_runtime_config(self.make_args(config=str(config_path)))

    def test_cli_device_mac_enables_macos_bluetooth_without_config_file(self) -> None:
        with TemporaryDirectory() as tempdir:
            missing_path = Path(tempdir) / "missing.toml"
            with mock.patch.dict(
                "os.environ",
                {"PIXOO_BRIDGE_CONFIG": str(missing_path)},
                clear=False,
            ):
                config = load_runtime_config(
                    self.make_args(device_mac="aa-bb-cc-dd-ee-ff")
                )

        self.assertEqual(config.transport, "macos-bluetooth")
        self.assertEqual(config.device_mac, "aa-bb-cc-dd-ee-ff")

    def test_usage_label_asset_path_and_brightness_load_from_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        "brightness_percent = 5",
                        'usage_label = "Sess"',
                        'mascot_asset_path = "/tmp/clawd.png"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_runtime_config(self.make_args(config=str(config_path)))

        self.assertEqual(config.brightness_percent, 5)
        self.assertEqual(config.usage_label, "Sess")
        self.assertEqual(config.mascot_asset_path, "/tmp/clawd.png")

    def test_cli_brightness_percent_overrides_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                "brightness_percent = 5\n",
                encoding="utf-8",
            )

            config = load_runtime_config(
                self.make_args(
                    config=str(config_path),
                    brightness_percent=1,
                )
            )

        self.assertEqual(config.brightness_percent, 1)

    def test_brightness_percent_rejects_values_outside_range(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                "brightness_percent = 101\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "between 0 and 100"):
                load_runtime_config(self.make_args(config=str(config_path)))

    def test_status_dot_toggle_loads_from_config_and_cli(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                'status_dot_enabled = false\n',
                encoding="utf-8",
            )

            config = load_runtime_config(self.make_args(config=str(config_path)))
            overridden = load_runtime_config(
                self.make_args(
                    config=str(config_path),
                    status_dot_enabled=True,
                )
            )

        self.assertFalse(config.status_dot_enabled)
        self.assertTrue(overridden.status_dot_enabled)
