from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image


@dataclass(frozen=True, slots=True)
class LoadedMascotAsset:
    width: int
    height: int
    rows: list[list[int | None]]
    palette: list[str]
    band_fill: str


@dataclass(frozen=True, slots=True)
class MascotPose:
    body_shift: int = 0
    left_arm_delta: int = 0
    right_arm_delta: int = 0
    leg_shifts: tuple[int, int, int, int] = (0, 0, 0, 0)
    duration_ms: int = 160


@dataclass(frozen=True, slots=True)
class DotPose:
    core_visible: bool = True
    halo_level: int = 0


@dataclass(slots=True)
class RenderedFrame:
    palette: list[str]
    rows: list[str]
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_ms": self.duration_ms,
            "palette": self.palette,
            "rows": self.rows,
        }


@dataclass(slots=True)
class RenderedScene:
    width: int
    height: int
    frames: list[RenderedFrame]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "frames": [frame.to_dict() for frame in self.frames],
        }


class SceneLike(Protocol):
    kind: Any
    detail: str
    footer: str


class PixooRenderer(Protocol):
    def render(self, scene: SceneLike) -> RenderedScene:
        ...


PIXOO_WIDTH = 32
PIXOO_HEIGHT = 32
USAGE_BAND_HEIGHT = 7
USAGE_BAND_Y = PIXOO_HEIGHT - USAGE_BAND_HEIGHT
GLYPH_WIDTH = 3
PALETTE_SYMBOLS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BACKGROUND_COLOR = "#000000"
TEXT_COLOR = "#E6E6E6"
DEFAULT_BODY_COLOR = "#D87753"
DEFAULT_BODY_SHADOW = "#BD6649"
DEFAULT_EYE_COLOR = "#000000"
DEFAULT_BAND_FILL = DEFAULT_BODY_COLOR
MAX_ASSET_COLORS = 24
MASCOT_MAX_WIDTH = 22
MASCOT_MAX_HEIGHT = 19

STATUS_DOT_COLORS = {
    "running": "#22D3EE",
    "attention": "#F59E0B",
    "thinking": "#A855F7",
    "waiting": "#22C55E",
    "stopped": "#22C55E",
    "idle": "#22C55E",
    "failure": "#DC2626",
    "unattended-warning": "#F97316",
    "unattended-critical": "#DC2626",
}

POSE_HOLD_FRAMES = 2

CLAWD_POSES = (
    MascotPose(),
    MascotPose(
        left_arm_delta=1,
        leg_shifts=(0, 0, 1, 0),
    ),
    MascotPose(),
    MascotPose(
        right_arm_delta=1,
        leg_shifts=(0, -1, 0, 0),
    ),
)

DOT_POSES = {
    "running": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
    ),
    "attention": (
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
    ),
    "thinking": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
    ),
    "waiting": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
    ),
    "stopped": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
    ),
    "idle": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=0),
    ),
    "failure": (
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
    ),
    "unattended-warning": (
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=2),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=True, halo_level=0),
        DotPose(core_visible=True, halo_level=1),
    ),
    "unattended-critical": (
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=True, halo_level=3),
        DotPose(core_visible=True, halo_level=1),
        DotPose(core_visible=False, halo_level=0),
        DotPose(core_visible=False, halo_level=0),
    ),
}

