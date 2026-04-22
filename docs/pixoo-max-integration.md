# Pixoo Max Integration

## What matters for this project

Pixoo Max is the **output device**. The local bridge decides what to draw, then sends that to the device.

## Practical transport options

### Option A: direct macOS Bluetooth Classic

Pros:

- no extra hardware
- cleanest final architecture
- now the primary runtime path for this project

Cons:

- Python's Linux-style RFCOMM socket path is not available on this macOS environment
- likely requires macOS-native APIs such as `IOBluetoothRFCOMMChannel`
- implementation complexity is higher

### Option B: proxy / TCP path

Pros:

- useful for transport debugging and inspection
- keeps a plain TCP seam when you want to isolate bridge rendering from packet delivery

Cons:

- needs an external proxy if you do not implement your own macOS bridge-to-Bluetooth transport

### Recommendation

Keep a clean transport abstraction, but treat direct macOS Bluetooth as the
normal path and TCP proxy as a debug tool:

- `PixooTransport.send_image(...)`
- `PixooTransport.send_text(...)`
- `PixooTransport.show_alert(...)`

Concrete paths now are:

- default: direct macOS Bluetooth
- optional: `TCPProxyTransport` for debugging

## Current sender options

The bridge now supports:

- `--transport log`
- `--transport macos-bluetooth`
- `--transport tcp-proxy`

If `brightness_percent` is configured, the bridge also sends Pixoo's
`set brightness` command during each scene update. This works on the direct
Bluetooth path and is forwarded through the optional TCP proxy path as well.

When `device_mac` is present in the bridge config, `pixoo-bridge` defaults to
`macos-bluetooth` automatically.

The optional local proxy still supports three concrete delivery modes:

- `--sender print`
- `--sender divoom-proxy`
- `--sender macos-bluetooth`

Both the direct bridge path and the optional proxy-side `--sender
macos-bluetooth` use the same tiny bundled helper app that calls
`IOBluetoothRFCOMMChannel`.

That helper is built on demand with `swiftc`, includes
`NSBluetoothAlwaysUsageDescription`, and is launched from its `.app` bundle so
macOS TCC sees a real app via LaunchServices instead of attributing Bluetooth
access to the plain CLI / terminal process chain.

## Optional TCP proxy path

The bridge still includes `TCPProxyTransport`, but only as an optional debug
path.

- transport is plain TCP from the bridge's point of view
- one scene update is sent as one newline-delimited JSON object
- the proxy must answer with one newline-delimited JSON acknowledgement
- the message now includes both high-level scene metadata and a rendered 32x32 payload

Request shape:

```json
{
  "schema_version": 1,
  "type": "present_scene",
  "sent_at": "2026-04-21T02:00:00+00:00",
  "brightness_percent": 5,
  "scene": {
    "kind": "running",
    "detail": "32",
    "footer": "5H 32.5%",
    "updated_at": "2026-04-21T02:00:00+00:00"
  },
  "rendering": {
    "width": 32,
    "height": 32,
    "frames": [
      {
        "duration_ms": 0,
        "palette": ["#00131A", "#D2F7FF", "#005F73", "#00C2FF"],
        "rows": ["2222...", "... 32 rows total ..."]
      }
    ]
  }
}
```

The scene metadata is intentionally session-free. `session_id` remains an
internal correlation key inside the bridge, but the emitted Pixoo scene is a
global summary payload.

Acknowledgement shape:

```json
{
  "ok": true,
  "packet_count": 1,
  "commands": ["set image"],
  "sender": "print"
}
```

That transport is intentionally bridge-owned rather than Divoom-owned. It is
still useful as a debug seam, while leaving the proxy free to evolve into:

- a render-only adapter that turns compact rows/palette data into Pixoo protocol packets
- an ESP32 / TCP-to-Bluetooth forwarder
- a macOS helper process with native Bluetooth APIs
- another adapter that speaks the Divoom/Pixoo protocol

## Current ownership decision

The current implementation now chooses:

- **bridge owns scene selection, 32x32 compact rendering, Pixoo packet generation, and the default direct Bluetooth delivery**
- **proxy is optional and exists for debug or alternate sender experiments**

That keeps the main runtime single-service while still making it easy to swap:

- a mock proxy that only prints packet bytes
- a real proxy that sends Pixoo packets over an auto-built macOS Bluetooth Classic helper app
- a real proxy that forwards Pixoo packets to a Divoom-compatible upstream
- a helper process that talks to another Divoom-compatible bridge

## What can be reused conceptually

The current repo already contains useful Divoom/Pixoo protocol work:

- `custom_components/divoom/devices/divoom.py`
- `custom_components/divoom/devices/pixoomax.py`

These files already cover:

- message framing
- image/text processing
- Pixoo Max-specific frame handling

## Pixoo Max-specific notes

### 1. Channel

The observed working channel for Pixoo Max is typically **channel 1**.

### 2. Display size

Pixoo Max is **32x32**.

That means the renderer should design for:

- extremely low text density
- strong iconography
- short labels
- compact bars / big digits

### 3. Image reliability

From earlier issue review:

- Pixoo Max is happiest on its GIF/animation path
- static image handling can be less forgiving than on some other Divoom devices

For a first useful project, text rendering or small generated frame animations may be more reliable than arbitrary image uploads.

### 4. Proxy compatibility

If a Divoom proxy is used later, keep in mind:

- `esp32-divoom` should be **v1.1.0+**
- earlier versions had Pixoo Max-related packet/chunk handling problems

## Suggested integration boundary

Break Pixoo handling into 3 layers:

### A. state -> scene

Example:

- `attention-needed + 32%` -> alert icon + `32%`

### B. scene -> pixels / frames

Renderer outputs:

- one static frame
- or a tiny animation sequence

### C. frames -> device transport

Protocol + transport send the result to Pixoo Max.

This separation keeps the display logic independent from the Bluetooth/proxy choice.
