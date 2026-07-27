import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from l2d_config_editor.logic import get_default_schema, load_document
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.template_batch import (
    BaseTemplateSpec,
    BatchTemplateDialog,
    create_base_template_files,
    parse_pasted_template_rows,
)


class TemplateBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_parse_pasted_excel_rows_accepts_and_removes_header(self) -> None:
        rows = parse_pasted_template_rows(
            "角色名\t角色资源名\t角色ID\t版本\t作者\t允许目光拖拽的待机\t备注\n"
            "信浓\txinnong_3\t302291\t2026-07-11\tasahi\t0,1\t泳装\n"
            "能代\tnengdai_2\t302292\t2026-07-12\t\t2\t"
        )
        self.assertEqual(
            rows,
            [
                ["信浓", "xinnong_3", "302291", "2026-07-11", "asahi", "0,1", "泳装"],
                ["能代", "nengdai_2", "302292", "2026-07-12", "", "2", ""],
            ],
        )

    def test_dialog_builds_multiple_specs_with_per_row_metadata(self) -> None:
        dialog = BatchTemplateDialog()
        first = ["信浓", "xinnong_3", "302291", "2026-07-11", "asahi", "0,1", "泳装"]
        for column, value in enumerate(first):
            dialog.table.item(0, column).setText(value)
        dialog.add_row(["能代", "nengdai_2", "302292", "2026-07-12", "", "2", ""])

        specs = dialog.template_specs()

        self.assertEqual(2, len(specs))
        self.assertEqual("2026-07-11", specs[0].version)
        self.assertEqual("", specs[1].author)
        self.assertEqual("0,1", specs[0].react_condition)
        self.assertEqual("2", specs[1].react_condition)
        self.assertEqual("idle0", specs[1].default_state)
        dialog.close()

    def test_dialog_rejects_duplicate_character_ids_before_writing(self) -> None:
        dialog = BatchTemplateDialog()
        for column, value in enumerate(["A", "a_1", "100", "2026-07-11", "", "0", ""]):
            dialog.table.item(0, column).setText(value)
        dialog.add_row(["B", "b_1", "100", "2026-07-11", "", "0", ""])

        with self.assertRaisesRegex(ValueError, "角色 ID.*重复"):
            dialog.template_specs()
        dialog.close()

    def test_batch_writer_creates_ready_idle0_bases_in_version_folder(self) -> None:
        schema = get_default_schema()
        specs = [
            BaseTemplateSpec("2026-07-11", "asahi", "信浓", "xinnong_3", 302291, "泳装"),
            BaseTemplateSpec("2026-07-11", "asahi", "能代", "nengdai_2", 302292),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = create_base_template_files(schema, temp_dir, specs)
            loaded = [load_document(schema, path) for path in paths]

            self.assertEqual(["20260711", "20260711"], [path.parent.name for path in paths])
            self.assertEqual(["信浓.json", "能代.json"], [path.name for path in paths])
            self.assertTrue(all(document.state.is_meta_ready for document in loaded))
            self.assertTrue(all([node.type for node in document.nodes] == ["Idle0"] for document in loaded))
            self.assertEqual("泳装", loaded[0].meta.tips)

    def test_batch_writer_rolls_back_if_any_staged_write_fails(self) -> None:
        schema = get_default_schema()
        specs = [
            BaseTemplateSpec("2026-07-11", "asahi", "A", "a_1", 1001),
            BaseTemplateSpec("2026-07-11", "asahi", "B", "b_1", 1002),
        ]
        from l2d_config_editor import logic

        real_save = logic.save_document
        calls = 0

        def flaky_save(active_schema, document, path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            return real_save(active_schema, document, path)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("l2d_config_editor.logic.save_document", side_effect=flaky_save):
                with self.assertRaisesRegex(OSError, "simulated"):
                    create_base_template_files(schema, temp_dir, specs)
            self.assertEqual([], list(Path(temp_dir).rglob("*.json")))
            self.assertEqual([], list(Path(temp_dir).rglob("*.tmp")))

    def test_batch_writer_rolls_back_if_publishing_a_later_file_fails(self) -> None:
        schema = get_default_schema()
        specs = [
            BaseTemplateSpec("2026-07-11", "asahi", "A", "a_1", 1001),
            BaseTemplateSpec("2026-07-11", "asahi", "B", "b_1", 1002),
        ]
        real_replace = Path.replace
        calls = 0

        def flaky_replace(source: Path, target: Path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated publish failure")
            return real_replace(source, target)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(Path, "replace", new=flaky_replace):
                with self.assertRaisesRegex(OSError, "publish"):
                    create_base_template_files(schema, temp_dir, specs)
            self.assertEqual([], list(Path(temp_dir).rglob("*.json")))
            self.assertEqual([], list(Path(temp_dir).rglob("*.tmp")))

    def test_main_window_exposes_base_creation_and_opens_first_created_file(self) -> None:
        specs = [BaseTemplateSpec("2026-07-11", "asahi", "信浓", "xinnong_3", 302291)]

        class AcceptedDialog:
            def __init__(self, _parent=None) -> None:
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

            def template_specs(self):
                return specs

        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            action_labels = {action.text() for action in window.findChildren(type(window.save_action))}
            self.assertIn("批量创建配置底座…", action_labels)
            self.assertEqual("创建配置底座", window.create_base_templates_button.text())

            with patch("l2d_config_editor.main_window.BatchTemplateDialog", AcceptedDialog), patch(
                "l2d_config_editor.main_window.QMessageBox.information"
            ):
                window._show_batch_template_dialog()

            self.assertEqual("信浓", window.controller.document.meta.CharName)
            self.assertEqual(["Idle0"], [node.type for node in window.controller.document.nodes])
            self.assertEqual("信浓.json", Path(window.controller.document.path).name)
            window._mark_saved_checkpoint(saved=True)
            window.close()


if __name__ == "__main__":
    unittest.main()
