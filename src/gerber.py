"""Strict local RS-274X and Excellon readers; every coordinate is in mm."""
import ast
import math
import re
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw

OX = OY = 0.0
PXM = 48
BW = BH = 38.0
SIZE = (1824, 1824)


def read_cam_text(path):
    data = Path(path).read_bytes()
    try: return data.decode('utf-8-sig')
    except UnicodeDecodeError: return data.decode('gb18030')


def rotated(pts, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return [(c*x-s*y, s*x+c*y) for x, y in pts]


def _expr(source, variables):
    """Only the four arithmetic operations permitted in aperture macros."""
    source = re.sub(r'\$(\d+)', lambda m: str(variables.get(int(m[1]), 0.)), source)
    source = source.replace('x', '*').replace('X', '*')
    def walk(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return walk(node.operand)*(-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add): return a+b
            if isinstance(node.op, ast.Sub): return a-b
            if isinstance(node.op, ast.Mult): return a*b
            return a/b
        raise ValueError('不支持的光圈宏表达式：'+source)
    try:
        value = walk(ast.parse(source, mode='eval').body)
        if not math.isfinite(value): raise ValueError('无效光圈宏数值')
        return value
    except (SyntaxError, ZeroDivisionError, RecursionError) as exc:
        raise ValueError('无效光圈宏表达式：'+source) from exc


def aperture_shapes(typ, params, macros, units):
    p = [float(v) for v in params.split('X')] if params else []; shapes = []
    if typ in ('C', 'R', 'O', 'P'):
        if not p or p[0] <= 0: raise ValueError('光圈尺寸必须为正数')
        if typ == 'P':
            if len(p) < 2 or p[1] != int(p[1]) or not 3 <= p[1] <= 12: raise ValueError('多边形光圈需要 3–12 个顶点')
            n = int(p[1]); a = math.radians(p[2] if len(p) > 2 else 0)
            shapes.append((1, 'P', [(p[0]*units/2*math.cos(a+2*math.pi*k/n), p[0]*units/2*math.sin(a+2*math.pi*k/n)) for k in range(n)])); holes = p[3:]
        else:
            if typ != 'C' and (len(p) < 2 or p[1] <= 0): raise ValueError('矩形/椭圆光圈缺少高度')
            w = p[0]*units; h = (p[1] if typ != 'C' else p[0])*units
            shapes.append((1, typ, (0, 0, w, h))); holes = p[1:] if typ == 'C' else p[2:]
        if len(holes) > 2: raise ValueError('光圈参数过多：'+typ)
        if holes and holes[0] > 0: shapes.append((0, 'R' if len(holes) == 2 else 'C', (0, 0, holes[0]*units, (holes[1] if len(holes) == 2 else holes[0])*units)))
        return shapes
    if typ not in macros: raise ValueError('不支持/未定义的光圈：'+typ)
    variables = {k+1: v for k, v in enumerate(p)}
    for item in macros[typ].split('*'):
        item = item.strip()
        if not item or re.match(r'^0(?:\s|,|$)', item): continue
        assign = re.fullmatch(r'\$(\d+)\s*=\s*(.+)', item)
        if assign: variables[int(assign[1])] = _expr(assign[2], variables); continue
        v = [_expr(s, variables) for s in item.split(',')]; code = int(v[0]); exp = int(v[1])
        if exp not in (0, 1): raise ValueError('光圈宏曝光值只能是 0 或 1')
        if code == 4:
            n = int(v[2])
            if len(v) != 4+2*(n+1): raise ValueError('光圈宏轮廓顶点数量不符')
            pts = rotated(list(zip(v[3:3+2*(n+1):2], v[4:4+2*(n+1):2])), v[-1])
        elif code in (21, 22):
            if len(v) != 7: raise ValueError('光圈宏矩形参数数量不符')
            _, _, w, h, x, y, a = v
            if code == 22: x += w/2; y += h/2
            pts = rotated([(x-w/2, y-h/2), (x+w/2, y-h/2), (x+w/2, y+h/2), (x-w/2, y+h/2)], a)
        elif code == 20:
            if len(v) != 8: raise ValueError('光圈宏矢量线参数数量不符')
            _, _, width, x1, y1, x2, y2, a = v; length = math.hypot(x2-x1, y2-y1)
            if not length: raise ValueError('光圈宏矢量线长度为零')
            dx, dy = (y2-y1)*width/length/2, -(x2-x1)*width/length/2
            pts = rotated([(x1+dx, y1+dy), (x2+dx, y2+dy), (x2-dx, y2-dy), (x1-dx, y1-dy)], a)
        elif code == 5:
            if len(v) != 7: raise ValueError('光圈宏正多边形参数数量不符')
            _, _, n, x, y, diam, a = v; n = int(n)
            if not 3 <= n <= 12: raise ValueError('光圈宏多边形顶点数量不符')
            pts = rotated([(x+diam/2*math.cos(2*math.pi*k/n), y+diam/2*math.sin(2*math.pi*k/n)) for k in range(n)], a)
        elif code == 1:
            if len(v) not in (5, 6): raise ValueError('光圈宏圆参数数量不符')
            _, _, diam, x, y, *angle = v
            if angle: x, y = rotated([(x, y)], angle[0])[0]
            shapes.append((exp, 'C', (x*units, y*units, diam*units, diam*units))); continue
        else: raise ValueError('暂不支持的光圈宏 primitive '+str(code))
        shapes.append((exp, 'P', [(x*units, y*units) for x, y in pts]))
    if not shapes: raise ValueError('光圈宏没有有效图形：'+typ)
    return shapes


def _fixed(value, fmt, zero='L', units=1.):
    if '.' in value: return float(value)*units
    sign = -1 if value.startswith('-') else 1; digits = value.lstrip('+-')
    if fmt is None: raise ValueError('缺少坐标数字格式；请提供 FILE_FORMAT 或 000.000 格式声明')
    total = sum(fmt)
    if len(digits) > total: raise ValueError('坐标数字长度超过格式声明：'+value)
    digits = digits.ljust(total, '0') if zero == 'T' else digits.rjust(total, '0')
    return sign*int(digits)*units/(10**fmt[1])


def _arc(start, end, i, j, clockwise, single=False):
    x, y = start; xx, yy = end
    candidates = [(i, j)] if not single else list({(si*abs(i), sj*abs(j)) for si in (-1, 1) for sj in (-1, 1)})
    possible = []
    for di, dj in candidates:
        cx, cy = x+di, y+dj; r = math.hypot(di, dj)
        if r == 0: continue
        error = abs(math.hypot(xx-cx, yy-cy)-r); a = math.atan2(y-cy, x-cx); b = math.atan2(yy-cy, xx-cx)
        if clockwise:
            while b >= a-1e-10: b -= 2*math.pi
        else:
            while b <= a+1e-10: b += 2*math.pi
        if single and abs(b-a) > math.pi/2+1e-5: continue
        possible.append((error, cx, cy, r, a, b))
    if not possible: raise ValueError('圆弧缺少有效 I/J 或无法确定单象限圆心')
    error, cx, cy, r, a, b = min(possible)
    if error > max(.003, r*.0005): raise ValueError('圆弧起点/终点半径不一致')
    n = max(4, math.ceil(abs(b-a)*r/.025))
    pts = [(cx+r*math.cos(a+(b-a)*k/n), cy+r*math.sin(a+(b-a)*k/n)) for k in range(n+1)]
    pts[0], pts[-1] = start, end
    return pts


def _identity(cmd):
    if cmd in ('ASAXBY', 'MIA0B0', 'LMN'): return True
    for pattern, values in [(r'OF(?:A([+-]?[\d.]+))?(?:B([+-]?[\d.]+))?', (0., 0.)), (r'SF(?:A([+-]?[\d.]+))?(?:B([+-]?[\d.]+))?', (1., 1.))]:
        m = re.fullmatch(pattern, cmd)
        if m: return all(v is None or float(v) == identity for v, identity in zip(m.groups(), values))
    m = re.fullmatch(r'(?:IR|LR|LS)([+-]?[\d.]+)', cmd)
    return bool(m and float(m[1]) == (1. if cmd.startswith('LS') else 0.))


def parse(path):
    path = Path(path); txt = read_cam_text(path)
    fmt = re.search(r'FS([LT])A[X](\d)(\d)Y(\d)(\d)', txt)
    if not fmt: raise ValueError(path.name+'：需要绝对坐标 FSLAX…Y… 或 FSTAX…Y… 格式声明')
    xf, yf = (int(fmt[2]), int(fmt[3])), (int(fmt[4]), int(fmt[5]))
    if 'MOIN' in txt or re.search(r'(?:^|\*)G70\*', txt): units = 25.4
    elif 'MOMM' in txt or re.search(r'(?:^|\*)G71\*', txt): units = 1.
    else: raise ValueError(path.name+'：缺少 MOIN/MOMM 单位声明')
    macros = {}; aps = {}; commands = []
    for m in re.finditer(r'%([^%]*)%|([^%]+)', txt):
        block = (m[1] if m[1] is not None else m[2]).strip()
        if m[1] is not None:
            if block.startswith('AM'):
                name, _, body = block[2:].partition('*'); macros[name] = body; continue
            for cmd in (c.strip() for c in block.split('*')):
                if not cmd: continue
                if re.fullmatch(r'FS[LT]AX\d\dY\d\d', cmd):
                    if cmd != fmt[0]: raise ValueError(path.name+'：文件内坐标格式发生变化，暂不支持')
                    continue
                if cmd in ('MOIN', 'MOMM'):
                    if (25.4 if cmd == 'MOIN' else 1.) != units: raise ValueError(path.name+'：文件内单位发生变化，暂不支持')
                    continue
                if cmd == 'IPPOS': continue
                if re.fullmatch(r'(?:TF|TA|TO)\..+|TD(?:\..+)?', cmd) or _identity(cmd): continue
                if cmd.startswith(('IN', 'LN')): continue
                ad = re.fullmatch(r'ADD(\d+)([^,*]+)(?:,([^*]+))?', cmd)
                if ad:
                    code = int(ad[1])
                    if code in aps: raise ValueError(path.name+'：光圈重复定义 D'+str(code))
                    aps[code] = aperture_shapes(ad[2], ad[3] or '', macros, units); continue
                if cmd in ('LPD', 'LPC'): commands.append(cmd); continue
                raise ValueError(path.name+'：暂不支持扩展指令 %'+cmd+'%（请先展开重复/变换/负片）')
        else: commands.extend(c.strip() for c in block.split('*') if c.strip())
    x = y = 0.; ap = None; interp = 1; op = 2; region = None; contour = []; dark = True; out = []; single = False
    for cmd in commands:
        if cmd.startswith('G04') or cmd in ('M02', 'M2'): continue
        if cmd in ('LPD', 'LPC'): dark = cmd == 'LPD'; continue
        if cmd == 'G36':
            if region is not None: raise ValueError(path.name+'：嵌套 G36 区域')
            region = []; contour = []; continue
        if cmd == 'G37':
            if region is None: raise ValueError(path.name+'：G37 缺少 G36')
            if contour: region.append(contour)
            if not region or any(len(c) < 3 for c in region): raise ValueError(path.name+'：区域轮廓不完整')
            out.append(('region', dark, region)); region = None; contour = []; continue
        cmd = cmd.removeprefix('G54')
        if re.fullmatch(r'D\d+', cmd) and int(cmd[1:]) >= 10:
            ap = int(cmd[1:])
            if ap not in aps: raise ValueError(path.name+'：未定义光圈 D'+str(ap))
            continue
        if cmd in ('G74', 'G75'): single = cmd == 'G74'; continue
        if cmd == 'G90': continue
        if cmd in ('G70', 'G71'):
            if (25.4 if cmd == 'G70' else 1.) != units: raise ValueError(path.name+'：文件内单位发生变化，暂不支持')
            continue
        if cmd in ('G01', 'G02', 'G03', 'G1', 'G2', 'G3'): interp = int(cmd[1:]); continue
        mm = re.fullmatch(r'(?:G0?([123]))?(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?(?:I([+-]?[\d.]+))?(?:J([+-]?[\d.]+))?(?:D0?([123]))?', cmd)
        if not mm or not any(mm.groups()): raise ValueError(path.name+'：暂不支持指令 '+cmd)
        if mm[1]: interp = int(mm[1])
        xx = _fixed(mm[2], xf, fmt[1], units) if mm[2] is not None else x
        yy = _fixed(mm[3], yf, fmt[1], units) if mm[3] is not None else y
        if mm[6]: op = int(mm[6])
        points = [(x, y), (xx, yy)]
        if op == 1 and interp in (2, 3):
            i = _fixed(mm[4], xf, fmt[1], units) if mm[4] else 0; j = _fixed(mm[5], yf, fmt[1], units) if mm[5] else 0
            points = _arc((x, y), (xx, yy), i, j, interp == 2, single)
        if region is not None:
            if op == 2:
                if contour: region.append(contour)
                contour = [(xx, yy)]
            elif op == 1:
                if not contour: contour = [(x, y)]
                contour.extend(points[1:])
            else: raise ValueError(path.name+'：区域内不允许闪光 D03')
        elif op in (1, 3):
            if ap not in aps: raise ValueError(path.name+'：绘图前没有选择有效光圈')
            out.append(('stroke', dark, points, aps[ap]) if op == 1 else ('flash', dark, xx, yy, aps[ap]))
        x, y = xx, yy
    if region is not None: raise ValueError(path.name+'：G36 区域未结束')
    return out


def px(x, y): return ((x-OX)*PXM, (BH-(y-OY))*PXM)


def shape(draw, typ, geo, x, y, color):
    if typ == 'P': draw.polygon([px(x+a, y+b) for a, b in geo], fill=color); return
    a, b, w, h = geo; cx, cy = px(x+a, y+b)
    bb = (cx-w*PXM/2, cy-h*PXM/2, cx+w*PXM/2, cy+h*PXM/2)
    if typ == 'C': draw.ellipse(bb, fill=color)
    elif typ == 'R': draw.rectangle(bb, fill=color)
    elif typ == 'O': draw.rounded_rectangle(bb, radius=min(w, h)*PXM/2, fill=color)
    else: raise ValueError('不支持的渲染图形：'+typ)


def render(prims):
    im = Image.new('L', SIZE if isinstance(SIZE, tuple) else (SIZE, SIZE), 0); d = ImageDraw.Draw(im)
    for p in prims:
        col = 255 if p[1] else 0
        if p[0] == 'region':
            tile = Image.new('1', im.size)
            for contour in p[2]:
                tmp = Image.new('1', im.size); ImageDraw.Draw(tmp).polygon([px(*q) for q in contour], fill=1); tile = ImageChops.logical_xor(tile, tmp)
            im.paste(col, mask=tile); d = ImageDraw.Draw(im)
        elif p[0] == 'flash':
            if len(p[4]) == 1 and p[4][0][0]: shape(d, p[4][0][1], p[4][0][2], p[2], p[3], col)
            else:
                # Clear macro primitives carve the aperture, not the existing layer.
                tile = Image.new('L', im.size); td = ImageDraw.Draw(tile)
                for exp, typ, geo in p[4]: shape(td, typ, geo, p[2], p[3], 255 if exp else 0)
                im.paste(col, mask=tile); d = ImageDraw.Draw(im)
        elif p[0] == 'stroke':
            points, aps = p[2], p[3]
            if len(aps) != 1 or not aps[0][0]: raise ValueError('暂不支持带孔/组合光圈的线段；请在源软件展开')
            _, typ, geo = aps[0]
            if typ == 'C':
                w = geo[2]*PXM; r = w/2; pp = [px(*q) for q in points]; d.line(pp, fill=col, width=max(1, round(w)))
                for a, b in pp: d.ellipse((a-r, b-r, a+r, b+r), fill=col)
            elif typ == 'R':
                from scipy.spatial import ConvexHull
                _, _, w, h = geo
                for a, b in zip(points, points[1:]):
                    verts = [(x+dx*w/2, y+dy*h/2) for x, y in (a, b) for dx, dy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]]
                    hull = ConvexHull(verts); d.polygon([px(*verts[i]) for i in hull.vertices], fill=col)
            else: raise ValueError('暂不支持此光圈的线段扫掠：'+typ)
        else: raise ValueError('未知图元 '+p[0])
    return im


def _capsule(points, diameter):
    """Offset an open cutter centreline, including rounded end caps."""
    clean=[points[0]]
    for p in points[1:]:
        if math.dist(p,clean[-1])>1e-8:clean.append(p)
    if len(clean)<2:raise ValueError('铣槽路径长度为零')
    if math.dist(clean[0],clean[-1])<1e-7:raise ValueError('暂不支持闭合的 Excellon 铣槽，请改用板框 Gerber 内轮廓')
    r=diameter/2; tangents=[]
    for a,b in zip(clean,clean[1:]):
        length=math.dist(a,b);tangents.append(((b[0]-a[0])/length,(b[1]-a[1])/length))
    offsets=[]
    for k in range(len(clean)):
        if k==0:tx,ty=tangents[0];offsets.append((-ty*r,tx*r));continue
        if k==len(clean)-1:tx,ty=tangents[-1];offsets.append((-ty*r,tx*r));continue
        a,b=tangents[k-1],tangents[k];nx,ny=-a[1]-b[1],a[0]+b[0];length=math.hypot(nx,ny)
        if length<1e-6:raise ValueError('铣槽路径折返，暂不支持')
        nx/=length;ny/=length;denom=nx*(-b[1])+ny*b[0]
        if denom<.25:raise ValueError('铣槽路径夹角过小，暂不支持')
        offsets.append((nx*r/denom,ny*r/denom))
    left=[[x+dx,y+dy] for (x,y),(dx,dy) in zip(clean,offsets)]
    right=[[x-dx,y-dy] for (x,y),(dx,dy) in zip(clean,offsets)]
    n=max(8,math.ceil(math.pi*r/.025));a=math.atan2(tangents[-1][1],tangents[-1][0]);b=math.atan2(tangents[0][1],tangents[0][0])
    end=[[clean[-1][0]+r*math.cos(a+math.pi/2-k*math.pi/n),clean[-1][1]+r*math.sin(a+math.pi/2-k*math.pi/n)] for k in range(1,n)]
    start=[[clean[0][0]+r*math.cos(b-math.pi/2-k*math.pi/n),clean[0][1]+r*math.sin(b-math.pi/2-k*math.pi/n)] for k in range(1,n)]
    return left+end+list(reversed(right))+start


def parse_drill(path, origin=(0., 0.), format_override=None):
    """Excellon tools, fixed/decimal coordinates, repeats, G85 and routes.

    A missing fixed format is rejected. Recognized Allegro Holesize/M00 grouped
    files use 0.00001 inch and validate the quantity declared for every tool.
    """
    path = Path(path); txt = read_cam_text(path)
    units = None; fmt = tuple(format_override) if format_override else None; zero = 'L'
    if fmt is not None and (len(fmt)!=2 or any(not isinstance(v,int) or not 0<=v<=8 for v in fmt) or not sum(fmt)):
        raise ValueError('drill_format 需为 [整数位数, 小数位数]，例如 [2, 4]')
    fm = re.search(r'(?:FILE_FORMAT|FORMAT)\s*[=:]\s*(\d+)\s*[:.]\s*(\d+)', txt, re.I)
    if fm and fmt is None: fmt = (int(fm[1]), int(fm[2]))
    legacy = list(re.finditer(r'Holesize\s+(\d+)\.\s*=\s*([\d.]+).*?\b(NON_PLATED|PLATED)\s+(MILS|MM)\s+Quantity\s*=\s*(\d+)', txt, re.I))
    grouped = bool(legacy) and not re.search(r'^T\d+', txt, re.M)
    tools = {}; quantities = {}; counts = {}; warnings = []
    legacy_route = (path.suffix.lower() == '.rou' and
                    all(re.search(r'^'+key+r'\s*:', txt, re.M) for key in ('EXTENTS','LEADER','HEADER','CODE','FILE')) and
                    re.search(r'^INCH,LZ\s*$', txt, re.M) and re.search(r'^G40\s*$', txt, re.M))
    if legacy_route and fmt is None:
        fmt = (3, 5)
        warnings.append('识别 Allegro 旧式 ROU（EXTENTS/LEADER/HEADER/CODE/FILE + INCH,LZ）；坐标采用 3:5 inch，G40 为无刀补中心线')
    plating = not bool(re.search(r'(?:NON[_ -]?PLATED|NPTH)', path.name, re.I))
    if grouped:
        tools = {int(m[1]): (float(m[2])*(.0254 if m[4].upper() == 'MILS' else 1), m[3].upper() == 'PLATED') for m in legacy}; quantities = {int(m[1]): int(m[5]) for m in legacy}
        if sorted(tools) != list(range(1, len(tools)+1)): raise ValueError(path.name+'：Holesize 刀具编号不连续')
        units = 25.4; fmt = fmt or (3, 5); warnings.append('识别 Allegro Holesize/M00 分组钻孔；坐标单位采用 0.00001 inch，并核对每组 Quantity')
    holes = []; slots = []; x = y = 0.; tool = 1 if grouped else None; mode = 'drill'; down = False; stopped = False;route_buffer=[]
    def coord(source, axis): return _fixed(source, fmt, zero, units) if source is not None else axis
    def current():
        if tool not in tools: raise ValueError(path.name+'：钻孔/铣槽前没有选择已定义刀具')
        if units is None: raise ValueError(path.name+'：缺少 INCH/METRIC 单位声明')
        return tools[tool]
    def hit(xx, yy):
        diameter, plated = current(); counts[tool] = counts.get(tool, 0)+1; holes.append(dict(x=xx-origin[0], y=yy-origin[1], d=diameter, plated=plated, tool=tool))
    def route(points):
        diameter, plated = current(); local = [(xx-origin[0], yy-origin[1]) for xx, yy in points]
        slots.append(_capsule(local, diameter))
    def flush_route():
        if len(route_buffer)>1:route(route_buffer)
        route_buffer.clear()
    in_header = True
    for number, raw in enumerate(txt.splitlines(), 1):
        if re.search(r';\s*TYPE\s*=\s*NON_PLATED', raw, re.I): plating = False
        if re.search(r';\s*TYPE\s*=\s*PLATED', raw, re.I): plating = True
        cmd = raw.split(';', 1)[0].strip().upper().replace(' ', '')
        if not cmd: continue
        if stopped: raise ValueError(path.name+'：M30 后仍有指令，第 '+str(number)+' 行')
        if in_header and re.match(r'^(?:EXTENTS|LEADER|HEADER|CODE|FILE)\s*:', raw.strip(), re.I): continue
        if cmd in ('%', 'M95'): in_header = False
        if cmd in ('M48', '%', 'M95', 'G90', 'M01'): continue
        if cmd == 'G40' or re.fullmatch(r'F\d+(?:\.\d+)?', cmd): continue
        if cmd in ('M30', 'M2', 'M02'): flush_route();stopped = True; continue
        if cmd == 'M00':
            if grouped: tool += 1
            continue
        if cmd == 'M71': units = 1.; continue
        if cmd == 'M72': units = 25.4; continue
        unit = re.fullmatch(r'(INCH|METRIC)(?:,(LZ|TZ))?(?:,([0#]+)\.([0#]+))?', cmd)
        if unit:
            units = 25.4 if unit[1] == 'INCH' else 1.
            # Excellon LZ/TZ name retained zeros (the inverse of Gerber FS).
            zero = 'T' if unit[2] == 'LZ' else 'L' if unit[2] == 'TZ' else zero
            if unit[3] and fmt is None: fmt = (len(unit[3]), len(unit[4]))
            continue
        if re.fullmatch(r'(?:FMAT,[12]|VER,[12]|ICI,OFF|ATC|DETECT,ON)', cmd): continue
        if cmd in ('G91', 'ICI,ON'): raise ValueError(path.name+'：暂不支持增量钻孔坐标')
        td = re.fullmatch(r'T(\d+)((?:[CFS][\d.]+)*)', cmd)
        if td:
            flush_route();code = int(td[1]);fields=re.findall(r'([CFS])([\d.]+)',td[2])
            if len({key for key,value in fields})!=len(fields):raise ValueError(path.name+'：刀具参数重复')
            fields=dict(fields)
            if 'C' in fields:
                if units is None: raise ValueError(path.name+'：刀具尺寸前缺少单位声明')
                diameter = float(fields['C'])*units
                if diameter <= 0: raise ValueError(path.name+'：刀具直径必须为正数')
                if code in tools: raise ValueError(path.name+'：刀具重复定义 T'+str(code))
                tools[code] = (diameter, plating)
            else: tool = code if code else None
            continue
        if cmd in ('M15', 'M16', 'M17'):
            if cmd!='M15':flush_route()
            down = cmd == 'M15'; continue
        if cmd == 'G05': flush_route();mode = 'drill'; down = False; continue
        if cmd in ('G00', 'G0'): flush_route();mode = 'rapid'; continue
        if cmd in ('G01', 'G1', 'G02', 'G2', 'G03', 'G3'): mode = {'1': 'line', '2': 'cw', '3': 'ccw'}[cmd[-1]]; continue
        repeat = re.fullmatch(r'R(\d+)(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?', cmd)
        if repeat:
            if mode != 'drill' or not holes: raise ValueError(path.name+'：R 重复指令必须位于钻孔命中之后')
            dx = coord(repeat[2], 0); dy = coord(repeat[3], 0)
            for _ in range(int(repeat[1])): x += dx; y += dy; hit(x, y)
            continue
        if 'G85' in cmd:
            flush_route()
            before, after = cmd.split('G85', 1)
            def pair(part):
                m = re.fullmatch(r'(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?', part)
                if not m: raise ValueError(path.name+'：无效 G85 铣槽坐标')
                return m
            a, b = pair(before), pair(after)
            if not any(b.groups()): raise ValueError(path.name+'：G85 缺少槽终点')
            sx, sy = coord(a[1], x), coord(a[2], y); ex, ey = coord(b[1], sx), coord(b[2], sy); route([(sx, sy), (ex, ey)]); x, y = ex, ey; continue
        m = re.fullmatch(r'(?:G0?([0123]))?(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?(?:I([+-]?[\d.]+))?(?:J([+-]?[\d.]+))?', cmd)
        if not m or not any(m.groups()): raise ValueError(path.name+'：暂不支持钻孔指令，第 '+str(number)+' 行：'+cmd)
        if units is None: raise ValueError(path.name+'：缺少 INCH/METRIC 单位声明')
        if m[1] is not None:
            mode = {'0': 'rapid', '1': 'line', '2': 'cw', '3': 'ccw'}[m[1]]
            if mode=='rapid':flush_route()
        xx, yy = coord(m[2], x), coord(m[3], y)
        if mode == 'drill': hit(xx, yy)
        elif mode != 'rapid' and down:
            pts = [(x, y), (xx, yy)]
            if mode in ('cw', 'ccw'): pts = _arc((x, y), (xx, yy), coord(m[4], 0), coord(m[5], 0), mode == 'cw')
            if route_buffer and math.dist(route_buffer[-1],pts[0])>1e-6:flush_route()
            route_buffer.extend(pts if not route_buffer else pts[1:])
        x, y = xx, yy
    flush_route()
    if not tools: raise ValueError(path.name+'：未找到刀具直径定义')
    if not holes and not slots: raise ValueError(path.name+'：没有找到钻孔/铣槽命中')
    if grouped and any(counts.get(k, 0) != v for k, v in quantities.items()): raise ValueError(path.name+'：Holesize Quantity 与实际命中数不符：'+str(counts))
    return holes, slots, dict(file=path.name, units='mm', coordinate_format=list(fmt) if fmt else 'decimal', tools={str(k): dict(d=d, plated=p) for k, (d, p) in tools.items()}, hits=len(holes), slots=len(slots), warnings=warnings)


def closed_contours(primitives):
    """Join drawing paths; leave open panel markings out of board candidates."""
    lines = []; loops = []
    for p in primitives:
        if not p[1]: continue
        if p[0] == 'stroke': lines.append(list(p[2]))
        elif p[0] == 'region': loops.extend(p[2])
    while lines:
        chain = lines.pop(0)
        while math.dist(chain[0], chain[-1]) > .02:
            found = None
            for k, line in enumerate(lines):
                if math.dist(chain[-1], line[0]) <= .02: found = k, line; break
                if math.dist(chain[-1], line[-1]) <= .02: found = k, list(reversed(line)); break
            if found is None: break
            k, line = found; lines.pop(k); chain.extend(line[1:])
        if len(chain) >= 4 and math.dist(chain[0], chain[-1]) <= .02: loops.append(chain)
    return loops


def polygon_area(points):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))/2)


