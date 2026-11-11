"""Local STEP model staging, tessellation and reusable user library."""
import json
import math
import re
import secrets
import shutil
from pathlib import Path

PALETTE = {'body': [.10,.11,.13], 'white': [.91,.90,.85], 'metal': [.78,.80,.83], 'ferrite': [.21,.22,.23]}


def write_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    temporary.replace(path)


def read_catalog(root):
    path=Path(root)/'models.json'
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def model_id(name):
    name=str(name).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name):
        raise ValueError('模型名称请用 1–64 个英文字母、数字、下划线或连字符')
    return 'USER_'+name


def model_summary(key,model,aliases=None):
    """Small searchable library entry; dimensions are board X/Y/height in mm."""
    low=[math.inf]*3;high=[-math.inf]*3
    for part in model.get('parts',[]):
        vertices=part.get('v',[])
        for index in range(0,len(vertices)-2,3):
            for axis in range(3):
                low[axis]=min(low[axis],vertices[index+axis]);high[axis]=max(high[axis],vertices[index+axis])
    size=[high[i]-low[i] for i in (0,2,1)] if all(math.isfinite(v) for v in low+high) else []
    source=model.get('source',{})
    return dict(id=key,parts=len(model.get('parts',[])),dimensions_mm=size,
                aliases=sorted(k for k,v in (aliases or {}).items() if v==key),
                source_file=source.get('file','') if isinstance(source,dict) else str(source),
                mount_type=model.get('mount_type',''))


def binding_aliases(aliases,footprint,model,model_ids):
    from generator import norm
    if not isinstance(footprint,str) or not footprint.strip() or len(footprint)>200:
        raise ValueError('请选择生产资料中的完整封装名')
    footprint=footprint.strip()
    if not norm(footprint):raise ValueError('封装名无效')
    if model not in model_ids:raise ValueError('所选模型不存在，请重新打开模型选择窗口')
    # A normalized spelling must have exactly one reusable binding.
    result={key:value for key,value in aliases.items() if norm(key)!=norm(footprint)}
    result[footprint]=model
    return result


def bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box=Bnd_Box();BRepBndLib.AddOptimal_s(shape,box,False,False)
    if box.IsVoid():raise ValueError('模型没有有效几何')
    a,b=box.CornerMin(),box.CornerMax()
    return [a.X(),a.Y(),a.Z(),b.X(),b.Y(),b.Z()]


def transform_shape(shape, values):
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    tr=gp_Trsf();tr.SetValues(*values)
    return BRepBuilderAPI_Transform(shape,tr,True).Shape()


def mesh(shape, material):
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE,TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location
    BRepMesh_IncrementalMesh(shape,.025,False,.2,False).Perform()
    vertices=[];indices=[];seen={};ex=TopExp_Explorer(shape,TopAbs_FACE)
    while ex.More():
        face=TopoDS.Face(ex.Current());location=TopLoc_Location()
        tri=BRep_Tool.Triangulation_s(face,location)
        if tri is not None:
            ids={}
            for k in range(1,tri.NbNodes()+1):
                p=tri.Node(k).Transformed(location.Transformation())
                xyz=tuple(round(v,7) for v in (p.X(),p.Y(),p.Z()))
                if xyz not in seen:seen[xyz]=len(vertices)//3;vertices.extend(xyz)
                ids[k]=seen[xyz]
            for k in range(1,tri.NbTriangles()+1):
                a,b,c=tri.Triangle(k).Get()
                if face.Orientation()==TopAbs_REVERSED:b,c=c,b
                indices.extend([ids[a],ids[b],ids[c]])
        ex.Next()
    if not indices:raise ValueError('模型未能生成预览网格')
    return dict(mat=material,v=vertices,i=indices)


def write_step(shape,path):
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    writer=STEPControl_Writer()
    if writer.Transfer(shape,STEPControl_AsIs)!=IFSelect_RetDone or writer.Write(str(path))!=IFSelect_RetDone:
        raise ValueError('无法写入本地 STEP 模型')


