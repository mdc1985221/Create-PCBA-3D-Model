"""Bare-board import must retain real geometry without inventing components."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from generator import build
from gerber import infer_board
from input_support import inspect_files,review_files


def loop(x,y,w,h):
    return ('stroke',True,[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)],[])


class PCBOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.out=Path(self.temp.name)/'pcb'
        self.files={p.name:p.read_bytes() for p in (ROOT/'examples/synthetic').iterdir()
                    if p.suffix in {'.art','.gko'}}

    def tearDown(self):self.temp.cleanup()

    def test_only_fabrication_no_xy_no_bom(self):
        report=build(ROOT/'assets',self.out,self.files,dict(name='PCB',auto_board=True,thickness=1.2,step=False),lambda _:None)
        self.assertTrue(report['board_only']);self.assertFalse(report['incomplete'])
        self.assertEqual(report['board_mm'],[40,30,1.2]);self.assertEqual(report['components'],0)
        data=json.loads((self.out/'data.json').read_text(encoding='utf8'))
        self.assertEqual(data['placements'],{});self.assertEqual(data['bom'],[])
        self.assertTrue((self.out/'board_top.png').is_file())
        self.assertTrue((self.out/'board_bot.png').is_file())
        self.assertEqual(json.loads((self.out/'models/models.json').read_text()),{})
        self.assertIn('PCB 裸板',(self.out/'index.html').read_text(encoding='utf8'))

    def test_explicit_bare_mode_ignores_component_inputs(self):
        files={**self.files,'bad.csv':b'not a coordinate table'}
        report=build(ROOT/'assets',self.out,files,dict(board_only=True,xy='bad.csv',bom='bad.csv',auto_board=True,step=False),lambda _:None)
        self.assertEqual(report['components'],0);self.assertFalse(report['pending_components'])
        self.assertEqual(review_files(files,dict(board_only=True,xy='bad.csv'),{},[])['xy_rows'],[])

    def test_explicit_pcba_still_requires_xy(self):
        with self.assertRaisesRegex(ValueError,'PCBA 模式需要 XY'):
            build(ROOT/'assets',self.out,self.files,dict(board_only=False,step=False),lambda _:None)

    def test_unique_outer_preserves_offset_and_cutout(self):
        result=infer_board([loop(12,20,40,30),loop(20,25,4,3)],[])
        self.assertEqual(result['origin'],[12,20]);self.assertEqual(result['size'],[40,30])
        self.assertEqual(result['matched_positions'],0)

    def test_independent_outlines_require_selection(self):
        with self.assertRaisesRegex(ValueError,'无法唯一确定 PCB'):
            infer_board([loop(0,0,20,20),loop(30,0,20,20)],[])
        # Existing XY behaviour still chooses the populated cell.
        self.assertEqual(infer_board([loop(0,0,20,20),loop(30,0,20,20)],[(35,5)])['origin'],[30,0])
        self.assertEqual(infer_board([loop(0,0,20,20),loop(30,0,20,20)],[],[30,0,50,20])['origin'],[30,0])
        with self.assertRaisesRegex(ValueError,'不一致'):
            infer_board([loop(0,0,20,20)],[],[0,0,40,30])

    def test_manual_geometry_without_outline(self):
        files={k:v for k,v in self.files.items() if not k.endswith('.gko')}
        report=build(ROOT/'assets',self.out,files,dict(board_only=True,auto_board=False,width=40,height=30,step=False),lambda _:None)
        self.assertEqual(report['board_mm'],[40,30,1.6]);self.assertIsNone(report['board_detection'])

    def test_discovery_offers_bare_board(self):
        result=inspect_files(self.files)
        self.assertEqual(result['xy_count'],0)
        self.assertTrue(any('PCB 裸板' in s for s in result['warnings']))

    def test_known_npth_breakaways_preserve_internal_holes(self):
        drill='M48\n;TYPE=NON_PLATED\nMETRIC,TZ\nT01C0.6\n%\nT01\nX0.0Y2.0\nX0.0Y3.524\nX0.0Y5.048\nX0.0Y6.572\nX10.0Y10.0\nM30\n'
        report=build(ROOT/'assets',self.out,{**self.files,'holes.drl':drill.encode()},dict(board_only=True,auto_board=True,step=False),lambda _:None)
        self.assertEqual(report['holes'],1)
        self.assertEqual(len(report['drills'][0]['excluded_breakaway_hits']),4)

    def test_plated_edge_holes_are_never_discarded_as_breakaways(self):
        drill='M48\n;TYPE=PLATED\nMETRIC,TZ\nT01C0.6\n%\nT01\nX0.0Y2.0\nX0.0Y3.524\nX0.0Y5.048\nX0.0Y6.572\nM30\n'
        with self.assertRaisesRegex(ValueError,'孔跨越板边'):
            build(ROOT/'assets',self.out,{**self.files,'holes.drl':drill.encode()},dict(board_only=True,auto_board=True,step=False),lambda _:None)


if __name__=='__main__':unittest.main()
