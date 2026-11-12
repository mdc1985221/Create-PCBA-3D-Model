"""Read geometric pin-one evidence from the selected fabrication silkscreen.

Never infer numbering from missing BGA balls or arbitrary copper/paste pads.
All evidence is expressed in the same component top view as the generated model.
"""
import json
import math
from pathlib import Path


def detect(root, result, data):
    unknown=dict(corner='unknown',status='unresolved',style='dot',evidence=[],
                 note='未找到唯一可靠的第一脚标记，请对照制板丝印或规格书确认。')
    root=Path(root)
    report=root/'导入检查.json'
    if not report.exists():return unknown
    layers=json.loads(report.read_text(encoding='utf8')).get('layers',{})
    filename=layers.get('top_silk' if result['side']=='top' else 'bot_silk')
    if not filename or Path(filename).name!=filename:return unknown
    path=root/'输入资料'/filename
    if not path.is_file():return unknown
    frame=data.get('coordinate_frame',{})
    if 'gerber_x0' not in frame or 'gerber_y0' not in frame:return unknown
    import gerber
    try:primitives=gerber.parse(path)
    except ValueError as error:
        return dict(unknown,note='第一脚识别未完成：'+str(error))
    # Clear artwork can erase an apparent dot/triangle. Do not interpret it
    # without a full polarity-aware topology analysis.
    if any(not p[1] for p in primitives):
        return dict(unknown,note='丝印含清除图形，第一脚请人工确认。')
    angle=math.radians(result['rotation']);c,s=math.cos(angle),math.sin(angle)
    sign=1 if result['side']=='top' else -1;cx,cy=result['center']
    def local(point):
        dx=point[0]-frame['gerber_x0']-cx;dy=point[1]-frame['gerber_y0']-cy
        return [c*dx+s*dy,(-s*dx+c*dy)*sign]
    w,d=result['width'],result['depth'];near=[];flashes=[]
    def nearby(points):return all(abs(x)<w/2+1.5 and abs(y)<d/2+1.5 for x,y in points)
    for p in primitives:
        if p[0]=='stroke':
            points=list(map(local,p[2]))
            if nearby(points):near.append(('stroke',True,points,p[3]))
        elif p[0]=='region':
            for ring in p[2]:
                points=list(map(local,ring))
                if nearby(points):near.append(('region',True,[points]))
        elif p[0]=='flash':
            for exposure,typ,shape in p[4]:
                if exposure and typ=='C':
                    x,y=local((p[2]+shape[0],p[3]+shape[1]))
                    if nearby([(x,y)]) and .15<=shape[2]<=1.0:flashes.append((x,y,'dot','丝印圆点'))
    loops=gerber.closed_contours(near);outlines=[]
    for loop in loops:
        xs,ys=zip(*loop);box=min(xs),min(ys),max(xs),max(ys)
        bw,bh=box[2]-box[0],box[3]-box[1]
        if (.8*w<=bw<=1.25*w and .8*d<=bh<=1.25*d and
            abs(box[0]+box[2])<min(result['pitch']) and abs(box[1]+box[3])<min(result['pitch']) and
            gerber.polygon_area(loop)>.75*bw*bh):outlines.append(box)
    # Requiring a matching package outline excludes dots on neighbouring parts
    # and panel legends. Unclosed/missing outlines remain manual for now.
    if not outlines:return unknown
    box=min(outlines,key=lambda b:abs(b[2]-b[0]-w)+abs(b[3]-b[1]-d))
    corners={'tl':(box[0],box[3]),'tr':(box[2],box[3]),
             'bl':(box[0],box[1]),'br':(box[2],box[1])}
    evidence=[]
    def add(x,y,style,kind):
        distances=sorted((math.dist((x,y),p),key) for key,p in corners.items())
        if distances[0][0]<=min(1.2,min(w,d)*.2) and distances[1][0]-distances[0][0]>.5:
            evidence.append(dict(corner=distances[0][1],style=style,kind=kind,point_mm=[round(x,5),round(y,5)]))
    for p in near:
        if p[0]!='stroke' or len(p[2])!=2:continue
        a,b=p[2];dx,dy=abs(a[0]-b[0]),abs(a[1]-b[1])
        if not .15<=math.hypot(dx,dy)<=min(w,d)*.22 or min(dx,dy)<.35*max(dx,dy):continue
        for corner,(x,y) in corners.items():
            # A diagonal joins the two edges meeting at this outline corner.
            if ((abs(a[0]-x)<.04 and abs(b[1]-y)<.04) or
                (abs(b[0]-x)<.04 and abs(a[1]-y)<.04)):
                add((a[0]+b[0])/2,(a[1]+b[1])/2,'triangle','封装角部斜线 / 倒角')
    for x,y,style,kind in flashes:add(x,y,style,kind)
    for loop in loops:
        if math.dist(loop[0],loop[-1])<.02:loop=loop[:-1]
        xs,ys=zip(*loop);bw,bh=max(xs)-min(xs),max(ys)-min(ys)
        if not .15<=bw<=1.0 or not .15<=bh<=1.0:continue
        x=(min(xs)+max(xs))/2;y=(min(ys)+max(ys))/2
        if len(loop)==3 and gerber.polygon_area(loop)>.02:add(x,y,'triangle','封闭丝印三角')
        elif len(loop)>=12 and .85<bw/bh<1.15:
            radii=[math.hypot(px-x,py-y) for px,py in loop];radius=sum(radii)/len(radii)
            if max(abs(r-radius) for r in radii)<radius*.08:add(x,y,'dot','封闭丝印圆圈')
    choices={e['corner'] for e in evidence}
    if len(choices)!=1:
        return dict(unknown,evidence=evidence,note='第一脚线索指向多个角，请人工核对。' if choices else unknown['note'])
    corner=next(iter(choices))
    return dict(corner=corner,status='detected',style=evidence[0]['style'],evidence=evidence,
                layer=filename,outline_topview_mm=list(box),basis='fabrication_silk_geometry',
                note='已从制板丝印识别：'+ '、'.join(sorted({e['kind'] for e in evidence}))+'；可人工更正。')
