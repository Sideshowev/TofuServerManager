"""Minecraft (Java Edition) support: finding/downloading Java, launching the
server jar, and the Server List Ping that reports players.

A Minecraft server is "server.jar" (vanilla/Paper/Fabric) or, for Forge and
NeoForge 1.17+, a run.bat that points Java at an argument file. Windows can't
run either directly, so Tofu builds the java command itself:

    java -Xms1024M -Xmx4096M [javaArgs] -jar server.jar nogui
    java -Xmx4096M @user_jvm_args.txt @libraries/.../win_args.txt nogui

Server fields used: javaPath (a java.exe to use), javaMemoryMB (max RAM,
default 4096), javaArgs (extra JVM flags), mcVersion, mcJava (Java major).
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import struct
import subprocess
from typing import Dict, List, Optional, Tuple

DEFAULT_MEMORY_MB = 4096


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
def _vtuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3]) or (0,)


def java_for_version(mc_version: Optional[str]) -> int:
    """Java major version a Minecraft release needs (newest if unknown)."""
    v = _vtuple(mc_version or "")
    if not mc_version or v == (0,):
        return 21
    if v[0] >= 26:          # year-based versions (26.1, ...) need Java 25
        return 25
    if v[0] == 1:
        minor = v[1] if len(v) > 1 else 0
        patch = v[2] if len(v) > 2 else 0
        if minor > 20 or (minor == 20 and patch >= 5):
            return 21
        if minor >= 18:
            return 17
        if minor == 17:
            return 16
        return 8
    return 21


def looks_minecraft(srv: dict) -> bool:
    exe = os.path.basename(str(srv.get("executable") or "")).lower()
    d = srv.get("directory") or ""
    if (srv.get("type") or "").lower() == "hytale" or exe == "hytaleserver.jar":
        return False
    if exe.endswith(".jar"):
        return True
    if srv.get("mcFlavor"):
        return True
    if exe in ("run.bat", "start.bat", "") and d:
        return any(os.path.exists(os.path.join(d, n)) for n in ("server.properties", "eula.txt"))
    return False


def read_properties(directory: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(os.path.join(directory, "server.properties"), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", "!")) or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def game_port(srv: dict) -> int:
    props = read_properties(srv.get("directory") or "")
    try:
        return int(props.get("server-port") or srv.get("port") or 25565)
    except ValueError:
        return int(srv.get("port") or 25565)


# --------------------------------------------------------------------------- #
# Java
# --------------------------------------------------------------------------- #
def java_runtime_dir() -> str:
    from core.paths import base_dir
    return os.path.join(str(base_dir()), "runtime", "java")


def _java_exe_name() -> str:
    return "java.exe" if os.name == "nt" else "java"


def java_major(java_exe: str) -> Optional[int]:
    """Major version of a java executable (from the JDK/JRE "release" file, else java -version)."""
    home = os.path.dirname(os.path.dirname(os.path.abspath(java_exe)))
    try:
        with open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as f:
            m = re.search(r'JAVA_VERSION="([^"]+)"', f.read())
        if m:
            return _major(m.group(1))
    except OSError:
        pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run([java_exe, "-version"], capture_output=True, timeout=15, creationflags=flags)
        m = re.search(r'version "([^"]+)"', (p.stderr or p.stdout or b"").decode("utf-8", "replace"))
        if m:
            return _major(m.group(1))
    except Exception:
        pass
    return None


def _major(v: str) -> Optional[int]:
    parts = re.findall(r"\d+", v)
    if not parts:
        return None
    return int(parts[1]) if parts[0] == "1" and len(parts) > 1 else int(parts[0])


def find_javas(extra: Optional[List[str]] = None) -> List[dict]:
    """Java installs on this PC: [{"path", "major"}] (Tofu's own downloads first)."""
    name = _java_exe_name()
    cands: List[str] = list(extra or [])
    cands += glob.glob(os.path.join(java_runtime_dir(), "*", "bin", name))
    jh = os.environ.get("JAVA_HOME")
    if jh:
        cands.append(os.path.join(jh, "bin", name))
    if os.name == "nt":
        for root in {os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("ProgramW6432", r"C:\Program Files")}:
            if not root:
                continue
            for vendor in ("Java", "Eclipse Adoptium", "Eclipse Foundation", "Microsoft", "Zulu",
                           "Amazon Corretto", "BellSoft", "Semeru", "AdoptOpenJDK", "Temurin"):
                cands += glob.glob(os.path.join(root, vendor, "*", "bin", name))
        local = os.environ.get("LOCALAPPDATA")
        if local:   # the Minecraft launcher's bundled runtimes
            cands += glob.glob(os.path.join(local, "Packages", "Microsoft.4297127D64EC6_*", "LocalCache",
                                            "Local", "runtime", "*", "*", "*", "bin", name))
    else:
        cands += glob.glob("/usr/lib/jvm/*/bin/java")
    import shutil
    w = shutil.which("java")
    if w:
        cands.append(w)
    seen, out = set(), []
    for c in cands:
        key = os.path.normcase(os.path.realpath(c))
        if key in seen or not os.path.isfile(c):
            continue
        seen.add(key)
        out.append({"path": c, "major": java_major(c)})
    return out


def pick_java(required: int, javas: Optional[List[dict]] = None) -> Optional[str]:
    """Best java for a required major: exact match, else the oldest newer one.
    Java 8 servers (old Forge) only take Java 8."""
    javas = find_javas() if javas is None else javas
    exact = [j for j in javas if j.get("major") == required]
    if exact:
        return exact[0]["path"]
    if required <= 8:
        return None
    newer = sorted((j for j in javas if (j.get("major") or 0) > required), key=lambda j: j["major"])
    return newer[0]["path"] if newer else None


def java_download_info(major: int) -> dict:
    """The Eclipse Temurin JRE build for Windows x64 (Adoptium API)."""
    from core.mods_common import http_json
    url = ("https://api.adoptium.net/v3/assets/latest/%d/hotspot?architecture=x64&image_type=jre"
           "&os=windows&vendor=eclipse" % int(major))
    items = http_json(url, timeout=30)
    for it in items or []:
        pkg = ((it.get("binary") or {}).get("package") or {})
        if pkg.get("link", "").endswith(".zip"):
            return {"url": pkg["link"], "sha256": pkg.get("checksum"), "size": pkg.get("size"),
                    "name": pkg.get("name"), "version": (it.get("version") or {}).get("semver")}
    # some majors only ship a JDK
    url = url.replace("image_type=jre", "image_type=jdk")
    for it in http_json(url, timeout=30) or []:
        pkg = ((it.get("binary") or {}).get("package") or {})
        if pkg.get("link", "").endswith(".zip"):
            return {"url": pkg["link"], "sha256": pkg.get("checksum"), "size": pkg.get("size"),
                    "name": pkg.get("name"), "version": (it.get("version") or {}).get("semver")}
    raise RuntimeError("No Java %d download found for Windows." % major)


def ensure_java(required: int, progress=None) -> str:
    """A java.exe for this major version, downloading Temurin into Tofu's
    runtime folder when the PC has none."""
    found = pick_java(required)
    if found:
        return found
    if os.name != "nt":
        raise RuntimeError("Java %d is needed - install it with your package manager." % required)
    from core.game_installers import download, unpack_zip_file
    info = java_download_info(required)
    dest = os.path.join(java_runtime_dir(), "temurin-%d" % required)
    tmp = os.path.join(java_runtime_dir(), "_java%d.zip" % required)
    os.makedirs(java_runtime_dir(), exist_ok=True)
    download(info["url"], tmp, progress=progress, sha256=info.get("sha256"), what="Java %d" % required)
    try:
        unpack_zip_file(tmp, dest, strip_single_root=True)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    exe = os.path.join(dest, "bin", _java_exe_name())
    if not os.path.isfile(exe):
        raise RuntimeError("The Java download didn't contain bin\\java.exe.")
    return exe


# --------------------------------------------------------------------------- #
# Launching
# --------------------------------------------------------------------------- #
def _argfile_tokens(bat_path: str) -> Optional[List[str]]:
    """The @argument-file tokens in a Forge/NeoForge run.bat (None if it isn't one)."""
    try:
        with open(bat_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if re.match(r'(?i)^"?[^"]*java(\.exe)?"?\s', s) and "@" in s:
            toks = re.findall(r'@"[^"]+"|@\S+', s)
            toks = [t.replace('"', "") for t in toks]
            if any("win_args.txt" in t or "unix_args.txt" in t for t in toks):
                return [t for t in toks if t != "%*"]
    return None


def memory_args(srv: dict) -> List[str]:
    try:
        mem = int(srv.get("javaMemoryMB") or DEFAULT_MEMORY_MB)
    except (TypeError, ValueError):
        mem = DEFAULT_MEMORY_MB
    mem = max(512, min(mem, 262144))
    return ["-Xms%dM" % min(1024, mem), "-Xmx%dM" % mem]


def required_java(srv: dict) -> int:
    try:
        if srv.get("mcJava"):
            return int(srv["mcJava"])
    except (TypeError, ValueError):
        pass
    return java_for_version(srv.get("mcVersion"))


def resolve_java(srv: dict) -> str:
    jp = (srv.get("javaPath") or "").strip().strip('"')
    if jp:
        if not os.path.isfile(jp):
            raise FileNotFoundError("Java not found at %s (set in Edit Server)." % jp)
        return jp
    need = required_java(srv)
    found = pick_java(need)
    if not found:
        raise RuntimeError(
            "This Minecraft server needs Java %d, which isn't installed. Use Install / Update on the "
            "server (Tofu downloads Java for you) or install Temurin %d from adoptium.net." % (need, need))
    return found


def launch_command(srv: dict, exe: str, arg_string: str):
    """The Popen command for a Minecraft server, or None if this isn't one."""
    low = exe.lower()
    tokens = None
    if low.endswith(".jar"):
        middle = ["-jar", exe]
    elif os.path.basename(low) in ("run.bat", "run.cmd") and looks_minecraft(srv):
        tokens = _argfile_tokens(exe)
        if not tokens:
            return None
        if os.name != "nt":
            tokens = [t.replace("win_args.txt", "unix_args.txt") for t in tokens]
        middle = tokens
    else:
        return None
    java = resolve_java(srv)
    extra = (srv.get("javaArgs") or "").strip()
    args = (arg_string or "").strip() or "nogui"
    parts = [java] + memory_args(srv)
    if os.name == "nt":
        cmd = subprocess.list2cmdline(parts)
        if extra:
            cmd += " " + extra
        return cmd + " " + subprocess.list2cmdline(middle) + " " + args
    from server_manager import split_args
    return parts + split_args(extra) + middle + split_args(args)


def start_bat_line(srv: dict, exe: str, arg_string: str) -> Optional[str]:
    try:
        cmd = launch_command(srv, exe, arg_string)
    except Exception:
        return None
    return cmd if isinstance(cmd, str) else (subprocess.list2cmdline(cmd) if cmd else None)


# --------------------------------------------------------------------------- #
# Server List Ping
# --------------------------------------------------------------------------- #
def _varint(n: int) -> bytes:
    out = b""
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(sock) -> int:
    n = shift = 0
    for _ in range(5):
        b = sock.recv(1)
        if not b:
            raise OSError("connection closed")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7
    raise OSError("bad varint")


def _recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("connection closed")
        data += chunk
    return data


def ping(host: str, port: int, timeout: float = 2.0) -> Optional[dict]:
    """{"count", "max", "names" (None when the server hides them), "version", "motd"}."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.settimeout(timeout)
            h = host.encode("utf-8")
            body = _varint(0) + _varint(0xFFFFFFFF) + _varint(len(h)) + h + struct.pack(">H", int(port)) + _varint(1)
            s.sendall(_varint(len(body)) + body)
            s.sendall(_varint(1) + _varint(0))
            length = _read_varint(s)
            if length <= 0 or length > 2_000_000:
                return None
            data = _recv_exact(s, length)
    except (OSError, ValueError):
        return None
    try:
        i = 0
        # packet id
        while data[i] & 0x80:
            i += 1
        i += 1
        n = shift = 0
        while True:
            b = data[i]
            i += 1
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
        info = json.loads(data[i:i + n].decode("utf-8", "replace"))
    except Exception:
        return None
    players = info.get("players") or {}
    count, mx = players.get("online"), players.get("max")
    sample = [p.get("name") for p in (players.get("sample") or []) if isinstance(p, dict) and p.get("name")]
    # servers send at most ~12 sample names, sometimes fake ones ("§7and 5 more")
    sample = [n for n in sample if re.match(r"^[A-Za-z0-9_]{2,16}$", n)]
    names = sample if isinstance(count, int) and len(sample) == count else None
    desc = info.get("description")
    if isinstance(desc, dict):
        desc = desc.get("text", "")
    return {"count": count, "max": mx, "names": names,
            "version": (info.get("version") or {}).get("name"), "motd": desc}
