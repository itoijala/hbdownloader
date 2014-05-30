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

def login():
    global token
    if os.path.exists("login-token"):
        token = open("login-token", "r").read()
    else:
        print("Login:")
        username = input("Email: ")
        password = getpass.getpass("Password: ")
        data = {"username": username, "password": password}
        response = session.post("https://www.humblebundle.com/login", data=data, allow_redirects=False)
        token = response.cookies["_simpleauth_sess"]
        open("login-token", "w").write(token)
    session.cookies.update({"_simpleauth_sess": token})

def get_keys():
    print("Getting keys…", end="\r")
    response = session.get("https://www.humblebundle.com/home", allow_redirects=False)
    regex = re.compile(r'gamekeys: \[(?:"([a-zA-Z0-9]+)", )*"([a-zA-Z0-9]+)"\]')
    match = regex.search(response.text)
    print("Getting keys… done")
    return [k.strip('"') for k in match.group()[11:-1].split(", ")]

def get_key_data(key):
    response = requests.get("https://www.humblebundle.com/api/v1/order/{}".format(key), cookies={"_simpleauth_sess": token})
    return response.json()

def hash_file(path):
    f = open(path, 'rb')
    block_size = 8 * 1024**2
    md5 = hashlib.md5()
    while True:
        data = f.read(block_size)
        if not data:
            break
        md5.update(data)
    return {
            "name": os.path.basename(path),
            "size": os.path.getsize(path),
            "md5": md5.hexdigest(),
            }

def parse_products(data):
    products = dict()
    for p in data["subproducts"]:
        product = {
                "machine_name": p["machine_name"].strip(),
                "human_name": p["human_name"].strip(),
                }
        downloads = dict()
        for d in p["downloads"]:
            platform = dict()
            for ds in d["download_struct"]:
                if "url" in ds:
                    name = ds["name"]
                    if d["platform"] == "linux" and "arch" in ds:
                        name = "{}-bit {}".format(ds["arch"], name)
                    name = normalise_linux(name)
                    platform[name] = {
                            "name": ds["url"]["web"].split("?")[0].split("/")[-1],
                            "url": ds["url"]["web"],
                            "size": ds["file_size"],
                            "md5": ds["md5"],
                            }
            if len(platform) > 0:
                downloads[d["platform"]] = platform
        if len(downloads) > 0:
            product["downloads"] = downloads
            products[product["machine_name"]] = product
    return products

# https://stackoverflow.com/a/1094933
def sizeof_fmt(num):
    for x in ['B','KiB','MiB','GiB']:
        if num < 1024 and num > -1024:
            return "{:>6.1f} {:>3s}".format(num, x)
        num /= 1024
    return "{:>6.1f} {}".format(num, 'TiB')

def download_file(url, path):
    chunk_size = 100 * 1024
    start = time.perf_counter()
    if os.path.exists(path + ".part"):
        downloaded = os.path.getsize(path + ".part")
    else:
        downloaded = 0
    with open(path + ".part", "ab") as fd:
        response = session.get(url, stream=True, headers={"Range": "bytes={}-".format(downloaded)})
        total = int(response.headers["Content-Range"].split("/")[-1])
        remaining = int(response.headers["Content-Length"])
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                fd.write(chunk)
                downloaded += len(chunk)
                speed = (downloaded - (total - remaining)) / (time.perf_counter() - start)
                print(r"{} / {} {}/s {:>8s}".format(sizeof_fmt(downloaded), sizeof_fmt(total).strip(), sizeof_fmt(speed), str(datetime.timedelta(seconds=int((total - downloaded) / speed)))), end="\r")
    if os.path.exists(path):
        os.rename(path, path + ".old")
    os.rename(path + ".part", path)
    print()

def process_file(game, download):
    print(game + "/" + download["name"])
    d = False
    if not os.path.exists("dl/links/" + game + "/" + download["name"]):
        d = True
    if not d:
        if os.path.getsize("dl/links/" + game + "/" + download["name"]) != download["size"]:
            d = True
    if not d:
        if os.path.exists("dl/json/" + game + "/" + download["name"] + ".json"):
            hashes = json.load(open("dl/json/" + game + "/" + download["name"] + ".json"))
        else:
            hashes = hash_file("dl/links/" + game + "/" + download["name"])
            json.dump(hashes, open("dl/json/" + game + "/" + download["name"] + ".json", "w"), indent=2)
        if hashes["md5"] != download["md5"]:
            d = True
    if d:
        download_file(download["url"], "dl/links/" + game + "/" + download["name"])
        json.dump({
                "name": download["name"],
                "size": download["size"],
                "md5": download["md5"],
                }, open("dl/json/" + game + "/" + download["name"] + ".json", "w"), indent=2)

