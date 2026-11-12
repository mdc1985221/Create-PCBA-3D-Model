"""Conservative BGA reconstruction from actual paste/mask flash centres."""
import base64,io,json,math,re,secrets
from pathlib import Path
from statistics import median

def read(root,name):return json.loads((Path(root)/name).read_text(encoding='utf8'))


def reference_affine(board, image_size, center, rotation, side, span, size=360):
    """Map component top-view pixels back to the unmirrored fabrication image."""
    a=math.radians(rotation);c,s=math.cos(a),math.sin(a)
    sign=1 if side=='top' else -1;k=span/size
    sx,sy=image_size[0]/board['w'],image_size[1]/board['h'];cx,cy=center
    return (c*k*sx,s*sign*k*sx,(cx-c*span/2-s*sign*span/2)*sx,
            -s*k*sy,c*sign*k*sy,(board['h']-cy+s*span/2-c*sign*span/2)*sy)


def board_reference(root, result):
    """Show original board artwork for manual A1 confirmation, never infer pin numbers."""
    from PIL import Image
    path=Path(root)/('board_top.png' if result['side']=='top' else 'board_bot.png')
    span=round(max(result['width'],result['depth'])*1.65,3)
    if not path.exists():return dict(image=None,span=span,note='当前工程无板面图，请对照规格书确认第一脚。')
    with Image.open(path) as image:
        affine=reference_affine(result['board'],image.size,result['center'],result['rotation'],result['side'],span)
        patch=image.convert('RGB').transform((360,360),Image.Transform.AFFINE,affine,Image.Resampling.BICUBIC,fillcolor=(24,36,30))
    output=io.BytesIO();patch.save(output,format='PNG')
    return dict(image='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii'),span=span,
                note='原制板图层的板面局部图，已按原坐标角度与面别转换为器件顶视；请对照圆点、三角、缺口及规格书确认。')

def bom_marking(data,ref):
    """BOM identification text; never imply this is a manufacturer's laser code."""
    for row in data.get('bom',[]):
        if ref not in re.split(r'[,，;；\s]+',row.get('refs','')):continue
        desc=row.get('desc','')
        tokens=re.findall(r'[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*',desc)
        for token in tokens:
            token=re.split(r'-(?:F?BGA|QFN|LQFP|SOP|SOT)\d*',token,flags=re.I)[0]
            if len(token)>=5 and re.search(r'[A-Za-z]',token) and re.search(r'\d',token):
                return token[:48],desc
        return str(row.get('val',''))[:48],desc
    return '', ''

def candidates(root):
    data=read(root,'data.json');items={**data['placements'],**{p['ref']:p for p in data.get('pending_components',[])}}
    return [dict(ref=p['ref'],pkg=p['pkg'],side=p['side'],missing=p['ref'] not in data['placements']) for p in items.values() if re.match(r'^(?:FBGA|BGA)',p.get('pkg',''),re.I)]

