#!/usr/bin/env python3

import datetime
import getpass
import hashlib
import json
import multiprocessing
import os
import os.path
import re
import sys
import time

import requests

session = requests.Session()

USE_CACHE = len(sys.argv) == 2 and sys.argv[1] == '--use-cache'

def get_csrf():
    response = session.get('https://www.humblebundle.com/')
    regex = re.compile(r'''<input\s[^>]*class=("|')csrftoken("|')\s[^>]*>''')
    match = regex.search(response.text)
    regex = re.compile(r'''value=("|')([^"']+)("|')''')
    match = regex.search(match.group(0))
    return match.group(2)

def login():
    global token
    if os.path.exists('login-cookies.json'):
        with open('login-cookies.json', 'r') as f:
            session.cookies.update(json.load(f))
    response = session.get('https://www.humblebundle.com/home', allow_redirects=False)
    if response.status_code != 200:
        print('Log in in firefox and open the developer tools network tab. Reload the page. Right-click the top request and click "copy request headers". Paste here and press Enter twice:')
        headers = []
        while True:
            line = input()
            if line:
                headers.append(line)
            else:
                break
        for header in headers:
            if header.lower().startswith('cookie:'):
                cookies = header
        cookies = dict([c.strip().split('=', 1) for c in cookies[len('cookie:'):].split(';')])
        session.cookies.clear()
        session.cookies.update(cookies)
        with open('login-cookies.json', 'w') as f:
            json.dump(dict(session.cookies), f, indent=2)

def get_keys():
    if USE_CACHE and os.path.exists('cache/keys.json'):
        with open('cache/keys.json', 'r') as f:
            keys = json.load(f)
    else:
        print('Getting keys…', end='\r')
        response = session.get('https://www.humblebundle.com/home/library', allow_redirects=False)
        regex = re.compile(r'"gamekeys": \[((?:"[a-zA-Z0-9]+"(?:, )?)+)\],')
        match = regex.search(response.text)
        keys = [k.strip('"') for k in match[1].split(', ')]
        print('Getting keys… done')
        if USE_CACHE:
            os.makedirs('cache', exist_ok=True)
            with open('cache/keys.json', 'w') as f:
                json.dump(keys, f, indent=2)
    return keys

def get_key_data(key):
    if USE_CACHE and os.path.exists('cache/' + key + '.json'):
        with open('cache/' + key + '.json', 'r') as f:
            data = json.load(f)
    else:
        response = requests.get('https://www.humblebundle.com/api/v1/order/{}?all_tpkds=true'.format(key), cookies=session.cookies)
        data = response.json()
        if USE_CACHE:
            with open('cache/' + key + '.json', 'w') as f:
                json.dump(data, f, indent=2)
    return data

def hash_file(path):
    with open(path, 'rb') as f:
        block_size = 8 * 1024**2
        md5 = hashlib.md5()
        while True:
            data = f.read(block_size)
            if not data:
                break
            md5.update(data)
    return {
        'name': os.path.basename(path),
        'size': os.path.getsize(path),
        'md5': md5.hexdigest(),
    }

def parse_products(data):
    products = dict()
    for p in data['subproducts']:
        product = {
            'machine_name': p['machine_name'].strip(),
            'human_name': p['human_name'].strip(),
        }
        downloads = dict()
        for d in p['downloads']:
            platform = dict()
            for ds in d['download_struct']:
                if 'url' in ds:
                    name = ds['name']
                    if d['platform'] == 'linux' and 'arch' in ds:
                        name = '{}-bit {}'.format(ds['arch'], name)
                    name = normalise_linux(name)
                    platform[name] = {
                        'name': ds['url']['web'].split('?')[0].split('/')[-1],
                        'url': ds['url']['web'],
                        'size': ds['file_size'],
                        'md5': ds['md5'],
                    }
            if len(platform) > 0:
                if d['platform'] not in downloads:
                    downloads[d['platform']] = dict()
                for download in platform:
                    downloads[d['platform']][download] = platform[download]
        if len(downloads) > 0:
            product['downloads'] = downloads
            products[product['machine_name']] = product
    return products

# https://stackoverflow.com/a/1094933
def sizeof_fmt(num):
    for x in ['B','KiB','MiB','GiB']:
        if num < 1024 and num > -1024:
            return '{:>6.1f} {:>3s}'.format(num, x)
        num /= 1024
    return '{:>6.1f} {}'.format(num, 'TiB')

def download_file(url, path):
    chunk_size = 100 * 1024
    start = time.perf_counter()
    if os.path.exists(path + '.part'):
        downloaded = os.path.getsize(path + '.part')
    else:
        downloaded = 0
    with open(path + '.part', 'ab') as fd:
        response = session.get(url, stream=True, headers={'Range': 'bytes={}-'.format(downloaded)})

        if response.status_code >= 300:
            print('Error: {}'.format(response))
            fd.close()
            os.remove(path + '.part')
            return

        total = int(response.headers['Content-Range'].split('/')[-1])
        remaining = int(response.headers['Content-Length'])
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                fd.write(chunk)
                downloaded += len(chunk)
                speed = (downloaded - (total - remaining)) / (time.perf_counter() - start)
                print(r'{} / {} {}/s {:>8s}'.format(sizeof_fmt(downloaded), sizeof_fmt(total).strip(), sizeof_fmt(speed), str(datetime.timedelta(seconds=int((total - downloaded) / speed)))), end='\r')
    if os.path.exists(path):
        os.rename(path, path + '.old')
    os.rename(path + '.part', path)
    print()

