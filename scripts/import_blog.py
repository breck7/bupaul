#!/usr/bin/env python3
"""Recreate the Scroll archive from saved Posthaven archive pages.

Requires beautifulsoup4. Run from the repository root. Downloads are optional;
the checked-in originals make conversion repeatable without network access.
"""
import argparse
import hashlib
import html
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString, Comment

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://paulgraham.com/'
assets = {}
failure_file = ROOT / 'originals/media-failures.json'
unavailable = {item['url'] for item in json.loads(failure_file.read_text())} if failure_file.exists() else set()

def clean(text):
    return re.sub(r'\s+', ' ', text).strip()

def esc(text):
    return html.escape(str(text), quote=True)

def indent(source, depth=1):
    return '\n'.join(' ' * depth + line for line in source.splitlines())

def element(cue='', text='', attrs=None, children=()):
    line = (cue + ' ' if cue and text else cue) + html.escape(str(text), quote=False)
    result = [line]
    for key, value in (attrs or {}).items():
        result.append(' ' + key + (' ' + esc(value) if str(value) else ''))
    result.extend(indent(child) for child in children)
    return '\n'.join(result)

def prose(text, *directives, **attrs):
    return element('', text, attrs, directives)

def link(text, url, **attrs):
    # A bare linked paragraph; use tag a + href only for cards containing children.
    cue = url if url.startswith(('http://', 'https://')) or url.endswith('.html') else 'link ' + url
    return prose(text, cue, **attrs)

def local_link(url):
    if url.startswith('#'): return url
    absolute = urljoin(current_url, re.sub(r'\s+', '', url))
    parsed = urlparse(absolute)
    slug = Path(parsed.path).stem
    if parsed.hostname in ('paulgraham.com','www.paulgraham.com') and slug in slugs:
        return slug + '.html' + ('#' + parsed.fragment if parsed.fragment else '')
    return absolute

def inline(node, directives):
    if isinstance(node, Comment): return ''
    if isinstance(node, NavigableString):
        return html.escape(str(node), quote=False)
    if node.name == 'anchorpoint':
        directives.append(('id', node['id']))
        return ''
    if node.name in ('script', 'style'):
        return ''
    content = ''.join(inline(child, directives) for child in node.children)
    label = clean(content)
    if node.name == 'a':
        if node.get('href') and label:
            url = local_link(node['href'])
            cue = url if url.startswith(('http://', 'https://')) or re.search(r'\.html(?:#.*)?$', url) else 'link ' + url
            directives.append((cue, label))
        if node.get('id') or node.get('name'):
            directives.append(('id', node.get('id', node.get('name'))))
    cue = {'b': 'bold', 'strong': 'bold', 'i': 'italics', 'em': 'italics', 'u': 'underline', 'sup': 'superscript', 'sub': 'subscript', 'code': 'code'}.get(node.name)
    if cue and label:
        directives.append((cue, label))
    return content

BLOCKS = {'p', 'div', 'ol', 'ul', 'li', 'blockquote', 'img', 'iframe', 'hr', 'h1', 'h2', 'h3', 'h4'}

def body_scroll(body, slug):
    lines = []
    def emit(nodes, prefix='', depth=0):
        directives = []
        text = clean(''.join(inline(node, directives) for node in nodes)).strip()
        if not text:
            return
        if not prefix and (text.startswith('free_feature') or re.match(r'^(?:[0-9]+[.)]|[-*])\s', text) or text.startswith(('http://', 'https://'))):
            prefix = 'h2' if ('bold', text) in directives else 'p'  # Escape command-like prose; promote whole bold numbered headings.
            if prefix == 'h2':
                directives.remove(('bold', text))
        # Bare prose uses Scroll's catchall; explicit cues are for structural elements.
        if re.fullmatch(r'h[1-4]', prefix):
            prefix = '#' * int(prefix[1])
        lines.append(' ' * depth + (prefix + ' ' if prefix else '') + text)
        if any(cue.startswith(('http', 'link ')) or '.html' in cue for cue, _ in directives) or re.search(r'https?://|www\.|@', text):
            lines.append(' ' * (depth + 1) + 'linkify false')
        for cue, label in dict.fromkeys(directives):
            selector = '' if label == text and cue != 'id' else ' ' + label
            lines.append(' ' * (depth + 1) + cue + selector)
        lines.append('')

    def walk(parent, depth=0, prefix=''):
        pending = []
        def flush():
            emit(pending, prefix, depth)
            pending.clear()
        for node in parent.children:
            if not isinstance(node, NavigableString) and node.name in ('script', 'style'):
                continue
            if not isinstance(node, NavigableString) and node.name == 'br':
                flush()
                continue
            if isinstance(node, NavigableString) or node.name not in BLOCKS:
                pending.append(node)
                continue
            flush()
            if node.name == 'anchorpoint':
                lines.extend(['span', ' id ' + node['id'], ''])
            elif node.name == 'img':
                url = urljoin(current_url, node.get('data-large-src') or node.get('src') or '')
                if not url:
                    continue
                ext = Path(urlparse(url).path).suffix.lower()
                if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
                    ext = '.jpg'
                filename = 'assets/' + hashlib.sha256(url.encode()).hexdigest()[:16] + ext
                assets[url] = filename
                if url in unavailable:
                    lines.extend(indent(element('', 'An image in the original post is no longer available. Original image link ↗', attrs={'addClass': 'unavailable-image'}, children=[url + ' Original image link ↗']), depth).splitlines())
                    continue
                lines.extend([' ' * depth + filename, ' ' * (depth + 1) + 'alt ' + (node.get('alt') or 'Image from ' + title_by_slug[slug]), ''])
            elif node.name == 'iframe':
                url = node.get('src', '').replace('http://', 'https://')
                lines.extend([' ' * depth + 'Read the embedded document.', ' ' * (depth + 1) + url, ''])
            elif node.name == 'hr':
                lines.extend([' ' * depth + '---', ''])
            elif node.name in ('ol', 'ul'):
                for number, item in enumerate(node.find_all('li', recursive=False), 1):
                    walk(item, depth, f'{number}.' if node.name == 'ol' else '-')
            elif node.name == 'blockquote':
                walk(node, depth, '>')
            else:
                walk(node, depth, node.name if node.name in ('li', 'h1', 'h2', 'h3', 'h4') else prefix)
        flush()
    walk(body)
    # An unindented blank closes a Scroll tree: never put one between list items.
    return '\n'.join(line for i, line in enumerate(lines) if line or (i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].startswith(' '))).strip()


