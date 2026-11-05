"""Manual placement overlays. Original XY and automatic pose remain unchanged."""
import json
import math
import re
from pathlib import Path

FIELDS = ('dx', 'dy', 'dz', 'dyaw')

def values(raw):
    if not isinstance(raw, dict):raise ValueError('校准值应为对象')
    result={}
    for key in FIELDS:
        v=raw.get(key,0)
        if isinstance(v,bool):raise ValueError('校准值必须是有限数字：'+key)
        try:v=float(v)
        except (TypeError,ValueError):raise ValueError('校准值必须是有限数字：'+key)
        limit=3600 if key=='dyaw' else 50 if key=='dz' else 100
        if not math.isfinite(v) or abs(v)>limit:raise ValueError('校准值超出范围：'+key)
        result[key]=v
    return result

def rule_key(pkg, model):
    return json.dumps([re.sub(r'[^A-Z0-9]','',pkg.upper()),model],ensure_ascii=False)

def base_pose(p,pose):
    if 'conn' in pose:return pose['conn']['cx'],pose['conn']['cy'],-pose['conn'].get('yaw',0)
    return p['x']+pose.get('dx',0),p['y']+pose.get('dy',0),p.get('rot',0)+pose.get('dyaw',0)

def make_rule(p,pose,model,raw):
    v=values(raw);_,_,angle=base_pose(p,pose);a=math.radians(angle);c,s=math.cos(a),math.sin(a)
    u=c*v['dx']+s*v['dy'];w=-s*v['dx']+c*v['dy'];top=p['side']=='top'
    return dict(footprint=p['pkg'],model=model,lx=u,lz=-w if top else w,dz=v['dz'],yaw=v['dyaw'] if top else -v['dyaw'])

def from_rule(p,pose,rule):
    _,_,angle=base_pose(p,pose);a=math.radians(angle);c,s=math.cos(a),math.sin(a);top=p['side']=='top'
    x=float(rule['lx']);v=float(rule['lz'])*(-1 if top else 1)
    return values(dict(dx=c*x-s*v,dy=s*x+c*v,dz=rule['dz'],dyaw=rule['yaw']*(1 if top else -1)))

def prepare(placements,poses,mapping,rules=None,overrides=None):
    if not isinstance(rules or {},dict) or not isinstance(overrides or {},dict):raise ValueError('校准配置应为对象')
    items={}
    for ref,p in placements.items():
        rule=(rules or {}).get(rule_key(p['pkg'],mapping.get(ref,mapping.get(p['pkg'],''))))
        if rule:items[ref]={**from_rule(p,poses.get(ref,{}),rule),'source':'footprint_rule'}
    for ref,raw in (overrides or {}).items():
        if ref not in placements:continue
        items[ref]={**values(raw),'source':'reference'}
    return dict(version=1,items=items)

def dump(path,data):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(path)

def read(path,default=None):
    path=Path(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default
