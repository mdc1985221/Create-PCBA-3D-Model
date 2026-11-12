"""Export a named, coloured STEP assembly. Units: mm, XY board, +Z top."""
import json,math,re,sys
from pathlib import Path

def export_step(project, destination, progress=print):
    from OCP.gp import gp_Pnt,gp_Vec,gp_Trsf,gp_Ax2,gp_Dir
    from OCP.BRep import BRep_Builder,BRep_Tool
    from OCP.TopoDS import TopoDS,TopoDS_Compound
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace,BRepBuilderAPI_Sewing,BRepBuilderAPI_MakeSolid,BRepBuilderAPI_Transform,BRepBuilderAPI_MakeWire,BRepBuilderAPI_MakeEdge
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism,BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.ShapeFix import ShapeFix_Shape,ShapeFix_Solid,ShapeFix_Shell
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopAbs import TopAbs_SHELL,TopAbs_SOLID,TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString,TCollection_AsciiString
    from OCP.TDataStd import TDataStd_Name
    from OCP.XCAFDoc import XCAFDoc_DocumentTool,XCAFDoc_ColorType,XCAFDoc_VisMaterial,XCAFDoc_VisMaterialCommon
    from OCP.Quantity import Quantity_Color,Quantity_ColorRGBA,Quantity_TOC_RGB
    from OCP.Graphic3d import Graphic3d_AlphaMode_Blend
    from OCP.STEPCAFControl import STEPCAFControl_Writer,STEPCAFControl_Reader
    from OCP.STEPControl import STEPControl_AsIs,STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopLoc import TopLoc_Location

    R=Path(project);load=lambda n:json.loads((R/n).read_text(encoding='utf8'))
    D=load('data.json');models=load('models/models.json');poses=load('posefix.json') if (R/'posefix.json').exists() else {}
    from calibration import values,base_pose
    calibration=load('calibration.json').get('items',{}) if (R/'calibration.json').exists() else {}
    geo=load('board_geometry.json');w,h=D['board']['w'],D['board']['h'];th=D['board'].get('thickness',1.6)
    html=(R/'index.html').read_text(encoding='utf8')
    mapping=dict(re.findall(r"'([^']+)':\s*'([^']+)'",html.split('const MODEL_MAP = {')[1].split('};')[0]))
    overrides=dict(re.findall(r"(\w+):\s*'([^']+)'",html.split('const MODEL_BY_REF = {')[1].split('};')[0]))
    if (R/'model_mapping.json').exists():mapping.update(load('model_mapping.json'))
    colours={k:tuple(int(v[i:i+2],16)/255 for i in (0,2,4)) for k,v in re.findall(r"'([^']+)':\s*(?:new THREE\.\w+Material|mat)\(\{ color: 0x([a-fA-F0-9]{6})",html)}
    for model in models.values():colours.update({key:tuple(rgb) for key,rgb in model.get('materials',{}).items()})
    transparency={'SENSOR-GLASS':.86,'SENSOR-FILM':.73}
    def compound(shapes):
        c=TopoDS_Compound();b=BRep_Builder();b.MakeCompound(c)
        for s in shapes:
            if not s.IsNull():b.Add(c,s)
        return c
    def volume(s):
        p=GProp_GProps();BRepGProp.VolumeProperties_s(s,p);return abs(p.Mass())
    def polywire(points):
        p=BRepBuilderAPI_MakePolygon()
        for x,y,z in points:p.Add(gp_Pnt(x,y,z))
        p.Close();return p.Wire()
    def prism(points,height):return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polywire(points)).Face(),gp_Vec(0,0,height)).Shape()
    # Analytic circular board corners, not a faceted outline.
    radius=geo.get('corner_radius_mm',0);r=min(radius,w/2,h/2)
    wire=BRepBuilderAPI_MakeWire()
    if geo.get('outline'):
        outline=polywire([(x,y,0) for x,y in geo['outline']])
    elif r:
        segments=[((r,0),(w-r,0)),((w,r),(w,h-r)),((w-r,h),(r,h)),((0,h-r),(0,r))]
        mids=[(w-r+r/math.sqrt(2),r-r/math.sqrt(2)),(w-r+r/math.sqrt(2),h-r+r/math.sqrt(2)),(r-r/math.sqrt(2),h-r+r/math.sqrt(2)),(r-r/math.sqrt(2),r-r/math.sqrt(2))]
        for i,(a,b) in enumerate(segments):
            wire.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*a,0),gp_Pnt(*b,0)).Edge())
            end=segments[(i+1)%4][0];mid=mids[i]
            curve=GC_MakeArcOfCircle(gp_Pnt(*b,0),gp_Pnt(*mid,0),gp_Pnt(*end,0)).Value()
            wire.Add(BRepBuilderAPI_MakeEdge(curve).Edge())
        outline=wire.Wire()
    else:outline=polywire([(0,0,0),(w,0,0),(w,h,0),(0,h,0)])
    face=BRepBuilderAPI_MakeFace(outline)
    all_drill_diameters=geo.get('include_all_drill_diameters') is True
    def visible_diameter(d):return d>0 and (all_drill_diameters or .4<=d<=3)
    # Include the same physical holes shown in the viewer.
    for q in geo.get('round_holes',[]):
        x,y,d=q['x'],q['y'],q['d']
        if visible_diameter(d) and x-d/2>=0 and x+d/2<=w and y-d/2>=0 and y+d/2<=h:
            circle=BRepBuilderAPI_MakeEdge(__import__('OCP.gp',fromlist=['gp_Circ']).gp_Circ(gp_Ax2(gp_Pnt(x,y,0),gp_Dir(0,0,1)),d/2)).Edge()
            hole=BRepBuilderAPI_MakeWire(circle).Wire();hole.Reverse();face.Add(hole)
    for pts in geo.get('routed_slots',[]):
        q=polywire([(x,y,0) for x,y in pts]);q.Reverse();face.Add(q)
    board=BRepPrimAPI_MakePrism(face.Face(),gp_Vec(0,0,th)).Shape()
    if not BRepCheck_Analyzer(board).IsValid():
        # Routed contours may have the opposite winding; boolean cuts are robust.
        board=BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(outline).Face(),gp_Vec(0,0,th)).Shape()
        for q in geo.get('round_holes',[]):
            x,y,d=q['x'],q['y'],q['d']
            if visible_diameter(d) and d/2<=x<=w-d/2 and d/2<=y<=h-d/2:
                board=BRepAlgoAPI_Cut(board,BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x,y,-.1),gp_Dir(0,0,1)),d/2,th+.2).Shape()).Shape()
        for pts in geo.get('routed_slots',[]):board=BRepAlgoAPI_Cut(board,prism([(x,y,-.1) for x,y in pts],th+.2)).Shape()
    assert BRepCheck_Analyzer(board).IsValid(),'PCB solid invalid'
    doc=TDocStd_Document(TCollection_ExtendedString('PCBA'))
    shape_tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main());color_tool=XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    material_tool=XCAFDoc_DocumentTool.VisMaterialTool_s(doc.Main());visual_materials={}
    def name(l,t):TDataStd_Name.Set_s(l,TCollection_ExtendedString(t))
    root=shape_tool.NewShape();name(root,D['board'].get('name','PCBA'))
    empty=compound([]);shape_tool.SetShape(root,empty)
    def add_def(shape,title,col,face_colour=None,material=None):
        l=shape_tool.AddShape(shape,False);name(l,title)
        color_tool.SetColor(l,Quantity_Color(*col,Quantity_TOC_RGB),XCAFDoc_ColorType.XCAFDoc_ColorGen)
        if material in transparency:
            color_tool.SetColor(l,Quantity_ColorRGBA(Quantity_Color(*col,Quantity_TOC_RGB),1-transparency[material]),XCAFDoc_ColorType.XCAFDoc_ColorGen)
            if material not in visual_materials:
                common=XCAFDoc_VisMaterialCommon();common.IsDefined=True
                common.DiffuseColor=Quantity_Color(*col,Quantity_TOC_RGB);common.Transparency=transparency[material]
                visual=XCAFDoc_VisMaterial();visual.SetCommonMaterial(common);visual.SetAlphaMode(Graphic3d_AlphaMode_Blend)
                visual_materials[material]=material_tool.AddMaterial(visual,TCollection_AsciiString(material))
            material_tool.SetShapeMaterial(l,visual_materials[material])
        if face_colour:
            ex=TopExp_Explorer(shape,TopAbs_FACE)
            while ex.More():
                f=ex.Current();props=GProp_GProps();BRepGProp.SurfaceProperties_s(f,props);c=props.CentreOfMass()
                co=face_colour((c.X(),c.Y(),c.Z()))
                sub=shape_tool.AddSubShape(l,f)
                if not sub.IsNull():color_tool.SetColor(sub,Quantity_Color(*co,Quantity_TOC_RGB),XCAFDoc_ColorType.XCAFDoc_ColorSurf)
                ex.Next()
        return l
    board_label=shape_tool.NewShape();shape_tool.SetShape(board_label,empty);name(board_label,'PCB_WITH_GERBER_ARTWORK')
    from board_appearance import appearance,theme
    board_appearance=appearance(R);board_colour=tuple(v/255 for v in theme(board_appearance['color'])['base'])
    core_label=add_def(board,'PCB_FR4_%.3fx%.3fx%.3f_mm'%(w,h,th),board_colour,
                       lambda xyz:board_colour if abs(xyz[2])<.001 or abs(xyz[2]-th)<.001 else (134/255,123/255,80/255))
    shape_tool.AddComponent(board_label,core_label,TopLoc_Location())
    # STEP cannot carry the viewer's PNG texture. Export its actual visible
    # lands/openings/silkscreen as coloured planar BREP artwork instead. These
    # cosmetic faces do not change the mechanical PCB thickness or add caps
    # over drilled holes. Both PNGs are in board XY, never mirror the bottom.
    from board_surfaces import board_layers,area
    artwork=[]
    for layer in board_layers(R,w,h,geo):
        progress('CAD board artwork '+layer['side']+' '+layer['kind'])
        faces=[];z=th+.004 if layer['side']=='top' else -.004
        for polygon in layer['polygons']:
            def surface_wire(points,outer):
                if (area(points)>0)!=outer:points=list(reversed(points))
                return polywire([(x,y,z) for x,y in points])
            make=BRepBuilderAPI_MakeFace(surface_wire(polygon['outer'],True))
            for points in polygon['holes']:make.Add(surface_wire(points,False))
            f=make.Face()
            if not BRepCheck_Analyzer(f).IsValid():
                fix=ShapeFix_Shape(f);fix.Perform();f=fix.Shape()
            if not BRepCheck_Analyzer(f).IsValid():raise ValueError('Invalid board artwork '+layer['side']+' '+layer['kind'])
            if layer['side']=='bot':f.Reverse()
            faces.append(f)
        if faces:
            label=add_def(compound(faces),'GERBER_'+layer['side'].upper()+'_'+layer['kind'],layer['colour'])
            shape_tool.AddComponent(board_label,label,TopLoc_Location())
        artwork.append(dict(side=layer['side'],kind=layer['kind'],faces=len(faces),source=layer['source'],pixel_size_mm=layer['pixel_size_mm']))
    shape_tool.AddComponent(root,board_label,TopLoc_Location())
    cache={};mesh_audit=[]
    def from_mesh(parts,key,material):
        sew=BRepBuilderAPI_Sewing(.0001,True,True,True,False)
        triangles=0;centres=[];materials=[]
        for p in parts:
            v=p['v'];idx=p['i']
            for j in range(0,len(idx),3):
                points=[tuple(v[3*k:3*k+3]) for k in idx[j:j+3]]
                if len(set(points))<3:continue
                try:
                    f=BRepBuilderAPI_MakeFace(polywire(points))
                    if f.IsDone() and BRepCheck_Analyzer(f.Face()).IsValid():
                        sew.Add(f.Face());triangles+=1
                        centres.append(tuple(sum(a[k] for a in points)/3 for k in range(3)));materials.append(p['mat'])
                except Exception:continue
        if not triangles:return None
        sew.Perform();s=sew.SewedShape()
        solids=[];open_shells=0
        ex=TopExp_Explorer(s,TopAbs_SHELL)
        while ex.More():
            shell=TopoDS.Shell(ex.Current())
            if True:  # ShapeFix determines closure; imported WRL shells lack the cached Closed flag.
                shfix=ShapeFix_Shell(shell);shfix.Perform();shell=shfix.Shell()
                sf=ShapeFix_Solid();sol=sf.SolidFromShell(shell)
                if not sol.IsNull() and BRepCheck_Analyzer(sol).IsValid() and volume(sol)>1e-10:solids.append(sol)
                else:open_shells+=1
            else:open_shells+=1
            ex.Next()
        if not solids or open_shells:
            raise ValueError(f'{key}/{material}: {len(solids)} closed solids, {open_shells} unclosed shells')
        s=compound(solids);unify=ShapeUpgrade_UnifySameDomain(s,True,True,True);unify.Build();s=unify.Shape()
        mesh_audit.append(dict(model=key,material=material,solids=len(solids),triangles=triangles,valid=BRepCheck_Analyzer(s).IsValid()))
        from scipy.spatial import cKDTree
        tree=cKDTree(centres)
        return s,lambda xyz:colours.get(materials[tree.query(xyz)[1]],(.12,.13,.14))
    records=[]
    for ref,p in D['placements'].items():
        key=overrides.get(ref,mapping.get(p['pkg']))
        if key is None:raise ValueError('Missing model '+ref+' '+p['pkg'])
        if key not in cache:
            progress('CAD '+key)
            # WRL colour regions can share open interfaces. Sew all colour
            # regions together, then restore per-face colours on the solid.
            model=models[key]
            if model.get('native_step_parts'):
                native_transform=gp_Trsf()
                native_transform.SetValues(*[v for row in model['native_transform'] for v in row])
                children=[]
                for item in model['native_step_parts']:
                    source=(R/item['path']).resolve()
                    if not source.is_relative_to(R.resolve()):raise ValueError('Native model path leaves project')
                    reader=STEPControl_Reader()
                    assert reader.ReadFile(str(source))==IFSelect_RetDone,'Native STEP read failed: '+str(source)
                    assert reader.TransferRoots(),'Native STEP has no shape: '+str(source)
                    shape=BRepBuilderAPI_Transform(reader.OneShape(),native_transform,True).Shape()
                    assert BRepCheck_Analyzer(shape).IsValid(),'Invalid native STEP: '+str(source)
                    ex=TopExp_Explorer(shape,TopAbs_SOLID);count=0
                    while ex.More():count+=1;ex.Next()
                    mesh_audit.append(dict(model=key,material=item['mat'],solids=count,triangles=0,valid=True,source='native STEP'))
                    colour=None
                    if model.get('native_face_colours'):
                        from scipy.spatial import cKDTree
                        points=[];palette=[]
                        for part in model['parts']:
                            if part['mat'].startswith('MARK'):continue
                            for j in range(0,len(part['i']),3):
                                ids=part['i'][j:j+3]
                                points.append(tuple(sum(part['v'][3*k+a] for k in ids)/3 for a in range(3)))
                                palette.append(part['mat'])
                        tree=cKDTree(points)
                        colour=lambda xyz,tree=tree,palette=palette:colours.get(palette[tree.query(xyz)[1]],(.12,.13,.14))
                    children.append((item['mat'],shape,colour))
                cache[key]=children
            elif model.get('mats'):
                shape,colour=from_mesh(model['parts'],key,'all colours')
                cache[key]=[(key,shape,colour)]
            else:
                children=[]
                for part in model['parts']:
                    if not part['i'] or part['mat'].startswith('MARK'):continue
                    # Generated solids use independent vertex indices. Keep
                    # touching boxes as separate solids, retaining inner faces.
                    parent={}
                    def find(i):
                        parent.setdefault(i,i)
                        if parent[i]!=i:parent[i]=find(parent[i])
                        return parent[i]
                    for j in range(0,len(part['i']),3):
                        a,b,c=part['i'][j:j+3];parent[find(b)]=find(a);parent[find(c)]=find(a)
                    groups={}
                    for j in range(0,len(part['i']),3):
                        tri=part['i'][j:j+3];groups.setdefault(find(tri[0]),[]).extend(tri)
                    for indices in groups.values():
                        sub=dict(mat=part['mat'],v=part['v'],i=indices)
                        shape,colour=from_mesh([sub],key,part['mat']);children.append((part['mat'],shape,colour))
                cache[key]=children
        pose=poses.get(ref,{})
        px,py,angle=base_pose(p,pose);correction=values(calibration.get(ref,{}))
        px+=correction['dx'];py+=correction['dy'];angle+=correction['dyaw']
        a=math.radians(angle);c,s=math.cos(a),math.sin(a);top=p['side']=='top'
        tr=gp_Trsf()
        if top:tr.SetValues(c,0,s,px,s,0,-c,py,0,1,0,th+.03+correction['dz'])
        else:tr.SetValues(c,0,-s,px,s,0,c,py,0,-1,0,-.03-correction['dz'])
        local=shape_tool.NewShape();shape_tool.SetShape(local,empty);name(local,ref+' '+p.get('val','')+' ['+p['pkg']+']')
        for material,shape,colour in cache[key]:
            label=add_def(shape,key,colours.get(material,(.12,.13,.14)),colour,material)
            shape_tool.AddComponent(local,label,TopLoc_Location())
        inst=shape_tool.AddComponent(root,local,TopLoc_Location(tr));name(inst,ref)
        records.append(dict(ref=ref,model=key,side=p['side'],x=px,y=py,z=th+.03+correction['dz'] if top else -.03-correction['dz'],rotation_deg=angle,calibration=correction))
    shape_tool.UpdateAssemblies()
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    writer=STEPCAFControl_Writer();writer.SetColorMode(True);writer.SetNameMode(True);writer.SetVisualMaterialMode(True)
    assert writer.Transfer(doc,STEPControl_AsIs),'STEP transfer failed'
    assert writer.Write(str(path))==IFSelect_RetDone,'STEP write failed'
    # Reopen with a fresh CAD document, verify all names and geometry.
    check=TDocStd_Document(TCollection_ExtendedString('check'));rd=STEPCAFControl_Reader()
    assert rd.ReadFile(str(path))==IFSelect_RetDone and rd.Transfer(check),'STEP read-back failed'
    st=XCAFDoc_DocumentTool.ShapeTool_s(check.Main());seq=TDF_LabelSequence();st.GetFreeShapes(seq)
    assert seq.Length()==1,'Unexpected STEP assembly root count'
    root_shape=st.GetShape_s(seq.Value(1));assert BRepCheck_Analyzer(root_shape).IsValid(),'Invalid STEP geometry'
    bb=Bnd_Box();BRepBndLib.Add_s(root_shape,bb)
    lo,hi=bb.CornerMin(),bb.CornerMax();bounds=(lo.X(),lo.Y(),lo.Z(),hi.X(),hi.Y(),hi.Z())
    components=TDF_LabelSequence();st.GetComponents_s(seq.Value(1),components,False)
    assert components.Length()==len(records)+1,(components.Length(),len(records))
    ex=TopExp_Explorer(root_shape,TopAbs_SOLID);solid_count=0
    while ex.More():solid_count+=1;ex.Next()
    report=dict(file=path.name,board_appearance=board_appearance,units='mm',board_mm=[w,h,th],corner_radius_mm=radius,components=len(records),board_plus_components=components.Length(),root_assemblies=seq.Length(),solid_count=solid_count,bounds_mm=list(bounds),valid=True,read_back=True,mesh_materials=mesh_audit,placements=records,board_artwork=artwork,visual_transparency=transparency,note='Native STEP retained where available. Gerber pad/opening/silkscreen artwork is exported as coloured planar BREP faces, sampled from the same board images as the preview. Film/glass transparency uses standard STEP visual materials; CAD display settings may override it. Lighting, reflections and mesh-only component lettering can differ. Approximate package details remain approximate.')
    path.with_suffix('.validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    progress('STEP verified: '+str(path));return report

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('destination');args=ap.parse_args()
    export_step(args.project,args.destination)
