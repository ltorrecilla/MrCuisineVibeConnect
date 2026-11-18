#!/usr/bin/env python3
"""Archive official French Monsieur Cuisine Connect recipes; Python 3.9+."""
import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

API = 'https://mc-api.tecpal.com'
HEADERS = {'Accept': 'application/json', 'Accept-Language': 'fr-FR',
           'device-type': 'web', 'User-Agent': 'ToqueRecipeArchive/1.0'}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_bytes(raw)
    temp.replace(path)


def save(path, obj):
    atomic(path, (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def valid_id(value):
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ValueError('Invalid recipe ID')
    return value


class Client:
    def __init__(self, delay=0.5):
        self.delay = delay
        self.last = 0.0

    def get(self, url, image=False):
        headers = {'User-Agent': HEADERS['User-Agent']} if image else HEADERS
        limit = 20 * 1024 * 1024 if image else 8 * 1024 * 1024
        for attempt in range(4):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                with urlopen(Request(url, headers=headers), timeout=30) as response:
                    if response.status != 200:
                        raise ValueError(f'Unexpected HTTP status {response.status}')
                    if image and not response.headers.get_content_type().startswith('image/'):
                        raise ValueError('Image URL did not return an image')
                    raw = response.read(limit + 1)
                    if not raw or len(raw) > limit:
                        raise ValueError('Empty or oversized response')
                    return raw
            except HTTPError as exc:
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise
                wait = 5 * (attempt + 1)
                retry = exc.headers.get('Retry-After', '')
                if retry:
                    try:
                        wait = max(wait, float(retry))
                    except ValueError:
                        try:
                            wait = max(wait, parsedate_to_datetime(retry).timestamp() - time.time())
                        except (ValueError, TypeError):
                            pass
                if attempt == 3:
                    raise
                print(f'HTTP {exc.code}; retrying in {wait:.0f}s', flush=True)
                time.sleep(wait)
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))


def catalogue(client, limit):
    entries = {}
    total = pages = None
    page = 1
    while pages is None or page <= pages:
        query = urlencode({'filters[deviceTypes][]': 'MC2.0', 'filters[source][]': 'official'})
        raw = client.get(f'{API}/api/v2/recipes/page/{page}/size/100?{query}')
        data = json.loads(raw)['data']
        if not isinstance(data.get('recipes'), list):
            raise ValueError('Catalogue is missing recipes')
        current = (data.get('total'), data.get('totalPage'))
        if any(type(n) is not int or n < 1 for n in current) or current[1] > 10000:
            raise ValueError('Invalid catalogue counts')
        if total is None:
            total, pages = current
            print(f'API reports {total} French Connect recipes across {pages} pages.', flush=True)
        elif current != (total, pages):
            raise ValueError('Catalogue changed during pagination; rerun to get a consistent list')
        if not data['recipes']:
            raise ValueError('Unexpected empty catalogue page')
        for entry in data['recipes']:
            key = valid_id(entry['id'])
            if key in entries:
                raise ValueError('Duplicate ID during pagination; rerun')
            entries[key] = entry
        print(f'Catalogue page {page}/{pages}', flush=True)
        if limit and len(entries) >= limit:
            return dict(list(entries.items())[:limit]), total
        page += 1
    if len(entries) != total:
        raise ValueError(f'Catalogue incomplete: {len(entries)} unique IDs, expected {total}')
    return entries, total


def image_urls(recipe):
    # Known API image fields; leave every other URL untouched in the raw JSON.
    urls = set()
    for field in ('thumbnail', 'detailsImage'):
        values = recipe.get(field) or {}
        if isinstance(values, dict):
            urls.update(v for v in values.values() if isinstance(v, str) and v.startswith('https://'))
    return sorted(urls)


