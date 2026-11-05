"""Placement counts for manual assembly; no inferred pin counts."""
import math,time
from pathlib import Path
from manufacturing_export import snapshot,write_csv
from calibration import dump

def estimate(root,options,export=False):
    root=Path(root)
    if not isinstance(options,dict):raise ValueError('估算参数格式错误')
    parsed={}
    for key in ('boards',):
        value=options.get(key)
        if key=='boards' and (value is None or value==''):value=1
        if value is None or value=='':parsed[key]=None;continue
        try:value=float(value)
        except (TypeError,ValueError):raise ValueError('估算参数需要有效数字') from None
        if not math.isfinite(value) or value<0 or value>1_000_000_000:raise ValueError('估算参数超出范围')
        if key=='boards' and (not value.is_integer() or not 1<=value<=10_000_000):raise ValueError('板数需为 1–10000000 的整数')
        parsed[key]=int(value) if key=='boards' else value
    state=snapshot(root);counts=dict(SMT_TOP=0,SMT_BOTTOM=0,THT=0,UNKNOWN=0);rows=[];seen=set()
    for p in state['rows']:
        kind=p['mount'];category=('SMT_'+p['side']) if kind=='SMT' else 'THT' if kind=='THT' else 'UNKNOWN'
        counts[category]+=1;seen.add(p['ref'])
        rows.append(dict(ref=p['ref'],side=p['side'],package=p['package'],type=category,status='已确认安装类型' if category!='UNKNOWN' else '安装类型待确认'))
    excluded=state['manufacturing'].get('mount_types',{})
    unresolved=list(state['data'].get('pending_components',[]))+list(state['pending'])
    for p in unresolved:
        ref=p.get('ref','')
        if not ref or ref in seen or excluded.get(ref)=='EXCLUDE':continue
        seen.add(ref);counts['UNKNOWN']+=1
        rows.append(dict(ref=ref,side=p.get('side',''),package=p.get('pkg',''),type='UNKNOWN',status=p.get('message') or p.get('reason') or '缺少模型或坐标'))
    smt=counts['SMT_TOP']+counts['SMT_BOTTOM'];known=smt+counts['THT'];qty=parsed['boards']
    lines=[f'每板 SMT：顶面 {counts["SMT_TOP"]} 件，底面 {counts["SMT_BOTTOM"]} 件，合计 {smt} 件。',f'插件 {counts["THT"]} 件；未确认 {counts["UNKNOWN"]} 件。',f'{qty} 块板：已确认 SMT {smt*qty} 件，插件 {counts["THT"]*qty} 件。','用于备料数量核对；找物料请在左侧筛选 BOM，选中后高亮 PCB。未确认项需补齐模型或坐标。']
    result=dict(counts=counts,boards=qty,known=known,total=len(rows),rows=rows,text='\n'.join(lines),paths=[])
    if export:
        dest=root/'贴装数量'/('统计_'+str(time.time_ns()));dest.mkdir(parents=True)
        write_csv(dest/'器件数量清单.csv',['ref','side','package','type','status'],rows)
        serial={k:v for k,v in result.items() if k!='paths'};dump(dest/'数量记录.json',serial)
        (dest/'数量说明.txt').write_text(result['text'],encoding='utf-8-sig')
        result['paths']=sorted(dest.iterdir())
    return result
