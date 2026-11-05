"""Run the built EXE itself through the synthetic import and STEP path."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    exe=(ROOT/'dist/PCBA3D.exe').resolve()
    output=ROOT/'_work/exe-smoke';output.mkdir(parents=True,exist_ok=True)
    request=dict(files=[str(p.resolve()) for p in (ROOT/'examples/synthetic').iterdir() if p.is_file() and p.suffix!='.md'],
        output=str(output),config=dict(name='Synthetic_EXE',xy='xy.csv',bom='bom.csv',width=40,height=30,
            thickness=1.6,auto_board=True,auto_pose=False,step=True))
    config=ROOT/'_work/exe-request.json';config.write_text(json.dumps(request),encoding='utf8')
    subprocess.run([str(exe),'--generate-config',str(config)],check=True,timeout=300)
    project=max(output.iterdir(),key=lambda p:p.stat().st_mtime)
    report=json.loads((project/'导入检查.json').read_text(encoding='utf8'))
    validation=json.loads(next(project.glob('*.validation.json')).read_text(encoding='utf8'))
    assert report['components']==6 and report['top']==4 and report['bottom']==2 and not report['incomplete'],report
    assert validation['valid'] and validation['read_back'] and validation['components']==6,validation
    print(json.dumps(dict(ok=True,exe=str(exe),components=6,step_readback=True)))
if __name__=='__main__':main()
