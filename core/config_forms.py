"""Full settings editor: turn a game's config file into form fields and back.

Every setting in the file becomes a field (toggle / number / text / dropdown),
grouped by section, with friendly labels and help from core/config_catalog.py
for the games Tofu knows. Saving changes only the values that changed, in
place: comments, blank lines, key order and quoting stay exactly as they were.

Formats: json, ini (Unreal-style too), toml, properties (key=value / .env),
cfg (Source/Arma "key value;"), yaml (scalars), xmlprop (7 Days to Die
<property name value>), paltuple (Palworld OptionSettings=(A=1,B="x")).

Files come from the server's config list (inside its folder) plus known files
per game that may live elsewhere (Vintage Story data folder, Zomboid folder...).
Passwords are never sent out: their fields only say whether one is set, and an
empty value on save keeps the current one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

SECRET_WORDS = {"password", "passwd", "pass", "pw", "secret", "token", "apikey", "adminpw", "rconpw"}


def is_secret(key: str) -> bool:
    k = key.lower()
    if "password" in k or "passwd" in k:
        return True
    words = re.sub(r"[^a-z0-9 ]", " ", humanize(key).lower()).split()
    return any(w in SECRET_WORDS for w in words) or (len(words) > 1 and words[-1] == "key" and words[-2] == "api")
BOOL_WORDS = {"true": True, "false": False}


class FormError(Exception):
    pass


# --------------------------------------------------------------------------- #
# which files
# --------------------------------------------------------------------------- #
# game key (core.quick_settings.game_key) -> [(label, path template, format)]
KNOWN_FILES = {
    "vintage-story": [("Server settings", "{data}/serverconfig.json", "json")],
    "minecraft": [("Server settings", "server.properties", "properties")],
    "2394010": [("World settings", "{palini}", "paltuple")],
    "2430930": [("Server settings", "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini", "ini"),
                ("Game rules", "ShooterGame/Saved/Config/WindowsServer/Game.ini", "ini")],
    "376030": [("Server settings", "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini", "ini"),
               ("Game rules", "ShooterGame/Saved/Config/WindowsServer/Game.ini", "ini")],
    "294420": [("Server settings", "serverconfig.xml", "xmlprop")],
    "2278520": [("Server settings", "enshrouded_server.json", "json")],
    # checked against WindowsGSM's notes for these games (paths inside the server folder)
    "403240": [("Server settings", "SquadGame/ServerConfig/Server.cfg", "properties")],
    "629800": [("Game settings", "Mordhau/Saved/Config/WindowsServer/Game.ini", "ini")],
    "443030": [("Server settings", "ConanSandbox/Saved/Config/WindowsServer/ServerSettings.ini", "ini"),
               ("Engine settings", "ConanSandbox/Saved/Config/WindowsServer/Engine.ini", "ini")],
    "232130": [("Game settings", "KFGame/Config/PCServer-KFGame.ini", "ini"),
               ("Engine settings", "KFGame/Config/PCServer-KFEngine.ini", "ini")],
    "1690800": [("Game settings", "FactoryGame/Saved/Config/WindowsServer/Game.ini", "ini"),
                ("Engine settings", "FactoryGame/Saved/Config/WindowsServer/Engine.ini", "ini")],
    "380870": [("Server settings", "{pzini}", "properties")],
    "1829350": [("Host settings", "{vrising}", "json"),
                ("Game rules", "{vrising_game}", "json")],
    "1874900": [("Server settings", "{config}", "json")],
    "233780": [("Server settings", "{config}", "cfg")],
    "223350": [("Server settings", "{config}", "cfg")],
    "4020": [("Server settings", "garrysmod/cfg/server.cfg", "cfg")],
    "258550": [],
    "source": [("Server settings", "{srccfg}", "cfg")],
    "3792580": [("Server settings", "SCUM/Saved/Config/WindowsServer/ServerSettings.ini", "ini")],
    "3349480": [("Server settings", "MoriaServerConfig.ini", "ini")],
    "2465200": [("Server settings", "{sotf}", "json")],
    "530870": [("Server settings", "dedicated.yaml", "yaml")],
}
EXT_FORMAT = {".json": "json", ".ini": "ini", ".toml": "toml", ".properties": "properties", ".env": "properties",
              ".cfg": "cfg", ".conf": "properties", ".yaml": "yaml", ".yml": "yaml", ".xml": "xmlprop",
              ".txt": "properties"}


def _resolve(srv: dict, template: str) -> str:
    from core.quick_settings import _resolve as qs_resolve
    if "{vrising_game}" in template:
        host = qs_resolve(srv, "{vrising}")
        return os.path.join(os.path.dirname(host), "ServerGameSettings.json")
    return qs_resolve(srv, template)


def files(mgr, server_id: str, include_all: bool = False) -> List[dict]:
    """[{"id", "label", "path" (shown), "format", "exists", "known"}] known files first.
    include_all also lists files that only open as raw text (format None)."""
    from core.quick_settings import game_key
    srv = mgr.get_server(server_id)
    if not srv:
        raise FormError("Server not found")
    d = srv.get("directory") or ""
    out, seen = [], set()
    key = game_key(srv)
    if not key and srv.get("appId"):
        key = str(srv.get("appId"))
    for i, (label, tmpl, fmt) in enumerate(KNOWN_FILES.get(key or "", [])):
        try:
            p = _resolve(srv, tmpl)
        except Exception:
            continue
        seen.add(os.path.normcase(os.path.realpath(p)))
        out.append({"id": "known:%d" % i, "label": label, "path": _show(p, d), "format": fmt,
                    "exists": os.path.isfile(p), "known": True})
    try:
        listed = mgr.list_configs(server_id)
    except Exception:
        listed = []
    for c in listed:
        full = c.get("absolute") or os.path.join(d, c["relative"])
        if os.path.normcase(os.path.realpath(full)) in seen:
            continue
        fmt = _format_for(full)
        if not fmt and not include_all:
            continue
        out.append({"id": "rel:" + c["relative"], "label": c["relative"], "path": c["relative"], "format": fmt,
                    "exists": True, "known": False, "fresh": bool(c.get("fresh"))})
    return out


def _show(p: str, d: str) -> str:
    try:
        rel = os.path.relpath(p, d)
        return p if rel.startswith("..") else rel.replace("\\", "/")
    except ValueError:
        return p


def _format_for(path: str) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    fmt = EXT_FORMAT.get(ext)
    if fmt == "xmlprop":
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                return "xmlprop" if "<property" in f.read(20000) else None
        except OSError:
            return None
    return fmt


def raw_read(mgr, server_id: str, file_id: str) -> dict:
    """The file as text (for the raw view)."""
    srv, path, fmt, label = _locate(mgr, server_id, file_id, raw=True)
    if not os.path.isfile(path):
        return {"text": "", "exists": False, "format": fmt, "path": _show(path, srv.get("directory") or "")}
    if os.path.getsize(path) > 600_000:
        raise FormError("This file is too big to edit here (over 600 KB) - it's probably game data, not settings.")
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    return {"text": text, "exists": True, "format": fmt, "path": _show(path, srv.get("directory") or "")}


def raw_write(mgr, server_id: str, file_id: str, text: str, user: str = "system", force: bool = False) -> dict:
    """Save the raw text (keeps a .bak). JSON/YAML that don't parse are refused unless force."""
    srv, path, fmt, label = _locate(mgr, server_id, file_id, raw=True)
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise FormError("That text can't be saved here.")
    if not force:
        try:
            if fmt == "json":
                json.loads(text)
            elif fmt == "yaml":
                import yaml
                yaml.safe_load(text)
        except Exception as e:
            raise FormError("This doesn't look like valid %s (%s). Save anyway?" % (fmt.upper(), str(e)[:160]))
    nl = "\n"
    bom = False
    if os.path.isfile(path):
        with open(path, "rb") as f:
            raw = f.read()
        bom = raw.startswith(b"\xef\xbb\xbf")
        nl = "\r\n" if b"\r\n" in raw else "\n"
        shutil.copy2(path, path + ".bak")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + text.replace("\r\n", "\n").replace("\n", nl).encode("utf-8"))
    os.replace(tmp, path)
    mgr._audit("config_edit", server_id, user=user, file=_show(path, srv.get("directory") or ""))
    return {"saved": True, "restart_needed": mgr.is_running(server_id)}


