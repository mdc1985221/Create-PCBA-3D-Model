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