def archive(args, client):
    root = args.output.resolve() / 'fr-FR'
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {
        'version': 1, 'language': 'fr-FR', 'device': 'MC2.0', 'source': 'official', 'recipes': {}}
    if (manifest.get('version'), manifest.get('language'), manifest.get('device'), manifest.get('source')) != (1, 'fr-FR', 'MC2.0', 'official'):
        raise ValueError('Incompatible manifest; choose a different output folder')
    entries, total = catalogue(client, args.test_count if args.mode == 'test' else None)
    # Only a fully validated full listing may mark archived recipes unavailable.
    if args.mode == 'full':
        for key, record in manifest['recipes'].items():
            record['available'] = key in entries
    manifest['last_run'] = {'started_at': now(), 'mode': args.mode, 'api_total': total,
                            'selected': len(entries), 'complete': False}
    save(root / f'catalogue-{args.mode}.json', {'fetched_at': now(), 'total': total, 'recipes': list(entries.values())})
    save(manifest_path, manifest)
    failures = {}
    downloaded = skipped = 0
    for index, (key, entry) in enumerate(entries.items(), 1):
        record = manifest['recipes'].setdefault(key, {})
        record.update(available=True, last_seen_at=now())
        path = root / 'recipes' / f'{key}.json'
        url = f'{API}/api/v2/recipes/{key}'
        try:
            raw = path.read_bytes() if path.exists() else b''
            unchanged = (not args.refresh and entry.get('lastUpdated') and
                         record.get('source_updated') == entry['lastUpdated'] and
                         raw and digest(raw) == record.get('sha256'))
            if unchanged:
                skipped += 1
            else:
                raw = client.get(url)
            recipe = json.loads(raw)['data']['recipe']
            if valid_id(recipe['id']) != key or not recipe.get('name') or not isinstance(recipe.get('servingSizes'), list) or not recipe['servingSizes']:
                raise ValueError('Missing recipe fields or mismatched recipe ID')
            if not unchanged:
                atomic(path, raw)  # Store response bytes without conversion or translation.
                record.update(source_url=url, source_updated=entry.get('lastUpdated'),
                              downloaded_at=now(), sha256=digest(raw))
                downloaded += 1
            if args.images:
                images = record.setdefault('images', {})
                for image_url in image_urls(recipe):
                    extension = Path(urlsplit(image_url).path).suffix.lower()
                    if extension not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                        extension = '.img'
                    filename = digest(image_url.encode()) + extension
                    image_path = root / 'images' / filename
                    old = images.get(image_url, {})
                    if not image_path.exists() or digest(image_path.read_bytes()) != old.get('sha256'):
                        image_raw = client.get(image_url, image=True)
                        atomic(image_path, image_raw)
                        images[image_url] = {'file': f'images/{filename}', 'sha256': digest(image_raw)}
            record.pop('error', None)
            print(f'[{index}/{len(entries)}] {key}: {"cached" if unchanged else "saved"}', flush=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures[key] = str(exc)
            record['error'] = str(exc)
            print(f'[{index}/{len(entries)}] {key}: FAILED: {exc}', file=sys.stderr, flush=True)
        save(manifest_path, manifest)
    manifest['last_run'].update(finished_at=now(), complete=not failures,
                                downloaded=downloaded, skipped=skipped, failures=failures)
    save(manifest_path, manifest)
    print(f'Done: {downloaded} downloaded, {skipped} cached, {len(failures)} failed. Output: {root}')
    return 1 if failures else 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('test', 'full'), default='test', help='test (default): first 5 recipes; full: entire French catalogue')
    p.add_argument('--test-count', type=int, default=5, help='sample size in test mode (default: 5)')
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'downloads')
    p.add_argument('--images', action='store_true', help='also download thumbnail and detail images at their original resolution')
    p.add_argument('--refresh', action='store_true', help='refetch selected recipe JSON even if unchanged')
    p.add_argument('--delay', type=float, default=0.5, help='minimum seconds between requests (at least 0.25)')
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.test_count < 1 or not math.isfinite(args.delay) or args.delay < 0.25:
        p.error('--test-count must be positive and --delay must be finite and at least 0.25')
    try:
        return archive(args, Client(args.delay))
    except KeyboardInterrupt:
        print('\nInterrupted. Rerun the same command to resume.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Download stopped: {exc}. Existing recipe files are retained.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
