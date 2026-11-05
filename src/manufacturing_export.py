"""Saved 3D poses to component-centre placement files and assembly geometry checks."""
import csv,json,math,re,time,hashlib
from pathlib import Path
from calibration import read,dump,base_pose,values

MOUNT_TYPES={'auto','SMT','THT','EXCLUDE'}
BODY_IGNORE=('PIN','LEAD','TERMINAL','MARK','IC-LABEL','LABEL')
THT_MODELS={'HDR_125_8','HDR_125_11','CONN_J2','CONN_J5'}
SMT_MODELS={'CONN_J1','CONN_J3','CONN_J12','SOP16_H1102','H1102NL_official','GC4693_C35YA','IND_NR4030','IND_SHC','IND_SPH252012'}

def body_bounds(model):
    parts=model.get('parts',[])
    body=[p for p in parts if not str(p.get('mat','')).upper().startswith(BODY_IGNORE)]
    fallback=not bool(body) or bool(model.get('native_step_parts') and not any(str(p.get('mat','')).upper().startswith(('IC-BODY','PLASTIC','RES-','CAP-','FERRITE','CORE-','MOLDED','SENSOR-')) for p in body));parts=body or parts
    lo=[float('inf')]*3;hi=[-float('inf')]*3
    for part in parts:
        v=part.get('v',[])
        if len(v)%3:return None
        for axis in range(3):
            a=v[axis::3]
            if a:
                if not all(isinstance(n,(float,int)) and math.isfinite(n) for n in a):return None
                lo[axis]=min(lo[axis],min(a));hi[axis]=max(hi[axis],max(a))
    if not all(math.isfinite(v) for v in lo+hi):return None
    return dict(min=lo,max=hi,center=[(a+b)/2 for a,b in zip(lo,hi)],fallback=fallback)

def mount_type(key,model,override='auto'):
    if override not in MOUNT_TYPES:raise ValueError('无效安装类型')
    if override!='auto':return override,'manual'
    native=str(model.get('mount_type','')).upper()
    if native in {'SMT','THT'}:return native,'model_metadata'
    if key in THT_MODELS:return 'THT','builtin'
    if key in SMT_MODELS or re.match(r'^(?:C_|R_|L_|D_SOD|LED_|SOT-|XTAL_|QFN|DFN|WSON|CSP|CONN_125_)',key):return 'SMT','builtin'
    return 'UNKNOWN','unconfirmed'

def mapping_for(root):
    mapping=read(root/'model_mapping.json',{})
    text=(root/'index.html').read_text(encoding='utf8')
    def declaration(name):
        match=re.search(r'const '+name+r' = (\{.*?\});',text,re.S)
        if not match:return {}
        try:return json.loads(match[1])
        except ValueError:return dict(re.findall(r"['\"]?([\w.+-]+)['\"]?:\s*'([^']+)'",match[1]))
    if not mapping:mapping=declaration('MODEL_MAP')
    overrides=declaration('MODEL_BY_REF')
    return mapping,overrides

def snapshot(root,basis='body',origin='source',angle_mode='board'):
    if basis not in {'body','origin'} or origin not in {'source','board'} or angle_mode not in {'board','face'}:raise ValueError('无效坐标导出选项')
    root=Path(root);data=read(root/'data.json');models=read(root/'models/models.json');poses=read(root/'posefix.json',{})
    manual=read(root/'calibration.json',{}).get('items',{});manufacturing=read(root/'manufacturing.json',{});mapping,overrides=mapping_for(root)
    offsets=data.get('coordinate_frame',{}).get('xy_offsets_mm',[0,0]);cache={};rows=[];pending=[]
    for item in data.get('pending_components',[]):pending.append(dict(ref=item.get('ref',''),reason=item.get('message') or item.get('reason') or '缺失模型或坐标'))
    for ref,p in data['placements'].items():
        if p.get('population') in {'accessory','nc'}:continue
        if manufacturing.get('mount_types',{}).get(ref)=='EXCLUDE':continue
        key=overrides.get(ref,mapping.get(p['pkg']));model=models.get(key)
        if not model:pending.append(dict(ref=ref,reason='缺少 3D 模型'));continue
        if key not in cache:cache[key]=body_bounds(model)
        bounds=cache[key]
        if not bounds:pending.append(dict(ref=ref,reason='模型本体几何无效'));continue
        kind,kind_source=mount_type(key,model,manufacturing.get('mount_types',{}).get(ref,'auto'))
        if kind=='EXCLUDE':continue
        if kind=='UNKNOWN':pending.append(dict(ref=ref,reason='安装类型待确认：请选 SMT 或 THT'))
        if bounds['fallback'] and basis=='body':pending.append(dict(ref=ref,reason='模型未区分本体与引脚，中心基准待复核；可改选模型原点'))
        px,py,angle=base_pose(p,poses.get(ref,{}));v=values(manual.get(ref,{}));px+=v['dx'];py+=v['dy'];angle+=v['dyaw']
        c=math.cos(math.radians(angle));s=math.sin(math.radians(angle));top=p['side']=='top'
        def xy(point):
            x,_,z=point
            return [px+c*x+(s*z if top else -s*z),py+s*x+(-c*z if top else c*z)]
        center=xy(bounds['center']);selected=center if basis=='body' else [px,py]
        board_xy=selected[:];out_xy=[selected[i]-(offsets[i] if origin=='source' else 0) for i in range(2)]
        polygon=[xy([x,0,z]) for x,z in [(bounds['min'][0],bounds['min'][2]),(bounds['max'][0],bounds['min'][2]),(bounds['max'][0],bounds['max'][2]),(bounds['min'][0],bounds['max'][2])]]
        output_angle=(angle if angle_mode=='board' or top else -angle)%360
        if abs(output_angle-360)<1e-8:output_angle=0.
        rows.append(dict(ref=ref,x=out_xy[0],y=out_xy[1],rotation=output_angle,side='TOP' if top else 'BOTTOM',package=p['pkg'],value=p.get('val',''),mount=kind,mount_source=kind_source,model=key,units='mm',basis=basis,angle_mode=angle_mode,eligible=kind=='SMT' and not (bounds['fallback'] and basis=='body'),
                         source_x=p['x']-offsets[0],source_y=p['y']-offsets[1],source_rotation=p['rot'],board_x=board_xy[0],board_y=board_xy[1],model_origin_x=px,model_origin_y=py,body_center_x=center[0],body_center_y=center[1],rotation_board_deg=angle,
                         shift_from_source_mm=math.hypot(selected[0]-p['x'],selected[1]-p['y']),body_fallback=bounds['fallback'],polygon=polygon))
    unique={(p['ref'],p['reason']):p for p in pending}
    return dict(rows=rows,pending=list(unique.values()),data=data,manufacturing=manufacturing,calibration=manual)

