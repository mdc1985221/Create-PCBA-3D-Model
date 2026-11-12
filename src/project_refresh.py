"""Apply library bindings to a project snapshot without rebuilding board layers."""
import copy
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from calibration import dump, prepare, read
from generator import aliases, copy_native_model_parts, norm
from manufacturing_export import mapping_for


def refresh(assets, root, library, user_aliases, user_root, rules=None):
    root, assets = Path(root), Path(assets)
    data = copy.deepcopy(read(root/'data.json'))
    report = copy.deepcopy(read(root/'导入检查.json', {}))
    config = copy.deepcopy(read(root/'生成配置.json', {}))
    models = read(root/'models/models.json')
    mapping, overrides = mapping_for(root)
    mapping = dict(mapping)
    original_mapping = dict(mapping)
    table = aliases(assets, user_aliases)
    latest = {norm(k): v for k,v in user_aliases.items()}
    placements = data['placements']
    pending = data.get('pending_components', [])
    added, replaced, changed_models = [], [], {}
    for ref, p in list(placements.items()) + [(p['ref'], p) for p in pending if p.get('reason')=='unmatched_model']:
        pkg = p.get('pkg', '')
        old = overrides.get(ref, original_mapping.get(pkg))
        key = overrides.get(ref, latest.get(norm(pkg), mapping.get(pkg)))
        if key is None:
            key = table.get(norm(pkg), pkg if pkg in library else None)
        if not key:
            continue
        if key not in library and key not in models:
            raise ValueError('绑定的模型不存在：'+str(key)+'；请先修复模型库')
        if ref not in overrides:
            mapping[pkg] = key
        if ref not in placements:
            placements[ref] = {k:v for k,v in p.items() if k not in {'reason','message'}}
            added.append(ref)
        elif old != key:
            replaced.append(ref)
        if key not in models:
            changed_models[key] = library[key]
        # Reusing a stable model id preserves this project's existing snapshot.
        if key in library and old != key:
            changed_models[key] = library[key]
    pending = [p for p in pending if p['ref'] not in added]
    result = dict(added=added, replaced=replaced, pending=len(pending), changed=bool(added or replaced))
    if not result['changed']:
        original=(root/'index.html').read_text(encoding='utf8')
        updated=upgrade_viewer(original,assets)
        if original!=updated:
            result['viewer_updated']=True;result['backup']='模型更新备份/'+str(time.time_ns())
            backup=root/result['backup'];backup.mkdir(parents=True)
            shutil.copy2(root/'index.html',backup/'index.html')
            temporary=root/'index.refresh.tmp'
            temporary.write_text(updated,encoding='utf8');temporary.replace(root/'index.html')
        return dict(result, report=report)
    data['pending_components'] = pending
    data['counts'] = dict(top=sum(p['side']=='top' and p.get('population')!='accessory' for p in placements.values()),
                          bottom=sum(p['side']=='bottom' and p.get('population')!='accessory' for p in placements.values()),
                          accessories=sum(p.get('population')=='accessory' for p in placements.values()))
    poses = read(root/'posefix.json', {})
    calibration = read(root/'calibration.json', dict(version=1, items={}))
    new_calibration = prepare({r:placements[r] for r in added}, poses, mapping, rules or {})
    calibration.setdefault('items', {}).update(new_calibration['items'])
    manufacturing = read(root/'manufacturing.json', dict(version=1, mount_types={}))
    manufacturing.pop('last_export', None)
    stamp = str(time.time_ns())
    result['backup'] = '模型更新备份/'+stamp
    report.update(components=data['counts']['top']+data['counts']['bottom'], accessories=data['counts']['accessories'],
                  top=data['counts']['top'], bottom=data['counts']['bottom'], pending_components=pending,
                  incomplete=bool(pending), unmatched_footprints=sorted({p.get('pkg','') for p in pending if p.get('reason')=='unmatched_model'}),
                  models=mapping, step_needs_export=True, model_refresh=dict(time=time.strftime('%Y-%m-%d %H:%M:%S'), **result))
    report.pop('coordinate_export', None)
    warnings=[w for w in report.get('warnings',[]) if not w.startswith('部分预览：以下封装尚未匹配模型')]
    if report['unmatched_footprints']:
        warnings.append('部分预览：以下封装尚未匹配模型，器件保留在待处理清单：'+', '.join(report['unmatched_footprints']))
    calibrated_replacements=[ref for ref in replaced if ref in calibration['items']]
    if calibrated_replacements:
        warnings.append('以下器件更换模型后保留原校准，请核对模型原点和方向：'+','.join(calibrated_replacements))
    report['warnings'] = warnings
    config['mapping'] = {**config.get('mapping',{}), **mapping}
    config['ref_calibration'] = {**config.get('ref_calibration',{}), **calibration['items']}
    text=(root/'index.html').read_text(encoding='utf8')
    encoded=json.dumps(mapping,ensure_ascii=True).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    text=re.sub(r'const MODEL_MAP = \{.*?\};',lambda m:'const MODEL_MAP = '+encoded+';',text,flags=re.S)
    text=re.sub(r'const ASSET_VERSION = [^;]+;',lambda m:'const ASSET_VERSION = '+json.dumps(stamp)+';',text,count=1)
    summary=f"{report['components']} 个已显示器件"+(f" + {report['accessories']} 件附件" if report['accessories'] else '')+(f' · {len(pending)} 个待处理（部分预览）' if pending else '')
    text=re.sub(r'(<div class="sub">.*?<br>\s*)[^<]*(<br>)',lambda m:m[1]+summary+m[2],text,count=1,flags=re.S)
    text=upgrade_viewer(text, assets)
    used=set(mapping.values())|set(overrides.values())
    updated_models={k:v for k,v in {**models,**changed_models}.items() if k in used}
    if not used<=updated_models.keys():
        raise ValueError('工程模型快照不完整，未应用更新')
    values={'data.json':data,'导入检查.json':report,'生成配置.json':config,
            'models/models.json':updated_models,'model_mapping.json':mapping,
            '待处理器件.json':pending,'calibration.json':calibration,'manufacturing.json':manufacturing}
    # Validate/copy native parts before modifying the live project. A failure
    # leaves the previous preview and exports usable.
    with tempfile.TemporaryDirectory(prefix='pcba-model-refresh-',dir=root.parent) as temp:
        stage=Path(temp)
        copy_native_model_parts(assets,stage,changed_models,user_root)
        for name,value in values.items():
            path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);dump(path,value)
        (stage/'index.html').write_text(text,encoding='utf8')
        targets=[p.relative_to(stage) for p in stage.rglob('*') if p.is_file()]
        backup=root/result['backup'];backup.mkdir(parents=True)
        existing=[];promoted=[];archived=[]
        try:
            for relative in targets:
                target=root/relative
                if target.exists():
                    saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(target,saved);existing.append(relative)
            for relative in targets:
                target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(stage/relative,target);promoted.append(relative)
            exports=list(root.glob('*.stp'))+list(root.glob('*.step'))+list(root.glob('*.validation.json'))
            if exports:
                history=root/'历史导出'/stamp;history.mkdir(parents=True)
                for path in exports:
                    destination=history/path.name;path.replace(destination);archived.append((destination,path))
        except Exception:
            for saved,original in archived:saved.replace(original)
            for relative in promoted:
                if relative in existing:shutil.copy2(backup/relative,root/relative)
                else:(root/relative).unlink(missing_ok=True)
            raise
    return dict(result, report=report)