def extract_body(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    candidates = soup.select('td > font')
    if not candidates: candidates = soup.select('font')
    body = max(candidates, key=lambda n: len(n.get_text()))
    for node in body.select('script,style'): node.decompose()
    for node in body.find_all(string=lambda s: isinstance(s, Comment)): node.extract()
    for node in list(body.select('font,xa,ax,nota,ximg')): node.unwrap()
    for node in list(body.select('a[name],a[id]')):
        marker=soup.new_tag('anchorpoint', id=node.get('name',node.get('id')))
        node.insert_before(marker)
        if node.get('href'): node.attrs.pop('name',None); node.attrs.pop('id',None)
        else: node.unwrap()
    return body

def fetch(url, dest):
    if dest.exists() and dest.stat().st_size: return
    subprocess.run(['curl','-fLsS','--retry','3','--max-time','90',url,'-o',str(dest)],check=True)

if __name__ == '__main__':
    from concurrent.futures import ThreadPoolExecutor
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--media', action='store_true')
    args = parser.parse_args()
    (ROOT/'originals').mkdir(exist_ok=True); (ROOT/'assets').mkdir(exist_ok=True)
    if args.download:
        (ROOT/'originals/articles.html').unlink(missing_ok=True)
        fetch(BASE+'articles.html', ROOT/'originals/articles.html')
    index=BeautifulSoup((ROOT/'originals/articles.html').read_bytes(),'html.parser')
    entries=[]
    for a in index.select('td > font > a[href]'):
        href=a['href']
        if a.parent.parent.find('img',src=re.compile('the-reddits')) and re.fullmatch(r'[\w-]+\.html',href):
            entries.append({'slug':Path(href).stem,'title':a.get_text(),'url':BASE+href})
    entries=list({p['slug']:p for p in entries}.values())
    if args.download:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda p: fetch(p['url'],ROOT/'originals'/(p['slug']+'.html')),entries))
    slugs={p['slug'] for p in entries}; title_by_slug={p['slug']:p['title'] for p in entries}
    posts=[]
    for p in entries:
        current_url=p['url']
        body=extract_body((ROOT/'originals'/(p['slug']+'.html')).read_bytes())
        text=clean(body.get_text(' ',strip=True))
        match=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',text[:700])
        if match:
            date=datetime.strptime(match.group(0),'%B %Y').strftime('%Y-%m-01')
        else:
            year=re.search(r'\b(19\d{2}|20\d{2})\b',text[:700])
            date=(year.group(0) if year else '2001')+'-01-01'
        p.update(date=date,year=int(date[:4]),datePrecision='month' if match else 'year' if year else 'unknown',text=text,original=p['slug']+'.html',scrollFile=p['slug']+'.scroll')
        source=f"title {esc(p['title'])}\ndescription {esc(text[:160])}\ndate {date}\n// publicationLabel {match.group(0) if match else year.group(0) if year else 'Date not stated'}\ncanonicalUrl {p['url']}\n\npost-header.scroll\n\n{body_scroll(body,p['slug'])}\n\nfooter.scroll\n"
        (ROOT/p['scrollFile']).write_text(source)
        posts.append(p)
    (ROOT/'archive.json').write_text(json.dumps(posts,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'originals/media.json').write_text(json.dumps(assets,indent=2)+'\n')
    if args.media:
        failures=[]
        for url,filename in assets.items():
            try: fetch(url,ROOT/filename)
            except subprocess.CalledProcessError: failures.append({'url':url,'file':filename})
        failure_file.write_text(json.dumps(failures,indent=2)+'\n')
    print('Converted',len(posts),'essays;',len(assets),'images')
    print('Dates needing review:',[(p['slug'],p['datePrecision'],p['text'][:100]) for p in posts if p['datePrecision']!='month'])
