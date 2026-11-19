"""Offline DFN demonstration: confirmed dimensions -> STEP draft, no API.

This is an authoring template, not PDF/OCR recognition or a factory model.
"""
import argparse,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def create(home,spec):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.gp import gp_Pnt,gp_Ax2,gp_Dir
    from PIL import Image,ImageDraw,ImageFont
    from user_models import stage
    def number(key):
        raw=spec.get(key)
        if isinstance(raw,bool):raise ValueError('Invalid dimension: '+key)
        value=float(raw)
        if not math.isfinite(value) or not 0<value<=50:raise ValueError('Invalid dimension: '+key)
        return value
    w,d,h,stand,pitch,pw,pl,mh,ew,ed,recess=[number(k) for k in
        ['width','depth','height','stand_off','pitch','pin_width','pin_length','metal_height','exposed_width','exposed_depth','recess']]
    count=spec.get('pin_count')
    if type(count)!=int or count%2 or not 4<=count<=100:raise ValueError('pin_count must be an even integer, 4..100')
    per=count//2
    if ((per-1)*pitch+pw>w or pw>=pitch or pl>=d/2 or ew>=w or ed>=d-2*pl or
        stand>=mh or mh>=h or recess>=h-mh):raise ValueError('Inconsistent body, lead, pad or engraving dimensions')
    corner=spec.get('pin1')
    if corner not in ['tl','tr','bl','br']:raise ValueError('Confirm pin1 in component top view')
    mark=str(spec.get('marking','')).strip()
    if not mark or len(mark)>24 or not mark.isascii() or any(ord(c)<32 for c in mark):raise ValueError('Use 1..24 printable ASCII marking characters')
    def box(x,y,z,a,b,c):return BRepPrimAPI_MakeBox(gp_Pnt(x,y,z),a,b,c).Shape()
    def compound():
        c=TopoDS_Compound();builder.MakeCompound(c);return c
    builder=BRep_Builder();metal=compound();groups=[]
    for side in [-1,1]:
        for i in range(per):
            x=(i-(per-1)/2)*pitch;y=-d/2 if side<0 else d/2-pl
            lead=box(x-pw/2,y,0,pw,pl,mh);builder.Add(metal,lead);groups.append((lead,[.65,.68,.72]))
    pad=box(-ew/2,-ed/2,0,ew,ed,mh);builder.Add(metal,pad);groups.append((pad,[.65,.68,.72]))
    body=BRepAlgoAPI_Cut(box(-w/2,-d/2,stand,w,d,h-stand),metal).Shape()
    cutters=compound();ink=compound();floor=h-recess
    try:font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',22)
    except OSError:font=ImageFont.load_default(size=22)
    bounds=font.getbbox(mark);im=Image.new('1',(bounds[2]-bounds[0],bounds[3]-bounds[1]))
    ImageDraw.Draw(im).text((-bounds[0],-bounds[1]),mark,font=font,fill=1)
    scale=min(w*.7/im.width,d*.19/im.height)
    for row in range(im.height):
        col=0
        while col<im.width:
            if not im.getpixel((col,row)):col+=1;continue
            start=col
            while col<im.width and im.getpixel((col,row)):col+=1
            x=(start-im.width/2)*scale;y=(im.height/2-row-1)*scale
            builder.Add(cutters,box(x,y,floor,(col-start)*scale,scale,recess+.002))
            builder.Add(ink,box(x,y,floor,(col-start)*scale,scale,recess*.2))
    radius=min(w,d)*.045;margin=radius*2
    x=(-1 if corner.endswith('l') else 1)*(w/2-margin);y=(1 if corner.startswith('t') else -1)*(d/2-margin)
    axis=gp_Ax2(gp_Pnt(x,y,floor),gp_Dir(0,0,1))
    builder.Add(cutters,BRepPrimAPI_MakeCylinder(axis,radius,recess+.002).Shape())
    builder.Add(ink,BRepPrimAPI_MakeCylinder(axis,radius,recess*.2).Shape())
    body=BRepAlgoAPI_Cut(body,cutters).Shape()
    groups=[(body,[.006,.007,.009]),*groups,(ink,[.5,.52,.47])]
    if any(not BRepCheck_Analyzer(shape).IsValid() for shape,_ in groups):raise ValueError('Invalid package geometry')
    source=dict(kind='synthetic_dfn_template',parameters=spec,estimated=True,
                note='Generic rectangular contacts and exposed pad; no factory branding, batch codes or PDF recognition')
    result=stage(home,spec['name'],groups,dict(origin='custom',origin_xyz=[0,0,0]),source,
                 ['Synthetic demonstration only. Verify actual datasheet dimensions and pin numbering before binding.'])
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True,help='Local tool data folder, or an isolated demo directory')
    parser.add_argument('--spec',type=Path,default=Path(__file__).resolve().parents[1]/'examples/package-template/dfn12.json')
    parser.add_argument('--save',action='store_true',help='After review, save a separate model into the user library')
    args=parser.parse_args();spec=json.loads(args.spec.read_text(encoding='utf-8-sig'));result=create(args.home,spec)
    if args.save:
        from user_models import commit
        result['saved']=commit(args.home,result['draft'])
    print(json.dumps(result,ensure_ascii=True,indent=2))

if __name__=='__main__':main()
