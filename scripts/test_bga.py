"""Synthetic regression for pad-derived BGA models; no production data needed."""
import json
import base64
import io
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bga_support import bom_marking, candidates, infer, stage_bga, board_reference


class BgaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.points = [(x*.8, y*.8) for x in range(-2, 3) for y in range(-2, 3)
                       if (x, y) != (2, 1)]
        self.data = dict(board={}, placements={'U1': dict(ref='U1', pkg='BGA24',
                         side='bottom', x=10, y=20, rot=90)}, bom=[dict(
                         refs='U1', desc='芯片-TEST123-BGA24-控制器', val='')])
        self.write('data.json', self.data)
        self.write('pads.json', {'bottom': [[10+x, 20+y, .3, .3, 'C'] for x,y in self.points]})
        self.write('alignment_pads.json', {'bottom': []})

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value), encoding='utf8')

    def test_asymmetric_bottom_array_and_marking(self):
        result = infer(self.root, 'U1')
        self.assertEqual(result['count'], 24)
        self.assertEqual(result['pitch'], [.8, .8])
        self.assertEqual(result['marking'], 'TEST123')
        self.assertEqual(sorted(map(tuple, result['points'])), sorted((y, x) for x,y in self.points))
        self.assertTrue(candidates(self.root))

    def test_mask_fallback(self):
        pads = json.loads((self.root/'pads.json').read_text())
        self.write('alignment_pads.json', pads)
        self.write('pads.json', {})
        self.assertEqual(infer(self.root, 'U1')['source'], '阻焊开窗')

    def test_mismatch_and_invalid_dimensions_rejected(self):
        self.data['placements']['U1']['pkg'] = 'BGA36'
        self.write('data.json', self.data)
        with self.assertRaisesRegex(ValueError, '球数'):
            stage_bga(self.root, self.root, 'U1', {})
        self.data['placements']['U1']['pkg'] = 'BGA24'
        self.write('data.json', self.data)
        for option in [dict(width=math.nan), dict(width=.1), dict(ball_diameter=.8), dict(a1='bogus')]:
            with self.assertRaises(ValueError):
                stage_bga(self.root, self.root, 'U1', option)
        with self.assertRaisesRegex(ValueError,'确认第一脚'):
            stage_bga(self.root,self.root,'U1',dict(a1='tl'))

    def test_board_reference_respects_bottom_and_xy_rotation(self):
        from PIL import Image,ImageDraw
        for side in ['top','bottom']:
            for angle in [0,90,180,270]:
                # A red landmark at component top-view (+1,+2) must appear upper-right.
                a=math.radians(angle);local_y=2 if side=='top' else -2
                x=20+math.cos(a)-math.sin(a)*local_y;y=20+math.sin(a)+math.cos(a)*local_y
                image=Image.new('RGB',(800,800));draw=ImageDraw.Draw(image)
                draw.ellipse((x*20-6,(40-y)*20-6,x*20+6,(40-y)*20+6),fill=(255,0,0))
                image.save(self.root/('board_top.png' if side=='top' else 'board_bot.png'))
                result=board_reference(self.root,dict(board=dict(w=40,h=40),side=side,rotation=angle,center=[20,20],width=8,depth=8))
                patch=Image.open(io.BytesIO(base64.b64decode(result['image'].split(',')[1])))
                px=round(180+360/result['span']);py=round(180-720/result['span'])
                self.assertGreater(patch.getpixel((px,py))[0],200,(side,angle))

    def test_bom_exact_reference_and_suffix(self):
        data = dict(bom=[dict(refs='U10', desc='芯片-WRONG123-BGA24'),
                         dict(refs='U1,U2', desc='芯片-K4B4G1646E-BYMA-FBGA96-4Gb')])
        self.assertEqual(bom_marking(data, 'U1')[0], 'K4B4G1646E-BYMA')
        self.assertEqual(bom_marking(data, 'U3')[0], '')

    def test_native_model_and_marking_roundtrip(self):
        from OCP.STEPControl import STEPControl_Reader
        draft = stage_bga(self.root, self.root, 'U1', {})
        library = json.loads((self.root/'模型草稿'/draft['draft']/'models.json').read_text(encoding='utf8'))
        model = library[draft['model']]
        self.assertEqual(model['source']['marking'], 'TEST123')
        self.assertEqual(model['mount_type'],'SMT')
        from manufacturing_export import body_bounds
        self.assertFalse(body_bounds(model)['fallback'])
        self.assertEqual(len(model['native_step_parts']), 26)
        for part in model['native_step_parts']:
            reader = STEPControl_Reader()
            self.assertEqual(int(reader.ReadFile(str(self.root/'模型草稿'/draft['draft']/part['path']))), 1)
            reader.TransferRoots()
            self.assertFalse(reader.OneShape().IsNull())

    def test_confirmed_pin1_ink_survives_step_at_selected_corner(self):
        from OCP.STEPControl import STEPControl_Reader
        from user_models import bounds
        for style in ['dot','triangle']:
            draft=stage_bga(self.root,self.root,'U1',dict(a1='br',a1_confirmed=True,pin1_style=style))
            library=json.loads((self.root/'模型草稿'/draft['draft']/'models.json').read_text(encoding='utf8'))
            model=library[draft['model']]
            self.assertEqual(model['source']['a1'],'br');self.assertTrue(model['source']['a1_confirmed'])
            self.assertEqual(model['source']['pin1_style'],style)
            self.assertEqual(len(model['native_step_parts']),27)
            path=self.root/'模型草稿'/draft['draft']/model['native_step_parts'][-1]['path']
            reader=STEPControl_Reader();self.assertEqual(int(reader.ReadFile(str(path))),1);reader.TransferRoots()
            box=bounds(reader.OneShape())
            # Stored model axes are X, height, -source Y.
            self.assertGreater(box[0],0);self.assertGreater(box[2],0)
            self.assertAlmostEqual(box[4]-box[1],.015,places=5)


if __name__ == '__main__':
    unittest.main()
