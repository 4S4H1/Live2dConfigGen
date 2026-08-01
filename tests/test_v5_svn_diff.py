from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QProcess, Signal

from l2d_config_editor.controller import EditorController
from l2d_config_editor.graph_diff import canonical_graph_snapshot, diff_documents
from l2d_config_editor.logic import (
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    load_document_payload,
    save_document,
)
from l2d_config_editor.models import CanvasStrokeRecord, GroupRecord
from l2d_config_editor.plan import parse_touchidle_plan_title
from l2d_config_editor.svn_tools import (
    SvnCommitRunner,
    SvnHistoryRunner,
    parse_info_xml,
    parse_log_xml,
    svn_error_message,
)


_QT_APP = QCoreApplication.instance() or QCoreApplication([])


class _StubProcess:
    def state(self):
        return QProcess.ProcessState.NotRunning


class _StubSvnExecutor(QObject):
    stdoutReceived = Signal(object)
    stderrReceived = Signal(object)
    finished = Signal(int, object)
    failedToStart = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.process = _StubProcess()
        self.starts: list[tuple[str, list[str]]] = []
        self.cancelled = False
        self.killed = False

    def start(self, executable, arguments) -> None:
        self.starts.append((str(executable), list(arguments)))

    def cancel(self) -> None:
        self.cancelled = True

    def kill(self) -> None:
        self.killed = True


def ready_controller() -> EditorController:
    controller = EditorController()
    controller.document.meta.author = "tester"
    controller.document.meta.ship_skin_id = 100
    controller.document.meta.memo = "asset"
    controller.document.meta.CharName = "character"
    controller.refresh_derived()
    return controller


