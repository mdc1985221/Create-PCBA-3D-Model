"""Project soldermask appearance; recolour only known mask and silk pixels."""
import json
from pathlib import Path
import shutil
import tempfile
import time

import numpy as np
from PIL import Image

THEMES = {
    'green': dict(label='绿色', base=[16,77,46], copper=[18,83,49], silk=[225,229,216], rough=175, coat=.16),
    'red': dict(label='红色', base=[133,19,25], copper=[150,24,31], silk=[225,229,216], rough=175, coat=.16),
    'yellow': dict(label='黄色', base=[193,151,15], copper=[211,171,22], silk=[35,37,40], rough=185, coat=.12),
    'blue': dict(label='蓝色', base=[13,46,125], copper=[17,55,145], silk=[225,229,216], rough=175, coat=.16),
    'white': dict(label='白色', base=[216,220,218], copper=[228,231,229], silk=[35,37,40], rough=195, coat=.08),
    'black': dict(label='哑黑色', base=[15,17,20], copper=[23,25,29], silk=[225,229,216], rough=225, coat=.015),
}


def theme(key):
    if not isinstance(key,str) or key not in THEMES:
        raise ValueError('请选择绿色、红色、黄色、蓝色、白色或哑黑色')
    return THEMES[key]


def read(root, name, default):
    path=Path(root)/name
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else default


def appearance(root):
    key=read(root,'board_appearance.json',{}).get('color','green')
    theme(key)
    return dict(version=1,color=key)


def recolour(rgb, old, new):
    source=np.asarray(rgb);result=source.copy()
    for kind in ('base','copper','silk'):
        result[np.all(source==old[kind],axis=2)]=new[kind]
    return result


def save(root, key):
    """Stage and rollback all texture/config/export changes as one operation."""
    root=Path(root);new=theme(key);current=appearance(root);old=theme(current['color'])
    if key==current['color'] and (root/'board_appearance.json').exists():
        return dict(saved=True,changed=False,appearance=current)
    config=read(root,'生成配置.json',{});config['board_color']=key
    report=read(root,'导入检查.json',{});report['step_needs_export']=True
    state=dict(version=1,color=key)
    with tempfile.TemporaryDirectory(prefix='pcba-colour-',dir=root.parent) as temporary:
        stage=Path(temporary)/'new';stage.mkdir();backup=Path(temporary)/'old';backup.mkdir()
        for side in ('top','bot'):
            name=f'board_{side}.png'
            with Image.open(root/name) as image:rgb=np.array(image.convert('RGB'))
            Image.fromarray(recolour(rgb,old,new)).save(stage/name)
            roughname=f'board_{side}_rough.png'
            if (root/roughname).exists():
                with Image.open(root/roughname) as image:rough=np.array(image.convert('L'))
                mask=np.all(rgb==old['base'],axis=2)|np.all(rgb==old['copper'],axis=2)
                rough[mask]=new['rough'];Image.fromarray(rough).save(stage/roughname)
        for name,value in [('board_appearance.json',state),('生成配置.json',config),('导入检查.json',report)]:
            (stage/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        promoted=[];archived=[];history=root/'历史导出'/('颜色_'+str(time.time_ns()))
        try:
            for path in stage.iterdir():
                target=root/path.name
                if target.exists():shutil.copy2(target,backup/path.name)
                promoted.append(path.name);shutil.copy2(path,target)
            exports=list(root.glob('*.stp'))+list(root.glob('*.step'))+list(root.glob('*.validation.json'))
            if exports:history.mkdir(parents=True)
            for path in exports:
                destination=history/path.name;path.replace(destination);archived.append((destination,path))
        except Exception:
            for saved,original in reversed(archived):saved.replace(original)
            for name in reversed(promoted):
                if (backup/name).exists():shutil.copy2(backup/name,root/name)
                else:(root/name).unlink(missing_ok=True)
            raise
    return dict(saved=True,changed=True,appearance=state)
