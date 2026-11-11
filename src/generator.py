"""Local BOM/XY/Gerber importer with reusable package aliases."""
import csv,io,json,re,shutil,zipfile,math,html as html_lib
from collections import Counter
from pathlib import Path, PurePosixPath
from PIL import Image,ImageChops
import gerber
from input_support import zip_member_name

def fit_connector_footprint(placement, model, pads):
    """Fit a declared SMT land pattern, including hold-downs, in board XY.

    XY exporters may reference the hold-down row rather than signal-row centre.
    Require every signal and both mechanical lands; never infer from board edges.
    """
    footprint = model.get('placement_footprint')
    if not footprint:
        return None
    signals = footprint['signals']
    mounts = footprint['mounts']
    expected = [(point, footprint['signal_pad_mm']) for point in signals]
    expected += [(point, footprint['mount_pad_mm']) for point in mounts]
    max_offset = footprint.get('max_origin_offset_mm', 4.)
    tolerance = .15
    reach = max(math.hypot(*point) for point, _ in expected) + max_offset + tolerance
    local = [pad for pad in pads if math.hypot(pad[0]-placement['x'], pad[1]-placement['y']) <= reach]
    best = None
    for delta in (0, 90, 180, 270):
        angle = math.radians(placement['rot'] + delta)
        c, s = math.cos(angle), math.sin(angle)
        def board_offset(point):
            x, z = point
            if placement['side'] == 'top':
                return c*x+s*z, s*x-c*z
            return c*x-s*z, s*x+c*z
        def board_size(size):
            u, v = size
            return abs(c)*u+abs(s)*v, abs(s)*u+abs(c)*v
        targets = [(board_offset(point), board_size(size)) for point, size in expected]
        anchor, anchor_size = targets[0]
        for pad in local:
            if max(abs(pad[2+i]-anchor_size[i]) for i in (0, 1)) > .08:
                continue
            cx, cy = pad[0]-anchor[0], pad[1]-anchor[1]
            dx, dy = cx-placement['x'], cy-placement['y']
            if math.hypot(dx, dy) > max_offset:
                continue
            used, distances = set(), []
            for (u, v), size in targets:
                candidates = [(math.hypot(q[0]-cx-u, q[1]-cy-v), i)
                              for i, q in enumerate(local) if i not in used
                              and max(abs(q[2+j]-size[j]) for j in (0, 1)) <= .08]
                if not candidates:
                    break
                distance, index = min(candidates)
                if distance > tolerance:
                    break
                used.add(index)
                distances.append(distance)
            if len(distances) != len(expected):
                continue
            score = sum(distances)/len(distances) + math.hypot(dx, dy)*1e-6 + delta*1e-9
            if best is None or score < best[0]:
                best = score, dict(dx=dx, dy=dy, dyaw=delta,
                    reason='Gerber signal row and both hold-down lands matched',
                    matched_lands=len(expected), max_land_error_mm=max(distances))
    return best[1] if best else None

def xlsx_content_row_limit(data,sheet_path):
    """Ignore trailing Excel row formatting without truncating sparse data."""
    last=0
    with zipfile.ZipFile(io.BytesIO(data)) as archive,archive.open(sheet_path.lstrip('/')) as stream:
        xml=stream.read()
    # Scan cell tokens directly: XML parsers still visit every formatted empty row.
    # Values remain decoded by openpyxl; this pass only bounds its trailing rows.
    prefix=rb'(?:[A-Za-z_][\w.-]*:)?'
    cells=rb'<'+prefix+rb'c\b([^>]*?)(?<!/)>(.*?)</'+prefix+rb'c\s*>'
    content=rb'<'+prefix+rb'(?:v|is|f)(?=[\s/>])'
    for cell in re.finditer(cells,xml,re.S):
        if re.search(content,cell[2]):
            match=re.search(rb'\br\s*=\s*[\x22\x27][A-Za-z]+(\d+)[\x22\x27]',cell[1])
            if match:last=max(last,int(match[1]))
    return last

def workbook_table(tables,active=0):
    """Merge split TOP/BOTTOM coordinate sheets by semantic column names."""
    coordinate_tables=[]
    for name,rows in tables:
        try:start,columns=header(rows,['ref','x','y'])
        except ValueError:continue
        coordinate_tables.append((name,rows,start,columns))
    if not coordinate_tables:
        rows=tables[active][1] if tables else []
        try:header(rows,['ref']);return rows
        except ValueError:
            for _,candidate in tables:
                try:header(candidate,['ref']);return candidate
                except ValueError:pass
        return rows
    sides={'top':'top','front':'top','顶层':'top','正面':'top','bottom':'bottom','bot':'bottom','back':'bottom','底层':'bottom','反面':'bottom'}
    if len(coordinate_tables)==1:
        name,rows,_,columns=coordinate_tables[0]
        if 'side' in columns or norm(name) not in sides:return rows
    keys=[key for key in HEADERS if key=='side' or any(key in table[3] for table in coordinate_tables)]
    merged=[[HEADERS[key][0] for key in keys]]
    for name,rows,start,columns in coordinate_tables:
        for row in rows[start+1:]:
            if not any(row):continue
            values=[row[columns[key]] if key in columns and columns[key]<len(row) else '' for key in keys]
            side=keys.index('side')
            if not values[side] and norm(name) in sides:values[side]=sides[norm(name)]
            merged.append(values)
    return merged