def process_file(game, download):
    print(game + '/' + download['name'])
    d = False
    if not os.path.exists(game + '/' + download['name']):
        d = True
    if not d:
        if os.path.exists('json/' + game + '/' + download['name'] + '.json'):
            with open('json/' + game + '/' + download['name'] + '.json', 'r') as f:
                hashes = json.load(f)
        else:
            hashes = hash_file(game + '/' + download['name'])
            with open('json/' + game + '/' + download['name'] + '.json', 'w') as f:
                json.dump(hashes, f, indent=2)
        if hashes['md5'] != download['md5']:
            d = True
    if d:
        download_file(download['url'], game + '/' + download['name'])
        with open('json/' + game + '/' + download['name'] + '.json', 'w') as f:
            json.dump({
                'name': download['name'],
                'size': download['size'],
                'md5': download['md5'],
            }, f, indent=2)

def filter_all(files):
    return files

def filter_audio(files):
    if 'FLAC' in files:
        return ['FLAC']
    return files

def filter_none(files):
    return []

def filter_windows(files):
    if '1080p' in files:
        return ['1080p']
    return files

def normalise_linux(name):
    for r, s in [
        (r'^\.i386\.',        '32-bit .'),
        (r'^\.x86_64\.',      '64-bit .'),
        (r'32-bit 32-bit',    '32-bit'),
        (r'64-bit 64-bit',    '64-bit'),
        (r'i386',             ''),
        (r'x86_64',           ''),
        (r'^AIR$',            'Air'),
        (r'\.tgz',            '.tar.gz'),
        (r'^Mojo Installer$', '.mojo.run'),
        (r'^tar\.gz$',        '.tar.gz'),
        (r'^bin$',            '.bin'),
        (r'^Download ',       ''),
        (r'^Native ',         ''),
        (r' Package$',        ''),
        (r' \(beta\)$',       ''),
        (r' tar.gz$',         ' .tar.gz'),
    ]:
        name = re.sub(r, s, name)
    return name

def remove_matching(pattern, l):
    new = list(l)
    for x in l:
        if re.search(pattern, x):
            new.remove(x)
    return new

def filter_linux(files):
    files = list(files)
    if '1080p' in files:
        return ['1080p']
    for f in list(files):
        if re.search(r'^64', f):
            files = remove_matching(r'^32{}$'.format(re.escape(re.sub(r'^64', '', f))), files)
        if re.search(r'\.(zip|tar(\.(gz|bz2))?)$', f):
            files = remove_matching(r'\.(mojo\.run|bin|sh|deb|rpm)$|^Installer$|^Download$', files)
        if re.search(r'\.tar(\.(gz|bz2))?$', f):
            files = remove_matching(r'\.zip$', files)
        if re.search(r'\.(mojo\.run|bin|sh)$|^Installer$|^Download$', f):
            files = remove_matching(r'\.(deb|rpm)$', files)
        if f == '.mojo.run':
            files = remove_matching(r'^\.bin$', files)
        if re.search(r'\.deb$', f):
            files = remove_matching(r'\.rpm$', files)
    return files

filter_table = {
    'android': filter_all,
    'windows': filter_windows,
    'mac': filter_none,
    'linux': filter_linux,
    'audio': filter_audio,
    'ebook': filter_all,
    'video': filter_all,
}

def process_platform(game, platform, downloads):
    files = filter_table[platform](downloads.keys())
    paths = []
    for f in sorted(files):
        process_file(game, downloads[f])
        paths.append(os.path.realpath(game + '/' + downloads[f]['name']))
    return paths

if __name__ == '__main__':
    login()
    keys = get_keys()

    print('Getting key data ({} keys)…'.format(len(keys)), end='\r')
    pool = multiprocessing.Pool(len(keys))
    data = pool.map(get_key_data, keys)
    pool.close()
    pool.join()
    print('Getting key data ({} keys)… done'.format(len(keys)))

    products = dict()
    for d in data:
        p = parse_products(d)
        for g in p:
            if g not in products:
                products[g] = p[g]

    os.makedirs('json', exist_ok=True)
    paths = []
    for p in sorted(products):
        stem = re.sub('(_(soundtrack_only|no_soundtrack|soundtrack|android_and_pc|android|pc|bundle|boxart|dlc))+$', '', p)
        if stem != p and not os.path.exists(p):
            os.symlink(stem, p, target_is_directory=True)
            os.makedirs(stem, exist_ok=True)
        if not (os.path.exists(p) and os.path.islink(p) and os.readlink(p) == '/dev/null'):
            os.makedirs(p, exist_ok=True)
            os.makedirs('json/' + p, exist_ok=True)
            for platform in sorted(products[p]['downloads']):
                paths += process_platform(p, platform, products[p]['downloads'][platform])
    print()
    print('Orphans:')
    printed = []
    for p in sorted(products):
        if not (os.path.exists(p) and os.path.islink(p) and os.readlink(p) == '/dev/null'):
            files = [os.path.join(p, f) for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
            for f in sorted(files):
                real = os.path.realpath(f)
                if real not in paths and real not in printed:
                    print(f)
                    printed.append(real)
