"""Download and install game servers that don't come from SteamCMD.

- Vintage Story: the official Windows server zip (api.vintagestory.at), also
  used to update an existing install in place (worlds live in the data folder,
  which the zip doesn't touch).
- Minecraft Java: vanilla (Mojang), Paper (PaperMC Fill API), Fabric
  (meta.fabricmc.net), Forge and NeoForge (their installers, run with
  --installServer). Java is downloaded too when the PC doesn't have the right one.

Progress goes to <server folder>/install.log as "BaoBun: <phase>, progress: N"
lines, which the wizard and the web panel read like SteamCMD's output.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, List, Optional

from core.mods_common import USER_AGENT, http_json

VS_API = "https://api.vintagestory.at/%s.json"
VS_CDN = "https://cdn.vintagestory.at/gamefiles/%s/vs_server_win-x64_%s.zip"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
FABRIC_META = "https://meta.fabricmc.net/v2"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"

MC_FLAVORS = [
    {"id": "vanilla", "label": "Vanilla (official)", "mods": False},
    {"id": "paper", "label": "Paper (plugins, faster)", "mods": False},
    {"id": "fabric", "label": "Fabric (mods)", "mods": True},
    {"id": "neoforge", "label": "NeoForge (mods)", "mods": True},
    {"id": "forge", "label": "Forge (mods)", "mods": True},
]
INSTALL_LOG = "install.log"


class InstallError(Exception):
    pass


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
class Progress:
    """Writes progress lines the GUI/web can follow (and keeps the last one)."""

    def __init__(self, directory: Optional[str], callback: Optional[Callable] = None):
        self.path = os.path.join(directory, INSTALL_LOG) if directory else None
        self.callback = callback
        self.last = (None, None)
        self._t = 0.0
        if self.path:
            try:
                os.makedirs(directory, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("BaoBun installer started %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            except OSError:
                self.path = None

    def __call__(self, phase: str, pct: Optional[float] = None, note: str = ""):
        now = time.time()
        if pct is not None and 0 < pct < 100 and now - self._t < 0.5 and phase == self.last[0]:
            return
        self._t = now
        self.last = (phase, pct)
        line = "BaoBun: %s, progress: %.1f" % (phase, pct if pct is not None else 0.0)
        if note:
            line += " (" + note + ")"
        self.log(line)
        if self.callback:
            try:
                self.callback(phase, pct)
            except Exception:
                pass

    def log(self, text: str):
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text.rstrip("\n") + "\n")
        except OSError:
            pass


def _fmt_mb(n) -> str:
    return "%.0f MB" % (n / 1048576.0) if n else "?"


def download(url: str, dest: str, progress: Optional[Callable] = None, sha1: Optional[str] = None,
             sha256: Optional[str] = None, md5: Optional[str] = None, what: str = "file",
             headers: Optional[dict] = None, timeout: int = 60) -> str:
    """Stream a URL to dest (via a .part file), checking a hash when given."""
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    algo = hashlib.sha256() if sha256 else hashlib.sha1() if sha1 else hashlib.md5() if md5 else None
    expect = (sha256 or sha1 or md5 or "").lower()
    part = dest + ".part"
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                if algo:
                    algo.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress("downloading " + what, got * 100.0 / total, _fmt_mb(total))
    except urllib.error.HTTPError as e:
        _rm(part)
        raise InstallError("Download of %s failed: the server answered %s." % (what, e.code))
    except (urllib.error.URLError, OSError) as e:
        _rm(part)
        raise InstallError("Download of %s failed: %s. Check this PC's internet connection."
                           % (what, getattr(e, "reason", e)))
    if algo and expect and algo.hexdigest().lower() != expect:
        _rm(part)
        raise InstallError("The %s download was damaged (checksum mismatch). Try again." % what)
    os.replace(part, dest)
    return dest


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def unpack_zip_file(path: str, dest: str, strip_single_root: bool = False, progress=None,
                    what: str = "files", skip_existing=()) -> int:
    """Extract a zip from disk safely (no zip-slip). Returns files written."""
    real = os.path.realpath(dest)
    os.makedirs(dest, exist_ok=True)
    skip = {s.lower() for s in skip_existing}
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        prefix = ""
        if strip_single_root:
            roots = {i.filename.split("/", 1)[0] for i in z.infolist()}
            if len(roots) == 1 and all("/" in i.filename for i in infos):
                prefix = roots.pop() + "/"
        n = 0
        for k, info in enumerate(infos):
            rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else info.filename
            if not rel:
                continue
            out = os.path.realpath(os.path.join(dest, rel))
            if os.path.commonpath([out, real]) != real:
                continue
            if rel.replace("\\", "/").lower() in skip and os.path.exists(out):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            n += 1
            if progress and infos:
                progress("unpacking " + what, (k + 1) * 100.0 / len(infos))
    return n


# --------------------------------------------------------------------------- #
# which installer
# --------------------------------------------------------------------------- #
def kind(srv: dict) -> Optional[str]:
    if (srv.get("type") or "").lower() == "vintage-story":
        return "vintage-story"
    from core.hytale import is_hytale
    if is_hytale(srv):
        return "hytale"
    if srv.get("appId"):
        return "steam"
    from core.minecraft import looks_minecraft
    if looks_minecraft(srv) or (srv.get("preset") or "").startswith("Minecraft"):
        return "minecraft"
    return None


# --------------------------------------------------------------------------- #
# Vintage Story
# --------------------------------------------------------------------------- #
def vs_releases(channel: str = "stable") -> List[dict]:
    """[{"version", "url", "md5", "size"}] newest first."""
    channel = channel if channel in ("stable", "unstable") else "stable"
    try:
        data = http_json(VS_API % channel, timeout=30)
    except Exception as e:
        raise InstallError("Couldn't get the Vintage Story version list (%s)." % e)
    out = []
    for ver, entry in (data or {}).items():
        ws = (entry or {}).get("windowsserver") or {}
        urls = ws.get("urls") or {}
        url = urls.get("cdn") or urls.get("local") or VS_CDN % (channel, ver)
        out.append({"version": str(ver), "url": url, "md5": ws.get("md5"),
                    "size": ws.get("filesize"), "latest": bool(ws.get("latest"))})
    out.sort(key=lambda r: _vkey(r["version"]), reverse=True)
    return out


def _vkey(v: str):
    nums = [int(x) for x in re.findall(r"\d+", v)]
    pre = 0 if re.search(r"(?i)(rc|pre|dev)", v) else 1   # 1.21.0 sorts after 1.21.0-rc.3
    return tuple(nums[:3]) + (pre,) + tuple(nums[3:])


def vs_pick(releases: List[dict], version: Optional[str]) -> dict:
    if not releases:
        raise InstallError("No Vintage Story server downloads were listed.")
    if not version or version == "latest":
        return releases[0]
    v = version.lstrip("v")
    for r in releases:
        if r["version"] == v:
            return r
    raise InstallError("Vintage Story %s isn't in the download list." % version)


def vs_install_state(srv: dict) -> str:
    """"vanilla" (official server files), "custom" (e.g. a Stratum build, which
    BaoBun never overwrites) or "empty" (nothing installed yet)."""
    d = srv.get("directory") or ""
    exe = os.path.basename(str(srv.get("executable") or ""))
    vanilla = os.path.isfile(os.path.join(d, "VintagestoryServer.exe"))
    if exe and exe.lower() != "vintagestoryserver.exe" and os.path.isfile(os.path.join(d, exe)):
        return "custom"
    if vanilla:
        return "vanilla"
    return "empty" if folder_is_empty(d) else "custom"


# what BaoBun itself may have put in a server folder before anything was installed
_OWN_FILES = {"logs", "install.log", "steamcmd_install.log", "start.bat", "backups", "data", "desktop.ini"}


def folder_is_empty(d: str) -> bool:
    """True if a folder doesn't exist yet or holds nothing but BaoBun's own files.
    Installers refuse anything else, so they never unpack over someone's files."""
    if not d:
        return False
    if not os.path.exists(d):
        return True
    try:
        names = os.listdir(d)
    except OSError:
        return False   # can't look inside (e.g. a share that isn't reachable): don't touch it
    return all(n.lower() in _OWN_FILES or n.lower().startswith("_vs_server_") for n in names)


