"""Mod browser helpers: richer search results and a details page per mod.

Descriptions on the mod sites are HTML (Vintage Story ModDB) or Markdown
(Modrinth) written by mod authors. They are turned into plain blocks
({"t": "h"|"p"|"li"|"img", "text"/"src"}) so the panels can show them with
pictures and headings without ever rendering someone else's HTML.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional

IMG_HOSTS = ("moddbcdn.vintagestory.at", "mods.vintagestory.at", "cdn.modrinth.com", "i.imgur.com",
             "raw.githubusercontent.com", "github.com", "user-images.githubusercontent.com",
             "media.forgecdn.net", "assets.umod.org", "steamuserimages-a.akamaihd.net",
             "images.steamusercontent.com", "gcdn.thunderstore.io")
SORTS = ["popular", "trending", "updated", "newest", "follows"]
MAX_BLOCKS = 220


def safe_img(src: Optional[str]) -> Optional[str]:
    """Only https images from the mod sites' own image hosts (the web panel's CSP allows these)."""
    s = (src or "").strip()
    m = re.match(r"^https://([^/]+)/", s)
    if not m:
        return None
    host = m.group(1).lower()
    return s if any(host == h or host.endswith("." + h) for h in IMG_HOSTS) else None


class _Blocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[dict] = []
        self.buf: List[str] = []
        self.kind = "p"
        self.skip = 0

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.out.append({"t": self.kind, "text": text[:2000]})
        self.buf = []
        self.kind = "p"

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe"):
            self.skip += 1
            return
        if tag in ("p", "div", "br", "tr", "blockquote", "summary"):
            self.flush()
        elif tag in ("h1", "h2", "h3", "h4", "h5"):
            self.flush()
            self.kind = "h"
        elif tag == "li":
            self.flush()
            self.kind = "li"
        elif tag == "img":
            src = safe_img(dict(attrs).get("src"))
            if src:
                self.flush()
                self.out.append({"t": "img", "src": src})

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "li", "tr", "blockquote", "summary"):
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def html_to_blocks(text: str) -> List[dict]:
    p = _Blocks()
    try:
        p.feed(text or "")
        p.close()
        p.flush()
    except Exception:
        return [{"t": "p", "text": re.sub(r"<[^>]+>", " ", text or "")[:4000]}]
    return _tidy(p.out)


def md_to_blocks(md: str) -> List[dict]:
    out: List[dict] = []
    para: List[str] = []

    def flush():
        if para:
            out.append({"t": "p", "text": _md_inline(" ".join(para))})
            para.clear()

    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)|<img[^>]+src=[\"']([^\"']+)", line)
        rest = re.sub(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>", "", line).strip()
        for a, b in imgs:
            src = safe_img(a or b)
            if src:
                flush()
                out.append({"t": "img", "src": src})
        if not rest:
            continue
        if rest.startswith("#"):
            flush()
            out.append({"t": "h", "text": _md_inline(rest.lstrip("#").strip())})
        elif re.match(r"^([-*+]|\d+\.)\s+", rest):
            flush()
            out.append({"t": "li", "text": _md_inline(re.sub(r"^([-*+]|\d+\.)\s+", "", rest))})
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", rest) or rest.startswith("|"):
            flush()
        else:
            para.append(re.sub(r"<[^>]+>", "", rest))
    flush()
    return _tidy(out)


def _md_inline(s: str) -> str:
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"(\*\*|__|\*|_|`)", "", s)
    return html.unescape(s).strip()


def _tidy(blocks: List[dict]) -> List[dict]:
    out, seen_img = [], set()
    for b in blocks:
        if b["t"] == "img":
            if b["src"] in seen_img:
                continue
            seen_img.add(b["src"])
        elif not b.get("text"):
            continue
        out.append(b)
        if len(out) >= MAX_BLOCKS:
            break
    return out


# --------------------------------------------------------------------------- #
# per-site details
# --------------------------------------------------------------------------- #
def vs_details(mgr, srv: dict, mod_id: str) -> dict:
    mod = mgr.vs_get_mod(int(mod_id) if str(mod_id).isdigit() else mod_id) or {}
    if not mod:
        raise ValueError("Mod not found")
    game = None
    try:
        game = mgr.vs_server_version(srv["id"])
    except Exception:
        pass
    releases = mod.get("releases") or []
    rel, match = mgr._vs_pick_release(releases, game) if releases else (None, "none")
    blocks = html_to_blocks(mod.get("text") or "")
    shots = [b["src"] for b in blocks if b["t"] == "img"][:12]
    links = [(lbl, mod.get(k)) for lbl, k in (("Homepage", "homepageurl"), ("Source code", "sourcecodeurl"),
                                               ("Issues", "issuetrackerurl"), ("Wiki", "wikiurl"))
             if (mod.get(k) or "").startswith("https://")]
    alias = mod.get("urlalias")
    return {"id": str(mod.get("modid") or mod_id), "name": mod.get("name"), "author": mod.get("author"),
            "icon": safe_img(mod.get("logofile") or mod.get("logofilename")),
            "downloads": mod.get("downloads"), "follows": mod.get("follows"), "tags": mod.get("tags") or [],
            "side": mod.get("side"), "updated": mod.get("lastreleased"), "created": mod.get("created"),
            "url": ("https://mods.vintagestory.at/" + alias) if alias else
                   "https://mods.vintagestory.at/show/mod/%s" % mod.get("assetid", mod_id),
            "blocks": blocks, "images": shots, "links": links,
            "game_version": game,
            "release": ({"version": rel.get("modversion"), "for": rel.get("tags") or [], "date": rel.get("created"),
                         "match": match} if rel else None),
            "versions": [{"version": r.get("modversion"), "for": (r.get("tags") or [])[:6], "date": r.get("created")}
                         for r in releases[:8]],
            "match_ids": _ids(mod.get("modid"), mod.get("urlalias"), *(r.get("modidstr") for r in releases[:1]))}


def modrinth_details(backend, srv: dict, slug: str) -> dict:
    from core.mods_common import http_json, friendly_http
    import urllib.parse
    try:
        p = http_json("%s/project/%s" % (backend.API, urllib.parse.quote(str(slug))))
    except Exception as e:
        raise friendly_http(e, "Modrinth project '%s'" % slug)
    try:
        loader, version = backend.detect(srv)
    except Exception:
        loader, version = None, None
    gallery = [safe_img(g.get("url")) for g in sorted(p.get("gallery") or [], key=lambda g: not g.get("featured"))]
    gv = p.get("game_versions") or []
    ok = None
    if version:
        ok = version in gv
    links = [(lbl, p.get(k)) for lbl, k in (("Source code", "source_url"), ("Issues", "issues_url"),
                                             ("Wiki", "wiki_url"), ("Discord", "discord_url"))
             if (p.get(k) or "").startswith("https://")]
    return {"id": p.get("slug") or slug, "name": p.get("title"), "author": None,
            "icon": safe_img(p.get("icon_url")), "downloads": p.get("downloads"), "follows": p.get("followers"),
            "tags": (p.get("categories") or []) + [l for l in p.get("loaders") or []],
            "side": "server" if p.get("client_side") == "unsupported" else
                    ("both" if p.get("server_side") == "required" else p.get("server_side")),
            "updated": p.get("updated"), "created": p.get("published"),
            "url": "https://modrinth.com/%s/%s" % (p.get("project_type") or "mod", p.get("slug") or slug),
            "summary": p.get("description"), "blocks": md_to_blocks(p.get("body") or ""),
            "images": [g for g in gallery if g][:12], "links": links, "game_version": version,
            "release": {"version": None, "for": gv[-6:], "date": p.get("updated"),
                        "match": "exact" if ok else ("incompatible" if ok is False else "unknown")},
            "versions": [], "match_ids": _ids(p.get("slug"), p.get("id"))}


def _ids(*vals) -> List[str]:
    return sorted({str(v).lower() for v in vals if v not in (None, "")})
