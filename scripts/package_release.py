"""Bundle only redistributable runtime files, with complete external models/licenses."""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dist',default='dist');args=parser.parse_args()
    source=(ROOT/args.dist).resolve();assert (source/'PCBA3D.exe').is_file()
    release=ROOT/'release';release.mkdir(exist_ok=True)
    name='PCBA3D-1.3.1-public-Windows-x64';package=release/name;package.mkdir(exist_ok=True)
    shutil.copy2(source/'PCBA3D.exe',package/'PCBA3D.exe')
    for path in ['用户模型库','thirdparty_licenses','docs','examples']:
        shutil.copytree(ROOT/path,package/path,dirs_exist_ok=True)
    for name_ in ['README.md','LICENSE','THIRD_PARTY_NOTICES.md','aliases.json']:
        shutil.copy2(ROOT/name_,package/name_)
    if (source/'build-info.json').exists():shutil.copy2(source/'build-info.json',package/'build-info.json')
    for name_ in ['自检结果.json']:
        if (source/name_).exists():shutil.copy2(source/name_,package/name_)
    assert not list(package.rglob('API设置.json'))
    assert not (package/'工程').exists()
    zip_path=release/(name+'.zip')
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(release).as_posix())
    digest=hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (release/'SHA256SUMS.txt').write_text(digest+'  '+zip_path.name+'\n',encoding='ascii')
    print(json.dumps(dict(file=str(zip_path),sha256=digest,bytes=zip_path.stat().st_size)))
if __name__=='__main__':main()
