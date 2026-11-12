"""Encrypt secrets at rest (Steam password, Discord token).

On Windows secrets are encrypted with DPAPI (CryptProtectData), tied to the
current Windows user account: the files can be backed up or copied, but only
the same user on the same PC can decrypt them. Both the app and the background
agent run as that user, so both can read them.

Stored form: "dpapi:<base64>". On other platforms (only used for development
and tests) the value is merely encoded ("b64:<base64>") and a warning is printed.
"""

from __future__ import annotations

import base64
import os
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_B64 = "b64:"
_ENTROPY = b"BaoBunControlPanel"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, _keep1 = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI " + ("encrypt" if encrypt else "decrypt") + " failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(plaintext: str) -> str:
    if not plaintext:
        return ""
    raw = plaintext.encode("utf-8")
    if os.name == "nt":
        return _PREFIX_DPAPI + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    print("[WARN] secrets are only encoded (not encrypted) on this platform", file=sys.stderr)
    return _PREFIX_B64 + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    """Decrypt a protected value. Plain strings are returned unchanged (legacy files)."""
    if not value:
        return ""
    if value.startswith(_PREFIX_DPAPI):
        return _dpapi(base64.b64decode(value[len(_PREFIX_DPAPI):]), False).decode("utf-8")
    if value.startswith(_PREFIX_B64):
        return base64.b64decode(value[len(_PREFIX_B64):]).decode("utf-8")
    return value


# --------------------------------------------------------------------------- #
# Discord token helpers (discord_config.json)
# --------------------------------------------------------------------------- #
PLACEHOLDER_TOKENS = ("", "YOUR_DISCORD_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")


def discord_token(config: dict) -> str:
    """The usable bot token from a discord_config dict ('' if not configured).

    A plaintext "token" (freshly pasted) wins over the stored encrypted one."""
    tok = config.get("token") or ""
    if tok not in PLACEHOLDER_TOKENS:
        return tok
    enc = config.get("token_protected")
    if enc:
        try:
            return unprotect(enc)
        except Exception as e:
            print(f"[WARN] could not decrypt the Discord token: {e}")
    return ""


SETTINGS_TOKEN_KEY = "discord_bot_token"


def bot_token(base_dir, config: dict = None) -> str:
    """The bot token to use: discord_config.json first (older setups), otherwise the
    one pasted into BaoBun's Discord bot window (kept encrypted in settings.json)."""
    tok = discord_token(config or {})
    if tok:
        return tok
    import json
    from pathlib import Path
    try:
        data = json.loads((Path(base_dir) / "settings.json").read_text(encoding="utf-8"))
        enc = data.get(SETTINGS_TOKEN_KEY + "_protected")
        return unprotect(enc) if enc else ""
    except Exception:
        return ""


def token_app_id(token: str):
    """The bot's application id, read from the first part of its token (for the
    invite link). None if it can't be read."""
    import base64
    try:
        part = (token or "").split(".")[0]
        part += "=" * (-len(part) % 4)
        val = base64.urlsafe_b64decode(part.encode()).decode("ascii")
        return int(val) if val.isdigit() and 15 <= len(val) <= 21 else None
    except Exception:
        return None


def looks_like_bot_token(token: str) -> bool:
    parts = (token or "").strip().split(".")
    return len(parts) == 3 and all(parts) and token_app_id(token) is not None


def migrate_discord_config(path) -> bool:
    """Encrypt a plaintext bot token / OAuth client secret in discord_config.json
    in place. Returns True if the file changed."""
    import json
    from pathlib import Path
    from core.paths import atomic_write_json
    p = Path(path)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    changed = False
    tok = cfg.get("token") or ""
    if tok not in PLACEHOLDER_TOKENS:
        try:
            cfg["token_protected"] = protect(tok)
            cfg["token"] = ""
            cfg["_token_note"] = ("The bot token is stored encrypted in token_protected (only this Windows "
                                  "user can read it). To change it, paste the new token into \"token\"; "
                                  "BaoBun encrypts it on the next start.")
            changed = True
        except Exception as e:
            print(f"[WARN] could not encrypt the Discord token: {e}")
    sec = cfg.get("oauth_client_secret") or ""
    if sec:
        try:
            cfg["oauth_client_secret_protected"] = protect(sec)
            cfg["oauth_client_secret"] = ""
            changed = True
        except Exception as e:
            print(f"[WARN] could not encrypt the OAuth client secret: {e}")
    if changed:
        atomic_write_json(p, cfg)
    return changed
