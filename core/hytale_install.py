"""Installing / updating a Hytale server with Hytale's own downloader.

Steps (all from the Install button):
  1. Java 25 - downloaded into Tofu's runtime folder if the PC has none.
  2. hytale-downloader - fetched from Hytale (downloader.hytale.com) into
     runtime/hytale-downloader the first time. It keeps its own sign-in there
     (.hytale-downloader-credentials.json), so only the first run asks.
  3. It runs; the first time it prints a link and a code. Tofu shows them in
     the install window (and the web panel) - open the link, sign in with your
     Hytale account, type the code. Tofu never sees the password.
  4. The game zip (Server/ + Assets.zip) is unpacked into the server's folder,
     leaving the server's own settings, worlds, mods and sign-in alone.

After the first start the *server* also needs signing in once (/auth login
device); ServerManager.hytale_sign_in sends the commands and returns the link.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import time
import zipfile
from typing import Optional

DOWNLOADER_URL = "https://downloader.hytale.com/hytale-downloader.zip"
MANUAL_URL = "https://support.hytale.com/hc/en-us/articles/45326769420827"
SIGNIN_TAG = "Tofu SIGNIN:"
GAME_ZIP = "_hytale_download.zip"
# never overwritten when unpacking an update
PROTECT = {"config.json", "permissions.json", "whitelist.json", "bans.json", "jvm.options", "auth.enc",
           "auth.key"}
PROTECT_DIRS = ("universe/", "mods/", "logs/")

_URL = re.compile(r"https://[^\s\"'<>]+")
_CODE = re.compile(r"(?i)\b(?:user[_ ]?code|code)\b\s*[:=]?\s*([A-Z0-9]{3,}(?:-[A-Z0-9]{3,})?)\b")
_VERSION = re.compile(r"\b(20\d\d\.\d\d\.\d\d-[0-9a-f]{6,})\b")
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def tool_dir(base: str) -> str:
    return os.path.join(base, "runtime", "hytale-downloader")


def find_downloader(folder: str) -> Optional[str]:
    if not os.path.isdir(folder):
        return None
    if os.name == "nt":
        pats = ["hytale-downloader-windows-amd64.exe", "hytale-downloader*.exe", "*.exe"]
    else:
        pats = ["hytale-downloader-linux-amd64", "hytale-downloader-linux*", "hytale-downloader"]
    for pat in pats:
        for p in sorted(glob.glob(os.path.join(folder, "**", pat), recursive=True)):
            if os.path.isfile(p) and "QUICKSTART" not in p:
                return p
    return None


def parse_signin(text: str) -> Optional[dict]:
    """A sign-in link (and code) from downloader or server output, or None."""
    urls = [u.rstrip(".,)") for u in _URL.findall(text or "")]
    urls = [u for u in urls if "hytale" in u.split("/")[2].lower()
            and any(k in u.lower() for k in ("device", "activate", "verify", "login", "oauth", "code"))]
    if not urls:
        return None
    url = sorted(urls, key=lambda u: ("code=" not in u, len(u)))[0]
    m = re.search(r"[?&]user_code=([A-Za-z0-9-]+)", url)
    code = m.group(1) if m else None
    if not code:
        m = _CODE.search(text or "")
        code = m.group(1) if m else None
    return {"url": url, "code": code}


def last_signin(log_text: str) -> Optional[dict]:
    """The newest SIGNIN line Tofu wrote into install.log."""
    found = None
    for line in (log_text or "").splitlines():
        if SIGNIN_TAG in line:
            rest = line.split(SIGNIN_TAG, 1)[1].split()
            if rest:
                found = {"url": rest[0], "code": rest[1] if len(rest) > 1 else None}
    return found


def ensure_downloader(base: str, progress) -> str:
    folder = tool_dir(base)
    exe = find_downloader(folder)
    if exe:
        return exe
    from core.game_installers import download, unpack_zip_file
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "_downloader.zip")
    try:
        download(DOWNLOADER_URL, tmp, progress=progress, what="Hytale downloader")
    except Exception as e:
        raise RuntimeError("Couldn't download Hytale's downloader (%s). Download hytale-downloader.zip from %s "
                           "and unzip it into %s, then press Install again." % (e, MANUAL_URL, folder))
    try:
        unpack_zip_file(tmp, folder)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    exe = find_downloader(folder)
    if not exe:
        raise RuntimeError("The Hytale downloader zip didn't contain a program for this PC.")
    if os.name != "nt":
        os.chmod(exe, 0o755)
    return exe


def run_downloader(exe: str, out_zip: str, progress, popen=subprocess.Popen, timeout: float = 3600) -> dict:
    """Run the downloader, passing sign-in links and progress to the log. Returns {"version"}."""
    try:
        os.remove(out_zip)
    except OSError:
        pass
    cmd = [exe, "-download-path", out_zip]
    proc = popen(cmd, cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    started = time.time()
    version, seen_signin, buf, tail = None, None, b"", []
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            tail = (tail + [line])[-40:]
            progress.log("hytale-downloader: " + line)
            si = parse_signin("\n".join(tail[-4:]))
            if si and si != seen_signin:
                seen_signin = si
                progress.log("%s %s %s" % (SIGNIN_TAG, si["url"], si.get("code") or ""))
                progress("waiting for you to sign in to Hytale", 2)
            v = _VERSION.search(line)
            if v:
                version = v.group(1)
            p = _PCT.search(line)
            if p:
                progress("downloading Hytale", min(99.0, float(p.group(1))))
        if time.time() - started > timeout:
            proc.kill()
            raise RuntimeError("The Hytale downloader took too long.")
    rc = proc.wait()
    if rc != 0 or not os.path.isfile(out_zip):
        msg = " / ".join(tail[-3:]) or "exit code %s" % rc
        if seen_signin:
            msg += " (did the Hytale sign-in finish?)"
        raise RuntimeError("Hytale's downloader failed: " + msg)
    return {"version": version}


def _protected(name: str) -> bool:
    n = name.replace("\\", "/")
    base = n.rsplit("/", 1)[-1]
    if base in PROTECT:
        return True
    low = n.lower()
    return any(low.startswith(d) or ("/" + d) in low for d in PROTECT_DIRS)


def unpack_game(zip_path: str, directory: str, progress=None) -> dict:
    """Unpack Server/ + Assets.zip into directory. Existing settings/worlds stay."""
    written = kept = 0
    root = os.path.realpath(directory)
    with zipfile.ZipFile(zip_path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for k, info in enumerate(infos):
            name = info.filename.replace("\\", "/").lstrip("/")
            dest = os.path.realpath(os.path.join(directory, name))
            if not dest.startswith(root + os.sep):
                continue
            if _protected(name) and os.path.exists(dest):
                kept += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest + ".part", "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
            os.replace(dest + ".part", dest)
            written += 1
            if progress and k % 25 == 0:
                progress("unpacking Hytale", 100.0 * (k + 1) / max(1, len(infos)))
    return {"written": written, "kept": kept}


def install(srv: dict, base: str, progress, popen=subprocess.Popen, java=None) -> dict:
    directory = srv.get("directory") or ""
    notes, fields = [], {}
    from core import hytale
    from core import minecraft as mc
    progress("checking Java %d" % hytale.JAVA, 0)
    had = mc.pick_java(hytale.JAVA)
    try:
        (java or mc.ensure_java)(hytale.JAVA, progress=progress)
        if not had:
            notes.append("Downloaded Java %d for Hytale." % hytale.JAVA)
    except Exception as e:
        notes.append("Java %d is still needed: %s" % (hytale.JAVA, e))
    exe = ensure_downloader(base, progress)
    out_zip = os.path.join(tool_dir(base), GAME_ZIP)
    progress("starting Hytale's downloader", 1)
    res = run_downloader(exe, out_zip, progress, popen=popen)
    try:
        unpack_game(out_zip, directory, progress)
    finally:
        try:
            os.remove(out_zip)
        except OSError:
            pass
    jar = os.path.join(directory, "Server", "HytaleServer.jar")
    cur = srv.get("executable") or ""
    if os.path.isfile(jar) and (not cur or not os.path.isfile(cur if os.path.isabs(cur) else
                                                             os.path.join(directory, cur))):
        fields["executable"] = "Server/HytaleServer.jar"
    if not os.path.isfile(jar):
        notes.append("Server/HytaleServer.jar wasn't in the download - check the server folder.")
    if not os.path.isfile(os.path.join(directory, "Server", "auth.enc")):
        notes.append("Start the server, then use 'Sign the server in' (or type /auth login device in the console) "
                     "once so players can join.")
    progress("done", 100)
    return {"status": "installed", "version": res.get("version"), "fields": fields, "notes": notes}


def server_signed_in(srv: dict) -> bool:
    d = srv.get("directory") or ""
    return any(os.path.isfile(os.path.join(d, sub, "auth.enc")) for sub in ("Server", ""))