def point_segment_distance(p,a,b):
    dx=b[0]-a[0];dy=b[1]-a[1];length=dx*dx+dy*dy
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)

def inside(p,polygon):
    hit=False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if point_segment_distance(p,a,b)<1e-7:return True
        if (a[1]>p[1])!=(b[1]>p[1]) and p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:hit=not hit
    return hit

def overlap(a,b):
    for poly in [a,b]:
        for p,q in zip(poly,poly[1:]+poly[:1]):
            ax,ay=q[1]-p[1],p[0]-q[0]
            if abs(ax)+abs(ay)<1e-10:continue
            aa=[x*ax+y*ay for x,y in a];bb=[x*ax+y*ay for x,y in b]
            if min(max(aa),max(bb))-max(min(aa),min(bb))<=1e-7:return False
    return True

def polygon_inside(poly,board):
    # Include all crossings along each edge: corner-only tests miss concave notches.
    if any(not inside(p,board) for p in poly):return False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1];cuts=[0.,1.]
        for c,d in zip(board,board[1:]+board[:1]):
            ex,ey=d[0]-c[0],d[1]-c[1];den=dx*ey-dy*ex
            if abs(den)<1e-12:continue
            qx,qy=c[0]-a[0],c[1]-a[1];t=(qx*ey-qy*ex)/den;u=(qx*dy-qy*dx)/den
            if 0<=t<=1 and 0<=u<=1:cuts.append(t)
        cuts.sort()
        if any(not inside([a[0]+dx*(t+u)/2,a[1]+dy*(t+u)/2],board) for t,u in zip(cuts,cuts[1:])):return False
    return True

def dfm_checks(state,root):
    data=state['data'];w=data['board']['w'];h=data['board']['h'];geo=read(Path(root)/'board_geometry.json',{})
    board=geo.get('outline') or [[0,0],[w,0],[w,h],[0,h]];issues=[]
    for p in state['pending']:issues.append(dict(level='待处理',check='missing_or_unconfirmed',ref=p['ref'],other='',message=p['reason']))
    for p in state['rows']:
        if p['shift_from_source_mm']>.5:issues.append(dict(level='信息',check='origin_offset',ref=p['ref'],other='',message=f"原始定位点到当前导出中心相差 {p['shift_from_source_mm']:.4f} mm，请核对定位基准"))
        if not polygon_inside(p['polygon'],board):issues.append(dict(level='预警',check='body_outside_board',ref=p['ref'],other='',message='本体包围盒投影超出板框，含允许悬空的连接器也会提示，请复核'))
    rows=state['rows']
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            if a['side']==b['side'] and overlap(a['polygon'],b['polygon']):issues.append(dict(level='预警',check='body_overlap',ref=a['ref'],other=b['ref'],message='同面本体包围盒投影重叠；未考虑凹腔和跨高安装，请复核'))
    return issues

def text_cell(v):
    if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')):return "'"+v
    return v

