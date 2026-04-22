from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from pixoo_bridge.macos_bluetooth_helper import (
    MacOSBluetoothHelperBuilder,
    SubprocessMacOSBluetoothHelperRunner,
)


@unittest.skipUnless(sys.platform == "darwin", "macOS-only helper bundle test")
@unittest.skipUnless(shutil.which("swiftc"), "swiftc is required to build the helper")
class MacOSBluetoothHelperBuilderTests(unittest.TestCase):
    def test_builder_creates_bundle_with_bluetooth_usage_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = MacOSBluetoothHelperBuilder(build_root=Path(tmpdir))
            runner = SubprocessMacOSBluetoothHelperRunner()

            built = builder.ensure_built()
            rebuilt = builder.ensure_built()

            self.assertEqual(rebuilt.app_path, built.app_path)
            self.assertTrue(built.app_path.exists())
            self.assertTrue(built.executable_path.exists())

            payload = runner.bundle_info(built)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["bundle_identifier"], built.bundle_identifier)
            self.assertEqual(
                Path(payload["bundle_path"]).resolve(),
                built.app_path.resolve(),
            )
            self.assertEqual(payload["has_bluetooth_usage_description"], True)