def vs_is_vanilla(srv: dict) -> bool:
    return vs_install_state(srv) == "vanilla"


def install_vintage_story(srv: dict, version: Optional[str] = None, channel: str = "stable",
                          progress: Optional[Progress] = None) -> dict:
    d = srv.get("directory") or ""
    if not d:
        raise InstallError("The server has no folder.")
    if vs_install_state(srv) == "custom":
        raise InstallError("This folder already has other files in it (a custom build or launcher?), so "
                           "BaoBun won't unpack the official server over them. Pick an empty folder to "
                           "install a new server.")
    progress = progress or Progress(d)
    progress("checking versions", 0)
    rel = vs_pick(vs_releases(channel), version)
    tmp = os.path.join(d, "_vs_server_%s.zip" % rel["version"])
    download(rel["url"], tmp, progress=progress, md5=rel.get("md5"), what="Vintage Story " + rel["version"])
    try:
        # serverconfig.json etc. live in the data folder; never overwrite anything there
        unpack_zip_file(tmp, d, progress=progress, what="Vintage Story")
    finally:
        _rm(tmp)
    exe = os.path.join(d, "VintagestoryServer.exe")
    if not os.path.isfile(exe):
        raise InstallError("The download didn't contain VintagestoryServer.exe.")
    progress("done", 100)
    notes = []
    need = vs_dotnet_major(rel["version"])
    if need and os.name == "nt" and not dotnet_has(need):
        notes.append("Vintage Story %s needs the .NET %d runtime. Get \".NET Desktop Runtime %d\" "
                     "(x64) from dot.net if the server doesn't start." % (rel["version"], need, need))
    return {"status": "installed", "version": rel["version"], "executable": exe, "notes": notes}


