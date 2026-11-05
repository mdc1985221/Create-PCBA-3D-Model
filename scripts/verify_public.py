"""Small, synthetic end-to-end check; contains no real production board data."""
import json, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

def main():
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    from generator import build, aliases
    from user_models import read_catalog
    from manufacturing_export import snapshot
    library = {**read_catalog(ROOT/'assets/library'), **read_catalog(ROOT/'用户模型库')}
    assert len(library) == 64
    user_aliases = json.loads((ROOT/'aliases.json').read_text(encoding='utf8'))
    mapping = aliases(ROOT/'assets', user_aliases)
    assert set(mapping.values()) <= set(library)
    for model in library.values():
        for part in model.get('native_step_parts', []):
            p = (ROOT/'用户模型库'/part['path']).resolve()
            assert p.is_relative_to((ROOT/'用户模型库').resolve()) and p.is_file()
    assert not (ROOT/'API设置.json').exists()
    assert not (ROOT/'assets/sample').exists()
    for folder in ['src','ui','docs']:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.suffix in {'.py','.js','.html','.md','.json'}:
                assert not re.search(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,})', p.read_text(encoding='utf8')), p
    source = ROOT/'examples/synthetic'
    files = {p.name:p.read_bytes() for p in source.iterdir() if p.is_file() and p.suffix != '.md'}
    output = ROOT/'_work/public-smoke';output.mkdir(parents=True, exist_ok=True)
    report = build(ROOT/'assets', output, files, dict(name='PCBA3D_Synthetic', xy='xy.csv', bom='bom.csv',
        width=40, height=30, thickness=1.6, auto_board=True, auto_pose=False, step=True,
        mapping=user_aliases, user_library_root=str(ROOT/'用户模型库')), print)
    assert report['components']==6 and not report['incomplete'], report
    assert report['top']==4 and report['bottom']==2
    validation=json.loads(next(output.glob('*.validation.json')).read_text(encoding='utf8'))
    assert validation['valid'] and validation['read_back'] and validation['components']==6
    state=snapshot(output);assert not state['pending'], state['pending']
    expected={'R1':(8,8,0),'C1':(16,8,90),'L1':(24,8,180),'D1':(32,8,270),'Q1':(8,20,0),'L2':(24,20,90)}
    for row in state['rows']:
        x,y,rot=expected[row['ref']]
        assert row['eligible'] and math.hypot(row['x']-x,row['y']-y)<.001 and row['rotation']==rot, row
    result=dict(ok=True,models=len(library),components=6,top=4,bottom=2,step_readback=True,
                coordinates=True,synthetic_only=True,api_credentials_included=False)
    (ROOT/'_work/public-verification.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))

if __name__=='__main__':main()
