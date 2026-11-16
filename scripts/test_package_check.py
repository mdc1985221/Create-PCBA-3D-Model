import sys,unittest,csv,io,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from package_check import signature,check,files

class PackageTests(unittest.TestCase):
    def inspect(self,bom_pkg,xy,model,desc='',name=''):
        return check({'U1':dict(ref='U1',pkg=xy,xy_pkg=xy)},
                     [dict(refs='U1',pkg=bom_pkg,desc=desc,name=name,location='Excel 第 9 行')],
                     {xy:model},{model:{'parts':[]}})['items'][0]

    def test_size_aliases_and_real_conflicts(self):
        for alias in ['SR0402','R0402','0402R']:
            self.assertEqual(self.inspect(alias,alias,'R_0402')['status'],'matched')
        self.assertEqual(self.inspect('SC0402-BGA','C0402','C_0402')['status'],'matched')
        row=self.inspect('C0603','SC0402','C_0402')
        self.assertEqual(row['status'],'error');self.assertEqual(row['bom_location'],'Excel 第 9 行')
        self.assertEqual(self.inspect('C0402','SC0402','R_0402')['status'],'error')

    def test_pin_counts_and_no_part_number_guessing(self):
        self.assertEqual(self.inspect('SOT23-5','SOT23-5','SOT-23')['status'],'error')
        self.assertEqual(self.inspect('QFN32','QFN32','QFN-48')['status'],'error')
        self.assertEqual(self.inspect('BGA96','BGA96','BGA-96')['status'],'matched')
        self.assertEqual(signature('STM32F0402A1 50V'),{})
        self.assertEqual(signature('SHC0402-2R2M'),{})
        self.assertEqual(signature('WSON 6×8mm 8L'),dict(family='WSON'))
        self.assertEqual(signature('QFN-4X4-32'),dict(family='QFN'))
        self.assertEqual(self.inspect('','SHC0402','IND_SHC','功率电感-2.2uH/5A (SHC0402-2R2M)','贴片电感')['status'],'review')

    def test_description_disagreement_and_unknowns(self):
        self.assertEqual(self.inspect('C0402','C0402','C_0402','电容-0603-X5R','贴片电容')['status'],'error')
        self.assertEqual(self.inspect('','C0402','C_0402','电容-0402-X5R','贴片电容')['status'],'matched')
        self.assertEqual(self.inspect('','C0402','C_0402')['status'],'review')
        row=check({'U1':dict(pkg='BGA96')},[dict(refs='U1',pkg='BGA96')],{'BGA96':'custom'},
                  {'custom':{'source':dict(kind='gerber_bga',count=100)}})['items'][0]
        self.assertEqual(row['status'],'error')
        # A self-declared footprint binding alone cannot verify the model.
        self.assertEqual(self.inspect('BGA96','BGA96','custom')['status'],'review')

    def test_missing_models_and_safe_csv(self):
        result=check({'R1':dict(pkg='R0402')},[dict(refs='R1',pkg='=C0603')],{}, {})
        self.assertEqual(result['summary']['error'],1)
        self.assertEqual(check({'R1':dict(pkg='R0402')},[dict(refs='R1',pkg='R0402')],{}, {})['summary']['review'],1)
        text=files(result)['BOM封装核对.csv']
        row=list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))[1]
        self.assertEqual(row[4],"'=C0603")

    def test_generation_keeps_bom_package_and_model_mismatch(self):
        from generator import build
        root=Path(__file__).resolve().parents[1]
        inputs={p.name:p.read_bytes() for p in (root/'examples/synthetic').iterdir() if p.suffix!='.md'}
        inputs['xy.csv']=b'ref,x,y,rotation,side,package,value\nC1,16,8,90,top,SC0402,1uF\nQ1,8,20,0,bottom,SOT23-5,IC\n'
        inputs['bom.csv']='ref,quantity,package,description\nC1,1,SC0603,电容-0603-X5R\nQ1,1,SOT23-5,芯片-SOT23-5\n'.encode()
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            report=build(root/'assets',out,inputs,dict(xy='xy.csv',bom='bom.csv',width=40,height=30,
                         auto_board=False,auto_pose=False,step=False,mapping={'SOT23-5':'SOT-23'}),lambda m:None)
            self.assertEqual(report['components'],2)
            self.assertEqual(report['package_check']['summary']['error'],2)
            data=json.loads((out/'data.json').read_text(encoding='utf8'))
            self.assertEqual(data['placements']['C1']['bom_pkg'],'SC0603')
            self.assertEqual(data['placements']['C1']['xy_pkg'],'SC0402')
            self.assertIn('3',data['placements']['Q1']['bom_location'])
            self.assertTrue((out/'BOM封装核对.csv').is_file())
            from project_refresh import refresh
            library=json.loads((root/'assets/library/models.json').read_text(encoding='utf8'))
            updated=refresh(root/'assets',out,library,{'SOT23-5':'SOT-23-5'},out)
            self.assertEqual(updated['replaced'],['Q1'])
            result=json.loads((out/'BOM封装核对.json').read_text(encoding='utf8'))
            self.assertEqual(result['summary']['error'],1)
            self.assertEqual(next(r for r in result['items'] if r['ref']=='Q1')['status'],'matched')

if __name__=='__main__':unittest.main()
