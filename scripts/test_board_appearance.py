"""Colour changes preserve manufacturing geometry, artwork and coordinates."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from board_appearance import THEMES,appearance,read,save
from board_surfaces import board_layers
from app import Application


class BoardColourTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        # Separate regions, including copper under mask, exposed pads and silk.
        self.rgb=np.full((32,40,3),THEMES['green']['base'],dtype=np.uint8)
        self.rgb[2:10,2:10]=THEMES['green']['copper']
        self.rgb[12:20,2:10]=[178,186,190]
        self.rgb[22:30,2:10]=[143,130,81]
        self.rgb[2:10,12:20]=THEMES['green']['silk']
        for side in ('top','bot'):
            Image.fromarray(self.rgb).save(self.root/f'board_{side}.png')
            Image.new('L',(40,32),175).save(self.root/f'board_{side}_rough.png')
            Image.new('L',(40,32),0).save(self.root/f'board_{side}_metal.png')
        for name,data in [('data.json',{'placements':{'R1':{'x':5,'rot':90}}}),
                          ('calibration.json',{'items':{'R1':{'dx':.2}}}),
                          ('board_geometry.json',{'outline':[[0,0],[40,0],[40,32],[0,32]]}),
                          ('manufacturing.json',{'last_export':{'files':['unchanged.csv']}}),
                          ('生成配置.json',{'thickness':1.2,'mapping':{'R0402':'R'}})]:
            (self.root/name).write_text(json.dumps(data),encoding='utf8')

    def tearDown(self):self.temp.cleanup()

    def test_all_colours_roundtrip_and_preserve_geometry(self):
        frozen={p.name:p.read_bytes() for p in self.root.iterdir() if p.name not in {'生成配置.json'} and not p.name.startswith('board_')}
        frozen.update({f'board_{s}_metal.png':(self.root/f'board_{s}_metal.png').read_bytes() for s in ('top','bot')})
        for key in ['red','yellow','blue','white','black','green']:
            save(self.root,key)
            self.assertEqual(appearance(self.root)['color'],key)
            for side in ('top','bot'):
                rgb=np.array(Image.open(self.root/f'board_{side}.png'))
                self.assertEqual(rgb[0,0].tolist(),THEMES[key]['base'])
                self.assertEqual(rgb[4,4].tolist(),THEMES[key]['copper'])
                self.assertEqual(rgb[4,14].tolist(),THEMES[key]['silk'])
                self.assertEqual(rgb[15,4].tolist(),[178,186,190])
                self.assertEqual(rgb[25,4].tolist(),[143,130,81])
            self.assertEqual(read(self.root,'生成配置.json',{})['thickness'],1.2)
        self.assertTrue(np.array_equal(np.array(Image.open(self.root/'board_top.png')),self.rgb))
        for name,raw in frozen.items():self.assertEqual((self.root/name).read_bytes(),raw,name)

    def test_step_artwork_shapes_unchanged_with_dark_silk(self):
        geometry=read(self.root,'board_geometry.json',{})
        before=board_layers(self.root,40,32,geometry)
        save(self.root,'white');after=board_layers(self.root,40,32,geometry)
        self.assertEqual([(q['kind'],q['polygons']) for q in before],[(q['kind'],q['polygons']) for q in after])
        self.assertEqual(next(q for q in after if q['kind']=='SILK')['colour'],[v/255 for v in THEMES['white']['silk']])

    def test_invalid_or_incomplete_change_leaves_project_intact(self):
        for key in ['purple',None,[],{}]:
            with self.assertRaises(ValueError):save(self.root,key)
        (self.root/'board_bot.png').unlink()
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaises(FileNotFoundError):save(self.root,'red')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_archive_stale_step_but_keep_coordinates(self):
        (self.root/'old.stp').write_bytes(b'previous export')
        save(self.root,'blue')
        self.assertFalse((self.root/'old.stp').exists())
        self.assertEqual(next((self.root/'历史导出').rglob('old.stp')).read_bytes(),b'previous export')
        self.assertTrue(read(self.root,'导入检查.json',{})['step_needs_export'])
        self.assertFalse(save(self.root,'blue')['changed'])

    def test_api_and_browser_use_same_palette(self):
        html=(ROOT/'assets/viewer.html').read_text(encoding='utf8')
        palettes=json.loads(re.search(r'const BOARD_THEMES = (.*);',html)[1])
        self.assertEqual(palettes,THEMES)
        app=Application(ROOT/'assets',ROOT/'ui',self.root)
        app.jobs['test']=dict(output=str(self.root),preview='/project/test/index.html')
        self.assertTrue(app.appearance_operation(dict(id='test',color='red'))['saved'])
        app.busy=True
        with self.assertRaisesRegex(ValueError,'正在生成或导出'):app.appearance_operation(dict(id='test',color='white'))
        self.assertEqual(appearance(self.root)['color'],'red')


if __name__=='__main__':unittest.main()
