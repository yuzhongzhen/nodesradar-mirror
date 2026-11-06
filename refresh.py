"""Mirror public NodesRadar subscriptions as data. Never execute downloaded content."""
import base64
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import yaml

SITE = 'https://nodesradar.com/'
OUT = Path(__file__).resolve().parent / 'feeds'
LIMIT = 20 * 1024 * 1024


def check_url(url, hosts):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.port not in (None, 443):
        raise ValueError('Unexpected URL origin')


def fetch(url, hosts):
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check_url(newurl, hosts)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    check_url(url, hosts)
    with build_opener(Redirect()).open(Request(url, headers={'User-Agent': 'NodesRadarMirror/1.0'}), timeout=20) as response:
        data = response.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise ValueError('Empty or oversized response')
    return data


def discover(home, index):
    metadata = json.loads(re.search(r'<script\b[^>]*\bid=["\']nodes-data["\'][^>]*>(.*?)</script>', home, re.S)[1])
    base = metadata['site']['subBase'] if 'site' in metadata and 'subBase' in metadata['site'] else next(
        value['subBase'] for value in metadata.values() if isinstance(value, dict) and 'subBase' in value)
    filename = re.search(r'\bclash\s*:\s*\{[^}]*?\bfile\s*:\s*["\']([^"\']+\.ya?ml)["\']', home)[1]
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or not re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname):
        raise ValueError('Invalid homepage subscription base')
    hosts = {'nodesradar.com', 'www.nodesradar.com', parsed.hostname}
    rows = {}
    for path, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', index, re.S):
        url = urljoin(SITE, unescape(path))
        page = urlsplit(url)
        match = re.fullmatch(r'/sources/([a-z0-9-]+)/?', page.path)
        label = unescape(re.sub(r'<[^>]+>', '', label)).strip()
        if match and page.hostname in {'nodesradar.com', 'www.nodesradar.com'} and re.fullmatch(r'[\w.-]+/[\w.-]+', label):
            slug = re.sub(r'[^a-z0-9]+', '-', label.casefold()).strip('-')
            if not slug or (slug in rows and rows[slug] != (label, url)):
                raise ValueError('Ambiguous source identifier')
            rows[slug] = (label, url)
    if not rows:
        raise ValueError('Source directory contains no recognized repositories')
    return urljoin(base, filename), hosts, rows


def source_url(page, base, hosts):
    matches = re.findall(r'https://[^\s"\'<>]+/by-source/[^\s"\'<>]+', page)
    matches += [urljoin(base, path) for path in re.findall(r'["\'](/by-source/[^\s"\'<>]+)', page)]
    for match in matches:
        url = unescape(match)
        if re.search(r'\.(?:txt|ya?ml)(?:\?|$)', url):
            check_url(url, hosts)
            return url
    if re.search(r'Passed the test\s*</dt>\s*<dd[^>]*>\s*0\s*<', page, re.I):
        raise ValueError('Upstream has zero passing nodes')
    raise ValueError('Source subscription link missing')


def decode64(data):
    clean = re.sub(rb'\s+', b'', data)
    return base64.b64decode(clean + b'=' * (-len(clean) % 4), altchars=b'-_', validate=True)


def valid_uri(line):
    parsed = urlsplit(line)
    if parsed.scheme == 'vmess':
        node = json.loads(decode64(line.split('://', 1)[1].split('#', 1)[0].encode()))
        return bool(node.get('add') and 1 <= int(node.get('port', 0)) <= 65535 and node.get('id'))
    if parsed.scheme in {'ss', 'ssr'} and '@' not in line:
        decoded = decode64(line.split('://', 1)[1].split('#', 1)[0].split('?', 1)[0].encode()).decode()
        if parsed.scheme == 'ssr':
            fields = decoded.split('/?', 1)[0].rsplit(':', 5)
            return len(fields) == 6 and bool(fields[0]) and 1 <= int(fields[1]) <= 65535
        parsed = urlsplit('ss://' + decoded)
    if parsed.scheme in {'ss', 'vless', 'trojan', 'hysteria', 'hysteria2', 'hy2', 'tuic', 'socks', 'socks5'}:
        return bool(parsed.hostname and parsed.port and 1 <= parsed.port <= 65535)
    return False


def validate(data, clash=False):
    text = data.decode('utf-8-sig').strip()
    if clash or re.search(r'(?m)^proxies\s*:', text):
        config = yaml.load(text, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
        proxies = config.get('proxies') if isinstance(config, dict) else None
        if not isinstance(proxies, list) or not proxies:
            raise ValueError('Clash YAML has no proxies')
        if not all(isinstance(node, dict) and all(isinstance(node.get(key), str) and node[key].strip()
                   for key in ('name', 'server', 'type')) and type(node.get('port')) is int and 1 <= node['port'] <= 65535 for node in proxies):
            raise ValueError('Invalid Clash proxy entry')
        return len(proxies)
    if '://' not in text:
        text = decode64(text.encode()).decode('utf-8-sig')
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not lines or not all(valid_uri(line) for line in lines):
        raise ValueError('Invalid or unsupported subscription URI')
    return len(lines)


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    website = {'nodesradar.com', 'www.nodesradar.com'}
    recommended, hosts, sources = discover(fetch(SITE, website).decode(), fetch(urljoin(SITE, 'sources/'), website).decode())
    manifest = OUT / 'index.json'
    previous = json.loads(manifest.read_text('utf-8'))['feeds'] if manifest.exists() else {}
    entries = dict(previous)
    tasks = [('recommended', 'NodesRadar 推荐 Clash', None, 'clash.yaml')]
    tasks.extend((slug, name, detail, f'by-source/{slug}.txt') for slug, (name, detail) in sorted(sources.items()))

    def update(task):
        slug, name, detail, relative = task
        try:
            url = source_url(fetch(detail, website).decode(), recommended, hosts) if detail else recommended
            body = fetch(url, hosts)
            count = validate(body, clash=not detail)
            target = OUT / relative
            if not target.exists() or target.read_bytes() != body:
                atomic(target, body)
            return slug, {'name': name, 'path': relative, 'format': 'clash' if not detail else 'subscription',
                          'nodes': count, 'sha256': hashlib.sha256(body).hexdigest(), 'upstream': url}, None
        except Exception as error:
            return slug, None, f'{type(error).__name__}: {error}'

    successes = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for slug, entry, error in pool.map(update, tasks):
            if entry:
                entries[slug] = entry
                successes += 1
                print(f'{slug}: {entry["nodes"]} nodes')
            else:
                print(f'::warning::{slug}: {error}; '+('retained previous file' if slug in previous else 'no previous file'))
    if not successes:
        raise SystemExit('All sources failed validation; retained previous publication')
    atomic(manifest, (json.dumps({'version': 1, 'feeds': entries}, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode())
    print(f'{successes}/{len(tasks)} validated this run')


if __name__ == '__main__':
    main()