def write_csv(path,columns,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        out=csv.writer(stream);out.writerow(columns)
        for row in rows:out.writerow([text_cell(round(row.get(k,0),6) if isinstance(row.get(k),float) else row.get(k,'')) for k in columns])

def export_centres(root,options):
    root=Path(root);basis=options.get('basis','body');origin=options.get('origin','source');angle_mode=options.get('angle_mode','board')
    state=snapshot(root,basis,origin,angle_mode);partial=bool(state['pending'])
    if partial and options.get('allow_partial') is not True:raise ValueError('有模型、坐标或安装类型待处理；补齐后导出，或勾选“仅导出已匹配器件（部分坐标）”')
    issues=dfm_checks(state,root);dest=root/'坐标导出'/('导出_'+str(time.time_ns()));dest.mkdir(parents=True)
    prefix='部分_' if partial else '';columns=['ref','x','y','rotation','side','package','value','mount','units','basis','angle_mode']
    tables={prefix+'SMT_TOP.csv':(columns,[p for p in state['rows'] if p['eligible'] and p['side']=='TOP']),prefix+'SMT_BOTTOM.csv':(columns,[p for p in state['rows'] if p['eligible'] and p['side']=='BOTTOM']),prefix+'全部器件中心_核对.csv':(columns,state['rows']),
            '原始与修正对照.csv':(['ref','mount','side','source_x','source_y','source_rotation','x','y','rotation','model_origin_x','model_origin_y','body_center_x','body_center_y','shift_from_source_mm','model','basis','angle_mode'],state['rows']),
            '待处理清单.csv':(['ref','reason'],state['pending']),'DFM检查.csv':(['level','check','ref','other','message'],issues)}
    for name,(cols,rows) in tables.items():write_csv(dest/name,cols,rows)
    notes=['单位：mm。X/Y 原点：'+('原始生产资料原点' if origin=='source' else '单板左下角'),
           '中心基准：'+('已调整 3D 本体包围盒中心，不等同于吸嘴可吸取点' if basis=='body' else '已调整 3D 模型原点'),
           '角度：'+('从板顶看逆时针为正，底面不额外镜像' if angle_mode=='board' else '分别从器件所在面看逆时针为正，底面角度取反')+'；归一化至 [0,360)。',
           '已包含自动定位、已保存的平移与旋转校准。原始生产资料未修改。',
           'SMT_TOP / SMT_BOTTOM 只包含确认安装类型为 SMT 的已匹配实装器件；插件单独保留在全部器件核对表，附件和明确排除项不导出。',
           '模型零度与贴片机元件库零度、底面镜像及吸取点需在设备导入时核对，本文件未做特定机型格式转换。',
           'DFM 为 3D 本体包围盒的几何预警，不是焊盘、钢网、极性或电气规则的完整 DFM 签核。',
           ('此为部分坐标，不能作为整板完整贴装清单；缺失项见待处理清单。' if partial else '所有显示实装器件已检查模型及安装类型；仍需结合 DFM 预警核对。')]
    (dest/'导出说明.txt').write_text('\n'.join(notes),encoding='utf-8-sig')
    from openpyxl import Workbook
    book=Workbook();book.remove(book.active)
    for name,(cols,rows) in tables.items():
        sheet=book.create_sheet({'SMT_TOP':'SMT_TOP','SMT_BOTTOM':'SMT_BOTTOM'}.get(name.replace(prefix,'').split('.')[0],name.replace('.csv','').replace(prefix,'')[:31]));sheet.append(cols)
        for row in rows:sheet.append([text_cell(round(row.get(k,0),6) if isinstance(row.get(k),float) else row.get(k,'')) for k in cols])
        from openpyxl.styles import Font,PatternFill,Alignment
        from openpyxl.utils import get_column_letter
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='24374D')
        for index,col in enumerate(cols,1):
            sheet.column_dimensions[get_column_letter(index)].width=70 if col in {'message','reason'} else 26 if col in {'package','value','model'} else 19
            for cell in list(sheet.columns)[index-1][1:]:
                if isinstance(cell.value,(int,float)):cell.number_format='0.000000' if col not in {'rotation','source_rotation'} else '0.0000'
                cell.alignment=Alignment(vertical='top',wrap_text=col in {'message','reason'})
    sheet=book.create_sheet('导出说明')
    for line in notes:sheet.append([line])
    book.save(dest/(prefix+'修正器件中心坐标.xlsx'))
    inputs={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['data.json','models/models.json','posefix.json','calibration.json','model_mapping.json','board_geometry.json'] if (root/name).exists()}
    digest=hashlib.sha256(json.dumps({'inputs':inputs,'mount_types':state['manufacturing'].get('mount_types',{}),'basis':basis,'origin':origin,'angle_mode':angle_mode},sort_keys=True).encode()).hexdigest()
    summary=dict(version=1,created_at=time.strftime('%Y-%m-%d %H:%M:%S'),partial=partial,basis=basis,origin=origin,angle_mode=angle_mode,units='mm',rows=len(state['rows']),smt_top=sum(p['eligible'] and p['side']=='TOP' for p in state['rows']),smt_bottom=sum(p['eligible'] and p['side']=='BOTTOM' for p in state['rows']),tht=sum(p['mount']=='THT' for p in state['rows']),pending=state['pending'],dfm_issues=issues,saved_state_sha256=digest,input_sha256=inputs)
    dump(dest/'导出记录.json',summary)
    return dest,summary
