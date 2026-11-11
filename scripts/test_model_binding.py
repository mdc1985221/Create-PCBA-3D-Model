"""Exercise import, persistent binding and the exact model-preview HTTP route."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from app import Application,make_server
from user_models import binding_aliases,model_summary,read_catalog

class ModelBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.app=Application(ROOT/'assets',ROOT/'ui',self.home)

    def tearDown(self):self.tmp.cleanup()

    def test_exact_alias_replaces_only_equivalent_target(self):
        result=binding_aliases({'BGA-96':'old','OTHER':'kept'},'BGA_96','new',{'new':{}})
        self.assertEqual(result,{'BGA_96':'new','OTHER':'kept'})
        for target in ['',None,'   ']:
            with self.assertRaises(ValueError):binding_aliases({},target,'new',{'new':{}})
        with self.assertRaises(ValueError):binding_aliases({},'BGA96','missing',{})

    def test_dimensions_use_board_axes(self):
        entry=model_summary('demo',{'parts':[{'v':[-2,-.3,-3,2,1.2,3]}]}, {'FOOTPRINT':'demo'})
        self.assertEqual(entry['dimensions_mm'],[4,6,1.5]);self.assertEqual(entry['aliases'],['FOOTPRINT'])

    def test_passive_aliases_and_manual_override(self):
        from generator import aliases,norm
        from input_support import review_files
        builtins=aliases(ROOT/'assets',{})
        for kind,sizes in [('R',['0201','0402','0603']),('C',['0201','0402','0603','0805','1206'])]:
            for size in sizes:
                for name in ['S'+kind+size,kind+size,size+kind]:self.assertEqual(builtins[norm(name)],kind+'_'+size)
        changed=aliases(ROOT/'assets',{'SR0201':'manual'})
        self.assertEqual(changed[norm('SR0201')],'manual')
        self.assertNotIn(norm('SC0402-BGA'),builtins)
        info=self.app.info()
        files={'xy.csv':b'Ref,X,Y,Rotation,Side,Package\nR1,1,2,0,Top,SR0201\nC1,3,4,0,Top,SC0402\n'}
        review=review_files(files,{'xy':'xy.csv'},info['aliases'],{m['id'] for m in info['models']})
        self.assertEqual({g['pkg']:g['model'] for g in review['footprints']},{'SR0201':'R_0201','SC0402':'C_0402'})

    def test_import_save_only_then_bind_and_restart(self):
        from user_models import stage_parametric
        spec={'primitives':[{'type':'box','center':[0,0,.5],'size':[3,2,1]}]}
        draft=stage_parametric(self.home,'Box',spec)
        saved=self.app.model_operation('/api/models/commit',{'draft':draft['draft'],'footprint':None})
        self.assertIsNone(saved['footprint']);self.assertEqual(self.app.user_aliases(),{})
        self.app.model_operation('/api/models/bind',{'footprint':'Other','model':'USER_Box'})
        self.app.model_operation('/api/models/bind',{'footprint':'BGA-96','model':'USER_Box'})
        self.app.model_operation('/api/models/bind',{'footprint':'BGA_96','model':'USER_Box'})
        restarted=Application(ROOT/'assets',ROOT/'ui',self.home)
        self.assertEqual(restarted.user_aliases(),{'Other':'USER_Box','BGA_96':'USER_Box'})
        self.assertEqual(self.app.model_operation('/api/models/commit',{'draft':draft['draft']})['already_saved'],True)
        with self.assertRaisesRegex(ValueError,'已存在'):stage_parametric(self.home,'Box',spec)
        # Import a real STEP produced by CAD, not just the mesh catalog.
        model=read_catalog(self.home/'用户模型库')['USER_Box'];native=self.home/'用户模型库'/model['native_step_parts'][0]['path']
        imported=self.app.model_operation('/api/models/import',{'name':'Imported','filename':'box.step','data':base64.b64encode(native.read_bytes()).decode(),'options':{}})
        with self.assertRaises(ValueError):self.app.model_operation('/api/models/commit',{'draft':imported['draft'],'footprint':''})
        self.assertNotIn('USER_Imported',self.app.library())
        self.app.model_operation('/api/models/commit',{'draft':imported['draft'],'footprint':'EXACT_FOOTPRINT'})
        self.assertEqual(self.app.user_aliases()['Other'],'USER_Box')
        self.assertEqual(self.app.user_aliases()['EXACT_FOOTPRINT'],'USER_Imported')

    def test_http_preview_and_binding_auth(self):
        server=make_server(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def get(path):
            with urllib.request.urlopen(base+path) as response:return response.read()
        try:
            key=next(iter(self.app.library()));path='/library-preview/'+urllib.parse.quote(key,safe='')+'/'
            html=get(path+'index.html').decode();self.assertIn('pcba-model-preview-ready',html);self.assertIn('顶视 / 引脚分布',html)
            self.assertEqual(list(json.loads(get(path+'models.json'))),[key])
            for invalid in ['/library-preview/missing/models.json',path+'secret.txt']:
                with self.assertRaises(urllib.error.HTTPError):get(invalid)
            body=json.dumps({'footprint':'TARGET','model':key}).encode()
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(base+'/api/models/bind',body,headers={'Content-Type':'application/json'}))
            request=urllib.request.Request(base+'/api/models/bind',body,headers={'Content-Type':'application/json','X-PCBA-Token':self.app.token})
            self.assertTrue(json.load(urllib.request.urlopen(request))['saved'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
