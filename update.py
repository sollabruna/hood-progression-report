#!/usr/bin/env python3
"""Keep this installation up to date with the latest GitHub release.

Called by the launchers before the app starts.  Two modes:

* The folder is a git clone  -> fetch tags and check out the newest ``vX.Y.Z``.
* Otherwise                   -> ask the GitHub API for the latest release and,
                                 if newer than ``VERSION``, download its zip
                                 and replace the program files in place.

Only program files are touched (Python code, assets, launchers, docs).  User
outputs, ``.venv`` and anything else are left alone.  Any failure (no network,
API limit, private repo without credentials) is reported and the current
version keeps running.

Usage:  python update.py            apply an update if one exists
        python update.py --check    only print whether an update exists
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "sollabruna/hood-progression-report"
HERE = Path(__file__).resolve().parent
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PROGRAM_PATHS = ("hood_progression", "assets", "app.py", "make_report.py", "batch_reports.py", "update.py",
                 "run_app.command", "run_app.bat", "requirements.txt", "VERSION", "README.md", "CHANGELOG.md", "RELEASING.md")


def current_version() -> str:
    try:
        return (HERE / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"


def _vtuple(v: str) -> tuple:
    v = v.lstrip("vV").split("-")[0]
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def latest_release() -> tuple[str, str] | None:
    """(tag, zipball_url) of the latest GitHub release, or None if unreachable."""
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "hood-progression-report"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return data["tag_name"], data["zipball_url"]
    except Exception as e:  # noqa: BLE001
        print(f"update check skipped ({type(e).__name__}: {e})")
        return None


def update_from_git() -> bool:
    try:
        subprocess.run(["git", "-C", str(HERE), "fetch", "--tags", "--quiet"], check=True, timeout=60)
        tags = subprocess.run(["git", "-C", str(HERE), "tag", "--list", "v*"], capture_output=True, text=True, check=True).stdout.split()
        if not tags:
            return False
        newest = max(tags, key=_vtuple)
        if _vtuple(newest) <= _vtuple(current_version()):
            print(f"up to date ({current_version()})")
            return False
        subprocess.run(["git", "-C", str(HERE), "checkout", "--quiet", newest], check=True, timeout=60)
        print(f"updated to {newest} (git)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"git update skipped ({type(e).__name__}: {e})")
        return False


def update_from_release(tag: str, url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hood-progression-report"})
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z, tempfile.TemporaryDirectory() as td:
            z.extractall(td)
            roots = [p for p in Path(td).iterdir() if p.is_dir()]
            if len(roots) != 1:
                raise RuntimeError("unexpected zip layout")
            src = roots[0]
            for name in PROGRAM_PATHS:
                s, d = src / name, HERE / name
                if not s.exists():
                    continue
                if s.is_dir():
                    if d.exists():
                        shutil.rmtree(d)
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
        for f in ("run_app.command", "make_report.py", "batch_reports.py", "update.py"):
            p = HERE / f
            if p.exists():
                p.chmod(p.stat().st_mode | 0o111)
        print(f"updated to {tag}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"update failed ({type(e).__name__}: {e}); keeping {current_version()}")
        return False


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    check_only = "--check" in argv
    cur = current_version()
    if (HERE / ".git").exists():
        if check_only:
            print(f"current {cur} (git clone; run update.py to fetch tags)")
            return 0
        return 0 if update_from_git() or True else 1
    rel = latest_release()
    if rel is None:
        return 0
    tag, url = rel
    if _vtuple(tag) <= _vtuple(cur):
        print(f"up to date ({cur})")
        return 0
    print(f"update available: {cur} -> {tag}")
    if check_only:
        return 0
    update_from_release(tag, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