def upgrade_viewer(text, assets):
    """Add the current refresh UI to older project pages without changing layout."""
    template=(Path(assets)/'viewer.html').read_text(encoding='utf8')
    # Upgrade the existing calibration controls without replacing project-specific data.
    start='<div class="row"><button id="calSave"'
    end='<div class="row" style="margin-top:7px"><button id="calRemoveRule"'
    if start in text and end in text:
        fragment=template.split(start,1)[1].split(end,1)[0]
        text=re.sub(re.escape(start)+r'.*?'+re.escape(end),lambda m:start+fragment+end,text,count=1,flags=re.S)
    text=text.replace('save_rule:rule,mount_type:', 'save_rule:rule,save_binding:rule,mount_type:')
    text=text.replace('已保存本器件及封装规则，下次生成会复用。当前 STP 和坐标需要重新导出。',
                      '已保存本器件，并绑定封装、模型和局部校准。下次按各自生产坐标、旋转角和面别复用；当前其他器件不变。STP 和坐标需要重新导出。')
    for start,end in [('<!-- MODEL_REFRESH_UI -->','<!-- /MODEL_REFRESH_UI -->'),
                      ('/* MODEL_REFRESH_SCRIPT */','/* /MODEL_REFRESH_SCRIPT */')]:
        fragment=template.split(start,1)[1].split(end,1)[0]
        block=start+fragment+end
        if start in text:
            text=re.sub(re.escape(start)+r'.*?'+re.escape(end),lambda m:block,text,count=1,flags=re.S)
        elif start.startswith('<!--'):
            text=text.replace('<details id="pendingSection"',block+'<details id="pendingSection"',1)
        else:
            pos=text.rfind('</script>');text=text[:pos]+block+'\n'+text[pos:]
    return text