class PlanFormalizationV5Tests(unittest.TestCase):
    def test_title_parser_is_case_insensitive_and_keeps_visible_segments(self) -> None:
        parsed = parse_touchidle_plan_title("  ToUcHiDlE7  -  TOUCH_idle19 ")
        self.assertIsNotNone(parsed)
        self.assertEqual((7, 19), (parsed.draw_index, parsed.action_index))
        self.assertEqual("ToUcHiDlE7", parsed.draw_text)
        self.assertEqual("  -  ", parsed.separator_text)
        self.assertEqual("TOUCH_idle19", parsed.action_text)
        for title in ("touchidle-touch_idle1", "touchidle1_touch_idle1", "x-y"):
            self.assertIsNone(parse_touchidle_plan_title(title))

    def test_materialization_is_atomic_preserves_edges_and_uses_placeholders(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        valid_uuid = controller.create_plan_topic(root_uuid, "TouchIdle7-touch_idle19")
        invalid_uuid = controller.create_plan_topic(valid_uuid, "待设计")
        controller.add_connection(root_uuid, invalid_uuid)
        valid = controller.get_node(valid_uuid)
        valid.locked = True
        valid.ui_size = {"width": 321.0, "height": 123.0}
        controller.document.groups = [
            GroupRecord(uuid="group-1", title="计划", node_uuids=[valid_uuid, invalid_uuid])
        ]
        connection_pairs = {
            (item.from_uuid, item.to_uuid) for item in controller.document.connections
        }
        before_index = controller.undo_stack.index()

        self.assertTrue(controller.materialize_plan_topics())

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        converted = controller.get_node(valid_uuid)
        placeholder = controller.get_node(invalid_uuid)
        self.assertEqual("TouchIdle", converted.type)
        self.assertEqual("TouchIdle7", converted.fields["draw_able_name"])
        self.assertEqual("empty", converted.fields["parameter"])
        self.assertEqual(
            "{type = 2 ,action = 'touch_idle19'}",
            converted.fields["action_trigger"],
        )
        self.assertEqual("animated", converted.fields["transition_type"])
        self.assertTrue(converted.locked)
        self.assertEqual({"width": 321.0, "height": 123.0}, converted.ui_size)
        self.assertEqual("PlanPlaceholder", placeholder.type)
        self.assertEqual("待设计", placeholder.fields["plan_source_title"])
        self.assertEqual("materialized", controller.plan_topic(valid_uuid).formalization_state)
        self.assertEqual("virtual", controller.plan_topic(invalid_uuid).formalization_state)
        self.assertEqual(
            connection_pairs,
            {(item.from_uuid, item.to_uuid) for item in controller.document.connections},
        )
        self.assertEqual(
            [valid_uuid, invalid_uuid], controller.document.groups[0].node_uuids
        )
        csv_rows = document_to_csv_rows(controller.schema, controller.document)
        self.assertEqual(1, len(csv_rows))
        self.assertEqual("TouchIdle7", csv_rows[0].values["draw_able_name"])

        controller.undo_stack.undo()
        self.assertEqual("PlanPlaceholder", controller.get_node(valid_uuid).type)
        self.assertEqual("draft", controller.plan_topic(valid_uuid).formalization_state)
        self.assertEqual(
            connection_pairs,
            {(item.from_uuid, item.to_uuid) for item in controller.document.connections},
        )

    def test_editing_materialized_or_virtual_title_returns_to_draft(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "touchidle1-touch_idle2")
        controller.materialize_plan_topics()
        self.assertEqual("materialized", controller.plan_topic(node_uuid).formalization_state)

        controller.set_plan_title(node_uuid, "尚未确定")
        self.assertEqual("draft", controller.plan_topic(node_uuid).formalization_state)
        controller.materialize_plan_topics()
        self.assertEqual("PlanPlaceholder", controller.get_node(node_uuid).type)
        self.assertEqual("virtual", controller.plan_topic(node_uuid).formalization_state)
        controller.set_plan_title(node_uuid, "touchidle8-touch_idle3")
        self.assertEqual("draft", controller.plan_topic(node_uuid).formalization_state)

    def test_v4_migration_marks_existing_topics_formal_without_heuristics(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "touchidle7-touch_idle7")
        payload = export_document_dict(controller.schema, controller.document)
        payload["format_version"] = 4
        payload.pop("plan_canvas_strokes", None)
        for topic in payload["plan_layout"]["topics"]:
            topic.pop("formalization_state", None)

        loaded = load_document_payload(controller.schema, payload)

        self.assertEqual("PlanPlaceholder", next(node for node in loaded.nodes if node.uuid == node_uuid).type)
        self.assertTrue(all(topic.formalization_state == "formal" for topic in loaded.plan_layout.topics))


class StrokeLayerAndPayloadTests(unittest.TestCase):
    def test_formal_and_plan_strokes_roundtrip_and_do_not_convert(self) -> None:
        controller = ready_controller()
        formal_id = controller.add_canvas_stroke([(0, 0), (10, 10)], "#112233", 3, "formal")
        plan_id = controller.add_canvas_stroke([(20, 20), (30, 30)], "#445566", 5, "plan")
        root_uuid = controller.document.nodes[0].uuid
        controller.create_plan_topic(root_uuid, "touchidle4-touch_idle5")
        controller.materialize_plan_topics()
        self.assertEqual([formal_id], [stroke.uuid for stroke in controller.document.canvas_strokes])
        self.assertEqual([plan_id], [stroke.uuid for stroke in controller.document.plan_canvas_strokes])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v5.json"
            save_document(controller.schema, controller.document, path)
            loaded = load_document(controller.schema, path)

        self.assertEqual([formal_id], [stroke.uuid for stroke in loaded.canvas_strokes])
        self.assertEqual([plan_id], [stroke.uuid for stroke in loaded.plan_canvas_strokes])

    def test_load_document_payload_accepts_bytes_and_rejects_future_or_bad_data(self) -> None:
        controller = ready_controller()
        payload = export_document_dict(controller.schema, controller.document)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(controller.document.nodes), len(load_document_payload(controller.schema, encoded).nodes))
        payload["format_version"] = 999
        with self.assertRaisesRegex(ValueError, "newer"):
            load_document_payload(controller.schema, payload)
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            load_document_payload(controller.schema, b"not-json")


