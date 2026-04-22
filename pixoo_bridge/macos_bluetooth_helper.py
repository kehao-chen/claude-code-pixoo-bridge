from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Protocol

_HELPER_APP_NAME = "PixooBluetoothHelper.app"
_HELPER_EXECUTABLE_NAME = "PixooBluetoothHelper"
_HELPER_SOURCE_NAME = "macos_bluetooth_helper.swift"
_BUILD_INFO_NAME = "build-info.json"
_DEFAULT_BUNDLE_IDENTIFIER = (
    "io.github.copilot.claude-code-pixoo-bridge.bluetooth-helper"
)
_DEFAULT_USAGE_DESCRIPTION = (
    "Claude Code Pixoo Bridge sends rendered Pixoo Max frames to your Pixoo "
    "device over Bluetooth Classic."
)
_DEFAULT_BUILD_TIMEOUT_SECONDS = 120.0
_DEFAULT_RUN_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class BuiltMacOSBluetoothHelper:
    app_path: Path
    executable_path: Path
    bundle_identifier: str
    usage_description: str


class MacOSBluetoothHelperBundleProvider(Protocol):
    def ensure_built(self) -> BuiltMacOSBluetoothHelper:
        ...


class MacOSBluetoothHelperRunner(Protocol):
    def run(
        self,
        helper: BuiltMacOSBluetoothHelper,
        request: dict[str, object],
    ) -> dict[str, object]:
        ...

    def bundle_info(self, helper: BuiltMacOSBluetoothHelper) -> dict[str, object]:
        ...


def default_helper_build_root() -> Path:
    configured = os.environ.get("PIXOO_BRIDGE_MACOS_HELPER_DIR")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Caches"
        / "claude-code-pixoo-bridge"
        / "macos-bluetooth-helper"
    )


