# Display States

## Primary goal

The display should answer a small set of questions at a glance:

1. **Does Claude Code need my attention?**
2. **Is something failing?**
3. **Across all active Claude Code sources, what is the most important state right now?**
4. **How full is the current short-term usage quota?**

## Current priority order

1. `PermissionRequest`
2. `StopFailure`
3. unattended source for more than 60 seconds
4. unattended source for more than 30 seconds
5. active thinking source
6. active working source
7. waiting source
8. idle summary

## State mapping

| Canonical state | Pixoo idea | Notes |
| --- | --- | --- |
| `attention` | amber status dot | `PermissionRequest` or approval prompt |
| `failure` | red status dot | `StopFailure` / non-interrupt tool failure |
| `unattended-warning` | orange status dot | no update for more than 30 seconds |
| `unattended-critical` | red status dot | no update for more than 60 seconds |
| `thinking` | purple status dot | after prompt submit or between tool phases |
| `running` | cyan status dot | active tool or work phase |
| `waiting` | green status dot | done or waiting for user input |
| `idle` | calm green dot | no active session |

## Layout

The current renderer is optimized for `32x32`:

- top `25px`: mascot area
- bottom `7px`: solid usage band
- top-right corner: animated status dot

Only the top-right status dot changes by state. The mascot itself keeps a fixed
color treatment across states, and the default mascot uses subtle pose changes
that are held for 2 frames at a time so the movement reads as calmer pixel-art
idle motion.

The status dot is independent from the mascot pose timing. It can run a finer
8-frame breathing / blink cycle while the mascot still holds each body pose for
2 frames.

If you want a cleaner mascot-only layout, `status_dot_enabled = false` disables
the status light entirely and leaves the top-right corner black.

The built-in default mascot is a hand-drawn pixel animation based on the Clawd
shape: rectangular dark-orange body, black eyes, side arms, and four moving
legs. The renderer can still load a user-supplied local mascot asset as an
override.

## Clawd style guide

Use the current built-in Clawd as the baseline design language for any future
idle, thinking, approval, error, or seasonal animation variants.

### Silhouette and proportions

- keep the mascot left-shifted inside the top `25px` area so the top-right dot
  still has breathing room
- main body: a wide rectangular block, currently `20x14`, with no outline
- right-side shadow: a darker vertical block on the right portion of the body
- arms: two `2x3` side blocks attached low on the body, starting slightly below
  the eye line
- eyes: two black vertical rectangles, currently `2x3` at `32x32` scale, with
  an `8px` clear gap between them
- legs: four short `2x4` legs starting directly under the body; the body should
  visually dominate more than the legs

### Color language

| Part | Current color | Notes |
| --- | --- | --- |
| background | `#000000` | always pure black |
| body | `#D87753` | dark orange base color |
| body shadow | `#BD6649` | right-side depth, not a separate outline |
| eyes | `#000000` | keep facial read simple and strong |
| usage band | `#D87753` | matches the Clawd body color in the built-in renderer |
| usage text | `#E6E6E6` | slightly dim white so the band reads softly at night |

### Motion language

- Clawd should feel alive, not excited or dance-like
- the body should mostly stay planted; prefer subtle weight shift over obvious
  travel
- arm movement should usually be `1px` vertical motion
- leg movement should usually be `1px` horizontal shift to imply stepping or
  balance transfer
- keep facial features stable during normal idle loops; save bigger face changes
  for explicit special animations
- preserve the chunky silhouette first, then add motion inside that silhouette

### Timing language

- default loop uses `4` unique poses
- each pose is held for `2` frames, producing `8` output frames total
- current frame duration is `160ms`
- use repeated holds and low-amplitude changes to make motion feel calm

### Rules for future animations

1. Preserve the same base body, eye spacing, and short-leg proportions unless a
   deliberate redesign is intended.
2. Prefer pose changes that read as breathing, swaying, or shifting weight
   instead of hopping, sliding, or bouncing.
3. If a new animation needs stronger emotion, change limbs first; only move the
   body mass more dramatically when the scene explicitly calls for it.
4. Keep the mascot readable even when the status dot is disabled.

## Usage choice

Use:

- `rate_limits.five_hour.used_percentage`
- fallback: `context_window.used_percentage`
- fallback: `rate_limits.seven_day.used_percentage`

This makes the default band track the shorter-term session quota first, while
still falling back to other available usage numbers if the 5-hour value is not
present.

This answers:

- how full the current short-term Claude Code usage quota is
- and, when needed, still falls back to context or weekly usage data

The bottom usage band is global rather than session-scoped: it follows the most
recently received status snapshot value, even if the mascot / state currently on
screen was selected from a different higher-priority session.

To reduce flicker from noisy quota reporting, a one-off `5H = 0` update is
treated as suspicious and ignored; `0%` is only accepted after the same source
reports `5H = 0` twice in a row.

For this version, drop the fractional part and render it as a simple integer
string like `18` or `64`, then show it as slightly dim white text in the bottom band with a
percent sign, for example:

- `S:21%`
- `SESS:21%`

In the built-in renderer, the band background matches the Clawd body color.
Glyphs use 1-pixel spacing so the numeric value and `%` symbol do not visually
merge together.

## Multi-source aggregation policy

If multiple Claude Code sources exist, the display still shows only one global
state at a time. Choose that state by priority:

1. any source that needs attention
2. else any failed source
3. else the most urgent unattended source
4. else the most recently updated thinking source
5. else the most recently updated working source
6. else the most recently updated waiting source

## Internal scene payload guidance

The renderer now only needs a few payload hints:

- `scene.kind` for the color / animation mode
- `scene.detail` for the usage number
- `scene.footer` for logs or debugging metadata
