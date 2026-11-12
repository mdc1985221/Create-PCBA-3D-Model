"""Refresh synthetic snapshots while preserving calibration and board evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from calibration import dump,read
from project_refresh import refresh


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name);self.assets=self.base/'assets';self.assets.mkdir()
        self.project=self.base/'project';self.project.mkdir()
        (self.assets/'viewer.html').write_text((ROOT/'assets/viewer.html').read_text(encoding='utf8'),encoding='utf8')
        dump(self.assets/'aliases.json',{})
        (self.project/'index.html').write_text('''<div class="sub">PCB<br>1 个器件<br>checked</div>
<details id="pendingSection"></details><script>const MODEL_MAP = {"R0402":"R"};
const MODEL_BY_REF = {}; const ASSET_VERSION = "old";</script>''',encoding='utf8')
        p=dict(ref='R1',pkg='R0402',side='top',x=5,y=5,rot=0,population='populated')
        q=dict(ref='U1',pkg='BGA24',side='bottom',x=10,y=10,rot=90,population='populated',reason='unmatched_model',message='missing')
        dump(self.project/'data.json',dict(placements={'R1':p},pending_components=[q],counts={'top':1,'bottom':0,'accessories':0}))
        dump(self.project/'model_mapping.json',{'R0402':'R'})
        dump(self.project/'models/models.json',{}) if (self.project/'models').exists() else (self.project/'models').mkdir()
        dump(self.project/'models/models.json',{'R':{'parts':[]}})
        dump(self.project/'导入检查.json',dict(components=1,accessories=0,warnings=[],incomplete=True))
        dump(self.project/'生成配置.json',dict(mapping={'R0402':'R'},thickness=1.2))
        self.cal=dict(version=1,items={'R1':dict(dx=.2,dy=-.1,dz=.02,dyaw=9,source='reference')})
        dump(self.project/'calibration.json',self.cal)
        dump(self.project/'posefix.json',{'R1':{'dyaw':90}})
        dump(self.project/'manufacturing.json',dict(version=1,mount_types={'R1':'THT'},last_export={'files':[]}))
        (self.project/'board_top.png').write_bytes(b'board evidence')
        self.library={'R':{'parts':[]},'BGA':{'parts':[]},'R_NEW':{'parts':[]}}

    def tearDown(self):self.temp.cleanup()

    def apply(self,aliases):
        return refresh(self.assets,self.project,self.library,aliases,self.base/'users')

    def test_add_and_replace_with_saved_data(self):
        (self.project/'old.stp').write_text('old export')
        result=self.apply({'BGA24':'BGA','R0402':'R_NEW'})
        self.assertEqual(result['added'],['U1']);self.assertEqual(result['replaced'],['R1'])
        self.assertEqual(result['pending'],0)
        self.assertEqual(read(self.project/'data.json')['counts'],dict(top=1,bottom=1,accessories=0))
        self.assertEqual(read(self.project/'calibration.json'),self.cal)
        self.assertEqual(read(self.project/'posefix.json'),{'R1':{'dyaw':90}})
        self.assertEqual((self.project/'board_top.png').read_bytes(),b'board evidence')
        self.assertEqual(read(self.project/'manufacturing.json')['mount_types'],{'R1':'THT'})
        self.assertNotIn('last_export',read(self.project/'manufacturing.json'))
        self.assertTrue(list((self.project/'历史导出').rglob('old.stp')))
        self.assertTrue((self.project/result['backup']/'data.json').is_file())
        self.assertFalse(self.apply({'BGA24':'BGA','R0402':'R_NEW'})['changed'])

    def test_native_file_failure_preserves_snapshot(self):
        before={p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.library['BROKEN']={'parts':[],'native_step_parts':[dict(path='models/user/missing.step',mat='body')]}
        with self.assertRaisesRegex(ValueError,'原生 STEP'):
            self.apply({'BGA24':'BROKEN'})
        self.assertEqual(before,{p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_missing_coordinates_never_invented(self):
        data=read(self.project/'data.json');data['pending_components'][0]['reason']='missing_coordinates'
        dump(self.project/'data.json',data)
        result=self.apply({'BGA24':'BGA'})
        self.assertFalse(result['changed']);self.assertEqual(result['pending'],1)
        self.assertNotIn('U1',read(self.project/'data.json')['placements'])

    def test_ui_only_update_preserves_exports_and_geometry(self):
        (self.project/'old.stp').write_text('valid export')
        result=self.apply({})
        self.assertFalse(result['changed']);self.assertTrue(result['viewer_updated'])
        self.assertTrue((self.project/result['backup']/'index.html').is_file())
        self.assertEqual((self.project/'old.stp').read_text(),'valid export')
        self.assertEqual(read(self.project/'calibration.json'),self.cal)
        self.assertFalse(self.apply({}).get('viewer_updated',False))


if __name__=='__main__':unittest.main()
