"""An offline template must produce reusable, valid native CAD at given size."""
from pathlib import Path
import json,sys,tempfile,unittest,copy
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
from create_dfn_demo import create

class DfnDemoTests(unittest.TestCase):
    def setUp(self):self.spec=json.loads((ROOT/'examples/package-template/dfn12.json').read_text())

    def test_invalid_pitch_does_not_create_a_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            self.spec['pitch']=2
            with self.assertRaisesRegex(ValueError,'Inconsistent'):create(Path(folder),self.spec)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_native_dimensions_and_library_restart(self):
        from user_models import commit,read_catalog
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.BRepCheck import BRepCheck_Analyzer
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);result=create(home,self.spec)
            self.assertEqual([round(v,6) for v in result['dimensions_mm']],[3,2,.75])
            commit(home,result['draft']);model=read_catalog(home/'用户模型库')[result['model']]
            self.assertEqual(len(model['native_step_parts']),15)
            self.assertEqual(model['source']['parameters']['pin_count'],12)
            self.assertEqual(model['source']['parameters']['pin1'],'bl')
            for part in model['native_step_parts']:
                path=home/'用户模型库'/part['path'];reader=STEPControl_Reader()
                self.assertEqual(reader.ReadFile(str(path)),IFSelect_RetDone)
                self.assertTrue(reader.TransferRoots())
                self.assertTrue(BRepCheck_Analyzer(reader.OneShape()).IsValid())

if __name__=='__main__':unittest.main()