def _helper_info_plist(
    *,
    bundle_identifier: str,
    usage_description: str,
) -> str:
    escaped_bundle_identifier = bundle_identifier.replace("&", "&amp;")
    escaped_usage_description = usage_description.replace("&", "&amp;")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>{_HELPER_EXECUTABLE_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>{escaped_bundle_identifier}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>{_HELPER_EXECUTABLE_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>NSBluetoothAlwaysUsageDescription</key>
  <string>{escaped_usage_description}</string>
  <key>NSBluetoothPeripheralUsageDescription</key>
  <string>{escaped_usage_description}</string>
</dict>
</plist>
"""


class MacOSBluetoothHelperBuilder:
    def __init__(
        self,
        *,
        build_root: Path | None = None,
        swiftc_path: str = "swiftc",
        codesign_path: str = "codesign",
        bundle_identifier: str = _DEFAULT_BUNDLE_IDENTIFIER,
        usage_description: str = _DEFAULT_USAGE_DESCRIPTION,
        build_timeout: float = _DEFAULT_BUILD_TIMEOUT_SECONDS,
    ) -> None:
        self._build_root = (build_root or default_helper_build_root()).expanduser()
        self._swiftc_path = swiftc_path
        self._codesign_path = codesign_path
        self._bundle_identifier = bundle_identifier
        self._usage_description = usage_description
        self._build_timeout = build_timeout

    def ensure_built(self) -> BuiltMacOSBluetoothHelper:
        if sys.platform != "darwin":
            raise RuntimeError(
                "macOS Bluetooth helper is only available when running on macOS"
            )

        source_text = self._read_source_text()
        fingerprint = self._build_fingerprint(source_text)
        app_path = self._app_path()
        executable_path = self._executable_path(app_path)

        if self._existing_build_matches(app_path, executable_path, fingerprint):
            return BuiltMacOSBluetoothHelper(
                app_path=app_path,
                executable_path=executable_path,
                bundle_identifier=self._bundle_identifier,
                usage_description=self._usage_description,
            )

        return self._build(source_text=source_text, fingerprint=fingerprint)

    def _build(
        self,
        *,
        source_text: str,
        fingerprint: str,
    ) -> BuiltMacOSBluetoothHelper:
        self._build_root.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(
            tempfile.mkdtemp(prefix="pixoo-helper-", dir=str(self._build_root))
        )
        tmp_app_path = tmp_root / _HELPER_APP_NAME
        try:
            contents_path = tmp_app_path / "Contents"
            macos_path = contents_path / "MacOS"
            resources_path = contents_path / "Resources"
            macos_path.mkdir(parents=True, exist_ok=True)
            resources_path.mkdir(parents=True, exist_ok=True)

            executable_path = self._executable_path(tmp_app_path)
            source_path = tmp_root / _HELPER_SOURCE_NAME
            source_path.write_text(source_text, encoding="utf-8")
            (contents_path / "Info.plist").write_text(
                _helper_info_plist(
                    bundle_identifier=self._bundle_identifier,
                    usage_description=self._usage_description,
                ),
                encoding="utf-8",
            )

            self._compile_helper(
                source_path=source_path,
                executable_path=executable_path,
            )
            self._ad_hoc_sign_if_available(tmp_app_path)
            (resources_path / _BUILD_INFO_NAME).write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "bundle_identifier": self._bundle_identifier,
                        "usage_description": self._usage_description,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            app_path = self._app_path()
            if app_path.exists():
                shutil.rmtree(app_path)
            shutil.move(str(tmp_app_path), str(app_path))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

        return BuiltMacOSBluetoothHelper(
            app_path=app_path,
            executable_path=self._executable_path(app_path),
            bundle_identifier=self._bundle_identifier,
            usage_description=self._usage_description,
        )

    def _compile_helper(
        self,
        *,
        source_path: Path,
        executable_path: Path,
    ) -> None:
        try:
            result = subprocess.run(
                [
                    self._swiftc_path,
                    "-O",
                    str(source_path),
                    "-o",
                    str(executable_path),
                    "-framework",
                    "Foundation",
                    "-framework",
                    "IOBluetooth",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=self._build_timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "swiftc is required to build the macOS Bluetooth helper"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "timed out while building the macOS Bluetooth helper"
            ) from exc

        if result.returncode == 0:
            return

        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "unknown swiftc error"
        )
        raise RuntimeError(f"failed building the macOS Bluetooth helper: {message}")

    def _ad_hoc_sign_if_available(self, app_path: Path) -> None:
        codesign_path = shutil.which(self._codesign_path)
        if codesign_path is None:
            return

        result = subprocess.run(
            [
                codesign_path,
                "--force",
                "--deep",
                "--sign",
                "-",
                str(app_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=self._build_timeout,
        )
        if result.returncode == 0:
            return

        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "unknown codesign error"
        )
        raise RuntimeError(f"failed signing the macOS Bluetooth helper: {message}")

    def _existing_build_matches(
        self,
        app_path: Path,
        executable_path: Path,
        fingerprint: str,
    ) -> bool:
        build_info_path = app_path / "Contents" / "Resources" / _BUILD_INFO_NAME
        if not executable_path.exists() or not build_info_path.exists():
            return False
        try:
            build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        return (
            isinstance(build_info, dict)
            and build_info.get("fingerprint") == fingerprint
            and build_info.get("bundle_identifier") == self._bundle_identifier
            and build_info.get("usage_description") == self._usage_description
        )

    def _build_fingerprint(self, source_text: str) -> str:
        payload = json.dumps(
            {
                "bundle_identifier": self._bundle_identifier,
                "usage_description": self._usage_description,
                "source_text": source_text,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _read_source_text(self) -> str:
        return (
            resources.files("pixoo_bridge")
            .joinpath(_HELPER_SOURCE_NAME)
            .read_text(encoding="utf-8")
        )

    def _app_path(self) -> Path:
        return self._build_root / _HELPER_APP_NAME

    def _executable_path(self, app_path: Path) -> Path:
        return app_path / "Contents" / "MacOS" / _HELPER_EXECUTABLE_NAME


class SubprocessMacOSBluetoothHelperRunner:
    def __init__(
        self,
        *,
        open_path: str = "open",
        timeout: float = _DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> None:
        self._open_path = open_path
        self._timeout = timeout

    def run(
        self,
        helper: BuiltMacOSBluetoothHelper,
        request: dict[str, object],
    ) -> dict[str, object]:
        return self._run_helper(helper, request=request)

    def bundle_info(self, helper: BuiltMacOSBluetoothHelper) -> dict[str, object]:
        return self._run_helper(helper, request=None, bundle_info=True)

    def _run_helper(
        self,
        helper: BuiltMacOSBluetoothHelper,
        *,
        request: dict[str, object] | None,
        bundle_info: bool = False,
    ) -> dict[str, object]:
        if bundle_info and request is not None:
            raise ValueError("bundle_info mode cannot include a request payload")

        if not helper.app_path.exists():
            raise RuntimeError(
                f"macOS Bluetooth helper app not found: {helper.app_path}"
            )

        with tempfile.TemporaryDirectory(prefix="pixoo-helper-run-") as tmpdir:
            tmpdir_path = Path(tmpdir)
            response_path = tmpdir_path / "response.json"
            args = [self._open_path, "-W", "-n", str(helper.app_path), "--args"]
            if bundle_info:
                args.append("--bundle-info")
            if request is not None:
                request_path = tmpdir_path / "request.json"
                request_path.write_text(
                    json.dumps(request, sort_keys=True),
                    encoding="utf-8",
                )
                args.extend(["--request-file", str(request_path)])
            args.extend(["--response-file", str(response_path)])

            try:
                result = subprocess.run(
                    args,
                    capture_output=True,
                    check=False,
                    timeout=self._timeout,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("macOS 'open' command is required") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "timed out while waiting for the macOS Bluetooth helper"
                ) from exc

            stdout_text = result.stdout.strip()
            stderr_text = result.stderr.strip()
            response = self._read_response_file(response_path)

        if result.returncode != 0:
            if response and isinstance(response.get("error"), str):
                raise RuntimeError(str(response["error"]))
            detail = stderr_text or stdout_text or f"exit code {result.returncode}"
            raise RuntimeError(f"macOS Bluetooth helper failed: {detail}")

        if response and response.get("ok") is False:
            error = response.get("error")
            if isinstance(error, str) and error:
                raise RuntimeError(error)
            raise RuntimeError(
                "macOS Bluetooth helper returned an unsuccessful response"
            )

        return response or {}

    def _read_response_file(self, response_path: Path) -> dict[str, object] | None:
        if not response_path.exists():
            return None
        try:
            parsed = json.loads(response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "macOS Bluetooth helper returned invalid JSON: "
                f"{response_path.read_text(encoding='utf-8', errors='replace')[:200]}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"failed reading macOS Bluetooth helper response: {response_path}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("macOS Bluetooth helper returned a non-object response")
        return parsed
