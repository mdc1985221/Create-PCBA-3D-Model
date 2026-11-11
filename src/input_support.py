"""Input discovery shared by the local API and generation import."""
import io
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

TABLE_EXTENSIONS = {'.xlsx', '.xls', '.csv', '.tsv', '.txt'}
INPUT_EXTENSIONS = TABLE_EXTENSIONS | {'.zip', '.art', '.gbr', '.ger', '.gtl', '.gbl', '.gts', '.gbs', '.gto', '.gbo', '.gtp', '.gbp', '.gko', '.gm1', '.gml', '.drl', '.xln', '.exc', '.rou'}


def zip_member_name(info):
    """Honor UTF-8; tolerate common GBK names in legacy Chinese ZIP files."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode('cp437').decode('gb18030')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace('\\', '/')


def expand_sources(files):
    warnings, sources = [], {}

    def include(name, content, source, value):
        suffix = Path(name).suffix.lower()
        if suffix not in INPUT_EXTENSIONS:
            return
        key = Path(name).name.lower()
        if key in sources:
            old = sources[key]
            if old['data'] != content:
                raise ValueError('资料中有不同内容的同名文件：' + name + '；请重命名后重新选择')
            return
        sources[key] = dict(data=content, source=source, value=value, name=Path(name).name)

    for name, content in files.items():
        if Path(name).suffix.lower() != '.zip':
            include(name, content, name, name)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(q.file_size for q in entries) > 200_000_000:
                    raise ValueError(name + '：压缩包解压后超过 200 MB 或文件过多')
                for entry in entries:
                    if entry.is_dir():
                        continue
                    member = zip_member_name(entry)
                    path = PurePosixPath(member)
                    if path.is_absolute() or '..' in path.parts or ':' in member:
                        raise ValueError(name + '：ZIP 包含不安全的路径')
                    if entry.flag_bits & 1:
                        raise ValueError(name + '：不支持加密 ZIP，请先解压或重新打包')
                    if path.suffix.lower() == '.zip':
                        warnings.append(name + ' 内还有 ZIP：' + member + '；请先解压后选择里面的资料')
                    elif path.suffix.lower() in INPUT_EXTENSIONS:
                        include(path.name, archive.read(entry), name + ' / ' + member, member)
        except zipfile.BadZipFile as error:
            raise ValueError(name + '：不是可读取的 ZIP 文件，请重新压缩资料') from error
        except (NotImplementedError, RuntimeError) as error:
            raise ValueError(name + '：无法读取 ZIP 压缩方式或加密内容，请先解压后选择资料文件夹') from error
    return sources, warnings


def inspect_files(files):
    from generator import read_table, header, num, side_value
    sources, warnings = expand_sources(files)
    candidates, signatures = [], []
    with tempfile.TemporaryDirectory(prefix='pcba-input-inspect-') as temporary:
        for item in sources.values():
            if Path(item['name']).suffix.lower() not in TABLE_EXTENSIONS:
                continue
            path = Path(temporary) / item['name']
            path.write_bytes(item['data'])
            try:
                rows = read_table(path)
                roles = []
                for role, required in [('xy', ['ref', 'x', 'y']), ('bom', ['ref'])]:
                    try:
                        header(rows, required)
                        roles.append(role)
                    except ValueError:
                        pass
                if roles:
                    candidates.append(dict(name=item['name'], value=item['value'], source=item['source'], roles=roles))
                    if 'xy' in roles:
                        start,columns=header(rows,['ref','x','y']);signature={}
                        for row in rows[start+1:]:
                            try:
                                ref=row[columns['ref']].upper().strip()
                                if ref:signature[ref]=(num(row[columns['x']]),num(row[columns['y']]),side_value(row[columns['side']]) if 'side' in columns else 'top')
                            except (ValueError,IndexError):continue
                        for other_name,other in signatures:
                            common=set(signature)&set(other)
                            if common and len(common)>=min(len(signature),len(other))*.8:
                                changed=[ref for ref in common if any(abs(signature[ref][i]-other[ref][i])>.001 for i in (0,1)) or signature[ref][2]!=other[ref][2]]
                                if changed:warnings.append(item['name']+' 与 '+other_name+' 的坐标内容不同（'+str(len(changed))+' 个位号，如 '+', '.join(sorted(changed)[:5])+'）；请确认使用哪份 XY，工具按选中源文件读取，不自动混合。')
                        signatures.append((item['name'],signature))
                elif path.suffix.lower() != '.txt':
                    warnings.append(item['source'] + '：未找到 XY / BOM 表头，不作为坐标候选')
            except Exception as error:
                if path.suffix.lower() != '.txt':
                    warnings.append(item['source'] + '：' + str(error))
    if not any('xy' in item['roles'] for item in candidates):
        warnings.append('没有找到带位号、X、Y列的坐标表。请用“选择文件夹”递归导入生产资料，或补充 XLSX / XLS / CSV 坐标表；只有 Gerber 不能确定器件位置。')
    return dict(candidates=candidates, warnings=list(dict.fromkeys(warnings)), discovered_files=len(sources),
                xy_count=sum('xy' in item['roles'] for item in candidates))


def review_files(files, config, model_aliases, model_ids):
    """Review the same parsed table rows consumed by generation; exclusions are local."""
    from generator import read_table, header, refs, not_fitted, norm, num, bom_duplicate_error, bom_row_location
    sources, warnings = expand_sources(files)
    result = dict(bom_rows=[], xy_rows=[], footprints=[], warnings=warnings)
    placements, populated = {}, set()
    for role in ('xy', 'bom'):
        selected = config.get(role)
        if not selected:
            continue
        source = sources.get(Path(selected.replace('\\', '/')).name.lower())
        if not source:
            raise ValueError('所选文件不存在：' + selected)
        with tempfile.TemporaryDirectory(prefix='pcba-table-review-') as temporary:
            path = Path(temporary) / source['name']
            path.write_bytes(source['data'])
            rows = read_table(path)
        start, columns = header(rows, ['ref', 'x', 'y'] if role == 'xy' else ['ref'])
        excluded = set(config.get('exclude_' + role + '_rows', []))
        seen = {}
        for number, row in enumerate(rows[start+1:], start+2):
            get = lambda key: row[columns[key]] if key in columns and columns[key] < len(row) else ''
            rr = refs(get('ref'))
            if not rr:
                continue
            item = dict(row=number, refs=rr, qty=get('qty'), pkg=get('pkg'), val=get('val'),
                        desc=get('desc'), name=get('name'), code=get('code'),
                        excluded=number in excluded, active=not not_fitted(get('populate'), get('val')),
                        duplicates=[], issues=[],
                        location=bom_row_location(path, number, columns, row) if role == 'bom' else '')
            if not item['excluded']:
                if role == 'bom':
                    duplicate_error = bom_duplicate_error(rr, {ref: value['location'] for ref, value in seen.items()}, number, get('qty'), item['location'])
                    if duplicate_error:item['issues'].append(duplicate_error)
                    if item['active'] and get('qty'):
                        try:
                            if num(get('qty')) != len(set(rr)):
                                item['issues'].append('用量 '+get('qty')+' 与不同位号数 '+str(len(set(rr)))+' 不符，请核对原表')
                        except ValueError:
                            item['issues'].append('无效用量：'+get('qty'))
                item['duplicates'] = sorted({ref for ref in rr if ref in seen or rr.count(ref) > 1})
                for ref in rr:
                    if ref in seen:
                        seen[ref]['duplicates'] = sorted(set(seen[ref]['duplicates'] + [ref]))
                    seen[ref] = item
                if item['active']:
                    if role == 'xy':
                        for ref in rr:
                            placements.setdefault(ref, dict(pkg=item['pkg'], ref=ref))
                    else:
                        populated.update(rr)
                        for ref in rr:
                            if ref in placements and not placements[ref]['pkg']:
                                placements[ref]['pkg'] = item['pkg']
            result[role + '_rows'].append(item)
    by_norm = {norm(key): value for key, value in {**model_aliases, **config.get('mapping', {})}.items()}
    groups = {}
    for ref, placement in placements.items():
        if config.get('bom') and ref not in populated:
            continue
        pkg = placement['pkg']
        model = by_norm.get(norm(pkg), pkg if pkg in model_ids else '')
        if model not in model_ids:
            model = ''
        group = groups.setdefault(pkg, dict(pkg=pkg, model=model, refs=[]))
        group['refs'].append(ref)
    result['footprints'] = sorted(groups.values(), key=lambda item: (bool(item['model']), item['pkg']))
    result['missing_coordinates'] = sorted(populated - set(placements))
    result['matched'] = sum(len(item['refs']) for item in result['footprints'] if item['model'])
    result['unmatched'] = sum(len(item['refs']) for item in result['footprints'] if not item['model'])
    return result