def infer(root,ref):
    from scipy.spatial import cKDTree
    data=read(root,'data.json');items={**data['placements'],**{p['ref']:p for p in data.get('pending_components',[])}}
    p=items.get(ref)
    if not p or not re.match(r'^(?:FBGA|BGA)',p.get('pkg',''),re.I):raise ValueError('请选择 BGA 器件')
    side=p['side'];results=[]
    for filename,label in [('pads.json','焊膏层'),('alignment_pads.json','阻焊开窗')]:
        groups={}
        for pad in read(root,filename).get(side,[]):
            x,y,w,h,typ=pad
            if typ=='C' and .08<=w<=1.2 and abs(w-h)<.015:
                groups.setdefault(round(w,2),[]).append(pad)
        for diameter,pads in groups.items():
            coords=list(dict.fromkeys((round(q[0],5),round(q[1],5)) for q in pads))
            if len(coords)<16:continue
            tree=cKDTree(coords);dist,_=tree.query(coords,k=2);pitch=median(float(row[1]) for row in dist)
            if not .2<=pitch<=2 or diameter>=pitch*.94:continue
            expected_match=re.match(r'^(?:F?BGA)(\d+)',p['pkg'],re.I)
            expected_all=int(expected_match[1]) if expected_match else None
            if len(coords)==expected_all:
                xs_all,ys_all=zip(*coords)
                centre_distance=math.hypot((min(xs_all)+max(xs_all))/2-p['x'],(min(ys_all)+max(ys_all))/2-p['y'])
                if centre_distance<max(8,(max(xs_all)-min(xs_all)+max(ys_all)-min(ys_all))*.7):
                    results.append((centre_distance+(0 if filename=='pads.json' else .5),coords,diameter,pitch,label))
            remaining=set(range(len(coords)))
            while remaining:
                stack=[remaining.pop()];component=[]
                while stack:
                    i=stack.pop();component.append(i)
                    for j in tree.query_ball_point(coords[i],pitch*1.45):
                        if j in remaining:remaining.remove(j);stack.append(j)
                if len(component)<16:continue
                points=[coords[i] for i in component];xs,ys=zip(*points);cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
                distance=math.hypot(cx-p['x'],cy-p['y'])
                if distance>max(8,(max(xs)-min(xs)+max(ys)-min(ys))*.7):continue
                expected=re.match(r'^(?:F?BGA)(\d+)',p['pkg'],re.I);expected=int(expected[1]) if expected else None
                score=distance+(abs(len(points)-expected)*.15 if expected else 0)+(0 if filename=='pads.json' else .5)
                results.append((score,points,diameter,pitch,label))
    if not results:raise ValueError('没有识别到可靠的圆形焊盘阵列。请先核对焊膏层 / 阻焊层，暂不猜测球位。')
    _,points,pad_diameter,pitch,source=min(results,key=lambda r:r[0])
    xs,ys=zip(*points);cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
    angle=math.radians(p['rot']);c,s=math.cos(angle),math.sin(angle)
    local=[]
    for x,y in points:
        dx,dy=x-cx,y-cy
        local.append([round(c*dx+s*dy,5),round((-s*dx+c*dy)*(1 if side=='top' else -1),5)])
    def axis_pitch(index):
        from collections import Counter
        diffs=[]
        for q in local:
            ds=[abs(q[index]-r[index]) for r in local if abs(q[1-index]-r[1-index])<.01 and abs(q[index]-r[index])>.2]
            if ds:diffs.append(round(min(ds),2))
        return Counter(diffs).most_common(1)[0][0] if diffs else round(pitch,3)
    px,py=axis_pitch(0),axis_pitch(1)
    width=round(max(q[0] for q in local)-min(q[0] for q in local)+px*1.5,2)
    depth=round(max(q[1] for q in local)-min(q[1] for q in local)+py*1.5,2)
    # Body dimensions are only estimates. Never call the silkscreen an exact body.
    marking,description=bom_marking(data,ref)
    result=dict(ref=ref,pkg=p['pkg'],side=side,marking=marking,description=description,count=len(points),points=local,board_points=points,center=[cx,cy],xy=[p['x'],p['y']],rotation=p['rot'],pitch=[px,py],pad_diameter=pad_diameter,
                width=width,depth=depth,height=1.,ball_diameter=round(min(px,py)*.55,3),source=source,
                expected_count=int(re.match(r'^(?:F?BGA)(\d+)',p['pkg'],re.I)[1]) if re.match(r'^(?:F?BGA)(\d+)',p['pkg'],re.I) else None,
                board=data['board'],note='球位来自实际图层；球径、本体长宽高为估算。A1 优先读取制板丝印几何；无明确线索时请人工确认。')
    from pin1_support import detect
    result['pin1']=detect(root,result,data)
    result['reference']=board_reference(root,result)
    return result

