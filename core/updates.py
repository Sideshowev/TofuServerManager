"""Is there a newer BaoBun? (for people who installed it with the installer)

Once a day the Watchdog asks GitHub for the newest release of the update
repository (core/version.py UPDATE_REPO, or the "update_repo" setting) and
remembers the answer in runtime/update_check.json. The desktop shows "BaoBun
x.y is available" with a button that downloads the installer from that release
and runs it; the web panel shows the notice with a link (installing needs
someone at the PC). Nothing is downloaded or run without that button.

The installer swaps the program file even while BaoBun runs (the old file is
renamed out of the way), and the Watchdog then moves to the new version on its
own when nobody is playing - the same way as deploy_to_host.bat.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from typing import Callable, Optional, Tuple

from core.version import UPDATE_REPO, VERSION

CHECK_EVERY = 24 * 3600
API = "https://api.github.com/repos/%s/releases/latest"
ALLOWED_HOSTS = ("https://github.com/", "https://objects.githubusercontent.com/",
                 "https://release-assets.githubusercontent.com/")
_REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_version(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", str(v or "").split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def newer(a: str, b: str) -> bool:
    """Is version a newer than b?"""
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def repo(mgr) -> str:
    r = (mgr.get_setting("update_repo", "") or UPDATE_REPO or "").strip()
    r = re.sub(r"^https?://github\.com/", "", r).strip("/")
    return r if _REPO_RX.match(r) else ""


def enabled(mgr) -> bool:
    return bool(repo(mgr)) and mgr.get_setting("update_check", True) is not False


def _state_path(mgr) -> str:
    return os.path.join(str(mgr.config_path.parent), "runtime", "update_check.json")


def cached(mgr) -> dict:
    try:
        with open(_state_path(mgr), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data["current"] = VERSION
    data["newer"] = bool(data.get("latest")) and newer(data["latest"], VERSION)
    data["enabled"] = enabled(mgr)
    return data


def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "BaoBun-update-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_asset(release: dict) -> Optional[dict]:
    """The Windows installer in a release (BaoBun-Setup-*.exe, else any .exe)."""
    assets = [a for a in release.get("assets") or [] if str(a.get("name", "")).lower().endswith(".exe")]
    setup = [a for a in assets if "setup" in a["name"].lower()]
    return (setup or assets or [None])[0]


def check(mgr, force: bool = False, fetch: Callable = None, now: Optional[float] = None) -> dict:
    """Ask GitHub (at most once a day unless forced). Returns cached()'s shape."""
    now = time.time() if now is None else now
    data = cached(mgr)
    if not enabled(mgr):
        return data
    if not force and now - float(data.get("checked") or 0) < CHECK_EVERY:
        return data
    fetch = fetch or _get_json
    out = {"checked": now, "repo": repo(mgr)}
    try:
        rel = fetch(API % repo(mgr))
        out["latest"] = str(rel.get("tag_name") or rel.get("name") or "").lstrip("vV")
        out["page"] = rel.get("html_url") if str(rel.get("html_url", "")).startswith("https://github.com/") else None
        out["notes"] = str(rel.get("body") or "")[:2000]
        a = pick_asset(rel)
        if a and str(a.get("browser_download_url", "")).startswith("https://github.com/"):
            out["asset"] = {"name": a["name"], "url": a["browser_download_url"], "size": int(a.get("size") or 0)}
            sums = [x for x in rel.get("assets") or [] if x.get("name") == a["name"] + ".sha256"]
            if sums:
                out["asset"]["sha256_url"] = sums[0].get("browser_download_url")
    except Exception as e:
        out["error"] = str(e)[:300]
        out["latest"] = data.get("latest")
    p = _state_path(mgr)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(p + ".tmp", p)
    return cached(mgr)


def _download(url: str, dest: str, progress: Callable[[float], None] = None, timeout: float = 30.0) -> str:
    if not url.startswith(ALLOWED_HOSTS):
        raise RuntimeError("Refusing to download from %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "BaoBun-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        final = r.geturl()
        if not final.startswith(ALLOWED_HOSTS):
            raise RuntimeError("The download was redirected somewhere unexpected (%s)" % final)
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(dest + ".partial", "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got * 100.0 / total)
    os.replace(dest + ".partial", dest)
    return dest


def download(mgr, info: Optional[dict] = None, progress: Callable[[float], None] = None) -> str:
    """Download the newest installer into runtime/downloads. Returns its path."""
    info = info or cached(mgr)
    a = info.get("asset")
    if not info.get("newer") or not a:
        raise RuntimeError("There's no newer BaoBun installer to download.")
    folder = os.path.join(str(mgr.config_path.parent), "runtime", "downloads")
    os.makedirs(folder, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", a["name"])
    dest = os.path.join(folder, name)
    _download(a["url"], dest, progress)
    if a.get("size") and os.path.getsize(dest) != a["size"]:
        os.remove(dest)
        raise RuntimeError("The download is incomplete - try again.")
    if a.get("sha256_url"):
        sums = os.path.join(folder, name + ".sha256")
        _download(a["sha256_url"], sums)
        with open(sums, encoding="utf-8", errors="replace") as f:
            want = (re.findall(r"\b[0-9a-fA-F]{64}\b", f.read()) or [""])[0].lower()
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if want and h.hexdigest() != want:
            os.remove(dest)
            raise RuntimeError("The download doesn't match its checksum - not running it.")
    return dest


def run_installer(path: str, silent: bool = True) -> None:
    """Start the installer (it relaunches BaoBun when it's done)."""
    import subprocess
    args = [path] + (["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"] if silent else [])
    subprocess.Popen(args, close_fds=True)


def banner_text(info: dict) -> Optional[str]:
    if not info.get("newer"):
        return None
    return "BaoBun %s is available (you have %s)." % (info["latest"], info["current"])