def finite_vector(value,label,limit=500):
    if not isinstance(value,list) or len(value)!=3:raise ValueError(label+' 需要三个数值')
    try:result=[float(v) for v in value]
    except (ValueError,TypeError):raise ValueError(label+' 需要三个有效数值')
    if any(not math.isfinite(v) or abs(v)>limit for v in result):raise ValueError(label+' 超出允许范围')
    return result


def stage(home,name,groups,options,source,notes=None):
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    import numpy as np
    key=model_id(name)
    if key in read_catalog(Path(home)/'用户模型库'):
        raise ValueError('模型名 '+key+' 已存在，请使用已有模型或换一个名称后重新预览')
    draft=secrets.token_hex(12);root=Path(home)/'模型草稿'/draft
    root.mkdir(parents=True)
    rotation=finite_vector(options.get('rotation',[0,0,0]),'XYZ 旋转',360)
    rx,ry,rz=map(math.radians,rotation)
    x=np.array([[1,0,0],[0,math.cos(rx),-math.sin(rx)],[0,math.sin(rx),math.cos(rx)]])
    y=np.array([[math.cos(ry),0,math.sin(ry)],[0,1,0],[-math.sin(ry),0,math.cos(ry)]])
    z=np.array([[math.cos(rz),-math.sin(rz),0],[math.sin(rz),math.cos(rz),0],[0,0,1]])
    rot=z@y@x
    groups=[(transform_shape(shape,[v for row in np.column_stack((rot,np.zeros(3))) for v in row]),colour) for shape,colour in groups]
    boxes=[bounds(shape) for shape,_ in groups]
    low=np.min([b[:3] for b in boxes],axis=0);high=np.max([b[3:] for b in boxes],axis=0)
    if options.get('origin','center')=='center':origin=np.array([(low[0]+high[0])/2,(low[1]+high[1])/2,low[2]])
    elif options.get('origin')=='custom':origin=np.array(finite_vector(options.get('origin_xyz',[0,0,0]),'自定义原点'))
    else:raise ValueError('原点方式无效')
    # Source +Z is package height; canonical viewer axes are X, height Y, -source Y.
    axes=np.array([[1,0,0],[0,0,1],[0,-1,0]])
    translation=-(axes@origin);tr=[float(v) for row in np.column_stack((axes,translation)) for v in row]
    if max(high-low)>500 or min(high-low)<=0:raise ValueError('器件模型尺寸无效或超过 500 mm，请检查原文件单位和方向')
    parts=[];native=[];materials={};solid_count=0
    for index,(shape,colour) in enumerate(groups):
        shape=transform_shape(shape,tr)
        if not BRepCheck_Analyzer(shape).IsValid():raise ValueError('STEP 中存在无效几何，请先在 CAD 中修复')
        ex=TopExp_Explorer(shape,TopAbs_SOLID);solids=0
        while ex.More():solids+=1;ex.Next()
        if not solids:raise ValueError('仅支持包含实体的 STEP；当前文件包含无实体的曲面组')
        solid_count+=solids
        rgb=[max(0,min(1,float(v))) for v in colour]
        material=key+'_'+str(index);materials[material]=rgb
        part=mesh(shape,material);parts.append(part)
        if sum(len(p['i']) for p in parts)>3_000_000:raise ValueError('模型网格超过 100 万三角形，请先简化 STEP')
        relative=f'models/user/{key}_{draft}_{index}.step';target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        write_step(shape,target);native.append(dict(path=relative,mat=material))
    model=dict(parts=parts,materials=materials,native_step_parts=native,
               native_transform=[[1,0,0,0],[0,1,0,0],[0,0,1,0]],source=source)
    write_json(root/'models.json',{key:model})
    summary=dict(draft=draft,model=key,dimensions_mm=[float(v) for v in high-low],solids=solid_count,
                 triangles=sum(len(p['i'])//3 for p in parts),source=source,rotation=rotation,
                 source_origin_mm=origin.tolist(),notes=notes or [],preview='/model-preview/'+draft+'/index.html')
    write_json(root/'summary.json',summary)
    (root/'index.html').write_text(PREVIEW_HTML,encoding='utf8')
    return summary


def import_step(home,name,filename,content,options):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool,XCAFDoc_ColorType
    from OCP.Quantity import Quantity_Color
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    if Path(filename).suffix.lower() not in ('.step','.stp'):raise ValueError('手动模型请提供 STEP / STP 文件')
    if not 0<len(content)<=30*1024*1024:raise ValueError('STEP 模型应在 0–30 MB 之间，请先简化大型模型')
    temp=Path(home)/'模型草稿'/('input_'+secrets.token_hex(8)+'.step');temp.parent.mkdir(parents=True,exist_ok=True)
    temp.write_bytes(content)
    try:
        reader=STEPControl_Reader()
        if reader.ReadFile(str(temp))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('无法读取 STEP 模型')
        shape=reader.OneShape()
        # Shape colours survive in ordinary XCAF product definitions. When a
        # source does not assign solid colours, use the chosen fallback colour.
        doc=TDocStd_Document(TCollection_ExtendedString('import'))
        coloured=STEPCAFControl_Reader();coloured.SetColorMode(True)
        groups=[];fallback=PALETTE.get(options.get('material','body'),PALETTE['body'])
        if coloured.ReadFile(str(temp))==IFSelect_RetDone and coloured.Transfer(doc):
            st=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main());ct=XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
            from OCP.collections import Sequence_TDF_Label
            labels=Sequence_TDF_Label();st.GetFreeShapes(labels)
            def walk(label,inherited,location):
                from OCP.TDF import TDF_Label
                from OCP.TopLoc import TopLoc_Location
                colour=Quantity_Color();rgb=inherited
                for kind in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,XCAFDoc_ColorType.XCAFDoc_ColorGen):
                    if ct.GetColor_s(label,kind,colour):rgb=[colour.Red(),colour.Green(),colour.Blue()];break
                if st.IsReference_s(label):
                    ref=TDF_Label();st.GetReferredShape_s(label,ref)
                    walk(ref,rgb,location.Multiplied(st.GetLocation_s(label)));return
                children=Sequence_TDF_Label();st.GetComponents_s(label,children,False)
                if children.Length():
                    for i in range(1,children.Length()+1):walk(children.Value(i),rgb,location)
                    return
                item=st.GetShape_s(label)
                if item.IsNull():return
                ex=TopExp_Explorer(item,TopAbs_SOLID)
                while ex.More():
                    solid=ex.Current();own=rgb
                    for kind in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,XCAFDoc_ColorType.XCAFDoc_ColorGen):
                        if ct.GetColor(solid,kind,colour):own=[colour.Red(),colour.Green(),colour.Blue()];break
                    groups.append((solid.Moved(location),own));ex.Next()
            from OCP.TopLoc import TopLoc_Location
            for i in range(1,labels.Length()+1):walk(labels.Value(i),fallback,TopLoc_Location())
        if not groups:groups=[(shape,fallback)]
        if len(groups)>500:raise ValueError('模型包含超过 500 个实体，请先简化模型')
        return stage(home,name,groups,options,dict(kind='manual_step',file=Path(filename).name),
                     ['STEP 按声明单位转换为 mm；旋转后使用指定原点。请在预览中核对引脚中心、贴板高度和颜色；没有实体颜色的部分使用所选默认材质。'])
    finally:
        temp.unlink(missing_ok=True)


