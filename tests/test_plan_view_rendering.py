"""Raster checks for visible plan notes and graph-view selection feedback."""
import os
import unittest

# These are font-raster checks, not headless geometry tests. Windows' offscreen
# plugin omits glyphs at some fractional scales even above the former 45%
# threshold; use the application's native font engine in this isolated module.
if os.name == "nt":
    os.environ["QT_QPA_PLATFORM"] = "windows"
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication, QPushButton

from l2d_config_editor.controller import EditorController
from l2d_config_editor.plan import PLAN_TOUCHIDLE_COLOR
from l2d_config_editor.plan_canvas import PlanCanvasView
from l2d_config_editor.styles import ThemeMode, stylesheet_for_theme


class PlanViewRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _canvas(self, title):
        controller = EditorController()
        meta = controller.document.meta
        meta.author, meta.CharName, meta.ship_skin_id, meta.memo = "测试", "角色", 1, "测试"
        controller.refresh_derived()
        node_uuid = controller.create_plan_topic(controller.document.nodes[0].uuid, title)
        controller.set_plan_topic_color(node_uuid, PLAN_TOUCHIDLE_COLOR)
        view = PlanCanvasView(controller.schema, controller)
        view.resize(900, 650)
        view.show()
        self.app.processEvents()
        self.addCleanup(view.close)
        return controller, view, node_uuid

    def _text_pixels(self, view, item, rect, mode):
        image = view.viewport().grab().toImage()
        crop = view.mapFromScene(item.mapRectToScene(rect)).boundingRect().intersected(image.rect())
        total = 0
        for y in range(crop.top(), crop.bottom() + 1):
            for x in range(crop.left(), crop.right() + 1):
                color = image.pixelColor(x, y)
                channels = color.red(), color.green(), color.blue()
                # The crop is inside the card margins, away from colored
                # outlines/edges. Bright-on-dark or dark-on-light ink here
                # proves that text reached the real viewport paint device.
                if ((mode is ThemeMode.DARK and min(channels) > 110)
                        or (mode is ThemeMode.LIGHT and max(channels) < 160)):
                    total += 1
        return total

    def test_semantic_card_notes_are_painted_on_both_sides_of_zoom_boundary(self):
        controller, view, node_uuid = self._canvas("TouchIdle12-touch_idle12-中文备注 NOTE remains visible")
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            view.set_ui_theme(mode)
            self.app.processEvents()
            item = view.topic_items[node_uuid]
            self.assertTrue(item._card_spec.note_lines)
            bounds = item.boundingRect()
            font_size = item._topic_font().pointSizeF()
            line_height = QFontMetricsF(item._topic_font()).lineSpacing()
            note_rect = QRectF(item.CARD_PADDING_X,
                               item.CARD_PADDING_Y + line_height + item.CARD_LINE_GAP,
                               bounds.width() - item.CARD_PADDING_X * 2,
                               len(item._card_spec.note_lines) * line_height)
            for scale in (.46, .45, .44, .35, .20):
                with self.subTest(theme=mode.value, scale=scale):
                    view.resetTransform()
                    view.scale(scale, scale)
                    view.centerOn(item)
                    view.viewport().update()
                    self.app.processEvents()
                    self.assertGreater(self._text_pixels(view, item, note_rect, mode), 0)
                    self.assertEqual(bounds, item.boundingRect())
                    self.assertEqual(font_size, item._topic_font().pointSizeF())
        self.assertEqual("TouchIdle12-touch_idle12-中文备注 NOTE remains visible",
                         controller.plan_topic(node_uuid).plan_title)

    def test_plain_plan_notes_remain_painted_when_zoomed_out(self):
        _controller, view, node_uuid = self._canvas("中文纯备注 PLAIN NOTE")
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            view.set_ui_theme(mode)
            self.app.processEvents()
            item = view.topic_items[node_uuid]
            self.assertFalse(item._card_spec.semantic)
            content = item.boundingRect().adjusted(item.CARD_PADDING_X, item.CARD_PADDING_Y,
                                                   -item.CARD_PADDING_X, -item.CARD_PADDING_Y)
            view.resetTransform()
            view.scale(.25, .25)
            view.centerOn(item)
            view.viewport().update()
            self.app.processEvents()
            self.assertGreater(self._text_pixels(view, item, content, mode), 0)

    def test_checked_view_button_has_distinct_rendered_fill_in_each_theme(self):
        button = QPushButton("正式图")
        button.setProperty("graphViewSwitch", True)
        button.setCheckable(True)
        button.resize(130, 44)
        button.show()
        self.addCleanup(button.close)
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            with self.subTest(theme=mode.value):
                button.setStyleSheet(stylesheet_for_theme(mode))
                colors = []
                for checked in (False, True):
                    button.setChecked(checked)
                    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
                    button.clearFocus()
                    self.app.processEvents()
                    colors.append(button.grab().toImage().pixelColor(8, 22).name())
                self.assertNotEqual(*colors)
                self.assertEqual("#2264d6" if mode is ThemeMode.DARK else "#2563eb", colors[1])


if __name__ == "__main__":
    unittest.main()
