#!/usr/bin/env python3
"""Verify source text, archive coverage, and local links after a Scroll build."""
import json,re
from pathlib import Path
from urllib.parse import unquote
from bs4 import BeautifulSoup
from import_blog import extract_body
ROOT=Path(__file__).resolve().parent.parent
posts=json.loads((ROOT/'archive.json').read_text())
errors=[]
for p in posts:
    original=extract_body((ROOT/'originals'/p['original']).read_bytes())
    page=BeautifulSoup((ROOT/(p['slug']+'.html')).read_text(),'html.parser')
    body=page.select_one('.prose')
    for node in body.select('.unavailable-image'): node.decompose()
    norm=lambda s:re.sub(r'\s+','',s)
    if norm(body.get_text()) != norm(original.get_text()): errors.append('Text mismatch: '+p['slug'])
for file in ROOT.glob('*.html'):
    page=BeautifulSoup(file.read_text(),'html.parser')
    if len(page.select('main,[role=main]'))!=1: errors.append('Main landmark: '+file.name)
    if len(page.select('h1'))!=1: errors.append('Heading: '+file.name)
    for node in page.select('[href],[src]'):
        url=node.get('href',node.get('src',''))
        if url.startswith(('https:','http:','mailto:','data:','javascript:')):continue
        target,_,fragment=url.partition('#')
        if target and not (ROOT/unquote(target.split('?')[0])).is_file():errors.append(f'Missing target {file.name}: {url}')
        if fragment and (not target or target.endswith('.html')):
            other=BeautifulSoup((ROOT/target).read_text(),'html.parser') if target and (ROOT/target).exists() else page
            if not other.find(id=unquote(fragment)) and not other.find(attrs={'name':unquote(fragment)}):errors.append(f'Missing anchor {file.name}: {url}')
archive=BeautifulSoup((ROOT/'index.html').read_text(),'html.parser')
assert len(archive.select('.post-row'))==len(posts)
print('\n'.join(errors[:80]))
print(f'{len(posts)} essays; {len(list(ROOT.glob("*.html")))} pages; {len(errors)} issues')
raise SystemExit(bool(errors))