def _locate(mgr, server_id: str, file_id: str, raw: bool = False) -> Tuple[dict, str, str, Optional[str]]:
    """(server, absolute path, format, known label) - only paths Tofu produced itself."""
    from core.quick_settings import game_key
    srv = mgr.get_server(server_id)
    if not srv:
        raise FormError("Server not found")
    if file_id.startswith("known:"):
        key = game_key(srv) or str(srv.get("appId") or "")
        try:
            label, tmpl, fmt = KNOWN_FILES.get(key, [])[int(file_id[6:])]
        except (ValueError, IndexError):
            raise FormError("Unknown settings file")
        return srv, _resolve(srv, tmpl), fmt, label
    if file_id.startswith("rel:"):
        rel = file_id[4:].replace("\\", "/")
        allowed = {c["relative"] for c in mgr.list_configs(server_id)}
        if rel not in allowed:
            raise FormError("That file can't be edited here")
        full = mgr._safe_server_path(srv, rel)
        fmt = _format_for(full)
        if rel.lower().endswith((".bat", ".cmd", ".ps1", ".exe", ".dll", ".vbs")):
            raise FormError("That file can't be edited here")
        if not fmt and not raw:
            raise FormError("Tofu can't show this file as a form - use the raw view")
        return srv, full, fmt, None
    raise FormError("Unknown settings file")


