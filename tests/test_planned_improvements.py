import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidgetItem, QMessageBox

from l2d_config_editor import main as app_main
from l2d_config_editor.app_settings import create_app_settings
from l2d_config_editor.canvas import TemporaryConnectionItem
from l2d_config_editor.controller import EditorController
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.styles import ThemeMode
from l2d_config_editor.svn_tools import SvnCommitRunner, parse_status_xml


def make_ready(controller: EditorController) -> None:
    controller.document.meta.CharName = "测试角色"
    controller.document.meta.memo = "test_role"
    controller.document.meta.ship_skin_id = 1001
    controller.refresh_derived()


def status_xml(entries: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<entry path="{path}"><wc-status item="{state}" props="none"/></entry>'
        for path, state in entries
    )
    return f'noise before xml\n<?xml version="1.0"?><status><target path=".">{body}</target></status>\n'


class PlannedImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_install_root_guard_uses_windows_path_semantics(self) -> None:
        from l2d_config_editor.main_window import is_path_within_install_root

        install_root = r"C:\Users\Example\AppData\Local\Programs\L2DConfigEditor"
        self.assertTrue(is_path_within_install_root(install_root, install_root))
        self.assertTrue(
            is_path_within_install_root(
                r"c:\users\example\appdata\local\programs\l2dconfigeditor\projects\a.json",
                install_root,
            )
        )
        self.assertFalse(
            is_path_within_install_root(
                r"C:\Users\Example\AppData\Local\Programs\L2DConfigEditor-backup\a.json",
                install_root,
            )
        )
        self.assertFalse(is_path_within_install_root(r"D:\Projects\a.json", install_root))

    def test_first_run_workspace_is_selected_once_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Mock()
            settings.value.return_value = None
            with patch.object(
                app_main.QFileDialog, "getExistingDirectory", return_value=temp_dir
            ) as choose:
                resolved = app_main.resolve_initial_workspace(
                    Path(temp_dir).parent,
                    settings=settings,
                )
            self.assertEqual(Path(temp_dir).resolve(), resolved)
            choose.assert_called_once()
            settings.setValue.assert_called_once_with("workspace_root", str(Path(temp_dir).resolve()))

    def test_first_run_reselects_when_workspace_is_inside_frozen_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            unsafe_workspace = install_root / "projects"
            safe_workspace = root / "user-projects"
            unsafe_workspace.mkdir(parents=True)
            safe_workspace.mkdir()
            settings = Mock()
            settings.value.return_value = None

            with patch.object(
                app_main.QFileDialog,
                "getExistingDirectory",
                side_effect=(str(unsafe_workspace), str(safe_workspace)),
            ) as choose, patch.object(app_main.QMessageBox, "warning") as warning:
                resolved = app_main.resolve_initial_workspace(
                    root,
                    settings=settings,
                    install_root=install_root,
                )

            self.assertEqual(safe_workspace.resolve(), resolved)
            self.assertEqual(2, choose.call_count)
            self.assertIn("安装目录", warning.call_args.args[2])
            settings.setValue.assert_called_once_with(
                "workspace_root",
                str(safe_workspace.resolve()),
            )

    def test_first_run_can_cancel_after_rejecting_install_root_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "installed-app"
            unsafe_workspace = install_root / "projects"
            unsafe_workspace.mkdir(parents=True)
            settings = Mock()
            settings.value.return_value = None

            with patch.object(
                app_main.QFileDialog,
                "getExistingDirectory",
                side_effect=(str(unsafe_workspace), ""),
            ), patch.object(app_main.QMessageBox, "warning"):
                resolved = app_main.resolve_initial_workspace(
                    temp_dir,
                    settings=settings,
                    install_root=install_root,
                )

            self.assertIsNone(resolved)
            settings.setValue.assert_not_called()

    def test_saved_workspace_skips_the_startup_picker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Mock()
            settings.value.return_value = temp_dir
            with patch.object(app_main.QFileDialog, "getExistingDirectory") as choose:
                resolved = app_main.resolve_initial_workspace(
                    Path(temp_dir).parent,
                    settings=settings,
                )
            self.assertEqual(Path(temp_dir).resolve(), resolved)
            choose.assert_not_called()

    def test_saved_install_root_workspace_is_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            unsafe_workspace = install_root / "projects"
            safe_workspace = root / "user-projects"
            unsafe_workspace.mkdir(parents=True)
            safe_workspace.mkdir()
            settings = Mock()
            settings.value.return_value = str(unsafe_workspace)

            with patch.object(
                app_main.QFileDialog,
                "getExistingDirectory",
                return_value=str(safe_workspace),
            ) as choose, patch.object(app_main.QMessageBox, "warning") as warning:
                resolved = app_main.resolve_initial_workspace(
                    root,
                    settings=settings,
                    install_root=install_root,
                )

            self.assertEqual(safe_workspace.resolve(), resolved)
            choose.assert_called_once()
            self.assertIn("安装目录", warning.call_args.args[2])
            settings.remove.assert_called_once_with("workspace_root")

    def test_workspace_widget_is_removed_and_menu_entry_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            self.assertFalse(hasattr(window, "workspace_path_edit"))
            labels = {action.text() for action in window.findChildren(type(window.save_action))}
            self.assertIn("更改工作区…", labels)
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_runtime_workspace_change_rejects_frozen_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            unsafe_workspace = install_root / "projects"
            safe_workspace = root / "user-projects"
            unsafe_workspace.mkdir(parents=True)
            safe_workspace.mkdir()
            with patch.dict(
                os.environ,
                {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(root / "settings")},
            ):
                window = MainWindow(
                    safe_workspace,
                    prefer_saved_workspace=False,
                    install_root=install_root,
                )
                with patch.object(
                    QFileDialog,
                    "getExistingDirectory",
                    return_value=str(unsafe_workspace),
                ), patch.object(QMessageBox, "warning") as warning, patch.object(
                    window,
                    "_ensure_safe_before_workspace_change",
                ) as save_prompt:
                    window._choose_workspace_directory()

                self.assertEqual(safe_workspace.resolve(), window.workdir)
                self.assertNotEqual(
                    str(unsafe_workspace.resolve()),
                    str(window.settings.value(window.SETTINGS_WORKSPACE_ROOT, "") or ""),
                )
                self.assertIn("安装目录", warning.call_args.args[2])
                save_prompt.assert_not_called()
                window._mark_saved_checkpoint(saved=True)
                window.close()

    def test_window_does_not_restore_saved_workspace_from_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            unsafe_workspace = install_root / "projects"
            safe_workspace = root / "user-projects"
            unsafe_workspace.mkdir(parents=True)
            safe_workspace.mkdir()
            with patch.dict(
                os.environ,
                {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(root / "settings")},
            ):
                settings = create_app_settings()
                settings.setValue("workspace_root", str(unsafe_workspace))
                settings.sync()

                window = MainWindow(
                    safe_workspace,
                    install_root=install_root,
                )

                self.assertEqual(safe_workspace.resolve(), window.workdir)
                window._mark_saved_checkpoint(saved=True)
                window.close()

    def test_window_refuses_install_root_as_explicit_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "installed-app"
            unsafe_workspace = install_root / "projects"
            unsafe_workspace.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "安装目录"):
                MainWindow(
                    unsafe_workspace,
                    prefer_saved_workspace=False,
                    install_root=install_root,
                )

    def test_external_json_inside_frozen_install_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            install_root.mkdir()
            unsafe_json = install_root / "example.json"
            unsafe_json.write_text("{}", encoding="utf-8")
            safe_workspace = root / "user-projects"
            safe_workspace.mkdir()
            with patch.dict(
                os.environ,
                {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(root / "settings")},
            ):
                window = MainWindow(
                    safe_workspace,
                    prefer_saved_workspace=False,
                    install_root=install_root,
                )
                with patch.object(QMessageBox, "warning") as warning:
                    opened = window.open_external_file(unsafe_json)

                self.assertFalse(opened)
                self.assertNotEqual(str(unsafe_json.resolve()), window.controller.document.path)
                self.assertIn("安装目录", warning.call_args.args[2])
                window._mark_saved_checkpoint(saved=True)
                window.close()

    def test_open_dialog_rejects_json_inside_frozen_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed-app"
            install_root.mkdir()
            unsafe_json = install_root / "example.json"
            unsafe_json.write_text("{}", encoding="utf-8")
            safe_workspace = root / "user-projects"
            safe_workspace.mkdir()
            with patch.dict(
                os.environ,
                {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(root / "settings")},
            ):
                window = MainWindow(
                    safe_workspace,
                    prefer_saved_workspace=False,
                    install_root=install_root,
                )
                with patch.object(
                    QFileDialog,
                    "getOpenFileName",
                    return_value=(str(unsafe_json), "JSON Files (*.json)"),
                ), patch.object(QMessageBox, "warning") as warning:
                    window._open_dialog()

                self.assertNotEqual(str(unsafe_json.resolve()), window.controller.document.path)
                self.assertIn("安装目录", warning.call_args.args[2])
                window._mark_saved_checkpoint(saved=True)
                window.close()

    def test_file_list_cannot_open_install_root_json_from_parent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            install_root = workspace / "installed-app"
            install_root.mkdir()
            unsafe_json = install_root / "example.json"
            unsafe_json.write_text("{}", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(workspace / "settings")},
            ):
                window = MainWindow(
                    workspace,
                    prefer_saved_workspace=False,
                    install_root=install_root,
                )
                item = QListWidgetItem("example")
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    {"kind": "file", "path": "installed-app/example.json"},
                )
                with patch.object(QMessageBox, "warning") as warning:
                    window._open_selected_file(item)

                self.assertNotEqual(str(unsafe_json.resolve()), window.controller.document.path)
                self.assertIn("安装目录", warning.call_args.args[2])
                window._mark_saved_checkpoint(saved=True)
                window.close()

    def test_search_is_a_popup_and_does_not_resize_the_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            before = window.canvas.geometry()
            window._focus_search()
            window.search_edit.setText("idle0")
            self.app.processEvents()
            self.assertTrue(window.search_panel.windowFlags() & Qt.WindowType.Popup)
            self.assertTrue(window.search_panel.isVisible())
            self.assertEqual(before, window.canvas.geometry())
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_light_theme_updates_actions_canvas_and_persisted_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(Path(temp_dir) / "settings")},
        ):
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window._apply_ui_theme(ThemeMode.LIGHT)
            self.app.processEvents()
            self.assertEqual(ThemeMode.LIGHT, window.theme_mode)
            self.assertTrue(window.light_theme_action.isChecked())
            self.assertEqual("#f3f6fa", window.canvas.theme_palette.canvas_background)
            self.assertEqual("light", window.settings.value(window.SETTINGS_THEME_MODE))
            window._apply_ui_theme(ThemeMode.DARK)
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_comment_is_edited_directly_as_one_large_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            make_ready(window.controller)
            node_uuid = window.controller.create_node("Comment", (200.0, 120.0))
            item = window.canvas.node_items[node_uuid]
            self.assertFalse(item.form.isVisible())
            self.assertTrue(item.begin_comment_edit())
            editor = item._comment_editor_proxy.widget()
            editor.setPlainText("唯一的大标题")
            item.commit_pending_inline_edit()
            self.assertEqual("唯一的大标题", window.controller.get_node(node_uuid).fields["content"])
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_idle0_title_expands_horizontally_instead_of_being_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            idle0 = next(node for node in window.controller.document.nodes if node.type == "Idle0")
            item = window.canvas.node_items[idle0.uuid]
            window.canvas._apply_view_state(0.35, QPointF())
            self.app.processEvents()
            text_width = QFontMetricsF(item._title_font()).horizontalAdvance(item._full_title_text())
            self.assertLessEqual(text_width, item._title_rect.width())
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_temporary_connection_explicitly_disables_path_fill(self) -> None:
        painter = Mock()
        view = SimpleNamespace(
            should_use_fast_rendering=lambda: True,
            theme_palette=SimpleNamespace(connection_preview="#38bdf8"),
        )
        item = TemporaryConnectionItem(view)
        item.update_path(QPointF(0.0, 0.0), QPointF(200.0, 120.0))
        item.paint(painter, None)
        painter.setBrush.assert_any_call(Qt.BrushStyle.NoBrush)

    def test_clone_advances_generated_and_manual_numbered_fields(self) -> None:
        controller = EditorController()
        make_ready(controller)
        controller.document.editor_settings.numeric_linkage_enabled = True
        source_uuid = controller.create_node("TouchIdle", (100.0, 100.0))
        payload = controller.serialize_selection([source_uuid])
        first_uuid = controller.paste_payload(payload, (300.0, 100.0))[0]
        second_uuid = controller.paste_payload(payload, (500.0, 100.0))[0]
        self.assertEqual("TouchIdle2", controller.get_node(first_uuid).fields["draw_able_name"])
        self.assertEqual(3, controller.get_node(second_uuid).fields["target_idle"])
        self.assertIn("touch_idle3", controller.get_node(second_uuid).fields["action_trigger"])

        controller.update_fields(
            source_uuid,
            {
                "draw_able_name": "CustomFrame99",
                "parameter": "CustomParameter99",
                "action_trigger": "custom_action99",
            },
            "simple",
        )
        manual_payload = controller.serialize_selection([source_uuid])
        manual_first = controller.get_node(controller.paste_payload(manual_payload, (700.0, 100.0))[0])
        manual_second = controller.get_node(controller.paste_payload(manual_payload, (900.0, 100.0))[0])
        self.assertEqual("CustomFrame102", manual_first.fields["draw_able_name"])
        self.assertEqual("CustomFrame103", manual_second.fields["draw_able_name"])
        self.assertEqual("CustomParameter102", manual_first.fields["parameter"])
        self.assertIn("custom_action102", manual_first.fields["action_trigger"])

    def test_return_default_idle_action_uses_each_new_shared_slot(self) -> None:
        controller = EditorController()
        make_ready(controller)
        first = controller.get_node(controller.create_node("ReturnDefaultIdle", (100.0, 100.0)))
        second = controller.get_node(controller.create_node("ReturnDefaultIdle", (300.0, 100.0)))
        self.assertIn("touch_idle1", first.fields["action_trigger"])
        self.assertIn("touch_idle2", second.fields["action_trigger"])
        self.assertEqual(0, second.fields["target_idle"])

    def test_svn_status_parser_tolerates_noise_and_runner_auto_adds(self) -> None:
        parsed = parse_status_xml(status_xml([("C:/wc/config.json", "unversioned")]))
        self.assertEqual("unversioned", parsed[0][1])
        runner = SvnCommitRunner("svn")
        runner._file_path = Path("C:/wc/config.json")
        runner._run = Mock()
        runner._handle_initial_status(status_xml([("C:/wc/config.json", "unversioned")]))
        runner._run.assert_called_once_with("add", ["add", "--parents", str(runner._file_path)])

    def test_svn_commit_message_and_depth_are_explicit(self) -> None:
        runner = SvnCommitRunner("svn")
        runner._message = "角色A.json"
        runner._run = Mock()
        target = Path("C:/wc/角色A.json")
        runner._commit([target])
        phase, arguments = runner._run.call_args.args
        self.assertEqual("commit", phase)
        self.assertIn("--depth", arguments)
        self.assertEqual("角色A.json", arguments[arguments.index("--message") + 1])
        self.assertEqual(str(target), arguments[-1])


if __name__ == "__main__":
    unittest.main()
