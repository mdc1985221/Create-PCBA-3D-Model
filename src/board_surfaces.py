"""Vectorise the actual viewer board layers for texture-free STEP readers.

Only numpy/Pillow are required. Pixel boundaries are traced with foreground on
the right; diagonal contacts remain separate loops. Coordinates stay in the
same board XY frame as the PNGs, including the unmirrored bottom image.
"""
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw


def area(points):
    return sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))/2


def simplify(points,tolerance):
    def rdp(p):
        if len(p)<3:return p
        a,b=p[0],p[-1];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        distances=[abs(dx*(a[1]-y)-(a[0]-x)*dy)/length if length else math.dist(a,(x,y)) for x,y in p[1:-1]]
        maximum=max(distances,default=0)
        if maximum<=tolerance:return [a,b]
        i=distances.index(maximum)+1
        return rdp(p[:i+1])[:-1]+rdp(p[i:])
    # Split a closed contour at distant points; never simplify identical ends.
    i=max(range(len(points)),key=lambda i:math.dist(points[0],points[i]))
    return rdp(points[:i+1])[:-1]+rdp(points[i:]+points[:1])[:-1]


def contours(mask,tolerance=0.6):
    """Return closed pixel-edge contours, outer clockwise and holes CCW."""
    a=np.pad(np.asarray(mask,dtype=bool),1);inner=a[1:-1,1:-1]
    edges={}
    def add(start,end):edges.setdefault(start,[]).append(end)
    for y,x in zip(*np.nonzero(inner & ~a[:-2,1:-1])):add((int(x),int(y)),(int(x+1),int(y)))
    for y,x in zip(*np.nonzero(inner & ~a[1:-1,2:])):add((int(x+1),int(y)),(int(x+1),int(y+1)))
    for y,x in zip(*np.nonzero(inner & ~a[2:,1:-1])):add((int(x+1),int(y+1)),(int(x),int(y+1)))
    for y,x in zip(*np.nonzero(inner & ~a[1:-1,:-2])):add((int(x),int(y+1)),(int(x),int(y)))
    directions={(1,0):0,(0,1):1,(-1,0):2,(0,-1):3};result=[]
    while edges:
        first=next(iter(edges));point=first;incoming=None;loop=[]
        while True:
            loop.append(point);choices=edges[point]
            if incoming is None or len(choices)==1:nxt=choices[0]
            else:
                def turn(q):
                    delta=(directions[(q[0]-point[0],q[1]-point[1])]-incoming)%4
                    return {1:0,0:1,3:2,2:3}[delta]
                nxt=min(choices,key=turn)
            choices.remove(nxt)
            if not choices:del edges[point]
            incoming=directions[(nxt[0]-point[0],nxt[1]-point[1])];point=nxt
            if point==first:break
        original=area(loop)
        if abs(original)<2:continue
        reduced=simplify(loop,tolerance)
        if len(reduced)>=3 and area(reduced)*original>0:result.append(reduced)
    return result


def inside(point,polygon):
    x,y=point;hit=False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:hit=not hit
    return hit


def board_layers(project,w,h,geo):
    layers=[]
    for side in ('top','bot'):
        filename=Path(project)/('board_'+side+'.png')
        if not filename.exists():continue
        im=Image.open(filename).convert('RGB');width,height=im.size;sx,sy=width/w,height/h
        rgb=np.asarray(im)
        keep=Image.new('1',im.size,0);draw=ImageDraw.Draw(keep)
        px=lambda q:(q[0]*sx,height-q[1]*sy)
        if geo.get('outline'):draw.polygon([px(q) for q in geo['outline']],fill=1)
        else:draw.rounded_rectangle((0,0,width-1,height-1),radius=geo.get('corner_radius_mm',0)*min(sx,sy),fill=1)
        # Slightly inset material from drilled boundaries; no coloured caps.
        for q in geo.get('round_holes',[]):
            if q['d']<=0 or (not geo.get('include_all_drill_diameters') and not .4<=q['d']<=3):continue
            x,y=px((q['x'],q['y']));rx=q['d']*sx/2+1;ry=q['d']*sy/2+1
            draw.ellipse((x-rx,y-ry,x+rx,y+ry),fill=0)
        for points in geo.get('routed_slots',[]):
            polygon=[px(q) for q in points];draw.polygon(polygon,fill=0);draw.line(polygon+polygon[:1],fill=0,width=3)
        valid=np.asarray(keep,dtype=bool)
        for kind,colour in [('PADS',(178,186,190)),('SILK',(225,229,216)),('EXPOSED_FR4',(143,130,81))]:
            mask=np.all(rgb==colour,axis=2)&valid
            loops=contours(mask,tolerance=.65)
            outers=[];holes=[]
            for loop in loops:
                record=dict(points=loop,bbox=(min(x for x,y in loop),min(y for x,y in loop),max(x for x,y in loop),max(y for x,y in loop)),holes=[])
                (outers if area(loop)>0 else holes).append(record)
            for hole in holes:
                x0,y0,x1,y1=hole['bbox'];candidate=[]
                for outer in outers:
                    a,b,c,d=outer['bbox']
                    if a<=x0 and b<=y0 and c>=x1 and d>=y1 and inside(hole['points'][0],outer['points']):candidate.append(outer)
                if not candidate:raise ValueError('板层轮廓孔没有外边界：'+side+' '+kind)
                min(candidate,key=lambda q:abs(area(q['points'])))['holes'].append(hole['points'])
            convert=lambda points:[(x/sx,h-y/sy) for x,y in points]
            layers.append(dict(side=side,kind=kind,colour=[v/255 for v in colour],
                polygons=[dict(outer=convert(q['points']),holes=[convert(p) for p in q['holes']]) for q in outers],
                pixel_size_mm=[1/sx,1/sy],source=filename.name))
    return layers