# --------------------------------------------------------------------------- #
# value helpers
# --------------------------------------------------------------------------- #
def infer_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    s = str(value).strip()
    if s.lower() in BOOL_WORDS:
        return "bool"
    if re.fullmatch(r"-?\d+", s) and len(s) < 16:
        return "int"
    if re.fullmatch(r"-?\d+\.\d*|-?\.\d+", s):
        return "float"
    return "text"


def humanize(key: str) -> str:
    k = key.split(".")[-1].lstrip("+-")
    k = re.sub(r"^(b|f|i|n|s)(?=[A-Z])", "", k)            # bSomething / fRate prefixes
    k = re.sub(r"[_\-]+", " ", k)
    k = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", k)
    k = re.sub(r"\s+", " ", k).strip()
    return (k[:1].upper() + k[1:]) if k else key


def _fmt_bool(new: bool, old: str) -> str:
    o = (old or "").strip()
    if o in ("0", "1"):
        return "1" if new else "0"
    word = "true" if new else "false"
    if o[:1].isupper():
        return word.capitalize() if not o.isupper() else word.upper()
    return word


def _fmt_float(new: float, old: str) -> str:
    m = re.fullmatch(r"-?\d*\.(\d+)", (old or "").strip())
    if m:
        return ("%." + str(len(m.group(1))) + "f") % new
    txt = repr(float(new))
    return txt[:-2] if txt.endswith(".0") and "." not in (old or ".") else txt


def coerce(typ: str, value, old_raw: str = "") -> Tuple[object, str]:
    """(python value, text for line formats) - raises FormError on bad input."""
    if typ == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value), _fmt_bool(bool(value), old_raw)
    if typ == "int":
        try:
            v = int(str(value).strip())
        except ValueError:
            raise FormError("“%s” isn't a whole number" % value)
        return v, str(v)
    if typ == "float":
        try:
            v = float(str(value).strip())
        except ValueError:
            raise FormError("“%s” isn't a number" % value)
        return v, _fmt_float(v, old_raw)
    v = "" if value is None else str(value)
    if "\n" in v or "\r" in v:
        raise FormError("Settings can't contain line breaks")
    return v, v


# --------------------------------------------------------------------------- #
# line-format scanners: each entry knows where its value sits in the line
# --------------------------------------------------------------------------- #
def _read(path: str) -> Tuple[List[str], str, bool]:
    """(lines without the final newline, newline style, had a BOM)."""
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    if text.endswith(nl):
        text = text[:-len(nl)]
    return text.split(nl) if text else [], nl, bom


def _write(path: str, lines: List[str], nl: str, bom: bool):
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        data = nl.join(lines) + nl
        f.write((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))
    os.replace(tmp, path)


def _span_value(line: str, start: int, cfg_semicolon: bool = False, hash_comments: bool = False):
    """(value, start, end, quoted) for the value that begins at `start`."""
    n = len(line)
    while start < n and line[start] in " \t":
        start += 1
    if start < n and line[start] == '"':
        end = start + 1
        while end < n and line[end] != '"':
            end += 2 if line[end] == "\\" else 1
        return line[start + 1:end], start + 1, min(end, n), True
    end = n
    if hash_comments:
        h = line.find(" #", start)
        end = h if h >= 0 else end
    if cfg_semicolon:
        sc = line.find(";", start)
        end = sc if sc >= 0 else end
        cm = line.find("//", start)
        end = min(end, cm) if cm >= 0 else end
    while end > start and line[end - 1] in " \t":
        end -= 1
    return line[start:end], start, end, False