def read_table(path):
    path=Path(path)
    if path.suffix.lower() in ('.xlsx','.xls'):
        data=path.read_bytes()
        if path.suffix.lower()=='.xls' and not data.startswith(b'PK'):
            try:
                import xlrd
                wb=xlrd.open_workbook(file_contents=data,on_demand=True,logfile=io.StringIO())
                try:
                    tables=[]
                    for sheet in wb.sheets():
                        rows=[[str('' if cell is None else int(cell) if isinstance(cell,float) and cell.is_integer() else cell).strip() for cell in sheet.row_values(i)] for i in range(sheet.nrows)]
                        tables.append((sheet.name,rows))
                    return workbook_table(tables)
                finally:wb.release_resources()
            except ImportError as error:raise ValueError('当前工具缺少 XLS 读取模块，请更新工具，或用 Excel 另存为 XLSX / UTF-8 CSV') from error
            except Exception as error:raise ValueError('无法读取 XLS：'+path.name+'；请用 Excel 另存为 XLSX / UTF-8 CSV。'+str(error)) from error
        import openpyxl
        wb=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        try:
            tables=[]
            for sheet in wb.worksheets:
                limit=sheet.max_row
                # Allegro production workbooks can contain a million formatted empty rows.
                if limit and limit>100000:limit=xlsx_content_row_limit(data,sheet._worksheet_path)
                rows=[] if limit==0 else sheet.iter_rows(max_row=limit,values_only=True)
                tables.append((sheet.title,[[str('' if x is None else x).strip() for x in row] for row in rows]))
            return workbook_table(tables,wb.worksheets.index(wb.active))
        finally:wb.close()
    if path.suffix.lower() not in ('.csv','.tsv','.txt'):
        raise ValueError('表格仅支持 XLSX、XLS、CSV、TSV 或文本表格')
    data=path.read_bytes()
    encodings=['utf-16','utf-8-sig','gb18030'] if data.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','gb18030']
    for enc in encodings:
        try:text=data.decode(enc);break
        except UnicodeError:continue
    else:raise ValueError('无法识别表格编码，请保存成 UTF-8 CSV 或 XLSX')
    # Allegro's original placement report has no column header. Its optional
    # mirror marker occupies a blank field on TOP, so splitting on whitespace
    # and assigning fixed column indexes would misclassify the package/side.
    units=re.search(r'^\s*UUNITS\s*=\s*(\w+)\s*$',text,re.M|re.I)
    if units:
        factors={'MILLIMETERS':1,'MILLIMETRES':1,'MM':1,'MILS':.0254,'INCHES':25.4}
        factor=factors.get(units[1].upper())
        if factor is None:raise ValueError('不支持的 Allegro 坐标单位：'+units[1])
        result=[['Ref','X(mm)','Y(mm)','Rotation','Side','Package']]
        number=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
        pattern=re.compile(r'^\s*(\S+)\s+('+number+r')\s+('+number+r')\s+('+number+r')\s+(?:(m)\s+)?(\S+)\s*$',re.I)
        for line_no,line in enumerate(text.splitlines(),1):
            if not line.strip() or re.match(r'\s*UUNITS\s*=',line,re.I):continue
            match=pattern.fullmatch(line)
            if not match:raise ValueError('Allegro 坐标文本第 '+str(line_no)+' 行无法识别：'+line[:100])
            ref,x,y,rotation,mirror,package=match.groups()
            result.append([ref,str(float(x)*factor),str(float(y)*factor),rotation,'Bottom' if mirror else 'Top',package])
        if len(result)==1:raise ValueError('Allegro 坐标文本没有器件记录')
        return result
    try:dialect=csv.Sniffer().sniff(text[:8000],delimiters=',\t;')
    except csv.Error:dialect=csv.excel
    return [[x.strip() for x in row] for row in csv.reader(io.StringIO(text),dialect)]

def norm(x):return re.sub(r'[\s_()（）\[\].-]','',str(x).lower())
HEADERS={
 'ref':['ref','refdes','reference','designator','designators','references','位号','位置','元件位号'],
 'x':['x','posx','midx','centerx','x坐标','横坐标','symx','位置x'],
 'y':['y','posy','midy','centery','y坐标','纵坐标','symy','位置y'],
 'rot':['rot','rotation','rotate','angle','旋转','角度','symrotate'],
 'side':['side','layer','面','层','装配层','放置层','symmirror'],
 'pkg':['package','comppackage','footprint','封装','封装形式','pattern','symname'],
 'val':['value','compvalue','comment','值','型号','参数','物料型号','物料规格','物料名称','devicetype','compdevicetype'],
 'desc':['description','描述','物料描述','规格型号','物料规格描述'],
 'name':['name','partname','物料名称','名称','品名'],
 'code':['code','partnumber','partno','mpn','物料编码','物料代码','料号'],
 'populate':['populate','fitted','实装','装配','是否贴装'],
 'qty':['quantity','qty','数量','用量'],
 'seq':['序号','项次','itemno','itemnumber'],
}
def headernorm(x):
    x=re.sub(r'[（(]\s*(?:mm|mil|mils|deg|°)\s*[）)]','',str(x),flags=re.I)
    return norm(x)
def header(rows,required):
    for i,row in enumerate(rows[:35]):
        normalized=[headernorm(x) for x in row];m={}
        for key,names in HEADERS.items():
            for name in names:
                if norm(name) in normalized:m[key]=normalized.index(norm(name));break
        if all(x in m for x in required):return i,m
    raise ValueError('找不到表头，需要列：'+', '.join(required)+'；支持中文表头或模板 CSV')
def refs(s):
    s=str(s).strip().upper()
    if not s:return []
    # Expand common BOM ranges; U1-1 remains an individual accessory reference.
    def expand(m):
        prefix,a,prefix2,b=m.groups();prefix2=prefix2 or prefix
        if prefix2!=prefix or int(b)<int(a) or int(b)-int(a)>10000:raise ValueError('无效位号范围：'+m[0])
        return ','.join(prefix+str(v) for v in range(int(a),int(b)+1))
    s=re.sub(r'([A-Z]+)(\d+)\s*(?:~|～|至|到|-(?=[A-Z]))\s*([A-Z]+)?(\d+)',expand,s)
    tokens=[p for p in re.split(r'[,，;；\s/、]+',s) if p]
    bad=[p for p in tokens if not re.fullmatch(r'(?:[A-Z]+\d+(?:-\d+)?|[+-]?\d+(?:\.\d+)?[A-Z]+\d*)',p)]
    if bad:raise ValueError('无法识别位号：'+','.join(bad)+'；请用逗号分隔，范围可写 R1-R3')
    return tokens

