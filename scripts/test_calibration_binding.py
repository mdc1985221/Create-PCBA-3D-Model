"""Placement binding must follow each XY angle and side, never copy a board location."""
import json
from pathlib import Path
import sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from app import Application
from calibration import prepare,read


class CalibrationBindingTests(unittest.TestCase):
    def test_persistent_binding_uses_local_offsets_for_each_side_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);project=home/'board';project.mkdir()
            def write(name,data):(project/name).write_text(json.dumps(data),encoding='utf8')
            placement=dict(ref='R1',pkg='SPECIAL_R0402',side='top',x=21,y=32,rot=90)
            write('data.json',dict(placements={'R1':placement}))
            write('model_mapping.json',{'SPECIAL_R0402':'R_0402'})
            write('posefix.json',{})
            (project/'index.html').write_text('const MODEL_BY_REF = {};',encoding='utf8')
            app=Application(ROOT/'assets',ROOT/'ui',home)
            app.jobs['1234']=dict(output=str(project),preview='/project/1234/index.html',report={})
            before=read(project/'data.json')
            result=app.calibration_operation(dict(id='1234',ref='R1',values=dict(dx=.4,dy=1.2,dz=.1,dyaw=90),save_rule=True,save_binding=True))
            self.assertTrue(result['binding_saved']);self.assertTrue(result['rule_saved'])
            self.assertEqual(read(project/'data.json'),before)
            restarted=Application(ROOT/'assets',ROOT/'ui',home)
            self.assertEqual(restarted.user_aliases()['SPECIAL_R0402'],'R_0402')
            rules=read(home/'calibration_rules.json')
            expected={('top',0):(1.2,-.4,90),('top',90):(.4,1.2,90),('top',180):(-1.2,.4,90),('top',270):(-.4,-1.2,90),
                      ('bottom',0):(1.2,.4,-90),('bottom',90):(-.4,1.2,-90),('bottom',180):(-1.2,-.4,-90),('bottom',270):(.4,-1.2,-90)}
            for (side,rot),(dx,dy,yaw) in expected.items():
                target=dict(placement,ref='R2',x=2,y=3,side=side,rot=rot)
                value=prepare({'R2':target},{},{'SPECIAL_R0402':'R_0402'},rules)['items']['R2']
                self.assertAlmostEqual(value['dx'],dx);self.assertAlmostEqual(value['dy'],dy)
                self.assertEqual(value['dyaw'],yaw);self.assertEqual(value['dz'],.1)
                self.assertLess(abs(value['dx']),2);self.assertLess(abs(value['dy']),2)

    def test_single_reference_save_does_not_change_global_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);project=home/'board';project.mkdir()
            for name,data in [('data.json',{'placements':{'R1':dict(ref='R1',pkg='R0402',side='top',x=1,y=2,rot=0)}}),('model_mapping.json',{'R0402':'R_0402'})]:
                (project/name).write_text(json.dumps(data),encoding='utf8')
            (project/'index.html').write_text('const MODEL_BY_REF = {};',encoding='utf8')
            app=Application(ROOT/'assets',ROOT/'ui',home);app.jobs['1234']=dict(output=str(project),preview='preview',report={})
            result=app.calibration_operation(dict(id='1234',ref='R1',values=dict(dx=1,dy=2,dyaw=180)))
            self.assertFalse(result['binding_saved']);self.assertFalse(result['rule_saved'])
            self.assertFalse((home/'aliases.json').exists());self.assertFalse((home/'calibration_rules.json').exists())

if __name__=='__main__':unittest.main()
