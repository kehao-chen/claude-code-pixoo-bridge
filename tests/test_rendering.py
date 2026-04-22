from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from pixoo_bridge.bridge import SceneKind, ScreenScene
from pixoo_bridge.rendering import SimplePixooRenderer


class SimplePixooRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = SimplePixooRenderer()

    def test_running_scene_renders_animated_mascot_and_usage_band(self) -> None:
        rendered_scene = self.renderer.render(
            ScreenScene(
                kind=SceneKind.RUNNING,
                detail="50",
                footer="Working",
            )
        )

        self.assertEqual(rendered_scene.width, 32)
        self.assertEqual(rendered_scene.height, 32)
        self.assertEqual(len(rendered_scene.frames), 8)
        self.assertEqual({frame.duration_ms for frame in rendered_scene.frames}, {160})
        frame = rendered_scene.frames[0]
        self.assertEqual(len(frame.palette), 8)
        self.assertEqual(frame.palette[0], "#000000")
        self.assertEqual(frame.palette[1], "#D87753")
        self.assertEqual(frame.palette[1], frame.palette[5])
        self.assertEqual(frame.palette[2], "#E6E6E6")
        self.assertEqual(len(frame.rows), 32)
        self.assertTrue(all(len(row) == 32 for row in frame.rows))
        self.assertEqual(frame.rows[25], "1" * 32)
        self.assertGreater(
            sum(char != "0" for char in frame.rows[18][:25]),
            sum(char != "0" for char in frame.rows[19][:25]),
        )
        self.assertNotEqual(frame.rows[18][6], "0")
        self.assertEqual(frame.rows[19][6], "0")
        self.assertEqual(frame.rows[9][4], "0")
        self.assertEqual(frame.rows[9][5], "0")
        self.assertEqual(frame.rows[9][26], "0")
        self.assertEqual(frame.rows[9][27], "0")
        self.assertEqual(frame.rows[10][4], "0")
        self.assertEqual(frame.rows[10][5], "0")
        self.assertEqual(frame.rows[10][26], "0")
        self.assertEqual(frame.rows[10][27], "0")
        self.assertNotEqual(frame.rows[11][4], "0")
        self.assertNotEqual(frame.rows[11][5], "0")
        self.assertNotEqual(frame.rows[11][26], "0")
        self.assertNotEqual(frame.rows[11][27], "0")
        self.assertNotEqual(frame.rows[13][4], "0")
        self.assertNotEqual(frame.rows[13][5], "0")
        self.assertEqual(frame.rows[14][4], "0")
        self.assertEqual(frame.rows[14][5], "0")
        self.assertNotEqual(frame.rows[19][8], "0")
        self.assertNotEqual(frame.rows[22][8], "0")
        self.assertEqual(frame.rows[23][8], "0")
        self.assertEqual(frame.rows[3][29], "3")
        self.assertGreater(
            sum(row.count("2") for row in frame.rows[26:31]),
            0,
        )
        self.assertEqual(
            sum(row.count("2") for row in frame.rows[:25]),
            0,
        )
        self.assertGreater(
            sum(row.count("7") for row in frame.rows[:25]),
            0,
        )
        eye_pixels = self._pixels_with_value(frame.rows, "7")
        self.assertEqual(len(eye_pixels), 12)
        self.assertEqual({x for x, _ in eye_pixels}, {10, 11, 20, 21})
        self.assertEqual({y for _, y in eye_pixels}, {9, 10, 11})
        halo_counts = [
            self._halo_pixel_count(frame.rows) for frame in rendered_scene.frames
        ]
        self.assertEqual(halo_counts[0], 0)
        self.assertGreater(halo_counts[1], halo_counts[0])
        self.assertGreater(halo_counts[2], halo_counts[1])
        self.assertGreaterEqual(halo_counts[3], halo_counts[2])
        self.assertEqual(
            self._mascot_area(rendered_scene.frames[0].rows),
            self._mascot_area(rendered_scene.frames[1].rows),
        )
        self.assertEqual(
            self._mascot_area(rendered_scene.frames[2].rows),
            self._mascot_area(rendered_scene.frames[3].rows),
        )
        self.assertNotEqual(
            self._mascot_area(rendered_scene.frames[1].rows),
            self._mascot_area(rendered_scene.frames[2].rows),
        )
        motion_diffs = [
            self._mascot_diff_count(
                rendered_scene.frames[index].rows,
                rendered_scene.frames[index + 1].rows,
            )
            for index in range(len(rendered_scene.frames) - 1)
        ]
        self.assertLess(max(motion_diffs), 20)

    def test_attention_scene_uses_amber_status_dot_only(self) -> None:
        running = self.renderer.render(
            ScreenScene(
                kind=SceneKind.RUNNING,
                detail="18",
                footer="Bash",
            )
        )
        attention = self.renderer.render(
            ScreenScene(
                kind=SceneKind.ATTENTION,
                detail="18",
                footer="Bash",
            )
        )

        self.assertEqual(len(attention.frames), 8)
        self.assertNotEqual(
            attention.frames[0].palette[3],
            running.frames[0].palette[3],
        )
        self.assertEqual(attention.frames[0].palette[3], "#F59E0B")
        self.assertEqual(attention.frames[0].palette[1], running.frames[0].palette[1])
        self.assertEqual(attention.frames[0].rows[3][29], "3")
        self.assertEqual(attention.frames[1].rows[3][29], "3")
        self.assertEqual(attention.frames[2].rows[3][29], "0")
        self.assertEqual(attention.frames[3].rows[3][29], "0")
        self.assertGreater(self._halo_pixel_count(attention.frames[0].rows), 0)
        self.assertGreater(self._halo_pixel_count(attention.frames[1].rows), 0)
        self.assertEqual(self._halo_pixel_count(attention.frames[2].rows), 0)
        self.assertEqual(
            self._mascot_area(attention.frames[0].rows),
            self._mascot_area(attention.frames[1].rows),
        )

    def test_renderer_can_disable_status_dot(self) -> None:
        renderer = SimplePixooRenderer(status_dot_enabled=False)
        rendered_scene = renderer.render(
            ScreenScene(
                kind=SceneKind.ATTENTION,
                detail="18",
                footer="Bash",
            )
        )

        self.assertEqual(len(rendered_scene.frames), 8)
        self.assertTrue(
            all(
                sum(row.count("3") + row.count("4") for row in frame.rows) == 0
                for frame in rendered_scene.frames
            )
        )
        self.assertTrue(
            all(frame.rows[3][29] == "0" for frame in rendered_scene.frames)
        )

    def test_renderer_can_use_local_asset_override(self) -> None:
        with TemporaryDirectory() as tempdir:
            asset_path = Path(tempdir) / "clawd.png"
            image = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
            for y in range(8, 32):
                for x in range(10, 28):
                    image.putpixel((x, y), (51, 102, 204, 255))
            image.save(asset_path)

            renderer = SimplePixooRenderer(
                usage_label="Sess",
                mascot_asset_path=str(asset_path),
            )
            rendered_scene = renderer.render(
                ScreenScene(
                    kind=SceneKind.WAITING,
                    detail="21",
                    footer="Waiting",
                )
            )

        frame = rendered_scene.frames[0]
        self.assertEqual(frame.palette[1], "#3366CC")
        self.assertEqual(frame.palette[2], "#E6E6E6")
        self.assertEqual(frame.palette[3], "#22C55E")
        self.assertTrue(
            any(char not in "01234" for row in frame.rows[:25] for char in row)
        )

    def _halo_pixel_count(self, rows: list[str]) -> int:
        total = 0
        for row in rows[:8]:
            total += row[25:32].count("4")
        return total

    def _mascot_area(self, rows: list[str]) -> list[str]:
        return [row[:25] for row in rows[:25]]

    def _mascot_diff_count(self, first_rows: list[str], second_rows: list[str]) -> int:
        first_area = self._mascot_area(first_rows)
        second_area = self._mascot_area(second_rows)
        return sum(
            first_char != second_char
            for first_row, second_row in zip(first_area, second_area)
            for first_char, second_char in zip(first_row, second_row)
        )

    def _pixels_with_value(self, rows: list[str], value: str) -> list[tuple[int, int]]:
        return [
            (x, y)
            for y, row in enumerate(rows)
            for x, char in enumerate(row)
            if char == value
        ]


if __name__ == "__main__":
    unittest.main()
