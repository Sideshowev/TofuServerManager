"""Shared helpers for the mod backends (HTTP, files, INI/launch-option editing)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Dict, Iterable, List, Optional

USER_AGENT = "TofuServerManager/1.0 (self-hosted game server manager)"


class ModError(Exception):
    pass


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_json(url: str, data=None, headers: Optional[dict] = None, timeout: int = 30, form: bool = False):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_bytes(url: str, timeout: int = 600, max_bytes: int = 2_000_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ModError("Download is too large")
    return data


def friendly_http(e: Exception, what: str) -> ModError:
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return ModError("%s wasn't found." % what)
        return ModError("The mod site answered %s for %s. Try again in a minute." % (e.code, what))
    if isinstance(e, urllib.error.URLError):
        return ModError("Couldn't reach the mod site (%s). Check this PC's internet connection." % e.reason)
    return ModError(str(e))


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def lenient_json(text: str):
    """JSON that may contain // comments, /* */ comments and trailing commas."""
    try:
        return json.loads(text)
    except Exception:
        pass
    text = re.sub(r"(?m)^\s*//[^\n]*", "", text)
    text = re.sub(r"(?<![:\"])//[^\n\"]*$", "", text, flags=re.M)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def ci(d: dict, *keys, default=None):
    """Case-insensitive dict lookup."""
    low = {str(k).lower(): v for k, v in (d or {}).items()}
    for k in keys:
        if k.lower() in low:
            return low[k.lower()]
    return default


def workshop_id(ref) -> Optional[str]:
    """Item id from "123456", a steamcommunity URL, or "...?id=123&searchtext"."""
    s = str(ref or "").strip()
    if s.isdigit():
        return s
    m = re.search(r"[?&]id=(\d+)", s)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6,})/?$", s)
    return m.group(1) if m else None


def parse_vdf(text: str) -> dict:
    """Tiny parser for Valve KeyValues files (appworkshop_*.acf)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root: dict = {}
    stack = [root]
    key = None
    for quoted, brace in tokens:
        if brace == "{":
            new: dict = {}
            stack[-1][key if key is not None else ""] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted
        else:
            stack[-1][key] = quoted
            key = None
    return root


def sha1_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(text: str, fallback: str = "mod") -> str:
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(text or "")).strip("_")
    return s[:60] or fallback


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def size_of(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def is_link(path: str) -> bool:
    """True for symlinks and Windows junctions."""
    if os.path.islink(path):
        return True
    try:
        return bool(os.path.isjunction(path))   # Python 3.12+
    except AttributeError:
        pass
    if os.name == "nt":
        try:
            import stat
            st = os.lstat(path)
            return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except OSError:
            return False
    return False


def link_dir(target: str, link: str):
    """Make ``link`` point at folder ``target`` (junction on Windows, no admin needed).
    Falls back to copying if links aren't possible."""
    if os.path.lexists(link):
        unlink_dir(link)
    os.makedirs(os.path.dirname(link) or ".", exist_ok=True)
    try:
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                return "link"
        else:
            os.symlink(target, link, target_is_directory=True)
            return "link"
    except OSError:
        pass
    shutil.copytree(target, link)
    return "copy"


def unlink_dir(path: str):
    """Remove a link/junction without touching what it points at (or a copied folder)."""
    if not os.path.lexists(path):
        return
    if is_link(path):
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)   # junctions on Windows
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def move_to(src: str, dest_dir: str) -> str:
    """Move a file/folder into dest_dir (renaming on a clash). Friendly error if in use."""
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(src.rstrip("\\/"))
    dest = os.path.join(dest_dir, base)
    if os.path.lexists(dest):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(dest_dir, "%s.%s%s" % (stem, time.strftime("%Y%m%d-%H%M%S"), ext))
    try:
        if is_link(src):
            unlink_dir(src)
            return dest
        shutil.move(src, dest)
    except PermissionError:
        raise ModError("%s is in use. Stop the server first, then try again." % base)
    except OSError as e:
        if getattr(e, "winerror", None) == 32:
            raise ModError("%s is in use. Stop the server first, then try again." % base)
        raise
    return dest


