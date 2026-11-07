#!/usr/bin/env python3
"""Конвертация docs/requirements/ТЗ.docx в Markdown (zip + OOXML, только stdlib)."""
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def q(tag):
    return f'{{{W}}}{tag}'


def run_is_bold(r):
    rPr = r.find(q('rPr'))
    if rPr is None:
        return False
    b = rPr.find(q('b'))
    return b is not None and b.get(q('val')) not in ('0', 'false')


BR = '\x00'          # маркер переноса строки внутри абзаца


def run_text(r):
    parts = []
    for el in r.iter():
        if el.tag == q('t'):
            parts.append(el.text or '')
        elif el.tag == q('tab'):
            parts.append(' ')
        elif el.tag == q('br'):
            parts.append(BR)
    return ''.join(parts)


def fragments(p):
    """[(kind, text)]: kind 'b' — жирный, 'n' — обычный; соседние склеены."""
    raw = []
    for child in p:
        if child.tag != q('r'):
            continue
        t = run_text(child)
        if not t:
            continue
        raw.append(('b' if run_is_bold(child) else 'n', t))
    merged = []
    for kind, t in raw:
        if merged and merged[-1][0] == kind:
            merged[-1][1] += t
        else:
            merged.append([kind, t])
    return [(k, t) for k, t in merged]


def render(frags, allow_bold=True):
    if not frags:
        return ''
    if all(k == 'b' for k, _ in frags):          # весь абзац жирный → без маркеров
        allow_bold = False
    out = []
    for kind, t in frags:
        core = t.strip()
        lead = t[:len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        if allow_bold and kind == 'b' and core:
            out.append(f'{lead}**{core}**{trail}')
        else:
            out.append(t)
    s = ''.join(out)
    s = re.sub(r'\*\*\s*\*\*', ' ', s)
    s = re.sub(r' {2,}', ' ', s)
    return s.strip()


def md(p, allow_bold=True):
    s = render(fragments(p), allow_bold)
    return s.replace(BR, ' ')


def md_cell(p):
    """Текст для ячейки таблицы: перенос строки внутри абзаца → <br>."""
    s = render(fragments(p))
    s = s.replace(BR + ' ', '<br>').replace(' ' + BR, '<br>').replace(BR, '<br>')
    return s


def plain(p):
    return ''.join(t for _, t in fragments(p)).replace(BR, ' ').strip()


def style_of(p):
    pPr = p.find(q('pPr'))
    if pPr is None:
        return ''
    st = pPr.find(q('pStyle'))
    return st.get(q('val')) if st is not None else ''


def size_of(p):
    sizes = {r.find(q('rPr')).find(q('sz')).get(q('val'))
             for r in p.findall(q('r'))
             if r.find(q('rPr')) is not None and r.find(q('rPr')).find(q('sz')) is not None}
    return sizes.pop() if len(sizes) == 1 else ''


def list_item(frags):
    """ListBullet: нумерация '1.' уже в тексте → вынести её из жирного фрагмента."""
    m = re.match(r'^(\d+)[.)]\s+', ''.join(t for _, t in frags))
    if not m:
        return None, frags
    num = str(int(m.group(1)))
    rest = frags[0][1][m.end():]
    new = [(frags[0][0], rest)] + frags[1:]
    return num, [f for f in new if f[1].strip() or f[0] == 'n']


def cell_text(tc):
    paras = [md_cell(p) for p in tc.findall(q('p'))]
    paras = [t for t in paras if t]
    return '<br>'.join(paras).replace('|', r'\|')


def table_md(tbl, out):
    rows = tbl.findall(q('tr'))
    grid = [[cell_text(tc) for tc in tr.findall(q('tc'))] for tr in rows]
    if not grid:
        return
    if len(grid) == 1:                     # каллаут из одной строки
        cells = [c.replace('**', '') for c in grid[0]]
        label, sep, body = cells[0].partition('<br>')
        out.append(f'> **{label.strip()}**')
        for chunk in ([body] if sep else []) + cells[1:]:
            for line in chunk.split('<br>'):
                if line.strip():
                    out.append(f'> {line.strip()}')
        out.append('')
        return
    width = max(len(r) for r in grid)
    for r in grid:
        r += [' '] * (width - len(r))
    out.append('| ' + ' | '.join(grid[0]) + ' |')
    out.append('|' + ' --- |' * width)
    for r in grid[1:]:
        out.append('| ' + ' | '.join(r) + ' |')
    out.append('')


def convert(path):
    z = zipfile.ZipFile(path)
    body = ET.fromstring(z.read('word/document.xml')).find(q('body'))
    out = []
    title_done = 0

    def blank():
        if out and out[-1] != '':
            out.append('')

    for el in body:
        if el.tag == q('sectPr'):
            continue
        if el.tag == q('tbl'):
            blank()
            table_md(el, out)
            continue
        if el.tag != q('p'):
            continue
        size = size_of(el)
        text = md(el)
        if title_done < 3:                              # титульный блок
            if not plain(el):
                continue
            title_done += 1
            if size == '40':
                out.append(f'# {plain(el)}')
            elif size == '23':
                out.append(f'**{plain(el)}**')
            else:
                out.append(md(el))
            out.append('')
            continue
        if not text:
            blank()
            continue
        allbold = all(k == 'b' for k, _ in fragments(el))
        if size == '27' and allbold:
            blank()
            out.append(f'## {plain(el)}')
        elif size == '23' and allbold:
            blank()
            out.append(f'### {plain(el)}')
        elif size == '21' and allbold and style_of(el) == '':
            blank()
            out.append(f'#### {plain(el)}')
        elif style_of(el) == 'ListBullet':
            num, frags = list_item(fragments(el))
            body_md = render(frags)
            out.append(f'{num}. {body_md}' if num else f'- {body_md}')
        else:
            blank()
            out.append(text)
    res = []
    for line in out:
        if line == '' and res and res[-1] == '':
            continue
        res.append(line)
    return '\n'.join(res).strip() + '\n'


if __name__ == '__main__':
    sys.stdout.write(convert(sys.argv[1]))