def scan(lines: List[str], fmt: str) -> List[dict]:
    entries = []
    section = None
    depth = 0
    ystack: List[Tuple[int, str]] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if fmt in ("ini", "toml", "properties"):
            if s[0] in ";#" or s.startswith("//"):
                continue
            if s.startswith("[") and s.endswith("]") and fmt != "properties":
                section = s[1:-1].strip()
                continue
            m = re.match(r"^(\s*)([^=:\s][^=]*?)\s*=", line) or (
                re.match(r"^(\s*)([^=:\s][^:]*?)\s*:", line) if fmt == "properties" else None)
            if not m:
                continue
            key = m.group(2).strip()
            val, a, b, q = _span_value(line, m.end(), hash_comments=(fmt == "toml"))
            if fmt == "toml" and val.startswith(("[", "{")):
                entries.append({"line": i, "section": section, "key": key, "value": val, "start": a, "end": b,
                                "quoted": q, "readonly": True})
                continue
            entries.append({"line": i, "section": section, "key": key, "value": val, "start": a, "end": b,
                            "quoted": q, "readonly": key[:1] in "+-!"})
            if fmt == "ini" and key == "OptionSettings" and val.startswith("("):
                entries[-1]["readonly"] = True
        elif fmt == "paltuple":
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1]
                continue
            m = re.match(r"^\s*OptionSettings\s*=\s*\(", line)
            if not m:
                continue
            pos = m.end()
            n = len(line)
            while pos < n:
                km = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line[pos:])
                if not km:
                    break
                key = km.group(1)
                vstart = pos + km.end()
                if vstart < n and line[vstart] == '"':
                    vend = line.find('"', vstart + 1)
                    vend = n if vend < 0 else vend
                    val, a, b, q = line[vstart + 1:vend], vstart + 1, vend, True
                    nxt = vend + 1
                elif vstart < n and line[vstart] == "(":
                    close = line.find(")", vstart)
                    close = n if close < 0 else close
                    val, a, b, q = line[vstart:close + 1], vstart, close + 1, False
                    nxt = close + 1
                    entries.append({"line": i, "section": "World settings", "key": key, "value": val,
                                    "start": a, "end": b, "quoted": q, "readonly": True})
                    pos = line.find(",", nxt)
                    pos = n if pos < 0 else pos + 1
                    continue
                else:
                    vend = vstart
                    while vend < n and line[vend] not in ",)":
                        vend += 1
                    val, a, b, q = line[vstart:vend], vstart, vend, False
                    nxt = vend
                entries.append({"line": i, "section": "World settings", "key": key, "value": val,
                                "start": a, "end": b, "quoted": q, "readonly": False})
                pos = line.find(",", nxt)
                if pos < 0:
                    break
                pos += 1
        elif fmt == "xmlprop":
            for m in re.finditer(r'<property\s+name="([^"]+)"\s+value="([^"]*)"', line):
                import html as _html
                entries.append({"line": i, "section": None, "key": m.group(1), "value": _html.unescape(m.group(2)),
                                "start": m.start(2), "end": m.end(2), "quoted": True, "readonly": False})
        elif fmt == "cfg":
            if s.startswith("//"):
                continue
            opens, closes = s.count("{"), s.count("}")
            if depth == 0 and not opens and not closes:
                m = re.match(r'^(\s*)([A-Za-z_][\w.\[\]]*)\s*(=\s*|\s+)(?=\S)', line)
                if m:
                    key = m.group(2)
                    val, a, b, q = _span_value(line, m.end(), cfg_semicolon=True)
                    ro = key.endswith("[]") or val.startswith("{")
                    entries.append({"line": i, "section": None, "key": key, "value": val, "start": a, "end": b,
                                    "quoted": q, "readonly": ro, "style": "eq" if "=" in m.group(3) else "space"})
            depth = max(0, depth + opens - closes)
        elif fmt == "yaml":
            if s.startswith("#") or s.startswith("- ") or s == "-" or s in ("---", "..."):
                continue
            m = re.match(r"^(\s*)([^:#\s][^:#]*?|\"[^\"]+\"|'[^']+')\s*:(\s+|$)", line)
            if not m:
                continue
            indent = len(m.group(1))
            key = m.group(2).strip().strip("'\"")
            while ystack and ystack[-1][0] >= indent:
                ystack.pop()
            path = ".".join([k for _, k in ystack])
            rest = line[m.end():]
            if not rest.strip() or rest.strip().startswith("#"):
                ystack.append((indent, key))
                continue
            val, a, b, q = _span_value(line, m.end(), hash_comments=True)
            qchar = '"' if q else None
            if not q and val[:1] == "'" and val.endswith("'") and len(val) >= 2:
                val, a, b, q, qchar = val[1:-1].replace("''", "'"), a + 1, b - 1, True, "'"
            ro = val[:1] in ("[", "{", "|", ">", "&", "*")
            entries.append({"line": i, "section": path or None, "key": key, "value": val, "start": a, "end": b,
                            "quoted": q, "qchar": qchar, "readonly": ro})
    # duplicate keys in one section (Unreal arrays, repeated lines) can't be edited one by one
    count: Dict[tuple, int] = {}
    for e in entries:
        count[(e["section"], e["key"].lower())] = count.get((e["section"], e["key"].lower()), 0) + 1
    for e in entries:
        if count[(e["section"], e["key"].lower())] > 1:
            e["readonly"] = True
            e["multi"] = True
    return entries