def stage_parametric(home,name,spec):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Pnt,gp_Ax2,gp_Dir
    if not isinstance(spec,dict) or not isinstance(spec.get('primitives'),list) or not 1<=len(spec['primitives'])<=300:
        raise ValueError('参数模型需要 1–300 个 box / cylinder 图元')
    groups=[]
    for p in spec['primitives']:
        if not isinstance(p,dict):raise ValueError('图元格式错误')
        centre=finite_vector(p.get('center'),'图元中心',250);material=p.get('material','body')
        if material not in PALETTE:raise ValueError('图元材质仅支持 body / white / metal / ferrite')
        if p.get('type')=='box':
            size=finite_vector(p.get('size'),'长宽高',200)
            if min(size)<=0:raise ValueError('长宽高必须大于零')
            shape=BRepPrimAPI_MakeBox(gp_Pnt(*[a-b/2 for a,b in zip(centre,size)]),*size).Shape()
        elif p.get('type')=='cylinder':
            try:r,h=float(p['radius']),float(p['height'])
            except (KeyError,ValueError,TypeError):raise ValueError('圆柱缺少半径/高度')
            if not all(math.isfinite(v) and 0<v<=200 for v in (r,h)):raise ValueError('圆柱尺寸无效')
            shape=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(centre[0],centre[1],centre[2]-h/2),gp_Dir(0,0,1)),r,h).Shape()
        else:raise ValueError('参数模型仅支持 box / cylinder；不会执行 AI 生成的代码')
        groups.append((shape,PALETTE[material]))
    return stage(home,name,groups,dict(origin='custom',origin_xyz=[0,0,0]),
                 dict(kind='ai_parametric',description=str(spec.get('description',''))[:2000]),
                 ['AI 参数模型是尺寸近似，保存前请核对规格书、引脚节距和原点；复杂外壳建议导入原厂 STEP。'])


