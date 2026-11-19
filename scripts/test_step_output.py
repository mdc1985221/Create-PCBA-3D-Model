"""Export destinations preserve project originals and previous external files."""
from pathlib import Path
import json,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from export_support import folder_path,save_verified_step
from viewer_support import upgrade_step_export

class StepOutputTests(unittest.TestCase):
    def test_verified_copy_and_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';root.mkdir();source=root/'板.stp';source.write_bytes(b'verified geometry')
            proof=source.with_suffix('.validation.json');proof.write_text(json.dumps(dict(valid=True,read_back=True,file=source.name)))
            self.assertEqual(save_verified_step(root,source)['path'],str(source.resolve()))
            other=Path(tmp)/'另存';first=save_verified_step(root,source,other);saved=Path(first['path'])
            self.assertEqual(saved.read_bytes(),source.read_bytes());self.assertTrue(first['external'])
            second=Path(save_verified_step(root,source,other)['path']);self.assertNotEqual(saved,second)
            self.assertEqual(saved.read_bytes(),source.read_bytes());self.assertEqual(json.loads(second.with_suffix('.validation.json').read_text(encoding='utf8'))['file'],second.name)
            proof.write_text(json.dumps(dict(valid=False,read_back=True)))
            with self.assertRaisesRegex(ValueError,'回读'):save_verified_step(root,source,Path(tmp)/'bad')
            self.assertFalse((Path(tmp)/'bad').exists())

    def test_folder_validation_and_copy_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);assets=home/'assets';assets.mkdir();source=home/'板.stp';source.write_bytes(b'original');source.with_suffix('.validation.json').write_text('{"valid":true,"read_back":true}')
            self.assertEqual(folder_path('',home,assets),'')
            self.assertEqual(folder_path('export',home,assets),str(home/'export'))
            for value in [42,True,'a\nb',str(assets/'internal'),str(source)]:
                with self.assertRaises(ValueError):folder_path(value,home,assets)
            target=home/'target'
            with patch('export_support.shutil.copy2',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):save_verified_step(home,source,target)
            self.assertEqual(list(target.iterdir()),[]);self.assertEqual(source.read_bytes(),b'original')

    def test_old_viewer_upgrade_is_idempotent(self):
        old='''<div class="sec"><a id="exportResult" hidden>PNG</a></div><script>const MODEL_MAP = {"PKG":"PRIVATE"};const calibration={x:2};</script>'''
        result=upgrade_step_export(old,ROOT/'assets')
        self.assertIn('id="stepExport"',result);self.assertIn('startStepExport',result)
        self.assertIn('const MODEL_MAP = {"PKG":"PRIVATE"}',result);self.assertIn('const calibration={x:2}',result)
        self.assertEqual(result,upgrade_step_export(result,ROOT/'assets'))
        self.assertEqual(upgrade_step_export('unrecognised',ROOT/'assets'),'unrecognised')

    def test_initial_and_reexport_routes_keep_selected_folder(self):
        # Native geometry is independently tested by browser/EXE integration.
        # Here isolate the two application routes and their saved metadata.
        from app import Application
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);project=home/'project';app=Application(ROOT/'assets',ROOT/'ui',home)
            app.jobs['abc']=dict(id='abc',state='running',output=str(project),messages=[])
            def build(*args):
                project.mkdir();(project/'data.json').write_text('{"board":{"name":"Board"}}',encoding='utf8')
                (project/'导入检查.json').write_text('{"components":0}',encoding='utf8')
                return dict(components=0,incomplete=False)
            def native(root,path,progress):
                path.write_bytes(b'route fixture');path.with_suffix('.validation.json').write_text('{"valid":true,"read_back":true}',encoding='utf8')
            with patch('generator.build',side_effect=build),patch('cad_worker.export_isolated',side_effect=native):
                app.run_job('abc',project,{},dict(name='Board',step=True,step_output=str(home/'custom')),False)
                job=app.jobs['abc'];self.assertEqual(job['state'],'done',job.get('error'))
                self.assertTrue(Path(job['report']['step_export']['path']).is_relative_to(home/'custom'))
                self.assertTrue(any(p['name']=='Board.stp' for p in job['downloads']))
                job['step_output']='';app.export_project_worker('abc')
                self.assertEqual(job['state'],'done',job.get('error'))
                self.assertEqual(job['report']['step_export']['path'],str(project/'Board.stp'))
                self.assertEqual(json.loads((project/'生成配置.json').read_text(encoding='utf8'))['step_output'],'')

if __name__=='__main__':unittest.main()