# --------------------------------------------------------------------------- #
# JSON (structured)
# --------------------------------------------------------------------------- #
def _json_load(path: str):
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    try:
        return json.loads(text, object_pairs_hook=OrderedDict), True, text
    except ValueError:
        from core.mods_common import lenient_json
        return lenient_json(text), False, text


def _json_entries(data, path=()) -> List[dict]:
    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            p = path + (k,)
            if isinstance(v, dict):
                out += _json_entries(v, p)
            elif isinstance(v, list):
                simple = all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v)
                out.append({"path": list(p), "section": ".".join(path) or None, "key": k, "value": v,
                            "readonly": not simple, "list": True})
            else:
                out.append({"path": list(p), "section": ".".join(path) or None, "key": k, "value": v,
                            "readonly": False})
    return out


# --------------------------------------------------------------------------- #
# building the form
# --------------------------------------------------------------------------- #
def form(mgr, server_id: str, file_id: str) -> dict:
    from core import config_catalog
    srv, path, fmt, label = _locate(mgr, server_id, file_id)
    exists = os.path.isfile(path)
    cat = config_catalog.lookup(srv, file_id, label)
    fields, present = [], set()
    if exists:
        if fmt == "json":
            data, strict, _ = _json_load(path)
            for e in _json_entries(data):
                fields.append(_field(e, json.dumps(e["path"]), cat, json_mode=True))
                present.add(e["key"].lower())
            if not strict:
                for f in fields:
                    f["readonly"] = True
        else:
            lines, _, _ = _read(path)
            for e in scan(lines, fmt):
                fields.append(_field(e, json.dumps([e["section"], e["key"]]), cat))
                present.add(e["key"].lower())
    # settings the game understands but the file doesn't list yet
    suggestions = []
    for key, meta in (cat or {}).items():
        if key.lower() in present or meta.get("hidden"):
            continue
        section = meta.get("section")
        fid = json.dumps(meta["path"]) if fmt == "json" and meta.get("path") else json.dumps([section, key])
        f = _field({"section": section, "key": key, "value": meta.get("default", ""), "readonly": False},
                   fid, cat)
        f["missing"] = True
        suggestions.append(f)
    order = []
    for meta in (cat or {}).values():
        if meta.get("group") and meta["group"] not in order:
            order.append(meta["group"])
    groups = _group(fields, order)
    return {"file": file_id, "path": _show(path, srv.get("directory") or ""), "format": fmt, "exists": exists,
            "label": label or os.path.basename(path), "groups": groups, "suggestions": suggestions,
            "running": mgr.is_running(server_id),
            "note": None if exists else "This file doesn't exist yet - the game usually writes it on its "
                                        "first start. Start the server once, stop it, then come back."}


def _field(e: dict, fid: str, cat: Optional[dict], json_mode: bool = False) -> dict:
    from core.config_catalog import meta_for
    key = e["key"]
    meta = meta_for(cat, key)
    raw = e["value"]
    if e.get("list"):
        typ = "list"
        value = ", ".join(str(x) for x in raw) if not e.get("readonly") else json.dumps(raw)
    else:
        typ = meta.get("type") or infer_type("" if raw is None else raw)
        value = "" if raw is None else raw
        if typ == "bool" and not isinstance(raw, bool):
            value = str(raw).strip().lower() in ("1", "true", "yes", "on")
        elif typ in ("int", "float") and isinstance(raw, str):
            try:
                value = int(raw) if typ == "int" else float(raw)
            except ValueError:
                typ, value = "text", raw
    secret = is_secret(key) or meta.get("type") == "password"
    f = {"id": fid, "key": key, "section": e.get("section"), "label": meta.get("label") or humanize(key),
         "help": meta.get("help"), "type": "password" if secret and typ in ("text", "int") else typ,
         "readonly": bool(e.get("readonly")), "group": meta.get("group")}
    if f["type"] == "password":
        f["set"] = bool(str(raw or "").strip())
        f["value"] = ""
    else:
        f["value"] = value
        if not json_mode and not isinstance(raw, (list, dict)):
            f["raw"] = "" if raw is None else str(raw)   # keeps 1.000000 / True style on save
    for k in ("min", "max", "options", "default", "unit", "step"):
        if k in meta:
            f[k] = meta[k]
    if e.get("multi"):
        f["help"] = (f.get("help") or "") + (" " if f.get("help") else "") + \
            "This setting appears more than once in the file - edit it in the raw file view."
    return f


