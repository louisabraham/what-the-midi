"""Downloads the archives of a source to data/archives/<source>/.

Downloads resume (curl -C -) and are skipped when the file is complete.
Gated Hugging Face datasets need the HF_TOKEN environment variable.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from sources import HF, Source

FREE_SPACE_MARGIN = 5 * 2**30  # keep 5 GiB free on the disk


def _get_json(url, token=None):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _head_size(url):
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        size = response.headers.get("Content-Length")
    return int(size) if size else None


def files_of(source: Source):
    """[(url, filename, size in bytes or None)]"""
    out = []
    for url in source.urls:
        out.append((url, url.rsplit("/", 1)[1], _head_size(url)))
    if source.archive_org:
        import re

        meta = _get_json(f"https://archive.org/metadata/{source.archive_org}")
        for f in meta["files"]:
            if f.get("source") == "original" and re.search(source.archive_org_pattern, f["name"], re.I):
                url = (f"https://archive.org/download/{source.archive_org}/"
                       f"{urllib.parse.quote(f['name'])}")
                out.append((url, f["name"], int(f["size"])))
    if source.huggingface:
        tree = _get_json(f"https://huggingface.co/api/datasets/{source.huggingface}/tree/main?recursive=true")
        sizes = {f["path"]: f.get("size") for f in tree if f.get("type") == "file"}
        for name in source.huggingface_files:
            url = f"{HF}/{source.huggingface}/resolve/main/{urllib.parse.quote(name)}"
            out.append((url, name, sizes.get(name)))
    return out


def download(source: Source, archives_dir: Path):
    dest_dir = archives_dir / source.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") if source.gated else None
    if source.gated and not token:
        raise SystemExit(f"{source.name}: set HF_TOKEN, and accept the terms at {source.homepage}")

    files = files_of(source)
    total = sum(size or 0 for _, _, size in files)
    print(f"{source.name}: {len(files)} files, {total / 1e9:.2f} GB")
    for url, name, size in files:
        dest = dest_dir / name
        have = dest.stat().st_size if dest.exists() else 0
        if size is not None and have == size:
            continue
        need = (size or 0) - have
        free = shutil.disk_usage(dest_dir).free
        if need + FREE_SPACE_MARGIN > free:
            raise SystemExit(f"not enough disk space for {name}: need {need / 1e9:.1f} GB, "
                             f"free {free / 1e9:.1f} GB (margin {FREE_SPACE_MARGIN / 1e9:.0f} GB)")
        print(f"  {name} ({(size or 0) / 1e6:.0f} MB)", flush=True)
        cmd = ["curl", "-L", "--fail", "--retry", "5", "--retry-delay", "5", "--retry-all-errors",
               "-C", "-", "-sS", "-o", str(dest), url]
        if token:
            cmd[1:1] = ["-H", f"Authorization: Bearer {token}"]
        for attempt in range(1, 21):  # each attempt resumes where the last one stopped
            if subprocess.run(cmd).returncode == 0:
                break
            print(f"  {name}: download interrupted (attempt {attempt}), resuming", flush=True)
            time.sleep(10)
        else:
            raise SystemExit(f"{name}: download failed 20 times")
        if size is not None and dest.stat().st_size != size:
            raise SystemExit(f"{name}: size {dest.stat().st_size}, expected {size}")
    return [dest_dir / name for _, name, _ in files]