def vs_dotnet_major(version: str) -> Optional[int]:
    v = _vkey(version)
    if v[:2] >= (1, 21):
        return 8
    if v[:2] >= (1, 18):
        return 7
    return None


def dotnet_has(major: int) -> bool:
    roots = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "dotnet", "shared",
                          "Microsoft.NETCore.App")]
    for r in roots:
        try:
            if any(n.split(".")[0] == str(major) for n in os.listdir(r)):
                return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- #
# Minecraft
# --------------------------------------------------------------------------- #
def _release_re(v: str) -> bool:
    return bool(re.match(r"^\d+\.\d+(\.\d+)?$", str(v)))


def mc_versions(flavor: str = "vanilla", limit: int = 40) -> List[str]:
    """Release versions offered for a flavor, newest first."""
    try:
        if flavor == "paper":
            data = http_json(PAPER_API, timeout=30)
            vs = []
            for group in (data.get("versions") or {}).values():
                vs += [v for v in group if _release_re(v)]
            vs.sort(key=_vkey, reverse=True)
            return vs[:limit]
        if flavor == "fabric":
            return [g["version"] for g in http_json(FABRIC_META + "/versions/game", timeout=30)
                    if g.get("stable")][:limit]
        if flavor == "forge":
            promos = http_json(FORGE_PROMOS, timeout=30).get("promos") or {}
            vs = sorted({k.rsplit("-", 1)[0] for k in promos}, key=_vkey, reverse=True)
            return [v for v in vs if _release_re(v)][:limit]
        if flavor == "neoforge":
            return sorted({_neo_mc(v) for v in _neo_versions() if _neo_mc(v)}, key=_vkey, reverse=True)[:limit]
        data = http_json(MOJANG_MANIFEST, timeout=30)
        return [v["id"] for v in data.get("versions") or [] if v.get("type") == "release"][:limit]
    except InstallError:
        raise
    except Exception as e:
        raise InstallError("Couldn't get the Minecraft version list (%s)." % e)


