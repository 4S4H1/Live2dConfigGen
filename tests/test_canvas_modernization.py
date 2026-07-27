import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.app_settings import create_app_settings
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.styles import ThemeMode


class CanvasModernizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, root: str) -> MainWindow:
        window = MainWindow(root, prefer_saved_workspace=False)
        window.controller.document.meta.author = "test"
        window.controller.document.meta.ship_skin_id = 1
        window.controller.document.meta.memo = "test"
        window.controller.document.meta.CharName = "test"
        window.controller.refresh_derived()
        window.show()
        self.app.processEvents()
        return window

    def _close(self, window: MainWindow) -> None:
        window._mark_saved_checkpoint(saved=True)
        window.close()
        self.app.processEvents()

    def test_pen_gesture_creates_and_deletes_complete_stroke(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(Path(root) / "settings")}
        ):
            window = self._window(root)
            canvas = window.canvas
            canvas.set_pen_mode(True)
            QTest.mousePress(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(50, 50),
            )
            QTest.mouseMove(canvas.viewport(), QPoint(100, 80), 10)
            QTest.mouseRelease(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(100, 80),
            )
            self.app.processEvents()
            self.assertEqual(1, len(window.controller.document.canvas_strokes))
            self.assertEqual(1, len(canvas.stroke_items))

            QTest.mouseClick(
                canvas.viewport(),
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(75, 65),
            )
            self.app.processEvents()
            self.assertEqual([], window.controller.document.canvas_strokes)
            self.assertEqual({}, canvas.stroke_items)
            window.controller.undo_stack.undo()
            self.assertEqual(1, len(window.controller.document.canvas_strokes))
            self._close(window)

    def test_pen_mode_off_preserves_ctrl_selection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            item = window.canvas.node_items[node_uuid]
            item.setSelected(False)
            window.canvas.set_pen_mode(False)
            view_point = window.canvas.mapFromScene(item.mapToScene(item.boundingRect().center()))
            QTest.mouseClick(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                view_point,
            )
            self.assertTrue(item.isSelected())
            self.assertEqual([], window.controller.document.canvas_strokes)
            self._close(window)

    def test_concise_mode_filters_fields_and_canvas_elements_without_dirtying_document(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            window._mark_saved_checkpoint(saved=True)
            undo_index = window.controller.undo_stack.index()
            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": False, "images": False, "strokes": False},
            )
            item = window.canvas.node_items[node_uuid]
            self.assertTrue(item._card_layout["identity"].isValid())
            self.assertTrue(item._card_layout["draw"].isNull())
            self.assertTrue(item._card_layout["parameter"].isNull())
            self.assertTrue(item._card_layout["action"].isValid())
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())
            self._close(window)

    def test_concise_hidden_parameter_table_renders_rows_as_nodes_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            parameter_uuid = window.controller.create_node("ParameterTrigger", (180, 360))
            target_uuid = window.controller.create_node("TouchIdle", (620, 360))
            window.controller.add_connection(parameter_uuid, target_uuid)
            parameter = window.controller.get_node(parameter_uuid)
            original_position = dict(parameter.ui_position)
            original_connections = [
                (connection.from_uuid, connection.to_uuid)
                for connection in window.controller.document.connections
            ]
            window._mark_saved_checkpoint(saved=True)
            undo_index = window.controller.undo_stack.index()

            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": False, "images": False, "strokes": False},
            )

            item = window.canvas.node_items[parameter_uuid]
            self.assertTrue(item._uses_compact_card())
            self.assertIn(str(parameter.type_slot or parameter.sequence_no or 1), item._compact_identity_text())
            self.assertTrue(item.input_pin_rect().isValid())
            self.assertTrue(item.output_pin_rect().isValid())
            self.assertIn((parameter_uuid, target_uuid), window.canvas.connection_items)
            self.assertNotIn(parameter_uuid, window.canvas.table_row_to_item)
            self.assertEqual(original_position, parameter.ui_position)
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())

            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": True, "images": False, "strokes": False},
            )

            self.assertNotIn(parameter_uuid, window.canvas.node_items)
            self.assertIn(parameter_uuid, window.canvas.table_row_to_item)
            self.assertTrue(window.canvas.table_row_to_item[parameter_uuid].isVisible())
            self.assertEqual(original_position, parameter.ui_position)
            self.assertEqual(
                original_connections,
                [
                    (connection.from_uuid, connection.to_uuid)
                    for connection in window.controller.document.connections
                ],
            )
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())
            self._close(window)

    def test_light_cards_choose_wcag_contrast_text(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            window.canvas.set_ui_theme(ThemeMode.LIGHT)
            item = window.canvas.node_items[node_uuid]
            palette = item._compact_card_palette()

            def luminance(color: QColor) -> float:
                values = []
                for channel in (color.redF(), color.greenF(), color.blueF()):
                    values.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
                return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]

            first = luminance(palette["note_fill"])
            second = luminance(palette["note_text"])
            ratio = (max(first, second) + 0.05) / (min(first, second) + 0.05)
            self.assertGreaterEqual(ratio, 4.5)
            self._close(window)

    def test_comment_and_compact_fonts_follow_zoom_without_geometry_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            comment_uuid = window.controller.create_node("Comment", (100, 100))
            node_uuid = window.controller.create_node("TouchIdle", (500, 100))
            comment = window.canvas.node_items[comment_uuid]
            card = window.canvas.node_items[node_uuid]
            comment_rect = comment.boundingRect()
            card_rect = card.boundingRect()
            baseline_comment = comment._comment_content_font().pointSizeF()
            baseline_card = card._compact_action_font().pointSizeF()
            window.canvas._apply_view_state(0.4, QPointF())
            self.app.processEvents()
            self.assertGreater(comment._comment_content_font().pointSizeF(), baseline_comment)
            self.assertGreater(card._compact_action_font().pointSizeF(), baseline_card)
            self.assertEqual(comment_rect, comment.boundingRect())
            self.assertEqual(card_rect, card.boundingRect())
            self._close(window)

    def test_removed_trash_controls_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            self.assertFalse(hasattr(window, "trash_button"))
            self.assertFalse(hasattr(window, "trash_enabled_checkbox"))
            self.assertFalse(hasattr(window, "trash_action"))
            self.assertNotIn("回收站", " ".join(action.text() for action in window.findChildren(type(window.save_action))))
            self._close(window)

    def test_settings_migration_uses_new_namespace_and_skips_trash(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"L2D_CONFIG_EDITOR_SETTINGS_DIR": root}
        ):
            legacy_path = Path(root) / "OpenAI" / "L2DConfigEditor.ini"
            current_path = Path(root) / "4S4H1" / "L2DConfigEditor.ini"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            current_path.parent.mkdir(parents=True, exist_ok=True)
            legacy = QSettings(str(legacy_path), QSettings.Format.IniFormat)
            legacy.clear()
            legacy.setValue("ui/theme_mode", "light")
            legacy.setValue("trash_enabled_default", True)
            legacy.sync()
            current = QSettings(str(current_path), QSettings.Format.IniFormat)
            current.clear()
            current.sync()

            migrated = create_app_settings()

            self.assertEqual("light", migrated.value("ui/theme_mode"))
            self.assertFalse(migrated.contains("trash_enabled_default"))
