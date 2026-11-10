"""Hytale dedicated server: a Java 25 program (Server/HytaleServer.jar + Assets.zip).

Install / Update runs Hytale's own downloader (core/hytale_install.py - you sign
in on Hytale's site when it asks), or the files can be copied in by hand. This
module starts it the right way:
  java -XX:AOTCache=HytaleServer.aot -jar HytaleServer.jar --assets Assets.zip
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

JAVA = 25
PORT = 5520


def is_hytale(srv: dict) -> bool:
    return (srv.get("type") or "").lower() == "hytale" or \
        os.path.basename(str(srv.get("executable") or "")).lower() == "hytaleserver.jar"


def find_assets(directory: str, jar: str) -> Optional[str]:
    for cand in (os.path.join(os.path.dirname(jar), "Assets.zip"), os.path.join(directory, "Assets.zip"),
                 os.path.join(os.path.dirname(os.path.dirname(jar)), "Assets.zip")):
        if os.path.isfile(cand):
            return cand
    return None


def resolve_java(srv: dict) -> str:
    from core.minecraft import pick_java
    jp = (srv.get("javaPath") or "").strip().strip('"')
    if jp:
        if not os.path.isfile(jp):
            raise FileNotFoundError("Java not found at %s (set in Edit Server)." % jp)
        return jp
    found = pick_java(JAVA)
    if not found:
        raise RuntimeError("Hytale needs Java %d, which isn't installed. Use Install / Update on the server "
                           "(BaoBun downloads it), install Temurin %d from adoptium.net, or set the Java path in "
                           "Edit Server." % (JAVA, JAVA))
    return found


def launch_command(srv: dict, exe: str, arg_string: str):
    if not is_hytale(srv):
        return None
    directory = srv.get("directory") or os.path.dirname(exe)
    java = resolve_java(srv)
    parts = [java]
    aot = os.path.join(os.path.dirname(exe), "HytaleServer.aot")
    if os.path.isfile(aot):
        parts.append("-XX:AOTCache=" + aot)
    extra = (srv.get("javaArgs") or "").strip()
    middle = ["-jar", exe]
    assets = find_assets(directory, exe)
    if not assets:
        raise FileNotFoundError("Assets.zip is missing - copy it from your Hytale install (or the downloader) "
                                "into %s." % directory)
    middle += ["--assets", assets]
    from server_manager import split_args
    args = split_args(arg_string or "")
    if not any(a.startswith("--bind") for a in args) and srv.get("port") and int(srv["port"]) != PORT:
        args = ["--bind", "0.0.0.0:%s" % int(srv["port"])] + args
    if os.name == "nt":
        return subprocess.list2cmdline(parts) + ((" " + extra) if extra else "") + " " + \
            subprocess.list2cmdline(middle + args)
    return parts + split_args(extra) + middle + args