def _neo_versions() -> List[str]:
    req = urllib.request.Request(NEOFORGE_MAVEN + "/maven-metadata.xml", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        xml = r.read().decode("utf-8", "replace")
    return re.findall(r"<version>([^<]+)</version>", xml)


def _neo_mc(neo: str) -> Optional[str]:
    """NeoForge 21.1.77 -> Minecraft 1.21.1, 20.4.237 -> 1.20.4, 21.0.x -> 1.21;
    year-based 26.1.0.x -> 26.1."""
    if re.search(r"(?i)beta|alpha|snapshot|pre|rc", neo):
        return None
    p = neo.split(".")
    if len(p) < 3 or not all(x.isdigit() for x in p[:3]):
        return None
    a, b = int(p[0]), int(p[1])
    if a >= 26:
        return "%d.%d" % (a, b)
    return "1.%d" % a if b == 0 else "1.%d.%d" % (a, b)


def _latest(flavor: str) -> str:
    if flavor == "vanilla":
        return http_json(MOJANG_MANIFEST, timeout=30)["latest"]["release"]
    vs = mc_versions(flavor, limit=1)
    if not vs:
        raise InstallError("No Minecraft versions found for %s." % flavor)
    return vs[0]


def install_minecraft(srv: dict, flavor: str = "vanilla", version: Optional[str] = None,
                      accept_eula: bool = False, progress: Optional[Progress] = None,
                      java_ok: bool = True) -> dict:
    """Download a Minecraft server into srv["directory"]. Returns the fields to
    store on the server (executable, mcVersion, mcFlavor, mcJava)."""
    from core import minecraft as mc
    d = srv.get("directory") or ""
    if not d:
        raise InstallError("The server has no folder.")
    flavor = (flavor or "vanilla").lower()
    if flavor not in {f["id"] for f in MC_FLAVORS}:
        raise InstallError("Unknown Minecraft type: %s" % flavor)
    if not folder_is_empty(d) and not _is_mc_folder(d):
        raise InstallError("This folder already has other files in it, so BaoBun won't put a Minecraft "
                           "server there. Pick an empty folder.")
    progress = progress or Progress(d)
    progress("checking versions", 0)
    try:
        version = version if version and version != "latest" else _latest(flavor)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError("Couldn't find the latest Minecraft version (%s)." % e)
    java_major = mc.java_for_version(version)
    fields = {"mcFlavor": flavor, "mcVersion": version}
    jar = os.path.join(d, "server.jar")

    if flavor == "vanilla":
        man = http_json(MOJANG_MANIFEST, timeout=30)
        entry = next((v for v in man.get("versions") or [] if v.get("id") == version), None)
        if not entry:
            raise InstallError("Minecraft %s wasn't found." % version)
        vj = http_json(entry["url"], timeout=30)
        srvdl = (vj.get("downloads") or {}).get("server")
        if not srvdl:
            raise InstallError("Minecraft %s has no server download." % version)
        java_major = int((vj.get("javaVersion") or {}).get("majorVersion") or java_major)
        download(srvdl["url"], jar, progress=progress, sha1=srvdl.get("sha1"), what="Minecraft " + version)
    elif flavor == "paper":
        builds = http_json(PAPER_API + "/versions/%s/builds" % version, timeout=30)
        stable = [b for b in builds or [] if str(b.get("channel", "")).upper() == "STABLE"] or list(builds or [])
        if not stable:
            raise InstallError("Paper has no builds for %s yet." % version)
        b = max(stable, key=lambda b: int(b.get("id") or 0))
        dl = (b.get("downloads") or {}).get("server:default") or {}
        if not dl.get("url"):
            raise InstallError("Paper build %s has no server download." % b.get("id"))
        download(dl["url"], jar, progress=progress, sha256=(dl.get("checksums") or {}).get("sha256"),
                 what="Paper %s build %s" % (version, b.get("id")))
        fields["mcBuild"] = b.get("id")
    elif flavor == "fabric":
        loaders = http_json(FABRIC_META + "/versions/loader/%s" % version, timeout=30)
        loader = next((l["loader"]["version"] for l in loaders if (l.get("loader") or {}).get("stable")),
                      loaders[0]["loader"]["version"] if loaders else None)
        inst = http_json(FABRIC_META + "/versions/installer", timeout=30)
        installer = next((i["version"] for i in inst if i.get("stable")), inst[0]["version"] if inst else None)
        if not loader or not installer:
            raise InstallError("Fabric doesn't support Minecraft %s." % version)
        jar = os.path.join(d, "fabric-server-launch.jar")
        download(FABRIC_META + "/versions/loader/%s/%s/%s/server/jar" % (version, loader, installer), jar,
                 progress=progress, what="Fabric %s" % version)
        fields["mcLoaderVersion"] = loader
    else:   # forge / neoforge: run their installer
        if flavor == "forge":
            promos = http_json(FORGE_PROMOS, timeout=30).get("promos") or {}
            fv = promos.get(version + "-recommended") or promos.get(version + "-latest")
            if not fv:
                raise InstallError("Forge has no build for Minecraft %s." % version)
            full = "%s-%s" % (version, fv)
            url = "%s/%s/forge-%s-installer.jar" % (FORGE_MAVEN, full, full)
        else:
            cands = [v for v in _neo_versions() if _neo_mc(v) == version]
            if not cands:
                raise InstallError("NeoForge has no build for Minecraft %s." % version)
            full = max(cands, key=_vkey)
            url = "%s/%s/neoforge-%s-installer.jar" % (NEOFORGE_MAVEN, full, full)
        fields["mcLoaderVersion"] = full
        installer = os.path.join(d, "_%s-installer.jar" % flavor)
        download(url, installer, progress=progress, what="%s installer" % flavor.capitalize())
        java = mc.ensure_java(java_major, progress=progress) if java_ok else mc.pick_java(java_major)
        if not java:
            raise InstallError("Java %d is needed to run the %s installer." % (java_major, flavor))
        progress("running the %s installer" % flavor, 50, "this takes a minute or two")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with open(os.path.join(d, INSTALL_LOG), "a", encoding="utf-8") as logf:
                p = subprocess.run([java, "-jar", installer, "--installServer"], cwd=d, stdout=logf,
                                   stderr=subprocess.STDOUT, timeout=1800, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise InstallError("The %s installer took too long." % flavor)
        finally:
            _rm(installer)
            _rm(installer + ".log")
        if p.returncode != 0:
            raise InstallError("The %s installer failed (see install.log in the server folder)." % flavor)
        run_bat = os.path.join(d, "run.bat")
        if os.path.isfile(run_bat):
            jar = run_bat
        else:   # old Forge (< 1.17): a forge-*.jar to run
            found = [f for f in os.listdir(d) if f.lower().startswith("forge-") and f.lower().endswith(".jar")
                     and "installer" not in f.lower()]
            if not found:
                raise InstallError("The Forge installer finished but no server jar was made.")
            jar = os.path.join(d, sorted(found)[-1])

    fields["mcJava"] = java_major
    fields["executable"] = jar
    fields["mcLoader"] = flavor if flavor != "vanilla" else None
    eula = os.path.join(d, "eula.txt")
    if accept_eula:
        with open(eula, "w", encoding="utf-8") as f:
            f.write("# Accepted in BaoBun (https://aka.ms/MinecraftEULA) %s\neula=true\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S"))
    notes = []
    if java_ok and flavor not in ("forge", "neoforge"):
        if not mc.pick_java(java_major):
            mc.ensure_java(java_major, progress=progress)
            notes.append("Downloaded Java %d for this server." % java_major)
    elif not mc.pick_java(java_major):
        notes.append("This server needs Java %d - install it before starting." % java_major)
    if not accept_eula and not _eula_ok(eula):
        notes.append("Accept the Minecraft EULA (set eula=true in eula.txt) before the first start.")
    progress("done", 100)
    return {"status": "installed", "version": version, "fields": fields, "notes": notes}


def _is_mc_folder(d: str) -> bool:
    try:
        names = [n.lower() for n in os.listdir(d)]
    except OSError:
        return False
    return any(n in ("server.properties", "eula.txt", "run.bat") or n.endswith(".jar") for n in names)


def _eula_ok(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return bool(re.search(r"(?im)^\s*eula\s*=\s*true\s*$", f.read()))
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# entry points used by ServerManager
# --------------------------------------------------------------------------- #
def options_for(srv: dict) -> dict:
    """What the install dialog should ask for."""
    k = kind(srv)
    if k == "minecraft":
        return {"kind": k, "flavors": MC_FLAVORS, "flavor": srv.get("mcFlavor") or "vanilla",
                "version": srv.get("mcVersion") or "latest", "needsEula": not _eula_ok(
                    os.path.join(srv.get("directory") or "", "eula.txt"))}
    if k == "vintage-story":
        return {"kind": k, "version": "latest", "channels": ["stable", "unstable"],
                "state": vs_install_state(srv)}
    if k == "hytale":
        from core import hytale_install
        return {"kind": k, "signedIn": hytale_install.server_signed_in(srv)}
    return {"kind": k}


def installed_version(mgr, srv: dict) -> Optional[str]:
    k = kind(srv)
    if k == "vintage-story":
        try:
            return mgr.vs_server_version(srv["id"])
        except Exception:
            return None
    if k == "minecraft":
        return srv.get("mcVersion")
    if k == "hytale":
        return srv.get("hytaleVersion")
    return None


def check_update(mgr, srv: dict) -> dict:
    """Vintage Story only: {"installed", "latest", "update_available", "patch"}.
    Minecraft is never updated automatically (mods and worlds are tied to a version)."""
    if kind(srv) != "vintage-story" or not vs_is_vanilla(srv):
        return {"update_available": None}
    inst = installed_version(mgr, srv)
    try:
        latest = vs_releases("stable")[0]["version"]
    except Exception as e:
        return {"update_available": None, "error": str(e)}
    if not inst:
        return {"installed": None, "latest": latest, "update_available": None}
    avail = _vkey(latest) > _vkey(inst)
    return {"installed": inst, "latest": latest, "update_available": avail,
            "patch": avail and _vkey(latest)[:2] == _vkey(inst)[:2]}