class GraphDiffTests(unittest.TestCase):
    def test_snapshot_excludes_view_state_and_reports_all_structural_categories(self) -> None:
        before_controller = ready_controller()
        before = before_controller.document
        after = copy.deepcopy(before)
        after.canvas_view.scale = 4.0
        after.plan_layout.view.offset_x = 999.0
        self.assertEqual(canonical_graph_snapshot(before), canonical_graph_snapshot(after))

        changed = copy.deepcopy(before)
        root = changed.nodes[0]
        root.ui_position["x"] += 5
        changed.connections = []
        changed.groups.append(GroupRecord(uuid="g", title="group", node_uuids=[]))
        changed.canvas_strokes.append(
            CanvasStrokeRecord("formal-stroke", [(0, 0), (1, 1)], "#123456", 2)
        )
        changed.plan_canvas_strokes.append(
            CanvasStrokeRecord("plan-stroke", [(2, 2), (3, 3)], "#654321", 4)
        )
        diff = diff_documents(before, changed)
        categories = {entry.category for entry in diff.entries}
        self.assertTrue({"nodes", "groups", "formal_strokes", "plan_strokes"} <= categories)
        self.assertFalse(diff.is_empty)


class SvnParsingTests(unittest.TestCase):
    def test_history_runner_uses_argument_arrays_for_info_log_and_cat(self) -> None:
        executor = _StubSvnExecutor()
        runner = SvnHistoryRunner("svn", executor=executor)
        infos = []
        revisions_seen = []
        contents = []
        runner.infoReady.connect(infos.append)
        runner.revisionsReady.connect(
            lambda revisions, has_more: revisions_seen.append((revisions, has_more))
        )
        runner.contentReady.connect(
            lambda revision, payload: contents.append((revision, payload))
        )

        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory) / "graph.json"
            runner.query_info(graph_path)
            self.assertEqual(
                ["info", "--xml", "--depth", "empty", "--", str(graph_path.resolve())],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(
                (
                    '<info><entry kind="file" path="graph.json" revision="8">'
                    '<url>file:///repo/trunk/graph.json</url><repository>'
                    '<root>file:///repo</root><uuid>repo-id</uuid></repository>'
                    '<wc-info><schedule>normal</schedule></wc-info></entry></info>'
                ).encode("utf-8")
            )
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(1, len(infos))

            runner.query_revisions()
            self.assertEqual(
                [
                    "log", "--xml", "-r", "HEAD:0", "--limit", "100",
                    "--", "file:///repo/trunk/graph.json@8",
                ],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(
                b'<log><logentry revision="8"><author>a</author></logentry></log>'
            )
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual([8], [item.revision for item in revisions_seen[0][0]])
            self.assertFalse(revisions_seen[0][1])

            runner.query_content(8)
            self.assertEqual(
                [
                    "cat", "--non-interactive", "-r", "8", "--",
                    "file:///repo/trunk/graph.json@8",
                ],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(b'{"format_version":5}')
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual([(8, b'{"format_version":5}')], contents)

    def test_history_runner_timeout_and_cancel_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory) / "graph.json"

            timeout_executor = _StubSvnExecutor()
            timeout_runner = SvnHistoryRunner("svn", executor=timeout_executor)
            failures: list[str] = []
            timeout_runner.failed.connect(failures.append)
            timeout_runner.query_info(graph_path)
            timeout_runner._timeout()
            self.assertTrue(timeout_executor.killed)
            timeout_executor.finished.emit(-1, QProcess.ExitStatus.CrashExit)
            self.assertTrue(any("60" in message for message in failures))

            cancel_executor = _StubSvnExecutor()
            cancel_runner = SvnHistoryRunner("svn", executor=cancel_executor)
            cancelled: list[bool] = []
            cancel_runner.cancelled.connect(lambda: cancelled.append(True))
            cancel_runner.query_info(graph_path)
            cancel_runner.cancel()
            self.assertTrue(cancel_executor.cancelled)
            cancel_executor.finished.emit(-1, QProcess.ExitStatus.CrashExit)
            self.assertEqual([True], cancelled)

    def test_commit_runner_reuses_actionable_error_mapping(self) -> None:
        runner = SvnCommitRunner("svn")
        seen: list[tuple[bool, str]] = []
        runner.finished.connect(lambda success, message: seen.append((success, message)))
        runner._phase = "commit"
        runner._error_buffer.extend(b"E170001 authentication failed")
        runner._process_finished(1, None)
        self.assertEqual(
            [(False, svn_error_message("E170001 authentication failed", "commit"))],
            seen,
        )

    def test_info_parses_unicode_and_local_copy_source(self) -> None:
        xml = """noise before
<info><entry kind="file" path="图.json" revision="42"><url>file:///repo/branches/图.json</url>
<repository><root>file:///repo</root><uuid>repo-uuid</uuid></repository>
<wc-info><schedule>added</schedule><copy-from-url>file:///repo/trunk/图.json</copy-from-url>
<copy-from-rev>37</copy-from-rev></wc-info></entry></info>noise after"""
        info = parse_info_xml(xml, "图.json")
        self.assertTrue(info.has_history)
        self.assertEqual("file:///repo/trunk/图.json@37", info.history_target)
        self.assertEqual("repo-uuid", info.repository_uuid)

    def test_info_normalizes_svn_add_schedule_and_new_files_have_no_history(self) -> None:
        xml = """<info><entry kind="file" path="new.json" revision="0">
<url>file:///repo/trunk/new.json</url><repository><root>file:///repo</root>
<uuid>repo-uuid</uuid></repository><wc-info><schedule>add</schedule></wc-info>
</entry></info>"""
        info = parse_info_xml(xml, "new.json")
        self.assertEqual("added", info.working_copy_status)
        self.assertFalse(info.has_history)

    def test_log_parses_orders_and_deduplicates_revisions(self) -> None:
        xml = """warning
<log><logentry revision="8"><author>甲</author><date>2026-01-01</date><msg>新</msg></logentry>
<logentry revision="3"><author>乙</author><date>2025-01-01</date><msg>旧</msg></logentry>
<logentry revision="8"><author>甲</author><msg>重复</msg></logentry></log>"""
        revisions = parse_log_xml(xml)
        self.assertEqual([8, 3], [item.revision for item in revisions])
        self.assertEqual("甲", revisions[0].author)

    def test_errors_are_actionable(self) -> None:
        self.assertIn("凭据", svn_error_message("E170001 authentication failed", "cat"))
        self.assertIn("证书", svn_error_message("server certificate verification failed", "log"))
        self.assertIn("网络", svn_error_message("could not resolve hostname", "info"))
        self.assertIn("没有 SVN 历史", svn_error_message("is not a working copy", "info"))

    @unittest.skipUnless(
        shutil.which("svn") and shutil.which("svnadmin"),
        "svn and svnadmin are not installed",
    )
    def test_file_repository_history_can_be_loaded_and_diffed_without_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            imported = root / "imported"
            working_copy = root / "working-copy"
            imported.mkdir()
            subprocess.run(["svnadmin", "create", str(repository)], check=True)

            controller = ready_controller()
            imported_graph = imported / "graph.json"
            save_document(controller.schema, controller.document, imported_graph)
            repository_url = repository.as_uri()
            subprocess.run(
                ["svn", "import", str(imported), repository_url, "-m", "initial"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["svn", "checkout", repository_url, str(working_copy)],
                check=True,
                capture_output=True,
            )

            checked_out_graph = working_copy / "graph.json"
            loaded = load_document(controller.schema, checked_out_graph)
            changed = EditorController(controller.schema)
            changed.document = loaded
            changed.create_node("Comment", (300.0, 200.0))
            save_document(changed.schema, changed.document, checked_out_graph)
            subprocess.run(
                ["svn", "commit", str(checked_out_graph), "-m", "add note"],
                check=True,
                capture_output=True,
            )

            info_payload = subprocess.run(
                [
                    "svn", "info", "--xml", "--depth", "empty", "--",
                    str(checked_out_graph),
                ],
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
            info = parse_info_xml(info_payload, checked_out_graph)
            log_payload = subprocess.run(
                [
                    "svn", "log", "--xml", "-r", "HEAD:0", "--limit", "100",
                    "--", info.history_target,
                ],
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
            revisions = parse_log_xml(log_payload)
            self.assertGreaterEqual(len(revisions), 2)

            documents = []
            for revision in (revisions[-1].revision, revisions[0].revision):
                content = subprocess.run(
                    [
                        "svn", "cat", "--non-interactive", "-r", str(revision),
                        "--", info.history_target,
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                documents.append(load_document_payload(controller.schema, content))
            self.assertFalse(diff_documents(*documents).is_empty)
            self.assertFalse(
                any("diff" in path.name.lower() for path in working_copy.iterdir())
            )


if __name__ == "__main__":
    unittest.main()
