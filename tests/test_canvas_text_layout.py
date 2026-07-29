import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication

from l2d_config_editor.main_window import MainWindow


class CanvasTextLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _ready(window: MainWindow) -> None:
        meta = window.controller.document.meta
        meta.author = "tester"
        meta.ship_skin_id = 1
        meta.memo = "canvas-text-layout"
        meta.CharName = "tester"
        window.controller.refresh_derived()

    def _close(self, window: MainWindow) -> None:
        window._mark_saved_checkpoint(saved=True)
        window.close()
        self.app.processEvents()

    def test_idle0_uses_compact_tall_geometry_and_fits_its_title_at_minimum_zoom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.show()
            self.app.processEvents()
            idle0 = next(node for node in window.controller.document.nodes if node.type == "Idle0")
            item = window.canvas.node_items[idle0.uuid]

            window.canvas._apply_view_state(0.18, QPointF())
            self.app.processEvents()

            self.assertLessEqual(item._rect.width(), 680.0)
            self.assertGreaterEqual(item._rect.height(), 150.0)
            self.assertLessEqual(item._rect.width() / item._rect.height(), 4.3)

            draw_rect, fitted_font, display_text = item._compact_text_layout(
                item._full_title_text(),
                item._title_rect,
                item._title_font(),
                min_point_size=7.0,
            )
            metrics = QFontMetricsF(fitted_font)
            self.assertEqual(item._full_title_text(), display_text)
            self.assertLessEqual(metrics.horizontalAdvance(display_text), draw_rect.width() + 0.5)
            self.assertLessEqual(metrics.height(), draw_rect.height() + 0.5)
            self._close(window)

    def test_draw_and_action_overflow_keep_numeric_suffix_and_only_realign_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            self._ready(window)
            node_uuid = window.controller.create_node("TouchIdle", (200.0, 120.0))
            item = window.canvas.node_items[node_uuid]
            window.canvas._apply_view_state(0.18, QPointF())
            self.app.processEvents()

            cases = (
                (
                    "draw_able_name",
                    item._card_layout["draw"],
                    item._compact_title_font(),
                    item.CARD_TITLE_MIN_POINT_SIZE,
                    12.0,
                    4.0,
                ),
                (
                    "action_trigger",
                    item._card_layout["action"].adjusted(18.0, 0.0, -40.0, 0.0),
                    item._compact_action_font(),
                    item.CARD_ACTION_MIN_POINT_SIZE,
                    2.0,
                    10.0,
                ),
            )
            for field_key, rect, font, min_size, horizontal_padding, vertical_padding in cases:
                short_plan = item._compact_field_text_layout(
                    field_key,
                    "53",
                    rect,
                    font,
                    Qt.AlignmentFlag.AlignCenter,
                    min_point_size=min_size,
                    horizontal_padding=horizontal_padding,
                    vertical_padding=vertical_padding,
                )
                self.assertEqual("53", short_plan[2])
                self.assertEqual(Qt.AlignmentFlag.AlignCenter, short_plan[3])

                long_text = f"very_wide_prefix_for_overflow_{field_key}_53"
                long_plan = item._compact_field_text_layout(
                    field_key,
                    long_text,
                    rect,
                    font,
                    Qt.AlignmentFlag.AlignCenter,
                    min_point_size=min_size,
                    horizontal_padding=horizontal_padding,
                    vertical_padding=vertical_padding,
                )
                self.assertNotEqual(long_text, long_plan[2])
                self.assertTrue(long_plan[2].endswith("53"))
                self.assertTrue(long_plan[3] & Qt.AlignmentFlag.AlignRight)
                self.assertTrue(long_plan[3] & Qt.AlignmentFlag.AlignVCenter)

            self._close(window)


if __name__ == "__main__":
    unittest.main()