def bom_row_location(path,line,columns,row):
    """Keep spreadsheet row numbers distinct from the BOM's item number."""
    excel=Path(path).suffix.lower() in ('.xlsx','.xls')
    label=('Excel 第 ' if excel else '第 ')+str(line)+' 行'
    details=[]
    index=columns.get('seq')
    if index is not None and index<len(row) and str(row[index]).strip():
        value=str(row[index]).strip()
        if re.fullmatch(r'\d+\.0',value):value=value[:-2]
        details.append('BOM 序号 '+value)
    if excel:
        column=columns['ref']+1;letters=''
        while column:
            column,remainder=divmod(column-1,26);letters=chr(65+remainder)+letters
        details.append('位号单元格 '+letters+str(line))
    if details:label+='（'+'；'.join(details)+'）'
    return label

def bom_duplicate_error(rr,previous,line,quantity='',location=None):
    """Report only duplicated references, with their original table rows."""
    counts=Counter(rr)
    within=sorted(ref for ref,count in counts.items() if count>1)
    across=sorted(set(rr).intersection(previous))
    duplicates=sorted(set(within+across))
    if not duplicates:return ''
    details=[]
    if within:details.append('本行内重复：'+','.join(within))
    if across:details.append('已出现在 '+', '.join(ref+'（'+(previous[ref] if isinstance(previous[ref],str) else '第 '+str(previous[ref])+' 行')+'）' for ref in across))
    if quantity:details.append('用量 '+quantity)
    details.append('位号条目 '+str(len(rr))+'，不同位号 '+str(len(counts)))
    return 'BOM '+(location or '第 '+str(line)+' 行')+'位号重复：'+','.join(duplicates)+'；'+'；'.join(details)+'。请核对原表位号和用量，未自动删减。'
def num(s):
    m=re.fullmatch(r'\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*(?:mm|mils?|deg|°)?\s*',str(s),re.I)
    if not m:raise ValueError('无效数值：'+str(s))
    value=float(m[1])
    if not math.isfinite(value):raise ValueError('数值必须是有限数')
    return value
def not_fitted(populate,value):
    return norm(populate) in ('0','no','false','否','nc','dnp','dni','不贴','不装') or norm(value) in ('nc','dnp','dni','nf','notfitted','不贴','不装','空贴')
def side_value(value):
    side=norm(value)
    if side in ('','top','toplayer','topside','front','fcu','正','正面','顶','顶层','上层','no','false'):return 'top'
    if side in ('bottom','bottomlayer','bottomside','bot','back','bcu','底','底层','底面','反','反面','下层','yes','true'):return 'bottom'
    raise ValueError('无法识别装配层：'+str(value)+'；请用 Top / Bottom，不能推测数字层号')
def aliases(assets,user):
    if not isinstance(user,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in user.items()):
        raise ValueError('封装映射必须是 {"封装名": "模型ID"} 的 JSON 对象')
    source=json.loads((assets/'aliases.json').read_text(encoding='utf8'));result={}
    for key,value in source.items():
        key=norm(key)
        if key in result and result[key]!=value:raise ValueError('内置封装别名归一化后冲突：'+key)
        result[key]=value
    result.update({norm(k):v for k,v in user.items()})
    return result

def copy_native_model_parts(assets,out,models,user_root=None):
    library_root=(Path(assets)/'library').resolve();project_root=Path(out).resolve();copied=set()
    for model in models.values():
        for part in model.get('native_step_parts',[]):
            name=part.get('path')
            if not isinstance(name,str) or not name:raise ValueError('原生 STEP 模型缺少相对路径')
            relative=PurePosixPath(name.replace('\\','/'))
            if relative.is_absolute() or '..' in relative.parts or ':' in name or relative.suffix.lower() not in ('.step','.stp'):
                raise ValueError('原生 STEP 模型路径无效：'+name)
            source_root=Path(user_root).resolve() if user_root and str(relative).startswith('models/user/') else library_root
            source=(source_root/relative).resolve();target=(project_root/relative).resolve()
            if not source.is_relative_to(source_root) or not target.is_relative_to(project_root):
                raise ValueError('原生 STEP 模型路径超出工程：'+name)
            if target in copied:continue
            if not source.is_file():raise ValueError('原生 STEP 模型文件不存在：'+name)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);copied.add(target)
