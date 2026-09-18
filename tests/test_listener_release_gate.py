"""The experimental listener remains readable, but has no released UI entry."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")
from PySide6.QtWidgets import QApplication
from l2d_config_editor import features
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.logic import create_node, export_document_dict, load_document_payload
from l2d_config_editor.history_view import GraphComparisonWidget


class ListenerReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self.window.close)
        self.controller = self.window.controller
        meta = self.controller.document.meta
        meta.author, meta.CharName, meta.ship_skin_id, meta.memo = "test", "test", 1, "test"
        self.controller.refresh_derived()

    def test_default_ui_and_all_creation_entry_points_are_disabled(self):
        self.assertFalse(features.LISTENER_EDITOR_ENABLED)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.listener_button.isVisible())
        self.assertFalse(self.window._listener_toolbar_action.isVisible())
        self.assertFalse(any("监听器" in action.text() for action in self.window.tools_menu.actions()))
        self.window._create_listener()
        self.assertIsNone(self.window.listener_dialog)
        self.assertIsNone(self.controller.create_node("Listener", (0, 0)))
        response = self.window.tool_service.invoke_tool("apply_graph_edits", {
            "expected_revision": self.window.tool_service.revision,
            "operations": [{"op": "add_node", "node_type": "Listener", "position": {"x": 0, "y": 0}}]})
        self.assertFalse(response["ok"])
        self.assertEqual("FEATURE_DISABLED", response["error"]["code"])

    def test_existing_graph_roundtrips_but_has_no_editor_or_history_entry(self):
        node = create_node(self.controller.schema, self.controller.document, "Listener")
        self.controller.document.nodes.append(node)
        self.controller.documentLoaded.emit()
        payload = export_document_dict(self.controller.schema, self.controller.document)
        restored = load_document_payload(self.controller.schema, payload)
        self.assertEqual(node.listener_graph.to_payload(), restored.nodes[-1].listener_graph.to_payload())
        self.window._show_listener_editor(node.uuid)
        self.assertIsNone(self.window.listener_dialog)
        self.assertFalse(self.window.canvas.node_items[node.uuid].form._listener_button.isVisible())
        widget = GraphComparisonWidget(self.controller.schema)
        widget.set_documents(restored, restored)
        widget.show()
        self.addCleanup(widget.close)
        self.assertFalse(widget.before_listener_button.isVisible())
        self.assertIsNone(widget.open_listener_graph("after", node.uuid))
        copied = self.controller.serialize_selection([node.uuid])
        self.assertEqual([], self.controller.paste_payload(copied, (500, 0)))


if __name__ == "__main__":
    unittest.main()
