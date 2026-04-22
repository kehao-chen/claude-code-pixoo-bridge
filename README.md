# Claude Code Pixoo Bridge

Turn Claude Code into a **global ambient dashboard** on a Pixoo Max.

This project listens to Claude Code hook events and status snapshots, reduces
them into one global state, renders a tiny `32x32` Clawd scene, and sends it
to a Pixoo Max. The normal runtime is a **single local process** with **direct
macOS Bluetooth** delivery.

## What it does

- receives Claude Code hook events via `POST /hooks`
- receives Claude Code status snapshots via `POST /status`
- keeps a small **internal** per-source cache keyed by `session_id`
- derives **one global display state** across all active Claude Code sources
- renders a built-in Clawd pixel animation plus a compact usage band
- sends the result directly to Pixoo Max over macOS Bluetooth
- optionally forwards rendered payloads to a TCP debug proxy

## Architecture at a glance

```text
Claude Code hooks/statusLine
          |
          v
local relay scripts
  pixoo-hook.sh
  pixoo-statusline.sh
  pixoo-statusline-chain.sh
          |
          v
Claude Code Pixoo Bridge
  |- POST /hooks
  |- POST /status
  |- per-source cache (internal only)
  |- global reducer
  |- usage stabilizer / 5H zero debounce
  |- 32x32 Clawd renderer
          |
          v
Pixoo transport
  |- default: macOS Bluetooth helper
  |- optional: TCP debug proxy
          |
          v
Pixoo Max
```

The important design choice is: **the Pixoo is not bound to one session**.
Internally the bridge still uses `session_id` to correlate raw inputs, but the
display itself is a **global summary surface**.

## Display model

- top `25px`: animated Clawd mascot
- bottom `7px`: slightly dim white usage text on a solid band that matches Clawd's body color
- top-right dot: optional state light

State colors:

- cyan = working
- amber = needs approval
- green = done / waiting for input
- purple = thinking
- orange = unattended for more than 30 seconds
- red = error or unattended for more than 60 seconds

### Usage band rules

The bottom usage band follows the **most recently received status snapshot
globally**, not the currently selected top-state source.

Usage source priority:

1. `rate_limits.five_hour.used_percentage`
2. `context_window.used_percentage`
3. `rate_limits.seven_day.used_percentage`

Display rules:

- fractional values are truncated (`16.9` -> `16`)
- a single `5H = 0` update is treated as suspicious
- `0%` is only accepted after the same source reports `5H = 0` **twice in a row**
- Pixoo is only updated when the **final rendered output changes**, so metadata-only changes or `48.1 -> 48.2 -> 48%` cases do not trigger a resend

The Clawd style guide and display semantics live in
[`docs/display-states.md`](docs/display-states.md).

## Quick start

### 1. Install dependencies

```bash
uv sync --group dev
```

### 2. Create a config file

```bash
mkdir -p ~/.config/claude-code-pixoo-bridge
cp examples/pixoo-bridge.toml ~/.config/claude-code-pixoo-bridge/config.toml
```

Then edit at least:

```toml
device_mac = "AA:BB:CC:DD:EE:FF"
```

Useful display options:

```toml
brightness_percent = 5
usage_label = "S"
status_dot_enabled = true
# mascot_asset_path = "/absolute/path/to/asset.png"
```

Use `brightness_percent = 1` or `5` if you want the Pixoo to stay very dim.

### 3. Run the bridge

```bash
uv run pixoo-bridge
```

If `device_mac` is set, the bridge automatically uses direct
`macos-bluetooth`. Otherwise it falls back to the log transport.

### 4. Check health

```bash
curl -sS http://127.0.0.1:8765/healthz | jq
```

## Claude Code wiring

Copy the relay scripts into `~/.claude/`:

```bash
mkdir -p ~/.claude
cp examples/pixoo-hook.sh ~/.claude/pixoo-hook.sh
cp examples/pixoo-statusline.sh ~/.claude/pixoo-statusline.sh
cp examples/pixoo-statusline-chain.sh ~/.claude/pixoo-statusline-chain.sh
chmod +x ~/.claude/pixoo-hook.sh ~/.claude/pixoo-statusline.sh ~/.claude/pixoo-statusline-chain.sh
```

`examples/pixoo-statusline.sh` uses `jq`. The chain wrapper does not parse the
payload itself, so it does not require `jq`.

Then wire Claude Code settings.

### Option A: Pixoo owns the whole status line

Use `examples/claude-settings.local.json` directly as your starting point.

### Option B: keep your existing status line command

If you already use another status line command such as `ccstatusline`, use the
chain wrapper:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/pixoo-statusline-chain.sh npx -y ccstatusline@latest",
    "padding": 0
  }
}
```

That sends the same JSON payload to:

1. the Pixoo bridge
2. your existing status line renderer

## Local API

Main endpoints:

- `POST /hooks`
- `POST /status`
- `GET /healthz`

Debug endpoint:

- `GET /debug/state`

`/debug/state` exposes the internal per-source cache plus the currently selected
global scene. It is useful for inspection, but it is **not** the public display
model.

## Optional TCP debug proxy

The project still includes `tcp-proxy`, but only as an optional debug seam.

Run the bridge against the proxy:

```bash
uv run pixoo-bridge --transport tcp-proxy --proxy-host 127.0.0.1 --proxy-port 9001
```

Run the bundled proxy in another terminal:

```bash
uv run python -m pixoo_bridge.proxy --host 127.0.0.1 --port 9001
```

Use this when you want to inspect rendered payloads or test alternate sender
backends without touching the main direct-Bluetooth runtime.

## Development

Lint and tests:

```bash
uv run ruff check .
uv run python -m unittest discover -s tests -v
```

## Project docs

- [`docs/architecture.md`](docs/architecture.md) — runtime architecture and data flow
- [`docs/display-states.md`](docs/display-states.md) — display semantics and Clawd style guide
- [`docs/pixoo-max-integration.md`](docs/pixoo-max-integration.md) — transport and Pixoo integration notes
- [`examples/pixoo-bridge.toml`](examples/pixoo-bridge.toml) — starter config