def _group(fields: List[dict], order: Optional[List[str]] = None) -> List[dict]:
    groups: "OrderedDict[str, List[dict]]" = OrderedDict()
    friendly = [f for f in fields if f.get("group")]
    order = list(order or [])
    for f in friendly:
        if f["group"] not in order:
            order.append(f["group"])
    for g in order:
        fs = [f for f in friendly if f["group"] == g]
        if fs:
            groups[g] = fs
    for f in fields:
        if f.get("group"):
            continue
        name = f.get("section") or "Other settings"
        if friendly and name == "Other settings":
            name = "More settings"
        groups.setdefault(name, []).append(f)
    return [{"name": k, "fields": v} for k, v in groups.items()]


# --------------------------------------------------------------------------- #
# saving
# --------------------------------------------------------------------------- #
def save(mgr, server_id: str, file_id: str, changes: Dict[str, object], user: str = "system") -> dict:
    """changes: {field id: new value}. Unknown ids, read-only fields and blank
    passwords are ignored. Returns {"saved": [keys], "restart_needed"}."""
    from core import config_catalog
    if not isinstance(changes, dict) or not changes:
        return {"saved": [], "restart_needed": False}
    if len(changes) > 500:
        raise FormError("Too many changes at once")
    srv, path, fmt, label = _locate(mgr, server_id, file_id)
    current = form(mgr, server_id, file_id)
    by_id = {f["id"]: f for g in current["groups"] for f in g["fields"]}
    by_id.update({f["id"]: f for f in current["suggestions"]})
    cat = config_catalog.lookup(srv, file_id, label) or {}
    plan = []
    for fid, value in changes.items():
        f = by_id.get(fid)
        if not f or f.get("readonly"):
            continue
        if f["type"] == "password":
            if value in (None, ""):
                continue   # blank = keep the current one
            typ = "text"
        else:
            typ = f["type"]
        if typ == "select":
            opts = [str(o[0] if isinstance(o, (list, tuple)) else o) for o in f.get("options") or []]
            if str(value) not in opts:
                raise FormError("%s: pick one of the listed choices" % f["label"])
            from core.config_catalog import meta_for
            base = meta_for(cat, f["key"]).get("valuetype") or "text"
            pyval, text = coerce(base, value, "")
        elif typ == "list":
            parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
            pyval, text = parts, ", ".join(parts)
        else:
            old = f.get("raw")
            if old is None:
                old = str(f.get("default", "")) if f.get("missing") else ""
            pyval, text = coerce(typ, value, old)
        if typ in ("int", "float"):
            if f.get("min") is not None and pyval < f["min"]:
                raise FormError("%s can't be below %s" % (f["label"], f["min"]))
            if f.get("max") is not None and pyval > f["max"]:
                raise FormError("%s can't be above %s" % (f["label"], f["max"]))
        if not f.get("missing") and f["type"] != "password" and _same(f, pyval):
            continue
        plan.append((f, pyval, text))
    if not plan:
        return {"saved": [], "restart_needed": False}
    if mgr.is_running(server_id) and rewritten_on_exit(path):
        # The game writes this file itself when it stops, which would undo the change -
        # keep it and apply it right before the next start instead.
        _queue(mgr, server_id, file_id, {f["id"]: changes[f["id"]] for f, _, _ in plan})
        mgr._audit("game_settings_queued", server_id, user=user, file=current["path"],
                   keys=[f["key"] for f, _, _ in plan][:50])
        return {"saved": [f["key"] for f, _, _ in plan], "restart_needed": True, "pending": True,
                "note": "The game rewrites this file when it stops, so Tofu will apply these changes "
                        "right before it next starts. Restart the server to use them."}
    existed = os.path.isfile(path)
    original = None
    if existed:
        with open(path, "rb") as fh:
            original = fh.read()
    if fmt == "json":
        _save_json(path, plan)
    else:
        _save_lines(path, fmt, plan)
    _verify(mgr, server_id, file_id, path, plan, original)
    mgr._audit("game_settings", server_id, user=user, file=current["path"], keys=[f["key"] for f, _, _ in plan][:50])
    return {"saved": [f["key"] for f, _, _ in plan], "restart_needed": mgr.is_running(server_id),
            "backup": os.path.basename(path) + ".bak"}