def build(assets,out,files,config,progress):
    config=dict(config)
    assets=Path(assets);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    inputs=out/'输入资料';inputs.mkdir(exist_ok=True)
    warnings=[];saved={}
    def save_input(name,data):
        base=Path(name.replace('\\','/')).name
        if not base or ':' in base or base in ('.','..'):raise ValueError('无效输入文件名：'+name)
        key=base.lower()
        if key in saved and saved[key]!=data:raise ValueError('输入中有不同内容的同名文件：'+base+'；请改名以区分图层')
        saved[key]=data;target=inputs/base;target.write_bytes(data);return target
    for name,data in files.items():
        target=save_input(name,data)
        if target.suffix.lower()=='.zip':
            with zipfile.ZipFile(target) as z:
                if sum(q.file_size for q in z.infolist())>200_000_000:raise ValueError('压缩包解压后超过 200 MB')
                for q in z.infolist():
                    if q.is_dir():continue
                    pp=Path(zip_member_name(q))
                    if pp.is_absolute() or '..' in pp.parts or ':' in q.filename:raise ValueError('ZIP 包含不安全的路径')
                    if pp.suffix.lower() in ['.art','.gbr','.ger','.gtl','.gbl','.gts','.gbs','.gto','.gbo','.gtp','.gbp','.gko','.gm1','.gml','.drl','.rou','.xln','.exc','.txt','.csv','.tsv','.xlsx','.xls']:
                        save_input(pp.name,z.read(q))
    xy=inputs/Path(config['xy']).name
    progress('读取坐标和 BOM')
    rows=read_table(xy);start,col=header(rows,['ref','x','y'])
    get=lambda row,k,default='':row[col[k]] if k in col and col[k]<len(row) else default
    if config.get('unit','mm') not in ('mm','mil'):raise ValueError('坐标单位只能是 mm 或 mil')
    scale=.0254 if config.get('unit')=='mil' else 1
    # Explicit column units take precedence over the fallback UI unit.
    if all(re.search(r'[（(]\s*mm\s*[）)]',rows[start][col[k]],re.I) for k in ('x','y')):scale=1
    placements={};nc=[];seen_xy=set();xy_not_in_bom=[];xy_errors={}
    excluded_rows={role:set(config.get('exclude_'+role+'_rows',[])) for role in ('xy','bom')}
    if any(not isinstance(number,int) or isinstance(number,bool) or number<1 for values in excluded_rows.values() for number in values):
        raise ValueError('排除行号必须是正整数')
    excluded_records=[]
    if 'side' not in col:warnings.append('坐标表缺少面列，全部按 Top 导入')
    for line,row in enumerate(rows[start+1:],start+2):
        rr=refs(get(row,'ref'))
        if not rr:continue
        if line in excluded_rows['xy']:
            excluded_records.append(dict(table='xy',row=line,refs=rr));continue
        if len(rr)!=1:raise ValueError('坐标表每行只能有一个位号，第 '+str(line)+' 行')
        ref=rr[0]
        if ref in seen_xy:raise ValueError('坐标表位号重复：'+ref)
        seen_xy.add(ref)
        side=side_value(get(row,'side','top'))
        value=get(row,'val');pkg=get(row,'pkg')
        if not_fitted(get(row,'populate'),value):
            nc.append(ref);continue
        try:
            placements[ref]=dict(ref=ref,x=num(get(row,'x'))*scale+num(config.get('xy_dx',0)),y=num(get(row,'y'))*scale+num(config.get('xy_dy',0)),rot=num(get(row,'rot') or '0'),side=side,pkg=pkg,val=value,population='populated',w=1,d=.5,h=.5)
        except ValueError as exc:xy_errors[ref]='坐标表第 '+str(line)+' 行 '+ref+'：'+str(exc)
    bom=[];missing=[];seen_bom={};bom_populated=set();accessory_candidates={}
    if config.get('bom'):
        rows=read_table(inputs/Path(config['bom']).name);start,bc=header(rows,['ref'])
        for line,row in enumerate(rows[start+1:],start+2):
            if bc['ref']>=len(row):continue
            rr=refs(row[bc['ref']]);getb=lambda k:row[bc[k]] if k in bc and bc[k]<len(row) else ''
            if not rr:continue
            if line in excluded_rows['bom']:
                excluded_records.append(dict(table='bom',row=line,refs=rr));continue
            v=getb('val');de=getb('desc')
            quantity=getb('qty')
            location=bom_row_location(inputs/Path(config['bom']).name,line,bc,row)
            duplicate_error=bom_duplicate_error(rr,seen_bom,line,quantity,location)
            if duplicate_error:raise ValueError(duplicate_error)
            seen_bom.update({ref:location for ref in rr})
            if not_fitted(getb('populate'),v):
                for r in rr:placements.pop(r,None);nc.append(r)
                continue
            if quantity and num(quantity)!=len(rr):raise ValueError('BOM '+location+'数量与位号数不符：'+','.join(rr)+'；用量 '+quantity+'，不同位号 '+str(len(set(rr))))
            bom_populated.update(rr)
            material_name=getb('name') or v;material_code=getb('code')
            bom.append(dict(refs=','.join(rr),qty=len(rr),val=v,desc=de,name=material_name,code=material_code))
            for r in rr:
                if r in placements:
                    p=placements[r];p['val']=v or p['val'];p['desc']=de;p['name']=material_name;p['code']=material_code
                    if not p['pkg']:p['pkg']=getb('pkg')
                else:
                    missing.append(r);accessory_candidates[r]=(v,de,material_name,material_code)
        xy_not_in_bom=sorted(set(placements)-bom_populated)
        for r in xy_not_in_bom:placements.pop(r)
        if xy_not_in_bom:warnings.append('按 BOM 实装集合排除坐标表中未列入 BOM 的位号：'+','.join(xy_not_in_bom))
    else:
        for r,p in placements.items():bom.append(dict(refs=r,qty=1,val=p['val'],desc='',name=p['val'],code=''))
    active_errors=[message for r,message in xy_errors.items() if not config.get('bom') or r in bom_populated]
    if active_errors:raise ValueError('; '.join(active_errors[:10]))
    library=json.loads((assets/'library/models.json').read_text(encoding='utf8'));a=aliases(assets,config.get('mapping',{}))
    user_root=config.get('user_library_root')
    if user_root:
        from user_models import read_catalog
        library.update(read_catalog(user_root))
    inferred_accessories=[]
    for ref in missing[:]:
        value,description,material_name,material_code=accessory_candidates[ref];parent=placements.get(ref.rsplit('-',1)[0]) if '-' in ref else None
        # This library's film is designed only for the GC4693 0.67 mm CSP.
        # Other missing components still require real coordinates.
        sizes=re.findall(r'(\d+(?:\.\d+)?)\s*MM',description,re.I)
        if (parent and a.get(norm(parent['pkg']))=='GC4693_C35YA' and
            '保护膜' in description and sorted(map(float,sizes))==[5.,7.] and 'SENSOR_FILM_7x5' in library):
            p=dict(parent);p.update(ref=ref,pkg='SENSOR-FILM-7X5',val=value or '7 × 5 mm',desc=description,
                name=material_name,code=material_code,population='accessory',w=7,d=5,h=.04,position_source='BOM 附件无坐标，按用途居中附于 '+parent['ref'])
            placements[ref]=p;missing.remove(ref);inferred_accessories.append(ref)
            warnings.append(ref+' 为无独立坐标的 7×5 mm Sensor 保护膜，按 BOM 用途居中附于 '+parent['ref']+'；位置为推定，可在预览中关闭')
    pending=[]
    if missing:
        if not config.get('allow_partial',True):
            raise ValueError('BOM 中这些实装位号缺少坐标或 XY 标为 NC：'+','.join(sorted(set(missing)))+ '；请核对坐标或在 BOM 中标为 NC')
        for ref in sorted(set(missing)):
            value,description,material_name,material_code=accessory_candidates[ref]
            pending.append(dict(ref=ref,pkg='',val=value,desc=description,name=material_name,code=material_code,reason='missing_coordinates',message='缺少坐标或 XY 标为 NC'))
        warnings.append('部分预览：'+str(len(set(missing)))+' 个 BOM 位号缺少坐标，未放置：'+','.join(sorted(set(missing))))
    if not placements:raise ValueError('BOM/坐标表没有可生成的实装器件')
    mapping={};unknown=[]
    for p in placements.values():
        key=a.get(norm(p['pkg']))
        if key is None and p['pkg'] in library:key=p['pkg']
        if key not in library:
            unknown.append(p['pkg'] or '(空封装)')
            pending.append(dict(p,reason='unmatched_model',message='封装尚未匹配 3D 模型'))
        else:mapping[p['pkg']]=key
    if unknown:
        if not config.get('allow_partial',True):
            raise ValueError('待匹配封装：'+', '.join(sorted(set(unknown)))+'。请在封装映射中设置对应模型后生成。')
        warnings.append('部分预览：以下封装尚未匹配模型，器件保留在待处理清单：'+', '.join(sorted(set(unknown))))
    w,h,th=[num(config.get(k,v)) for k,v in [('width',38),('height',38),('thickness',1.6)]]
    if min(w,h,th)<=0 or max(w,h)>500:raise ValueError('板尺寸需为正，最大支持 500 mm')
    radius=num(config.get('radius',0));ox=num(config.get('gerber_x0',0));oy=num(config.get('gerber_y0',0))
    if radius<0 or radius>min(w,h)/2:raise ValueError('圆角半径需在 0 到短边一半之间')
    def find(kind):
        explicit=config.get('layers',{}).get(kind)
        if explicit:
            target=inputs/Path(explicit).name
            if not target.is_file():raise ValueError('指定图层文件不存在：'+str(explicit))
            return target
        patterns={
          # Allegro film numbers change with the layer stack. Match the film
          # name independently, while retaining boundaries to avoid adt/adb.
          'top_copper':[r'^(?:\d+)?top\.art$',r'\.gtl$',r'(?:^|[-_. ])(?:top|f)[_. -]?(?:copper|cu|layer)\.(?:gbr|ger|art)$'],
          'bot_copper':[r'^(?:\d+)?bottom\.art$',r'\.gbl$',r'(?:^|[-_. ])(?:bottom|b)[_. -]?(?:copper|cu|layer)\.(?:gbr|ger|art)$'],
          'top_mask':[r'^(?:\d+)?soldtop\.art$',r'\.gts$',r'(?:^|[-_. ])(?:top|f)[_. -]?(?:mask|sold|solder|soldermask)\.(?:gbr|ger|art)$'],
          'bot_mask':[r'^(?:\d+)?soldbottom\.art$',r'\.gbs$',r'(?:^|[-_. ])(?:bottom|b)[_. -]?(?:mask|sold|solder|soldermask)\.(?:gbr|ger|art)$'],
          'top_silk':[r'^(?:\d+)?sliktop\.art$',r'\.gto$',r'(?:^|[-_. ])(?:top|f)[_. -]?(?:silk|silkscreen|silks|overlay)\.(?:gbr|ger|art)$'],
          'bot_silk':[r'^(?:\d+)?slikbottom\.art$',r'\.gbo$',r'(?:^|[-_. ])(?:bottom|b)[_. -]?(?:silk|silkscreen|silks|overlay)\.(?:gbr|ger|art)$'],
          'top_paste':[r'^(?:\d+)?pasttop\.art$',r'\.gtp$',r'(?:^|[-_. ])(?:top|f)[_. -]?paste\.(?:gbr|ger|art)$'],
          'bot_paste':[r'^(?:\d+)?pastbottom\.art$',r'\.gbp$',r'(?:^|[-_. ])(?:bottom|b)[_. -]?paste\.(?:gbr|ger|art)$'],
          'outline':[r'\.(?:gko|gm1|gml)$',r'(?:^|[-_. ])(?:edge[_. -]?cuts|outline|board[_. -]?outline)\.(?:gbr|ger|art)$',r'^(?:\d+)?panel\.art$'],
        }
        matches=set()
        for pattern in patterns[kind]:
            candidates=[p for p in inputs.iterdir() if re.search(pattern,p.name,re.I)]
            matches.update(candidates)
        if kind=='outline' and not matches:
            # Drill drawings contain a genuine board outline alongside drill
            # symbols and a legend. Flashes are excluded by closed_contours;
            # infer_board then selects the contour enclosing this board's XY.
            # Keep separate panel/profile layers ahead of drill drawings, since
            # those can contain additional routed cutouts absent from the map.
            for p in inputs.iterdir():
                if p.suffix.lower() not in ('.art','.gbr','.ger'):continue
                named=re.fullmatch(r'\d*(?:dill|dril|drill)(?:[_. -]?(?:map|drawing|draw))?\.(?:art|gbr|ger)',p.name,re.I)
                with p.open('rb') as stream:text=stream.read(16000).decode('ascii',errors='ignore')
                legend='NCLEGEND' in text.upper() and bool(re.search(r'BOARD\s+GEOMETRY/OUTLINE',text,re.I))
                profile=bool(re.search(r'TF\.FileFunction,Profile(?:[,.*])',text,re.I))
                if named or legend or profile:matches.add(p)
        if len(matches)>1:raise ValueError(kind+' 有多个候选图层：'+','.join(sorted(p.name for p in matches))+'；请在图层配置中指定')
        if matches:return next(iter(matches))
        return None
    pad_data={'top':[],'bottom':[]};alignment_pad_data={'top':[],'bottom':[]};layers={}
    outline_file=find('outline');outline=[];slots=[];board_detection=None
    outline_primitives=gerber.parse(outline_file) if outline_file else None
    if config.get('auto_board',False):
        if not outline_file:
            raise ValueError('未识别到板框图层。支持 GKO / Edge.Cuts / 任意编号 panel.art，以及含板框的 dill / dril / drill 钻孔图；若使用其他文件名，请在高级设置的图层文件名中指定，例如 {"outline":"板框.gbr"}。若确实缺少板框，请补充闭合板框 Gerber，或关闭“自动识别单板与原点”后填写尺寸、圆角与原点。')
        progress('识别单板轮廓并对齐 Gerber / XY 原点')
        dx=num(config.get('xy_dx',0));dy=num(config.get('xy_dy',0))
        points=[(p['x']-dx,p['y']-dy) for p in placements.values() if p['population']!='accessory']
        board_detection=gerber.infer_board(outline_primitives,points)
        ox,oy=board_detection['origin'];w,h=board_detection['size']
        for p in placements.values():p['x']-=dx+ox;p['y']-=dy+oy
        config.update(width=w,height=h,gerber_x0=ox,gerber_y0=oy,xy_dx=-ox,xy_dy=-oy,radius=0)
        radius=0 # The Gerber polygon already includes its rounded corners.
        board_detection['file']=outline_file.name
        warnings.append(f'自动识别单板 {w:.3f} × {h:.3f} mm，源原点 ({ox:.4f}, {oy:.4f})；XY 已同步平移，保留 Gerber 圆角。识别到 {board_detection["candidates"]} 个闭合候选，按所选 XY 确定单板范围。板厚仍采用输入值。')
        progress(f'单板 {w:.3f} × {h:.3f} mm；原点 ({ox:.4f}, {oy:.4f})，XY 已对齐')
    gerber.OX,gerber.OY,gerber.BW,gerber.BH,gerber.PXM=ox,oy,w,h,min(48,3000/max(w,h))
    gerber.SIZE=(round(w*gerber.PXM),round(h*gerber.PXM))
    if outline_file:
        outline,slots=gerber.extract_outline(outline_primitives,(ox,oy),w,h)
        if not outline:
            raise ValueError('板框 '+outline_file.name+' 中没有与输入尺寸/原点一致的闭合外轮廓；请核对板宽、板高和 Gerber 原点，或移除此候选板框并使用参数板框')
        outline_basis='Gerber: '+outline_file.name+'，圆弧以最大 0.025 mm 线段逼近'
    else:
        outline_basis='用户输入的板宽、板高及圆角半径（未提供可识别的板框 Gerber）'
        warnings.append('板框采用输入尺寸和圆角；要复现异形板请提供闭合 Edge.Cuts / GKO 并核对原点')
    layers['outline']=outline_file.name if outline_file else None
    holes=[];drill_reports=[]
    explicit_drill=config.get('layers',{}).get('drill') or config.get('drills')
    if explicit_drill:
        if isinstance(explicit_drill,str):explicit_drill=[explicit_drill]
        drill_candidates=[inputs/Path(p).name for p in explicit_drill]
        if any(not p.is_file() for p in drill_candidates):raise ValueError('指定钻孔文件不存在')
    else:
        drill_candidates=[]
        for p in inputs.iterdir():
            if p.suffix.lower() in ('.drl','.xln','.exc','.rou') or (p.suffix.lower()=='.txt' and re.search(r'^(?:M48|INCH|METRIC)|Holesize\s+\d+',p.read_text(errors='replace'),re.M)):
                drill_candidates.append(p)
    for f in sorted(set(drill_candidates)):
        hh,ss,info=gerber.parse_drill(f,(ox,oy),config.get('drill_format'))
        included=[q for q in hh if -q['d']/2<=q['x']<=w+q['d']/2 and -q['d']/2<=q['y']<=h+q['d']/2]
        local_slots=[q for q in ss if max(x for x,y in q)>=0 and min(x for x,y in q)<=w and max(y for x,y in q)>=0 and min(y for x,y in q)<=h]
        boundary_hits=[q for q in included if q['x']-q['d']/2<0 or q['x']+q['d']/2>w or q['y']-q['d']/2<0 or q['y']+q['d']/2>h]
        # Recognise the standard 20-mil NPTH / 40-mil-pitch mouse-bite arrays
        # used in Allegro fabrication panels. They are separation tooling,
        # not component holes. Retain the supplied smooth single-board outline
        # and report every excluded hit; other boundary holes remain errors.
        breakaway=[]
        for axis,edge,other in [('x',0,'y'),('x',w,'y'),('y',0,'x'),('y',h,'x')]:
            candidates=sorted([q for q in boundary_hits if q.get('plated') is False and abs(q['d']-.508)<.001 and abs(q[axis]-edge)<.001],key=lambda q:q[other])
            groups=[]
            for q in candidates:
                if not groups or abs(q[other]-groups[-1][-1][other]-1.016)>.002:groups.append([])
                groups[-1].append(q)
            breakaway.extend(q for group in groups if len(group)>=4 for q in group)
        if breakaway:
            included=[q for q in included if q not in breakaway];boundary_hits=[q for q in boundary_hits if q not in breakaway]
            warnings.append(f.name+'：识别并排除 '+str(len(breakaway))+' 个板边 20mil/40mil 成组拼板分离孔；预览采用单板平滑外轮廓，断板残痕未建模')
        info.update(included_hits=len(included),excluded_panel_hits=len(hh)-len(included),excluded_breakaway_hits=breakaway,boundary_intersecting_hits=len(boundary_hits),included_slots=len(local_slots),excluded_panel_slots=len(ss)-len(local_slots))
        holes.extend(included);slots.extend(local_slots);drill_reports.append(info);warnings.extend(info.get('warnings',[]))
        if info['excluded_panel_hits'] or info['excluded_panel_slots']:warnings.append(f.name+'：排除板框以外命中 '+str(info['excluded_panel_hits'])+' 孔 / '+str(info['excluded_panel_slots'])+' 槽；请核对原点和单板/拼板范围')
        if boundary_hits:raise ValueError(f.name+'：'+str(len(boundary_hits))+' 个孔跨越板边，当前通用导入不支持半孔裁切；请核对原点或提供已裁切的板框')
        if any(x<0 or x>w or y<0 or y>h for q in local_slots for x,y in q):raise ValueError(f.name+'：铣槽跨越板边，当前通用导入不支持边缘槽裁切；请核对原点或提供已裁切的板框')
    if not drill_candidates:warnings.append('未提供可识别的 Excellon 钻孔文件，板实体没有钻孔；请补充 DRL / XLN 文件')
    elif not holes and not slots:raise ValueError('钻孔文件中的孔/槽全部位于当前板框外；请检查 Gerber 原点与板尺寸')
    # Repeated PTH/NPTH files sometimes contain the same geometric hole.
    hole_keys={};duplicates=0
    for q in holes:
        key=tuple(round(q[k],5) for k in ('x','y','d'))
        if key in hole_keys:duplicates+=1;continue
        hole_keys[key]=q
    holes=list(hole_keys.values())
    if duplicates:warnings.append('去除重复钻孔命中 '+str(duplicates)+' 个')
    slots,duplicate_slots=gerber.deduplicate_slots(slots)
    if duplicate_slots:warnings.append('板框与铣槽资料中重复的内轮廓已合并 '+str(duplicate_slots)+' 个（边界差不超过 0.001 mm），避免重复挖孔')
    for face,side in [('top','top'),('bot','bottom')]:
        progress('生成'+('顶面' if face=='top' else '底面')+'板层')
        images={}
        for kind in ['copper','mask','silk','paste']:
            f=find(face+'_'+kind);layers[face+'_'+kind]=f.name if f else None
            if not f:
                if kind in ['copper','mask']:raise ValueError('无法找到 '+face+'_'+kind+'。可在图层文件名配置中指定。')
                images[kind]=Image.new('L',gerber.SIZE);continue
            primitives=gerber.parse(f);images[kind]=gerber.render(primitives)
            if kind in ('paste','mask'):
                for q in primitives:
                    if q[0]!='flash' or not q[1]:continue
                    for exp,typ,v in q[4]:
                        if not exp:continue
                        if typ=='P':
                            xs=[p[0] for p in v];ys=[p[1] for p in v];cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2;pw=max(xs)-min(xs);ph=max(ys)-min(ys)
                        else:cx,cy,pw,ph=v
                        x,y=q[2]+cx-ox,q[3]+cy-oy
                        if 0<=x<=w and 0<=y<=h:
                            target=pad_data if kind=='paste' else alignment_pad_data
                            target[side].append([x,y,pw,ph,typ])
        cu,mask,silk=images['copper'],images['mask'],images['silk'];exposed=ImageChops.multiply(cu,mask);substrate=ImageChops.subtract(mask,cu)
        im=Image.new('RGB',gerber.SIZE,(16,77,46));im.paste((18,83,49),mask=cu);im.paste((143,130,81),mask=substrate);im.paste((178,186,190),mask=exposed)
        visible=ImageChops.subtract(silk,mask);im.paste((225,229,216),mask=visible)
        metal=Image.new('L',gerber.SIZE);metal.paste(225,mask=exposed);rough=Image.new('L',gerber.SIZE,175);rough.paste(118,mask=exposed)
        im.save(out/f'board_{face}.png');metal.save(out/f'board_{face}_metal.png');rough.save(out/f'board_{face}_rough.png');mask.save(out/f'gerber_{face}_mask.png')
    outside=[r for r,p in placements.items() if not (-3<=p['x']<=w+3 and -3<=p['y']<=h+3)]
    if outside:raise ValueError('坐标超出板框，请检查单位/XY 原点：'+','.join(outside[:20]))
    # Keep every valid XY for board detection; only matched parts enter the assembly.
    for item in pending:
        if item['reason']=='unmatched_model':item.update(placements[item['ref']])
    for ref in [p['ref'] for p in pending if p['reason']=='unmatched_model']:
        placements.pop(ref,None)
    inferred_accessories=[ref for ref in inferred_accessories if ref in placements]
    # Four-way pose matching against paste; keep raw angle on symmetric ties.
    pose={};progress('匹配器件方向')
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    def contacts(model):
        # Estimate separate signal lands from connected metal triangles.
        groups=[]
        for part in model['parts']:
            if not part['mat'].startswith('PIN'):continue
            vs=np.array(part['v']).reshape(-1,3)
            if not len(vs):continue
            # Boxes and mesh surfaces are grouped by welded vertices.
            parent={};edges={}
            def root(x):
                parent.setdefault(x,x)
                if parent[x]!=x:parent[x]=root(parent[x])
                return parent[x]
            for j in range(0,len(part['i']),3):
                ids=[tuple(np.round(vs[k],4)) for k in part['i'][j:j+3]]
                for q in ids[1:]:parent[root(q)]=root(ids[0])
            for q in list(parent):edges.setdefault(root(q),[]).append(q)
            for points in edges.values():
                ar=np.array(points);low=ar[:,1].min()
                if low>.16:continue
                ar=ar[ar[:,1]<=low+.05];groups.append(((ar[:,0].min()+ar[:,0].max())/2,(ar[:,2].min()+ar[:,2].max())/2))
        # Many KiCad WRL colour surfaces represent one terminal; reduce close centres.
        result=[]
        for q in groups:
            if not any(math.dist(q,v)<.14 for v in result):result.append(q)
        return result
    cs_cache={}
    if config.get('auto_pose',True):
        for ref,p in placements.items():
            key=mapping[p['pkg']]
            if library[key].get('placement_footprint'):
                fitted = fit_connector_footprint(p, library[key], pad_data[p['side']])
                if fitted:
                    pose[ref] = fitted
                else:
                    warnings.append(ref+'：连接器信号焊盘和两端固定焊盘未完整匹配，保留原始 XY；请核对封装映射与 Gerber')
                continue
            if key not in cs_cache:cs_cache[key]=contacts(library[key])
            cs=cs_cache[key];pp=np.array(pad_data[p['side']],dtype=object)
            if not cs or len(pp)<len(cs) or key.startswith(('CONN','HDR')):continue
            near=np.array([q[:4] for q in pp if math.hypot(q[0]-p['x'],q[1]-p['y'])<max(2,5+max(abs(v) for q in cs for v in q))],float)
            if len(near)<len(cs):continue
            best=None
            for da in [0,90,180,270]:
                a=math.radians(p['rot']+da);c,s=math.cos(a),math.sin(a)
                if p['side']=='top':q=np.array([(p['x']+c*x+s*z,p['y']+s*x-c*z) for x,z in cs])
                else:q=np.array([(p['x']+c*x-s*z,p['y']+s*x+c*z) for x,z in cs])
                distance=np.linalg.norm(q[:,None,:]-near[None,:,:2],axis=2);ii,jj=linear_sum_assignment(distance)
                score=float(distance[ii,jj].mean())+da*.000001
                if best is None or score<best[0]:best=(score,da)
            if best and best[0]<.4 and best[1]:pose[ref]=dict(dyaw=best[1],reason='paste-centre automatic pose match; verify in preview')
    for ref,delta in config.get('ref_rotation',{}).items():
        if any(item['ref']==ref for item in pending):continue
        if ref not in placements:raise ValueError('旋转修正位号不在实装表中：'+ref)
        pose.setdefault(ref,{})['dyaw']=num(delta)
    from calibration import prepare, dump
    dump(out/'calibration.json',prepare(placements,pose,mapping,config.get('calibration_rules'),config.get('ref_calibration')))
    dump(out/'manufacturing.json',dict(version=1,mount_types=config.get('manufacturing',{}).get('mount_types',{})))
    used={key:library[key] for key in set(mapping.values())};(out/'models').mkdir(exist_ok=True)
    (out/'models/models.json').write_text(json.dumps(used,separators=(',',':')),encoding='utf8')
    copy_native_model_parts(assets,out,used,user_root)
    geometry=dict(round_holes=holes,routed_slots=slots,corner_radius_mm=radius,outline=outline,outline_basis=outline_basis,include_all_drill_diameters=True)
    for name,value in [('pads.json',pad_data),('alignment_pads.json',alignment_pad_data),('posefix.json',pose),('align.json',{}),('padfix.json',{}),('board_geometry.json',geometry),('drills.json',holes),('model_mapping.json',mapping)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
    geom={k:[] for k in ['outline_a','silk_top','silk_bot','text_top','text_bot']}
    geom['outline_a']=[dict(closed=True,pts=outline or [[0,0],[w,0],[w,h],[0,h]])]
    project_name=str(config.get('name') or 'PCBA').strip()
    if not project_name or len(project_name)>100 or re.search(r'[<>:"/\\|?*\x00-\x1f]',project_name) or project_name.endswith(('.', ' ')):
        raise ValueError('工程名称需为 1–100 个普通字符，不能含 Windows 文件名禁用字符')
    data=dict(board=dict(w=w,h=h,name=project_name,thickness=th),placements=placements,pending_components=pending,excluded_rows=excluded_records,panels=[dict(x=0,y=0)],geom=geom,bom=bom,counts=dict(top=sum(p['side']=='top' and p['population']!='accessory' for p in placements.values()),bottom=sum(p['side']=='bottom' and p['population']!='accessory' for p in placements.values()),accessories=len(inferred_accessories)),coordinate_frame=dict(gerber_x0=ox,gerber_y0=oy,unit='mm',xy_offsets_mm=[num(config.get('xy_dx',0)),num(config.get('xy_dy',0))]))
    (out/'data.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    html=(assets/'viewer.html').read_text(encoding='utf8')
    # JSON-escape names, and neutralize HTML script terminators inside strings.
    mp=json.dumps(mapping,ensure_ascii=True).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=re.sub(r'const MODEL_MAP = \{.*?\};',lambda m:'const MODEL_MAP = '+mp+';',html,flags=re.S)
    html=re.sub(r'const MODEL_BY_REF = \{.*?\};','const MODEL_BY_REF = {};',html,flags=re.S)
    html=html.replace('const isConn = CONN_RE.test(modelKey(p));','const isConn = false; // Generic input: honour XY rotation, not sample-board edge heuristics.')
    html=html.replace('const T = 1.6;',f'const T = {th};').replace('BOARD_GEOMETRY.corner_radius_mm||1.0','BOARD_GEOMETRY.corner_radius_mm||0')
    html=html.replace('GK7205V10_MB_V01_RA',html_lib.escape(project_name,quote=True))
    html=html.replace('单板 38 × 38 × 1.6 mm',f'单板 {w:g} × {h:g} × {th:g} mm')
    component_summary=f'{len(placements)-len(inferred_accessories)} 个'+('已显示器件' if pending else '实装器件')+(f' + {len(inferred_accessories)} 件附件' if inferred_accessories else '')+(f' · {len(pending)} 个待处理（部分预览）' if pending else '')
    html=html.replace('242 个实装器件 + 1 件 Sensor 保护膜',component_summary).replace('45 个 NC · 3 个测试点 · BOM 已核对',f'{len(set(nc))} 个明确 NC · {len(xy_not_in_bom)} 个 XY 位号不在实装 BOM')
    html=html.replace('72 行 / 242 器件 + 1 保护膜',f'{len(bom)} 行 / {len(placements)} 器件')
    html=html.replace('${DATA.bom.length} 行 / 242 器件 + 1 保护膜','${DATA.bom.length} 行 / ${Object.keys(DATA.placements).length} 器件')
    html=re.sub(r"const ASSET_VERSION = [^;]+;",lambda m:'const ASSET_VERSION = '+json.dumps(str(__import__('time').time()))+';',html,count=1)
    (out/'index.html').write_text(html,encoding='utf8');shutil.copytree(assets/'vendor',out/'vendor',dirs_exist_ok=True)
    report=dict(components=len(placements)-len(inferred_accessories),accessories=len(inferred_accessories),inferred_accessories=inferred_accessories,top=data['counts']['top'],bottom=data['counts']['bottom'],nc=sorted(set(nc)),xy_not_in_bom=xy_not_in_bom,bom_missing_coordinates=sorted(set(missing)),pending_components=pending,unmatched_footprints=sorted(set(unknown)),incomplete=bool(pending),excluded_rows=excluded_records,layers=layers,models=mapping,auto_pose=pose,warnings=warnings,board_mm=[w,h,th],origin_mm=[ox,oy],outline_basis=outline_basis,holes=len(holes),routed_slots=len(slots),duplicate_slots_removed=duplicate_slots,drills=drill_reports,
                board_detection=board_detection,resolved_config=dict(width=w,height=h,thickness=th,radius=radius,gerber_x0=ox,gerber_y0=oy,xy_dx=num(config.get('xy_dx',0)),xy_dy=num(config.get('xy_dy',0))))
    (out/'导入检查.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (out/'待处理器件.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf8')
    if config.get('step',True) and not pending:
        from step_export import export_step
        export_step(out,out/(project_name+'.stp'),progress)
    return report
