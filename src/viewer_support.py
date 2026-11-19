"""Upgrade image export in saved viewers without replacing project model data."""
from pathlib import Path
import re


def upgrade_image_export(text, assets):
    pattern = r"function saveView\(kind\)\s*\{[\s\S]*?(?=\$\('saveTop'\)\.onclick)"
    template = (Path(assets) / 'viewer.html').read_text(encoding='utf8')
    current = re.search(pattern, template)
    previous = re.search(pattern, text)
    if current is None or previous is None:
        return text
    block = current[0]
    # Generated viewers carry their own board name in the download expression.
    filename_pattern = r'(?m)^[ \t]*a\.download=[^\r\n]+'
    filename = re.search(filename_pattern, previous[0])
    if filename:
        block = re.sub(filename_pattern, lambda _: filename[0], block, count=1)
    return re.sub(pattern, lambda _: block, text, count=1)


def upgrade_step_export(text,assets):
    """Upgrade export controls without changing geometry or model bindings."""
    template=(Path(assets)/'viewer.html').read_text(encoding='utf8')
    for start,end in [('<!-- STEP_EXPORT_UI -->','<!-- /STEP_EXPORT_UI -->'),('/* STEP_EXPORT_SCRIPT */','/* /STEP_EXPORT_SCRIPT */')]:
        if start not in template:continue
        block=start+template.split(start,1)[1].split(end,1)[0]+end
        if start in text:text=re.sub(re.escape(start)+r'.*?'+re.escape(end),lambda _:block,text,count=1,flags=re.S)
        elif start.startswith('<!--'):
            anchor='<a id="exportResult"'
            if anchor not in text:continue
            position=text.find('</div>',text.index(anchor))+len('</div>')
            text=text[:position]+'\n'+block+text[position:]
        else:
            position=text.rfind('</script>')
            if position<0 or 'id="stepExport"' not in text:continue
            text=text[:position]+block+'\n'+text[position:]
    return text