def _loosely_same(a, b) -> bool:
    """The same setting written differently (3 vs "3", True vs "true", 1.0 vs "1")."""
    def norm(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, tuple)):
            return ",".join(norm(x) for x in v)
        t = str(v if v is not None else "").strip().strip('"').strip("'").lower()
        if t in ("true", "false"):
            return t
        try:
            fl = float(t)
            return repr(int(fl)) if fl == int(fl) else repr(fl)
        except ValueError:
            return t
    return norm(a) == norm(b)


def _verify(mgr, server_id, file_id, path, plan, original):
    """Read the file back the way the game would: every changed value must be there
    and the file must still parse. If not, put the previous version back."""
    problem = None
    try:
        after = form(mgr, server_id, file_id)
        got = {f["id"]: f for g in after["groups"] for f in g["fields"]}
        for f, pyval, _text in plan:
            if f["type"] == "password":
                continue
            g = got.get(f["id"])
            if g is None:
                problem = "%s didn't end up in the file" % f["label"]
                break
            if not _same(g, pyval) and not _loosely_same(g.get("value"), pyval):
                problem = "%s reads back as %r" % (f["label"], g.get("value"))
                break
    except Exception as e:
        problem = "the file no longer reads correctly (%s)" % e
    if problem:
        try:
            if original is not None:
                with open(path + ".tmp", "wb") as fh:
                    fh.write(original)
                os.replace(path + ".tmp", path)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        raise FormError("Not saved - %s, so the file was put back as it was. Use the Raw file view for this "
                        "one." % problem)


# --------------------------------------------------------------------------- #
# changes waiting for the server to stop (games that rewrite their settings on exit)
# --------------------------------------------------------------------------- #
def rewritten_on_exit(path: str) -> bool:
    """Unreal Engine servers (ARK, Palworld, Conan, Satisfactory, Mordhau ...) write
    Saved/Config/... back when they shut down."""
    p = path.replace("\\", "/").lower()
    return "/saved/config/" in p


def _pending_path(mgr, server_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(server_id))
    return os.path.join(str(mgr.config_path.parent), "runtime", "pending_settings", safe + ".json")