def commit(home,draft,footprint=None):
    if not re.fullmatch(r'[a-f0-9]{24}',str(draft)):raise ValueError('模型草稿编号无效')
    if footprint is not None and (not isinstance(footprint,str) or not footprint.strip() or len(footprint)>200):raise ValueError('请选择生产坐标表中的完整封装名，或选择仅保存到库')
    home=Path(home);root=home/'模型草稿'/draft
    models=read_catalog(root)
    if len(models)!=1:raise ValueError('模型草稿不存在')
    key=next(iter(models));library=home/'用户模型库';catalog=read_catalog(library)
    if key in catalog:
        if catalog[key]==models[key]:return dict(model=key,footprint=footprint.strip() if footprint else None,library=str(library),already_saved=True)
        raise ValueError('此模型名已存在；请换一个名称，避免覆盖已校准模型')
    for part in models[key]['native_step_parts']:
        relative=part['path'];target=library/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/relative,target)
    catalog.update(models);write_json(library/'models.json',catalog)
    return dict(model=key,footprint=footprint.strip() if footprint else None,library=str(library))


PREVIEW_HTML='''<!doctype html><meta charset="utf-8"><style>html,body{width:100%;height:100%;overflow:hidden}body{margin:0;background:#161b22;color:#eee;font:12px sans-serif}#tools{position:absolute;top:8px;left:10px;right:8px;z-index:2}button{background:#303a48;border:1px solid #657183;color:#eee;padding:5px 9px;border-radius:4px;cursor:pointer}#hint{margin-top:6px;line-height:1.5;pointer-events:none}canvas{display:block}</style><div id="tools"><button id="iso">等轴</button> <button id="top">顶视 / 引脚分布</button> <button id="bottom">底视</button> <button id="front">侧视 / 高度</button><div id="hint">正在载入模型…</div></div><script type="importmap">{"imports":{"three":"/model-vendor/three.module.js"}}</script><script type="module">
import * as T from 'three';import {OrbitControls} from '/model-vendor/OrbitControls.js';
const hint=document.getElementById('hint');
function failed(error){hint.textContent='模型预览失败：'+(error?.message||String(error));}
addEventListener('error',event=>failed(event.error||event.message));addEventListener('unhandledrejection',event=>failed(event.reason));
const data=await (await fetch('./models.json')).json(),model=Object.values(data)[0],scene=new T.Scene();
const camera=new T.PerspectiveCamera(35,innerWidth/innerHeight,.01,3000),renderer=new T.WebGLRenderer({antialias:true});renderer.setClearColor(0x161b22);renderer.setSize(innerWidth,innerHeight);document.body.append(renderer.domElement);
scene.add(new T.HemisphereLight(0xffffff,0x555566,3));const light=new T.DirectionalLight(0xffffff,3);light.position.set(20,30,40);scene.add(light);const root=new T.Group();scene.add(root);
const baseColours={'PIN-01':0xd0d5db,'PIN-02':0xd0d5db,'IC-BODY-EPOXY-04':0x1a1c21,'IC-LABEL-01':0x2a2d33,'RES-SMD-01':0x15171b,'CAP-CERAMIC-06':0xb8864f,'CAP-CERAMIC-05':0xb8864f,'PLASTIC-WHITE-01':0xe9e7dc,'PLASTIC-BLACK-01':0x1e2024,'FERRITE-01':0x35383b,'FERRITE-DARK-01':0x242627,'MOLDED-GRAY':0x626363,'CORE-GRAY':0x303333,'MARK-GRAY':0x929792,'MARK-DARK':0x2b2c2a};
for(const part of model.parts){const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(part.v,3));geo.setIndex(part.i);geo.computeVertexNormals();const rgb=model.materials?.[part.mat],color=rgb?new T.Color(...rgb):new T.Color(baseColours[part.mat]??0x33363a);const mat=new T.MeshStandardMaterial({color,roughness:.5,metalness:part.mat.startsWith('PIN-')?.9:.15,side:T.DoubleSide});root.add(new T.Mesh(geo,mat));}
const box=new T.Box3().setFromObject(root),size=box.getSize(new T.Vector3()),centre=box.getCenter(new T.Vector3()),span=Math.max(size.x,size.y,size.z,1);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.copy(centre);
const grid=new T.GridHelper(span*1.5,10,0x47a977,0x354c44);grid.material.transparent=true;grid.material.opacity=.5;scene.add(grid);
for(const [direction,colour] of [[[1,0,0],0xe76f62],[[0,0,-1],0x68ce8a],[[0,1,0],0x69a8ed]])scene.add(new T.ArrowHelper(new T.Vector3(...direction),new T.Vector3(),span*.65,colour,span*.07,span*.04));
const origin=new T.Mesh(new T.SphereGeometry(span*.014,12,8),new T.MeshBasicMaterial({color:0xffd26e}));scene.add(origin);
function view(name){const distance=span*2.5/Math.min(1,camera.aspect);camera.up.set(0,1,0);let direction;
 if(name==='top'){direction=new T.Vector3(0,1,0);camera.up.set(0,0,-1);}
 else if(name==='bottom'){direction=new T.Vector3(0,-1,0);camera.up.set(0,0,1);}
 else if(name==='front')direction=new T.Vector3(0,0,1);else direction=new T.Vector3(1,1,1).normalize();
 camera.position.copy(centre).addScaledVector(direction,distance);controls.target.copy(centre);controls.update();}
for(const name of ['iso','top','bottom','front'])document.getElementById(name).onclick=()=>view(name);view('iso');
hint.textContent='长 X × 宽 Y × 高 Z：'+[size.x,size.z,size.y].map(v=>v.toFixed(3)).join(' × ')+' mm。绿格为贴板面，黄点为放置原点；红 X / 绿 Y / 蓝 Z。拖动旋转，滚轮缩放。';
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera)});addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)});
parent.postMessage({type:'pcba-model-preview-ready',model:Object.keys(data)[0]},location.origin);
</script>'''