def stage_bga(home,root,ref,options):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeSphere,BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt,gp_Ax2,gp_Dir,gp_Vec
    from user_models import stage,PALETTE
    result=infer(root,ref)
    if result['expected_count'] and result['count']!=result['expected_count']:raise ValueError('球数与封装名不一致，请核对图层')
    values={}
    for key in ['width','depth','height','ball_diameter']:
        try:value=float(options.get(key,result[key]))
        except (TypeError,ValueError):raise ValueError('请输入有效尺寸')
        if not math.isfinite(value) or not .05<=value<=60:raise ValueError('尺寸需在 0.05–60 mm 之间')
        values[key]=value
    if values['ball_diameter']>=min(result['pitch'])*.95:raise ValueError('球径需要小于球间距')
    extent=[max(abs(q[i]) for q in result['points']) for i in [0,1]]
    if any(values[key]/2<extent[i]+values['ball_diameter']/2 for i,key in enumerate(['width','depth'])):raise ValueError('本体尺寸小于球阵列，请核对长宽')
    detected=result['pin1']
    corner=options.get('a1',detected['corner'])
    if corner not in ['unknown','tl','tr','bl','br']:raise ValueError('A1 角无效')
    marker=options.get('pin1_style',detected['style'])
    if marker not in ['dot','triangle']:raise ValueError('第一脚标识样式无效')
    manual=options.get('a1_confirmed') is True
    automatic=corner!='unknown' and corner==detected['corner'] and detected['status']=='detected' and not manual
    if corner!='unknown' and not manual and not automatic:raise ValueError('请先对照板面丝印或规格书，确认第一脚 / A1 方向')
    w,d,h,diam=[values[k] for k in ['width','depth','height','ball_diameter']];r=diam/2;stand=diam*.7
    # Colours are linear RGB in both the viewer and CAD exporter.
    body=BRepPrimAPI_MakeBox(gp_Pnt(-w/2,-d/2,stand),w,d,h).Shape()
    groups=[(body,[.006,.007,.009])]
    for x,y in result['points']:groups.append((BRepPrimAPI_MakeSphere(gp_Pnt(x,y,r),r).Shape(),PALETTE['metal']))
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    cutters=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(cutters)
    recess=min(.035,h*.15);floor=stand+h-recess;inlay_depth=recess*.2
    has_cut=False
    def engraved(shape,inlay):
        nonlocal has_cut
        builder.Add(cutters,shape);has_cut=True
        return inlay
    marking=str(options.get('marking',result['marking'])).strip()
    if len(marking)>48 or re.search(r'[^A-Za-z0-9 ._+/()-]',marking):raise ValueError('模型标记支持最多 48 个英文字母、数字及常用符号')
    if marking:
        # Cut actual pockets; contrasting floors sit below the original surface.
        from PIL import Image,ImageDraw,ImageFont
        try:font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
        except OSError:font=ImageFont.load_default(size=18)
        box=font.getbbox(marking);image=Image.new('1',(box[2]-box[0],box[3]-box[1]))
        ImageDraw.Draw(image).text((-box[0],-box[1]),marking,font=font,fill=1)
        image=image.crop(image.getbbox()) if image.getbbox() else None
    if marking and image is not None:
        scale=min(w*.78/image.width,d*.18/image.height)
        compound=TopoDS_Compound();builder.MakeCompound(compound)
        # Merge consecutive equal runs so the boolean operation stays compact.
        active={};rectangles=[]
        for iy in range(image.height):
            ix=0;runs=set()
            while ix<image.width:
                if not image.getpixel((ix,iy)):ix+=1;continue
                start=ix
                while ix<image.width and image.getpixel((ix,iy)):ix+=1
                runs.add((start,ix))
            for run in list(active):
                if run not in runs:rectangles.append((*run,active.pop(run),iy))
            for run in runs:active.setdefault(run,iy)
        rectangles.extend((*run,start,image.height) for run,start in active.items())
        for start,end,top,bottom in rectangles:
            point=gp_Pnt((start-image.width/2)*scale,(image.height/2-bottom)*scale,floor)
            cut=BRepPrimAPI_MakeBox(point,(end-start)*scale,(bottom-top)*scale,recess+.002).Shape()
            ink=BRepPrimAPI_MakeBox(point,(end-start)*scale,(bottom-top)*scale,inlay_depth).Shape()
            builder.Add(compound,engraved(cut,ink))
        groups.append((compound,[.24,.25,.23]))
    if corner!='unknown':
        radius=min(.24,min(w,d)*.045);margin=radius*2
        x=(-1 if corner.endswith('l') else 1)*(w/2-margin);y=(1 if corner.startswith('t') else -1)*(d/2-margin)
        z=floor
        if marker=='dot':
            axis=gp_Ax2(gp_Pnt(x,y,z),gp_Dir(0,0,1))
            cut=BRepPrimAPI_MakeCylinder(axis,radius,recess+.002).Shape()
            shape=BRepPrimAPI_MakeCylinder(axis,radius,inlay_depth).Shape()
        else:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace
            polygon=BRepBuilderAPI_MakePolygon();sx=-1 if corner.endswith('l') else 1;sy=1 if corner.startswith('t') else -1
            for u,v in [(sx*radius,sy*radius),(-sx*radius,sy*radius),(sx*radius,-sy*radius)]:polygon.Add(gp_Pnt(x+u,y+v,z))
            polygon.Close();face=BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
            cut=BRepPrimAPI_MakePrism(face,gp_Vec(0,0,recess+.002)).Shape()
            shape=BRepPrimAPI_MakePrism(face,gp_Vec(0,0,inlay_depth)).Shape()
        groups.append((engraved(cut,shape),[.48,.50,.45]))
    if has_cut:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepCheck import BRepCheck_Analyzer
        operation=BRepAlgoAPI_Cut(body,cutters)
        if not operation.IsDone() or operation.Shape().IsNull() or not BRepCheck_Analyzer(operation.Shape()).IsValid():
            raise ValueError('芯片表面凹刻失败，请缩短标记或调整本体尺寸后重试')
        groups[0]=(operation.Shape(),groups[0][1])
    name='BGA_'+re.sub('[^A-Za-z0-9_]','_',ref)+'_'+secrets.token_hex(3)
    draft=stage(home,name,groups,dict(origin='custom',origin_xyz=[0,0,0]),dict(kind='gerber_bga',ref=ref,pkg=result['pkg'],estimated=True,a1=corner,a1_confirmed=corner!='unknown' and manual,a1_basis=detected['basis'] if automatic else 'manual_board_or_datasheet' if corner!='unknown' else 'unconfirmed',a1_detection=detected,pin1_style=marker if corner!='unknown' else None,marking=marking,marking_style='recessed',recess_depth_mm=recess,body_color='black',description=result['description'],parameters=values,pitch=result['pitch'],count=result['count']),[result['note'],detected['note']])
    draft.update(inference=result,parameters=values,a1=corner)
    # Store the exact target server-side; commit cannot change it by editing the page.
    from user_models import write_json
    folder=Path(home)/'模型草稿'/draft['draft']
    catalog=read(folder,'models.json');model=catalog[draft['model']];model['mount_type']='SMT'
    materials={}
    for index,(part,native) in enumerate(zip(model['parts'],model['native_step_parts'])):
        previous=part['mat'];prefix='IC-BODY-' if index==0 else 'PIN-' if index<=result['count'] else 'MARK-'
        name=prefix+previous;materials[name]=model['materials'][previous];part['mat']=name;native['mat']=name
    model['materials']=materials
    write_json(folder/'models.json',catalog)
    write_json(folder/'bga.json',dict(ref=ref,pkg=result['pkg']))
    return draft