def filter_all(files):
    return files

def filter_audio(files):
    if "FLAC" in files:
        return ["FLAC"]
    return files

def filter_none(files):
    return []

def filter_windows(files):
    if "Download 1080p" in files:
        return ["Download 1080p"]
    return files

def normalise_linux(name):
    for r, s in [
            (r"^\.i386\.",        "32-bit ."),
            (r"^\.x86_64\.",      "64-bit ."),
            (r"32-bit 32-bit",    "32-bit"),
            (r"64-bit 64-bit",    "64-bit"),
            (r"i386",             ""),
            (r"x86_64",           ""),
            (r"^AIR$",            "Air"),
            (r"\.tgz",            ".tar.gz"),
            (r"^Mojo Installer$", ".mojo.run"),
            (r"^tar\.gz$",        ".tar.gz"),
            (r"^bin$",            ".bin"),
            (r"^Download ",       ""),
            (r"^Native ",         ""),
            (r" Package$",        ""),
            (r" \(beta\)$",       ""),
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
    if "1080p" in files:
        return ["1080p"]
    for f in list(files):
        if re.search(r"^64", f):
            files = remove_matching(r"^32{}$".format(re.escape(re.sub(r"^64", "", f))), files)
        if re.search(r"\.(zip|tar(\.(gz|bz2))?|deb|rpm)$", f):
            files = remove_matching(r"\.(mojo\.run|bin|sh)$|^Installer$", files)
        if re.search(r"\.(zip|tar(\.(gz|bz2))?)$", f):
            files = remove_matching(r"\.(deb|rpm)$", files)
        if re.search(r"\.deb$", f):
            files = remove_matching(r"\.rpm$", files)
        if re.search(r"\.tar(\.(gz|bz2))?$", f):
            files = remove_matching(r"\.zip$", files)
        if f == ".mojo.run":
            files = remove_matching(r"^\.bin$", files)
    return files

filter_table = {
        "android": filter_all,
        "windows": filter_windows,
        "mac": filter_none,
        "linux": filter_linux,
        "audio": filter_audio,
        "ebook": filter_all,
        }

def process_platform(game, platform, downloads):
    files = filter_table[platform](downloads.keys())
    for f in sorted(files):
        process_file(game, downloads[f])

if __name__ == "__main__":
    login()
    keys = get_keys()

    print("Getting key data ({} keys)…".format(len(keys)), end="\r")
    pool = multiprocessing.Pool(len(keys))
    data = pool.map(get_key_data, keys)
    pool.close()
    pool.join()
    print("Getting key data ({} keys)… done".format(len(keys)))

    products = dict()
    for d in data:
        p = parse_products(d)
        for g in p:
            if g not in products:
                products[g] = p[g]

    os.makedirs("dl/links", exist_ok=True)
    os.makedirs("dl/json", exist_ok=True)
    for p in sorted(products):
        stem = re.sub("(_(soundtrack_only|no_soundtrack|soundtrack|android_and_pc|android|pc|bundle|boxart))+$", "", p)
        if stem != p and not os.path.exists("dl/links/" + p):
            os.symlink(stem, "dl/links/" + p, target_is_directory=True)
        if not os.path.exists("dl/links/" + stem):
            os.symlink("../" + stem, "dl/links/" + stem, target_is_directory=True)
        if stem != p and not os.path.exists("dl/json/" + p):
            os.symlink(stem, "dl/json/" + p, target_is_directory=True)
        if not os.path.exists("dl/json/" + stem):
            os.makedirs("dl/json/" + stem)
        dirname = "dl/links/" + p
        while os.path.islink(dirname):
            dirname = os.path.join(os.path.dirname(dirname), os.readlink(dirname))
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        for platform in sorted(products[p]["downloads"]):
            process_platform(p, platform, products[p]["downloads"][platform])
