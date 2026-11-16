"""Conservative BOM / placement / model package checks, not electrical validation."""
import csv
import io
import json
import re
from pathlib import Path


def signature(text, kind=''):
    text=str(text or '').upper()
    # Require package tokens; numbers in part numbers/voltages are not packages.
    match=re.search(r'(?<![A-Z0-9])(?:F?BGA|CSP|QFN|DFN|WSON|VSON|SOIC|SOP|TSSOP|SSOP)[-_ ]?(\d+)(?!\d)',text)
    if match:
        token=match.group(0);family=re.match(r'[A-Z]+',token)[0]
        if re.match(r'\s*[X×*]\s*\d',text[match.end():]):
            return dict(family='BGA' if family=='FBGA' else family)
        return dict(family='BGA' if family=='FBGA' else family,pins=int(match[1]))
    match=re.search(r'(?<![A-Z0-9])SOT[-_ ]?(23|223|323|353|363)(?:[-_ ]([3568]))?(?!\d)',text)
    if match:
        family='SOT'+match[1]
        return dict(family=family,pins=int(match[2]) if match[2] else 3 if family=='SOT23' else None)
    match=re.search(r'(?<![A-Z0-9])SOD[-_ ]?(123|323|523|923|882)(?!\d)',text)
    if match:return dict(family='SOD'+match[1],pins=2)
    # Imperial chip sizes only, with explicit R/C/L prefix or BOM category.
    match=re.search(r'(?<![A-Z0-9])S?([RCL])[_-]?(0201|0402|0603|0805|1206|1210|2010|2512)(?!\d)',text)
    if not match:
        reverse=re.search(r'(?<![A-Z0-9])(0201|0402|0603|0805|1206|1210|2010|2512)([RCL])(?![A-Z])',text)
        if reverse:return dict(family=reverse[2],size=reverse[1])
    if match:return dict(family=match[1],size=match[2])
    category='C' if '电容' in kind else 'R' if '电阻' in kind else 'L' if '电感' in kind else ''
    if category:
        match=re.search(r'(?<![A-Z0-9])(0201|0402|0603|0805|1206|1210|2010|2512)(?![A-Z0-9])',text)
        if match:return dict(family=category,size=match[1])
    return {}


def conflicts(a,b):
    if not a or not b:return []
    errors=[]
    if a['family']!=b['family']:errors.append('封装系列不同：'+a['family']+' / '+b['family'])
    elif a.get('size') and b.get('size') and a['size']!=b['size']:
        errors.append('贴片尺寸代码不同：'+a['size']+' / '+b['size'])
    if a.get('pins') and b.get('pins') and a['pins']!=b['pins']:
        errors.append('引脚数不同：'+str(a['pins'])+' / '+str(b['pins']))
    return errors


def model_signature(key,model):
    # A mapping's footprint is not independent proof of a model's geometry.
    source=model.get('source',{})
    source=source if isinstance(source,dict) else {}
    candidates=[model.get('package_spec',{}),signature(key),signature(Path(source.get('file','')).stem)]
    if source.get('kind')=='gerber_bga' and source.get('count'):
        candidates.insert(0,dict(family='BGA',pins=int(source['count'])))
    for candidate in candidates:
        if isinstance(candidate,dict) and candidate.get('family'):return candidate
    return {}


def check(placements,bom,mapping,library,overrides=None):
    by_ref={}
    for row in bom:
        for ref in row.get('refs','').split(','):by_ref[ref]=row
    rows=[]
    for ref,p in placements.items():
        if p.get('population')=='accessory':continue
        row=by_ref.get(ref,{})
        pkg=row.get('pkg',p.get('bom_pkg',''));xy=p.get('xy_pkg',p.get('pkg',''))
        desc=row.get('desc',p.get('desc',''));kind=row.get('name',p.get('name',''))
        declared=signature(pkg,kind);description=signature(desc,kind);placed=signature(xy,kind)
        key=(overrides or {}).get(ref,mapping.get(p.get('pkg','')))
        model=library.get(key,{})
        actual=model_signature(key or '',model)
        issues=[]
        for label,a,b in [('BOM 封装与描述冲突',declared,description),
                          ('BOM 与坐标封装冲突',declared or description,placed),
                          ('BOM 与 3D 模型冲突',declared or description,actual),
                          ('坐标封装与 3D 模型冲突',placed,actual)]:
            issues.extend(label+'；'+error for error in conflicts(a,b))
        if not model:issues.append('未匹配 3D 模型，无法核对')
        known=declared or description
        comparable=bool(known and actual and any(known.get(k) and actual.get(k) for k in ('pins','size')))
        errors=[i for i in issues if '冲突' in i]
        status='error' if errors else 'matched' if comparable and not issues else 'review'
        if not issues and status=='review':issues.append('BOM 或模型缺少可独立核对的封装系列、尺寸代码或引脚数，请人工核对')
        if not issues:issues.append('已识别封装特征一致；未验证全部尺寸、公差、极性或实际焊盘')
        rows.append(dict(ref=ref,bom_file=row.get('file',''),bom_location=row.get('location',p.get('bom_location','')),
                         bom_package=pkg,bom_description=desc,xy_package=xy,model=key or '',
                         bom_signature=known,model_signature=actual,status=status,reasons=list(dict.fromkeys(issues))))
    return dict(version=1,summary={s:sum(r['status']==s for r in rows) for s in ('error','review','matched')},items=rows,
                note='冲突表示 BOM、坐标或模型绑定至少一处需检查，不直接认定 BOM 错误。匹配仅覆盖已识别特征。')


def files(result):
    output=io.StringIO(newline='');writer=csv.writer(output)
    writer.writerow(['状态','位号','BOM文件','BOM位置','BOM封装','BOM描述','坐标封装','3D模型','原因'])
    labels={'error':'封装冲突','review':'待核对','matched':'特征一致'}
    for row in sorted(result['items'],key=lambda r:({'error':0,'review':1,'matched':2}[r['status']],r['ref'])):
        values=[labels[row['status']],*[row[k] for k in ('ref','bom_file','bom_location','bom_package','bom_description','xy_package','model')],'；'.join(row['reasons'])]
        # Spreadsheet consumers must not interpret user-provided text as formulas.
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values])
    return {'BOM封装核对.json':json.dumps(result,ensure_ascii=False,indent=2),'BOM封装核对.csv':'\ufeff'+output.getvalue()}


def save(root,result):
    for name,text in files(result).items():(Path(root)/name).write_text(text,encoding='utf8')


def report_warning(result):
    s=result['summary']
    return f"封装核对：{s['error']} 个冲突、{s['review']} 个待核对、{s['matched']} 个已识别特征一致。详见 BOM封装核对.csv；特征一致不等同于全部尺寸或电气验证。"