def infer_board(primitives, points):
    """Choose the smallest closed outline enclosing the source XY positions.

    In a panel, the XY table identifies the populated cell. Never derive a
    board from the overall artwork bounds (which also contain tooling/text).
    """
    def bounds(loop):
        xs, ys = zip(*loop)
        return min(xs), min(ys), max(xs), max(ys)

    def near_inside(point, loop, tolerance=.5):
        x, y = point; inside = False
        for (ax, ay), (bx, by) in zip(loop, loop[1:]+loop[:1]):
            dx, dy = bx-ax, by-ay; length = dx*dx+dy*dy
            t = max(0., min(1., ((x-ax)*dx+(y-ay)*dy)/length)) if length else 0.
            if math.hypot(x-ax-t*dx, y-ay-t*dy) <= tolerance: return True
            if (ay > y) != (by > y) and x < ax+(y-ay)*(bx-ax)/(by-ay): inside = not inside
        return inside

    candidates = []
    for loop in closed_contours(primitives):
        box = bounds(loop); x0, y0, x1, y1 = box; area = polygon_area(loop)
        if min(x1-x0, y1-y0) < 1 or max(x1-x0, y1-y0) > 500 or area < 1: continue
        # Allegro sometimes draws the same outline twice with sub-micron offsets.
        if any(max(abs(a-b) for a, b in zip(box, item['bounds'])) < .002 and abs(area-item['area']) < .01 for item in candidates): continue
        candidates.append(dict(bounds=box, area=area, loop=loop))
    compatible = [q for q in candidates if points and all(
        q['bounds'][0]-.5 <= x <= q['bounds'][2]+.5 and q['bounds'][1]-.5 <= y <= q['bounds'][3]+.5
        and near_inside((x, y), q['loop']) for x, y in points)]
    if not compatible:
        raise ValueError('未找到包含所选 XY 器件坐标的单板闭合轮廓；请确认坐标版本/单位，或关闭“自动识别单板与原点”后填写板宽高、Gerber 原点和 XY 平移。工具不会把拼板外包围框当成单板。')
    compatible.sort(key=lambda q: q['area'])
    chosen = compatible[0]
    if len(compatible) > 1 and compatible[1]['area'] < chosen['area']*1.02:
        raise ValueError('存在多个相近的单板轮廓，无法唯一确定；请在图层配置中指定单板外框，或关闭自动识别并填写板框与原点。')
    x0, y0, x1, y1 = chosen['bounds']
    return dict(origin=[x0, y0], size=[x1-x0, y1-y0], candidates=len(candidates),
                matched_positions=len(points), coordinate_basis='XY 与 Gerber 采用同一源坐标系')


def extract_outline(primitives, origin, width, height, tolerance=.1):
    """Return a closed board polygon matching the specified size and its cutouts."""
    loops = closed_contours(primitives)
    def area(points): return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))/2)
    loops = [[(x-origin[0], y-origin[1]) for x, y in loop] for loop in loops]; compatible = []
    for loop in loops:
        xs, ys = zip(*loop)
        if all(abs(a-b) <= tolerance for a, b in zip((min(xs), min(ys), max(xs), max(ys)), (0, 0, width, height))): compatible.append(loop)
    if not compatible: return [], []
    outer = max(compatible, key=area)
    if area(outer) <= 0: return [], []
    inner = [q for q in loops if area(q) > .0001 and area(q) < area(outer)-.01 and all(.02 < x < width-.02 and .02 < y < height-.02 for x, y in q)]
    return [[x, y] for x, y in outer], [[[x, y] for x, y in q] for q in inner]
