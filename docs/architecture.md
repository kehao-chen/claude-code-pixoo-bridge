# Architecture

## Summary

This project is a **global Pixoo dashboard for Claude Code**, not a visual mirror
of one specific session.

Internally, the bridge still keeps a small per-source cache keyed by
`session_id` so it can correlate hooks and status snapshots correctly. That key
is an implementation detail, not the product model shown on the Pixoo.

## High-level flow

```text
Claude Code hooks/statusLine
          |
          v
local shell relay scripts
          |
          v
POST /hooks + POST /status
          |
          v
BridgeService
  |- per-source cache (internal only)
  |- global reducer
  |- usage stabilizer / 5H zero debounce
          |
          v
32x32 renderer
          |
          v
Pixoo transport
  |- default: macOS Bluetooth helper
  |- optional: TCP debug proxy
          |
          v
Pixoo Max
```

## Core components

### 1. Input relays

Claude Code cannot fan out one `statusLine` to multiple handlers directly, so
the repo provides small shell relays:

- `examples/pixoo-hook.sh`
- `examples/pixoo-statusline.sh`
- `examples/pixoo-statusline-chain.sh`

These forward JSON payloads to the local bridge.

### 2. Local bridge API

The bridge exposes a small localhost API:

- `POST /hooks`
- `POST /status`
- `GET /healthz`
- `GET /debug/state`

`/debug/state` is for inspection only. The main runtime path is still just
`/hooks` + `/status`.

### 3. Per-source cache

The bridge keeps one in-memory record per incoming Claude Code source. That
record stores:

- recent hook-derived state such as attention, failure, thinking, working, or waiting
- latest status-derived values such as context percentage, 5-hour quota, and weekly quota
- timestamps used for unattended detection

This cache exists only so the bridge can merge multiple raw input streams
correctly.

### 4. Global reducer

The reducer turns the per-source cache into one global scene for the Pixoo.

Important rules:

- attention beats failure
- failure beats unattended
- unattended beats thinking / working / waiting
- the top display state is chosen by priority across all active sources
- the bottom usage band is **global**, not tied to the selected top-state source
- transport updates are deduped by **rendered output**, not by internal state metadata

### 5. Usage selection

The bottom usage band follows the most recently received status snapshot and uses
this preference order:

1. `rate_limits.five_hour.used_percentage`
2. `context_window.used_percentage`
3. `rate_limits.seven_day.used_percentage`

The bridge truncates fractional values to integers for display.

To reduce flicker from noisy quota reporting, a single `5H = 0` update is
treated as suspicious. `0%` is only accepted after the same source reports
`5H = 0` twice in a row.

### 6. Renderer

The renderer converts the global scene into a compact `32x32` animation:

- top `25px`: Clawd mascot area
- bottom `7px`: solid usage band
- top-right status dot: optional, independently animated state indicator

The renderer is intentionally transport-agnostic.

### 7. Transport layer

The bridge currently supports:

- direct macOS Bluetooth delivery via the bundled helper app
- optional TCP proxy delivery for debugging or alternate sender experiments

The Bluetooth helper exists because the reliable macOS path required a real app
bundle with Bluetooth usage descriptions instead of raw terminal-process
RFCOMM access.

## Scene contract

The renderer / transport boundary uses a compact scene payload:

- `kind` — global state category
- `detail` — bottom usage number
- `footer` — small debugging / state hint
- `updated_at` — timestamp

The scene payload is intentionally **session-free**. Session IDs stay internal to
the bridge cache and debug state endpoint.
