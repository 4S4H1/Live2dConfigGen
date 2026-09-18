from __future__ import annotations

import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from l2d_config_editor.canvas import NodeCanvasView
from l2d_config_editor.controller import EditorController
from l2d_config_editor.csv_export import export_current_document_csv
from l2d_config_editor.logic import create_document, create_node, document_to_csv_rows
from l2d_config_editor.schema import load_editor_schema


class CsvUnicodeNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.schema = load_editor_schema()
        self.document = create_document(self.schema)
        self.document.meta.author = 'tester'
        self.document.meta.ship_skin_id = 1001
        self.document.meta.memo = '角色备注'
        self.document.meta.CharName = '中文角色'
        self.node = create_node(self.schema, self.document, 'ParameterTrigger', (220, 120))
        self.document.nodes.append(self.node)

    def row(self):
        rows = document_to_csv_rows(self.schema, self.document)
        return next(row.values for row in rows if str(row.values['id']) == str(self.node.fields['id']))

    def test_visible_note_is_exported_when_description_is_empty(self) -> None:
        self.node.fields['tips'] = '中文备注，点击「肩膀」：切换待机。'
        self.node.fields['desc'] = ''
        self.assertEqual(self.node.fields['tips'], self.row()['desc'])
        self.assertEqual('角色备注', self.row()['memo'])

    def test_explicit_description_wins_without_changing_either_note(self) -> None:
        self.node.fields['tips'] = '画布备注'
        self.node.fields['desc'] = '  明确导出说明，保留空格  '
        self.assertEqual('  明确导出说明，保留空格  ', self.row()['desc'])
        self.assertEqual('画布备注', self.node.fields['tips'])

    def test_whitespace_description_uses_note(self) -> None:
        self.node.fields['tips'] = '备注'
        self.node.fields['desc'] = ' \t '
        self.assertEqual('备注', self.row()['desc'])

    def test_export_preserves_unicode_quotes_newlines_and_literal_invalid_range(self) -> None:
        note = '中文备注，触摸肩膀 "轻触"\n第二行：切换待机① café 🙂'
        self.node.fields.update(tips=note, desc='', range='｛下限，１｝')
        before = copy.deepcopy(self.node.fields)
        with tempfile.TemporaryDirectory() as folder:
            path = export_current_document_csv(self.schema, self.document, folder)
            raw = path.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        row = next(row for row in rows if row['parameter'] == self.node.fields['parameter'])
        self.assertEqual(note, row['desc'])
        self.assertEqual('｛下限，１｝', row['range'])
        self.assertEqual(before, self.node.fields)

    def test_parameter_table_commit_to_csv_preserves_chinese_punctuation(self) -> None:
        controller = EditorController()
        controller.document = self.document
        controller.refresh_derived()
        view = NodeCanvasView(self.schema, controller)
        try:
            view.rebuild_scene()
            table = view.table_row_to_item[self.node.uuid]
            self.assertTrue(table.begin_cell_edit(self.node.uuid, 'tips'))
            table._editor_proxy.widget().setText('参数专用备注，保留中文标点！')
            table.commit_pending_edit()
            for value in ('{0，1}', '｛０，１｝', '（0，1）', '{下限，上限}'):
                with self.subTest(value=value):
                    table = view.table_row_to_item[self.node.uuid]
                    self.assertTrue(table.begin_cell_edit(self.node.uuid, 'range'))
                    table._editor_proxy.widget().setText(value)
                    table.commit_pending_edit()
                    self.assertEqual(value, controller.get_node(self.node.uuid).fields['range'])
                    with tempfile.TemporaryDirectory() as folder:
                        path = export_current_document_csv(self.schema, controller.document, folder)
                        with Path(path).open(encoding='utf-8-sig', newline='') as handle:
                            rows = list(csv.DictReader(handle))
                    row = next(row for row in rows if row['parameter'] == self.node.fields['parameter'])
                    self.assertEqual(value, row['range'])
                    self.assertEqual('参数专用备注，保留中文标点！', row['desc'])
        finally:
            view.close()
            view.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
