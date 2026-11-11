"""BOM discovery and duplicate diagnostics; synthetic data only."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from generator import bom_duplicate_error, bom_row_location, build, header, refs
from input_support import inspect_files, review_files


class BomImportTests(unittest.TestCase):
    def test_excel_row_is_not_bom_item_number(self):
        _, columns = header([['序号','物料编码','名称','描述','用量','位号']], ['ref'])
        location = bom_row_location('module.xlsx', 46, columns, ['44','','','','10','R1,R1'])
        self.assertEqual(location, 'Excel 第 46 行（BOM 序号 44；位号单元格 F46）')
        message = bom_duplicate_error(['R1','R1'], {}, 46, '2', location)
        self.assertIn(location+'位号重复：R1；', message)
        self.assertNotIn('BOM 序号 46', message)

    def test_no_sequence_column_does_not_invent_an_item_number(self):
        self.assertEqual(bom_row_location('module.xlsx', 2, {'ref': 0}, ['R1']),
                         'Excel 第 2 行（位号单元格 A2）')
        self.assertEqual(bom_row_location('module.csv', 2, {'ref': 0}, ['R1']), '第 2 行')

    def test_review_preserves_item_number_and_source_row(self):
        content = '序号,用量,位号\n44,2,"R1,R1"\n46,1,R2\n'.encode()
        rows = review_files({'module.csv': content}, {'bom': 'module.csv'}, {}, set())['bom_rows']
        self.assertEqual(rows[0]['row'], 2)
        self.assertIn('BOM 序号 44', rows[0]['location'])
        self.assertIn('BOM 序号 44', rows[0]['issues'][0])
        self.assertEqual(rows[1]['duplicates'], [])

    def test_cross_row_error_keeps_both_item_numbers(self):
        content = '序号,用量,位号\n44,1,R1\n46,1,R1\n'.encode()
        row = review_files({'module.csv': content}, {'bom': 'module.csv'}, {}, set())['bom_rows'][1]
        self.assertIn('第 3 行（BOM 序号 46）', row['issues'][0])
        self.assertIn('第 2 行（BOM 序号 44）', row['issues'][0])

    def test_filename_is_not_required(self):
        files = {'module.csv': '位号,用量,值,封装\nR1,1,1K,R0402\n'.encode()}
        candidate = inspect_files(files)['candidates'][0]
        self.assertEqual(candidate['roles'], ['bom'])
        self.assertEqual(candidate['value'], 'module.csv')

    def test_zip_without_bom_filename(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('production/module.csv', 'Ref,Qty,Value\nR1,1,1K\n')
        candidate = inspect_files({'production.zip': buffer.getvalue()})['candidates'][0]
        self.assertEqual(candidate['roles'], ['bom'])
        self.assertEqual(candidate['value'], 'production/module.csv')

    def test_only_actual_within_row_duplicates_are_reported(self):
        message = bom_duplicate_error(refs('R1,R2,R3,R2,R3'), {}, 46, '5')
        self.assertTrue(message.startswith('BOM 第 46 行位号重复：R2,R3；'), message)
        self.assertIn('不同位号 3', message)
        self.assertIn('用量 5', message)

    def test_cross_row_and_within_row_duplicates_are_combined(self):
        message = bom_duplicate_error(refs('R1,R2,R2,R3'), {'R1': 5}, 9, '4')
        self.assertTrue(message.startswith('BOM 第 9 行位号重复：R1,R2；'), message)
        self.assertIn('R1（第 5 行）', message)

    def test_valid_rows_and_ranges_are_not_duplicates(self):
        self.assertEqual(bom_duplicate_error(refs('R1-R3'), {'R4': 3}, 5, '3'), '')
        self.assertEqual(bom_duplicate_error(refs('R1,R10,R100'), {}, 5, '3'), '')

    def test_review_reports_quantity_and_exact_duplicates(self):
        files = {'module.csv': b'Ref,Qty,Value\n"R1,R2,R3,R2",4,1K\n'}
        review = review_files(files, {'bom': 'module.csv'}, {}, set())
        row = review['bom_rows'][0]
        self.assertEqual(row['duplicates'], ['R2'])
        self.assertTrue(any('用量 4 与不同位号数 3 不符' in message for message in row['issues']))

    def test_quantity_issue_without_duplicates(self):
        files = {'module.csv': b'Ref,Qty,Value\n"R1,R2",3,1K\n'}
        row = review_files(files, {'bom': 'module.csv'}, {}, set())['bom_rows'][0]
        self.assertEqual(row['duplicates'], [])
        self.assertIn('用量 3 与不同位号数 2 不符', row['issues'][0])

    def test_exclusions_remain_reversible_and_do_not_create_duplicates(self):
        files = {'module.csv': b'Ref,Qty,Value\nR1,1,1K\nR1,1,1K\n'}
        config = {'bom': 'module.csv', 'exclude_bom_rows': [2]}
        rows = review_files(files, config, {}, set())['bom_rows']
        self.assertTrue(rows[0]['excluded'])
        self.assertEqual(rows[1]['duplicates'], [])
        restored = review_files(files, {'bom': 'module.csv'}, {}, set())['bom_rows']
        self.assertEqual([row['duplicates'] for row in restored], [['R1'], ['R1']])

    def test_build_uses_same_precise_validation(self):
        files = {'xy.csv': b'Ref,X,Y,Package\nR1,5,5,R0402\nR2,6,5,R0402\n',
                 'module.csv': b'Ref,Qty,Value\n"R1,R2,R2",3,1K\n'}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'BOM 第 2 行位号重复：R2；'):
                build(ROOT / 'assets', Path(folder), files,
                      {'xy': 'xy.csv', 'bom': 'module.csv', 'width': 20, 'height': 20}, lambda _: None)


if __name__ == '__main__':
    unittest.main()