def extract_zip(data: bytes, dest: str, strip_single_root: bool = False, subdir: Optional[str] = None) -> List[str]:
    """Safely extract a zip (no paths outside dest). Returns the top-level names written."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if n and not n.endswith("/")]
    prefix = ""
    if subdir:
        prefix = subdir.rstrip("/") + "/"
    elif strip_single_root:
        roots = {n.split("/", 1)[0] for n in z.namelist() if n}
        if len(roots) == 1 and all("/" in n for n in names):
            prefix = roots.pop() + "/"
    os.makedirs(dest, exist_ok=True)
    real_dest = os.path.realpath(dest)
    tops = set()
    for n in names:
        if prefix and not n.startswith(prefix):
            continue
        rel = n[len(prefix):]
        if not rel:
            continue
        out = os.path.realpath(os.path.join(dest, rel))
        if os.path.commonpath([out, real_dest]) != real_dest:
            continue   # zip-slip
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(n) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tops.add(rel.split("/", 1)[0])
    return sorted(tops)


# --------------------------------------------------------------------------- #
# launch options
# --------------------------------------------------------------------------- #
def get_arg(args: str, name: str) -> Optional[str]:
    """Value of -name=value / -name value / -name "value" in a launch string."""
    m = re.search(r'(?i)(?:^|\s)[-+]%s(?:=|\s+)("([^"]*)"|(\S+))' % re.escape(name.lstrip("-+")), args or "")
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else m.group(3)


def set_arg(args: str, name: str, value: Optional[str], sep: str = "=", prefix: str = "-") -> str:
    """Replace (or add, or remove when value is None/"") a launch option."""
    args = args or ""
    pat = re.compile(r'(?i)(^|\s)[-+]%s(?:=|\s+)(?:"[^"]*"|\S+)' % re.escape(name.lstrip("-+")))
    args = pat.sub(r"\1", args).strip()
    args = re.sub(r"\s{2,}", " ", args)
    if value:
        v = '"%s"' % value if (" " in value and not value.startswith('"')) else value
        args = (args + " " + prefix + name.lstrip("-+") + sep + v).strip()
    return args


def has_flag(args: str, flag: str) -> bool:
    return re.search(r"(?i)(^|\s)%s(\s|$)" % re.escape(flag), args or "") is not None


def add_flag(args: str, flag: str) -> str:
    return args if has_flag(args, flag) else ((args or "") + " " + flag).strip()


# --------------------------------------------------------------------------- #
# INI editing that keeps everything else in the file as it was
# --------------------------------------------------------------------------- #
def _read_lines(path: str) -> List[str]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read().splitlines()


def _write_lines(path: str, lines: List[str], bak: bool = True):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if bak and os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\r\n" if os.name == "nt" else "\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def ini_get(path: str, section: Optional[str], key: str) -> List[str]:
    """All values of key in section (section None = before any [section])."""
    out, cur = [], None
    for line in _read_lines(path):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            continue
        if (cur or None) == (section or None) or (section and cur and cur.lower() == section.lower()):
            if "=" in s and s.split("=", 1)[0].strip().lower() == key.lower():
                out.append(s.split("=", 1)[1].strip())
    return out


def ini_set(path: str, section: Optional[str], key: str, values, first_in_section: bool = False):
    """Set key to one value (str) or several lines (list). None/[] removes it."""
    if values is None:
        values = []
    if isinstance(values, str):
        values = [values]
    lines = _read_lines(path)
    out: List[str] = []
    cur = None
    in_target = section is None
    inserted = False
    sec_found = section is None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_target and not inserted and values:
                while out and not out[-1].strip():
                    out.pop()
                out += ["%s=%s" % (key, v) for v in values] + [""]
                inserted = True
            cur = s[1:-1]
            in_target = section is not None and cur.lower() == section.lower()
            sec_found = sec_found or in_target
            out.append(line)
            if in_target and first_in_section and values and not inserted:
                out += ["%s=%s" % (key, v) for v in values]
                inserted = True
            continue
        if in_target and "=" in s and s.split("=", 1)[0].strip().lower() == key.lower():
            continue   # drop old values
        out.append(line)
    if values and not inserted:
        if not sec_found:
            if out and out[-1].strip():
                out.append("")
            out.append("[%s]" % section)
        out += ["%s=%s" % (key, v) for v in values]
    _write_lines(path, out)


def first_existing(*paths) -> Optional[str]:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def user_home() -> str:
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def documents_dir() -> str:
    return os.path.join(user_home(), "Documents")


def appdata() -> str:
    return os.environ.get("APPDATA") or os.path.join(user_home(), "AppData", "Roaming")


def rel_or_abs(base: str, p: str) -> str:
    p = (p or "").strip('"')
    return p if os.path.isabs(p) else os.path.join(base, p)


def dedupe(seq: Iterable) -> list:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def steam_details(ids: List[str]) -> Dict[str, dict]:
    """Title / update time / size for Workshop items from Steam's public API ({} if offline)."""
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        chunk = [str(x) for x in ids[i:i + 100]]
        form = {"itemcount": len(chunk)}
        for n, x in enumerate(chunk):
            form["publishedfileids[%d]" % n] = x
        try:
            data = http_json("https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                             form, form=True, timeout=20)
            for d in (data.get("response") or {}).get("publishedfiledetails") or []:
                out[str(d.get("publishedfileid"))] = d
        except Exception:
            break
    return out