def pending(mgr, server_id: str) -> Dict[str, dict]:
    try:
        with open(_pending_path(mgr, server_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _queue(mgr, server_id, file_id, changes):
    data = pending(mgr, server_id)
    data.setdefault(file_id, {}).update(changes)
    p = _pending_path(mgr, server_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(p + ".tmp", p)


def apply_pending(mgr, server_id: str) -> List[str]:
    """Apply queued changes (called right before the server starts). Returns notes."""
    data = pending(mgr, server_id)
    if not data:
        return []
    notes = []
    for file_id, changes in data.items():
        try:
            r = save(mgr, server_id, file_id, changes, user="queued")
            if r.get("saved"):
                notes.append("%d setting%s in %s" % (len(r["saved"]), "" if len(r["saved"]) == 1 else "s", file_id))
        except Exception as e:
            notes.append("couldn't apply waiting changes to %s: %s" % (file_id, e))
    try:
        os.remove(_pending_path(mgr, server_id))
    except OSError:
        pass
    return notes


def _same(f, pyval) -> bool:
    cur = f.get("value")
    if f["type"] == "list":
        return [p.strip() for p in str(cur or "").split(",") if p.strip()] == pyval
    if f["type"] in ("int", "float"):
        try:
            return float(cur) == float(pyval)
        except (TypeError, ValueError):
            return False
    return cur == pyval


def _save_json(path: str, plan):
    if os.path.isfile(path):
        data, strict, text = _json_load(path)
        if not strict:
            raise FormError("This file has comments, so edit it in the raw file view instead.")
        m = re.search(r"\n([ \t]+)\S", text)
        indent = m.group(1) if m else "  "
    else:
        data, indent = OrderedDict(), "  "
    for f, pyval, _ in plan:
        keys = [k for k in json.loads(f["id"]) if k is not None] if isinstance(json.loads(f["id"]), list) else []
        if not keys or not all(isinstance(k, str) for k in keys):
            continue
        if f.get("missing") and len(keys) == 2 and "." in keys[0]:
            keys = keys[0].split(".") + [keys[1]]
        cur = data
        for k in keys[:-1]:
            if not isinstance(cur.get(k), dict):
                cur[k] = OrderedDict()
            cur = cur[k]
        cur[keys[-1]] = pyval
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(data, indent=indent if indent != "\t" else "\t", ensure_ascii=False))
    os.replace(tmp, path)


def _quote_for(fmt: str, text: str, quoted: bool, qchar: Optional[str] = None) -> str:
    if fmt == "xmlprop":
        return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    if fmt == "yaml" and quoted:
        return text.replace("'", "''") if qchar == "'" else text.replace("\\", "\\\\").replace('"', '\\"')
    if quoted:
        return text.replace('"', "'") if fmt in ("paltuple", "xmlprop", "cfg", "toml", "yaml") else text
    if fmt == "yaml" and (re.search(r":\s|#|^[\s\-?&*!|>'\"%@`{\[]", text) or text.strip() != text):
        return '"%s"' % text.replace('"', '\\"')
    if fmt == "cfg" and re.search(r"[\s;]", text):
        return '"%s"' % text.replace('"', "'")
    if fmt == "xmlprop":
        return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return text


def _save_lines(path: str, fmt: str, plan):
    if os.path.isfile(path):
        lines, nl, bom = _read(path)
    else:
        lines, nl, bom = [], "\r\n" if os.name == "nt" else "\n", False
    entries = {(e["section"], e["key"]): e for e in scan(lines, fmt)}
    edits: Dict[int, List[tuple]] = {}
    additions = []
    for f, pyval, text in plan:
        sec, key = json.loads(f["id"])
        e = entries.get((sec, key))
        if e is None and not f.get("missing"):
            raise FormError("The file changed while you were editing - reload and try again.")
        if e is not None:
            if fmt == "paltuple" and not e["quoted"] and isinstance(pyval, str):
                text = text.replace(",", " ").replace(")", " ").strip()   # would break the (A=1,B=2) list
            edits.setdefault(e["line"], []).append((e["start"], e["end"],
                                                    _quote_for(fmt, text, e["quoted"], e.get("qchar"))))
        else:
            additions.append((sec, key, pyval, text, f))
    for ln, items in edits.items():
        line = lines[ln]
        for a, b, val in sorted(items, key=lambda x: -x[0]):
            line = line[:a] + val + line[b:]
        lines[ln] = line
    for sec, key, pyval, text, f in additions:
        _add(lines, fmt, sec, key, text, isinstance(pyval, str) and f["type"] in ("text", "password"))
    _write(path, lines, nl, bom)


def _add(lines: List[str], fmt: str, sec, key: str, text: str, is_text: bool):
    if fmt == "paltuple":
        for i, line in enumerate(lines):
            m = re.match(r"^\s*OptionSettings\s*=\s*\(", line)
            if m:
                close = line.rfind(")")
                val = '"%s"' % text.replace('"', "'") if is_text else text
                sep = "," if line[m.end():close].strip() else ""
                lines[i] = line[:close] + sep + "%s=%s" % (key, val) + line[close:]
                return
        if not any(l.strip() == "[/Script/Pal.PalGameWorldSettings]" for l in lines):
            lines.append("[/Script/Pal.PalGameWorldSettings]")
        lines.append('OptionSettings=(%s=%s)' % (key, '"%s"' % text if is_text else text))
        return
    if fmt == "xmlprop":
        val = _quote_for("xmlprop", text, False)
        for i in range(len(lines) - 1, -1, -1):
            if "</ServerSettings>" in lines[i]:
                ind = re.match(r"^(\s*)", lines[i - 1] if i else "").group(1) or "\t"
                lines.insert(i, '%s<property name="%s" value="%s" />' % (ind, key, val))
                return
        raise FormError("Couldn't find </ServerSettings> in the file")
    if fmt == "cfg":
        style = next((l for l in lines if re.match(r"^\s*\w+\s*=", l)), None)
        val = '"%s"' % text.replace('"', "'") if is_text else text
        lines.append(("%s = %s;" % (key, val)) if style else ("%s %s" % (key, val)))
        return
    if fmt == "yaml":
        if sec:
            raise FormError("Add nested YAML settings in the raw file view")
        lines.append("%s: %s" % (key, _quote_for("yaml", text, False)))
        return
    val = '"%s"' % text.replace('"', "'") if (fmt == "toml" and is_text) else text
    sep = " = " if any(re.match(r"^\s*[^=\s]+ = ", l) for l in lines) else "="
    new = "%s%s%s" % (key, sep, val)
    if not sec:
        # before the first [section] (or at the end of a sectionless file)
        first = next((i for i, l in enumerate(lines) if l.strip().startswith("[") and l.strip().endswith("]")), None)
        if first is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(new)
        else:
            lines.insert(first, new)
        return
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == "[%s]" % sec.lower()), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines += ["[%s]" % sec, new]
        return
    end = start + 1
    while end < len(lines) and not (lines[end].strip().startswith("[") and lines[end].strip().endswith("]")):
        end += 1
    ins = end
    while ins > start + 1 and not lines[ins - 1].strip():
        ins -= 1
    lines.insert(ins, new)