FONT_3X5 = {
    " ": ("000", "000", "000", "000", "000"),
    "%": ("101", "001", "010", "100", "101"),
    "+": ("000", "010", "111", "010", "000"),
    "-": ("000", "000", "111", "000", "000"),
    ".": ("000", "000", "000", "010", "010"),
    "/": ("001", "001", "010", "100", "100"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    ":": ("000", "010", "000", "010", "000"),
    "?": ("111", "001", "010", "000", "010"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "010", "001"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "_": ("000", "000", "000", "000", "111"),
}


class SimplePixooRenderer:
    def __init__(
        self,
        *,
        usage_label: str = "S",
        mascot_asset_path: str | None = None,
        status_dot_enabled: bool = True,
    ) -> None:
        normalized_label = usage_label.strip()
        if normalized_label not in {"S", "Sess"}:
            raise ValueError("usage_label must be either 'S' or 'Sess'")
        self._usage_label = normalized_label.upper()
        self._status_dot_enabled = status_dot_enabled
        self._asset = (
            self._load_mascot_asset(Path(mascot_asset_path).expanduser())
            if mascot_asset_path
            else None
        )
        self._band_fill = self._asset.band_fill if self._asset else DEFAULT_BAND_FILL

    def render(self, scene: SceneLike) -> RenderedScene:
        kind = getattr(scene.kind, "value", str(scene.kind))
        dot_color = STATUS_DOT_COLORS.get(kind, STATUS_DOT_COLORS["idle"])
        halo_color = darken_color(dot_color, factor=0.45)
        dot_poses = DOT_POSES.get(kind, DOT_POSES["idle"])
        mascot_poses = self._expanded_mascot_poses()
        frames: list[RenderedFrame] = []
        for mascot_pose, dot_pose in zip(mascot_poses, dot_poses, strict=True):
            frames.append(
                self._render_frame(
                    scene,
                    mascot_pose=mascot_pose,
                    dot_pose=dot_pose,
                    dot_color=dot_color,
                    halo_color=halo_color,
                )
            )
        return RenderedScene(width=PIXOO_WIDTH, height=PIXOO_HEIGHT, frames=frames)

    def _render_frame(
        self,
        scene: SceneLike,
        *,
        mascot_pose: MascotPose,
        dot_pose: DotPose,
        dot_color: str,
        halo_color: str,
    ) -> RenderedFrame:
        if self._asset is None:
            palette = [
                BACKGROUND_COLOR,
                self._band_fill,
                TEXT_COLOR,
                dot_color,
                halo_color,
                DEFAULT_BODY_COLOR,
                DEFAULT_BODY_SHADOW,
                DEFAULT_EYE_COLOR,
            ]
        else:
            palette = [
                BACKGROUND_COLOR,
                self._band_fill,
                TEXT_COLOR,
                dot_color,
                halo_color,
                *self._asset.palette,
            ]
        if len(palette) > len(PALETTE_SYMBOLS):
            raise ValueError("rendered palette exceeds Pixoo base36 row encoding limit")

        canvas = [[0 for _ in range(PIXOO_WIDTH)] for _ in range(PIXOO_HEIGHT)]
        self._draw_rect(
            canvas,
            x=0,
            y=USAGE_BAND_Y,
            width=PIXOO_WIDTH,
            height=USAGE_BAND_HEIGHT,
            color_index=1,
        )
        if self._status_dot_enabled:
            self._draw_status_dot(canvas, pose=dot_pose, core_index=3, halo_index=4)
        if self._asset is None:
            self._draw_default_clawd(canvas, pose=mascot_pose)
        else:
            self._draw_asset_mascot(canvas)
        self._draw_centered_text(
            canvas,
            self._format_usage_text(scene),
            y=26,
            color_index=2,
            scale=1,
            letter_spacing=1,
        )

        return RenderedFrame(
            palette=palette,
            rows=[
                "".join(PALETTE_SYMBOLS[pixel] for pixel in row)
                for row in canvas
            ],
            duration_ms=mascot_pose.duration_ms,
        )

    def _format_usage_text(self, scene: SceneLike) -> str:
        value = self._extract_usage_number(scene.detail, scene.footer)
        return f"{self._usage_label}:{value}%"

    def _extract_usage_number(self, *texts: str) -> str:
        for text in texts:
            match = re.search(r"(\d{1,3})", text or "")
            if match:
                return match.group(1)
        return "--"

    def _expanded_mascot_poses(self) -> list[MascotPose]:
        return [pose for pose in CLAWD_POSES for _ in range(POSE_HOLD_FRAMES)]

    def _load_mascot_asset(self, path: Path) -> LoadedMascotAsset:
        if not path.exists():
            raise FileNotFoundError(f"mascot asset does not exist: {path}")

        image = Image.open(path).convert("RGBA")
        content_bbox = self._content_bbox(image)
        cropped = image.crop(content_bbox)
        resized = self._resize_asset(cropped)
        quantized_rgb, alpha_mask = self._quantize_asset(resized)

        raw_palette = quantized_rgb.getpalette()
        rows: list[list[int | None]] = []
        used_colors: dict[int, str] = {}
        color_counts: dict[str, int] = {}
        for y in range(quantized_rgb.height):
            row: list[int | None] = []
            for x in range(quantized_rgb.width):
                if alpha_mask.getpixel((x, y)) <= 24:
                    row.append(None)
                    continue
                palette_index = int(quantized_rgb.getpixel((x, y)))
                color = "#{:02X}{:02X}{:02X}".format(
                    raw_palette[palette_index * 3],
                    raw_palette[(palette_index * 3) + 1],
                    raw_palette[(palette_index * 3) + 2],
                )
                if palette_index not in used_colors:
                    used_colors[palette_index] = color
                color_counts[color] = color_counts.get(color, 0) + 1
                row.append(palette_index)
            rows.append(row)

        palette_lookup = {old: new for new, old in enumerate(sorted(used_colors))}
        normalized_rows = [
            [None if pixel is None else palette_lookup[pixel] for pixel in row]
            for row in rows
        ]
        palette = [used_colors[index] for index in sorted(used_colors)]
        band_fill = self._select_band_fill_color(color_counts)
        return LoadedMascotAsset(
            width=quantized_rgb.width,
            height=quantized_rgb.height,
            rows=normalized_rows,
            palette=palette,
            band_fill=band_fill,
        )

    def _content_bbox(self, image: Image.Image) -> tuple[int, int, int, int]:
        min_x = image.width
        min_y = image.height
        max_x = -1
        max_y = -1

        for y in range(image.height):
            for x in range(image.width):
                red, green, blue, alpha = image.getpixel((x, y))
                if alpha <= 16:
                    continue
                if red >= 245 and green >= 245 and blue >= 245:
                    continue
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

        if max_x < min_x or max_y < min_y:
            alpha_bbox = image.getchannel("A").getbbox()
            if alpha_bbox is not None:
                return alpha_bbox
            return (0, 0, image.width, image.height)

        return (min_x, min_y, max_x + 1, max_y + 1)

    def _resize_asset(self, image: Image.Image) -> Image.Image:
        width_ratio = MASCOT_MAX_WIDTH / image.width
        height_ratio = MASCOT_MAX_HEIGHT / image.height
        scale = min(width_ratio, height_ratio, 1.0)
        target_size = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        return image.resize(target_size, Image.Resampling.LANCZOS)

    def _quantize_asset(self, image: Image.Image) -> tuple[Image.Image, Image.Image]:
        rgb_image = image.convert("RGB")
        alpha_mask = image.getchannel("A")
        quantized = rgb_image.quantize(
            colors=MAX_ASSET_COLORS,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        return quantized, alpha_mask

    def _select_band_fill_color(self, color_counts: dict[str, int]) -> str:
        filtered = {
            color: count
            for color, count in color_counts.items()
            if not self._is_near_white(color)
        }
        if filtered:
            return max(filtered.items(), key=lambda item: item[1])[0]
        if color_counts:
            return max(color_counts.items(), key=lambda item: item[1])[0]
        return DEFAULT_BAND_FILL

    def _is_near_white(self, color: str) -> bool:
        red = int(color[1:3], 16)
        green = int(color[3:5], 16)
        blue = int(color[5:7], 16)
        return red >= 225 and green >= 225 and blue >= 225

    def _draw_default_clawd(
        self,
        canvas: list[list[int]],
        *,
        pose: MascotPose,
    ) -> None:
        body = 5
        shadow = 6
        eye = 7
        body_x = 6 + pose.body_shift
        body_y = 5
        body_width = 20
        body_height = 14

        self._draw_rect(
            canvas,
            x=body_x,
            y=body_y,
            width=body_width,
            height=body_height,
            color_index=body,
        )
        self._draw_rect(
            canvas,
            x=body_x + 12,
            y=body_y,
            width=8,
            height=body_height,
            color_index=shadow,
        )

        self._draw_rect(
            canvas,
            x=body_x - 2,
            y=11 + pose.left_arm_delta,
            width=2,
            height=3,
            color_index=body,
        )
        self._draw_rect(
            canvas,
            x=body_x + body_width,
            y=11 + pose.right_arm_delta,
            width=2,
            height=3,
            color_index=shadow,
        )

        self._draw_rect(canvas, x=body_x + 4, y=9, width=2, height=3, color_index=eye)
        self._draw_rect(canvas, x=body_x + 14, y=9, width=2, height=3, color_index=eye)

        leg_positions = (body_x + 2, body_x + 6, body_x + 12, body_x + 16)
        for leg_index, base_x in enumerate(leg_positions):
            fill_index = body if leg_index < 2 else shadow
            self._draw_rect(
                canvas,
                x=base_x + pose.leg_shifts[leg_index],
                y=19,
                width=2,
                height=4,
                color_index=fill_index,
            )

    def _draw_asset_mascot(self, canvas: list[list[int]]) -> None:
        assert self._asset is not None
        start_x = max(1, (25 - self._asset.width) // 2)
        start_y = max(2, (USAGE_BAND_Y - self._asset.height) // 2)
        for y, row in enumerate(self._asset.rows):
            for x, pixel in enumerate(row):
                if pixel is None:
                    continue
                self._draw_point(
                    canvas,
                    x=start_x + x,
                    y=start_y + y,
                    color_index=5 + pixel,
                )

    def _draw_status_dot(
        self,
        canvas: list[list[int]],
        *,
        pose: DotPose,
        core_index: int,
        halo_index: int,
    ) -> None:
        center_x = 29
        center_y = 3
        for delta_x, delta_y in halo_points(pose.halo_level):
            self._draw_point(
                canvas,
                x=center_x + delta_x,
                y=center_y + delta_y,
                color_index=halo_index,
            )
        if pose.core_visible:
            for delta_y in range(-1, 2):
                for delta_x in range(-1, 2):
                    if abs(delta_x) + abs(delta_y) > 1:
                        continue
                    self._draw_point(
                        canvas,
                        x=center_x + delta_x,
                        y=center_y + delta_y,
                        color_index=core_index,
                    )

    def _draw_centered_text(
        self,
        canvas: list[list[int]],
        text: str,
        *,
        y: int,
        color_index: int,
        scale: int,
        letter_spacing: int,
    ) -> None:
        normalized = self._normalize_text(text)
        if not normalized:
            return
        width = self._measure_text(
            normalized,
            scale=scale,
            letter_spacing=letter_spacing,
        )
        x = max(0, (PIXOO_WIDTH - width) // 2)
        self._draw_text(
            canvas,
            normalized,
            x=x,
            y=y,
            color_index=color_index,
            scale=scale,
            letter_spacing=letter_spacing,
        )

    def _normalize_text(self, text: str) -> str:
        return "".join(
            char if char in FONT_3X5 else "?" for char in text.upper()[:9]
        )

    def _measure_text(self, text: str, *, scale: int, letter_spacing: int) -> int:
        if not text:
            return 0
        return (len(text) * GLYPH_WIDTH * scale) + ((len(text) - 1) * letter_spacing)

    def _draw_text(
        self,
        canvas: list[list[int]],
        text: str,
        *,
        x: int,
        y: int,
        color_index: int,
        scale: int,
        letter_spacing: int,
    ) -> None:
        cursor_x = x
        for char in text:
            glyph = FONT_3X5.get(char, FONT_3X5["?"])
            self._draw_glyph(
                canvas,
                glyph,
                x=cursor_x,
                y=y,
                color_index=color_index,
                scale=scale,
            )
            cursor_x += (GLYPH_WIDTH * scale) + letter_spacing

    def _draw_glyph(
        self,
        canvas: list[list[int]],
        glyph: tuple[str, ...],
        *,
        x: int,
        y: int,
        color_index: int,
        scale: int,
    ) -> None:
        for row_index, row in enumerate(glyph):
            for column_index, bit in enumerate(row):
                if bit != "1":
                    continue
                self._draw_rect(
                    canvas,
                    x=x + (column_index * scale),
                    y=y + (row_index * scale),
                    width=scale,
                    height=scale,
                    color_index=color_index,
                )

    def _draw_point(
        self, canvas: list[list[int]], *, x: int, y: int, color_index: int
    ) -> None:
        if 0 <= x < PIXOO_WIDTH and 0 <= y < PIXOO_HEIGHT:
            canvas[y][x] = color_index

    def _draw_rect(
        self,
        canvas: list[list[int]],
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        color_index: int,
    ) -> None:
        for row in range(max(0, y), min(PIXOO_HEIGHT, y + height)):
            for column in range(max(0, x), min(PIXOO_WIDTH, x + width)):
                canvas[row][column] = color_index


def darken_color(color: str, *, factor: float) -> str:
    normalized = color.strip().lstrip("#")
    red = round(int(normalized[0:2], 16) * factor)
    green = round(int(normalized[2:4], 16) * factor)
    blue = round(int(normalized[4:6], 16) * factor)
    return f"#{red:02X}{green:02X}{blue:02X}"


def halo_points(level: int) -> tuple[tuple[int, int], ...]:
    patterns = {
        0: (),
        1: ((0, -2), (-2, 0), (2, 0), (0, 2)),
        2: (
            (0, -2),
            (-2, 0),
            (2, 0),
            (0, 2),
            (-1, -1),
            (1, -1),
            (-1, 1),
            (1, 1),
        ),
        3: (
            (0, -2),
            (-2, 0),
            (2, 0),
            (0, 2),
            (-1, -1),
            (1, -1),
            (-1, 1),
            (1, 1),
            (-2, -1),
            (-1, -2),
            (1, -2),
            (2, -1),
            (-2, 1),
            (-1, 2),
            (1, 2),
            (2, 1),
        ),
    }
    return patterns[max(0, min(3, level))]
