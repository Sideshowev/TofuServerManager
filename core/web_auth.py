"""Web panel sign-in: "Log in with Discord" + sessions + permissions.

Who gets in is decided by your Discord guild:
  * admins    - user IDs in ``web_admin_ids`` or members with a role in ``web_admin_role_ids``
                (everything the panel offers)
  * operators - members with a role in ``web_operator_role_ids``
                (view, start/stop/restart, console)
Anyone else (or anyone not in the guild) is refused. Roles are read fresh from
Discord at every login, so removing someone's role removes their access at
their next login; sessions last 12 hours.

Settings live in discord_config.json: oauth_client_id, oauth_client_secret
(encrypted on first start into oauth_client_secret_protected), guild_id,
panel_url, web_admin_ids, web_admin_role_ids, web_operator_role_ids
(separate from the bot's admin_ids, so bot stop-rights never grant panel access).
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional

DISCORD_API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
SESSION_TTL = 12 * 3600
USER_AGENT = "DiscordBot (https://github.com/tofu-server-manager, 1.0) TofuServerManager"

VIEW = {"server.status", "logs.since", "backup.list",
        "schedules.get", "server.get", "server.check_update", "games.list", "activity.list",
        "mods.list", "mods.check", "mods.updates", "mods.search", "metrics.history", "metrics.series", "server.connect_info", "server.reach", "server.game_settings", "server.quick_commands",
        "metrics.sparklines", "server.install_status", "server.install_options",
        "server.install_versions", "health.checkup", "mods.details", "ports.plan",
        "ark.cluster", "ark.rules", "ark.spawns", "ark.loot", "ark.catalog", "agent.update_status", "host.settings",
        "app.version", "fleet.status"}
OPERATE = {"server.start", "server.stop", "server.restart", "console.send", "server.broadcast"}
# config files can hold passwords in plain text, so reading them is for admins too
MANAGE = {"config.list", "config.read", "config.form_files", "config.form", "config.form_save", "config.write", "schedules.set", "server.update_settings", "backup.create",
          "backup.restore", "server.update", "server.add_preset", "server.install", "server.hytale_signin", "server.remove",
          "mods.install", "mods.update", "mods.set_enabled", "mods.remove", "mods.install_deps",
          "mods.move", "mods.pin", "mods.auto_update", "mods.set_field", "mods.set_option", "mods.setup", "server.upnp",
          "server.save_game_settings", "backup.download", "ports.fix", "ports.set", "rcon.setup",
          "ark.fix", "ark.separate_plan", "ark.separate", "ark.rules_save", "ark.spawns_save", "ark.loot_save", "ark.wipe_wild", "agent.update_now", "host.settings_save", "host.mirror_test", "diagnostics.download",
          "servers.bring_scan", "servers.bring", "servers.bring_job",
          "players.access", "players.access_change", "players.allow_on", "app.update_check"}
ROLE_PERMS = {"admin": VIEW | OPERATE | MANAGE, "operator": VIEW | OPERATE}


def _ids(values):
    out = set()
    for v in values or []:
        try:
            out.add(str(int(v)))
        except (TypeError, ValueError):
            pass
    return out


class DiscordAuth:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self._sessions: Dict[str, dict] = {}
        self._states: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- config ------------------------------------------------------------------
    def config(self) -> dict:
        try:
            return json.loads((self.base_dir / "discord_config.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def client_secret(self, cfg: dict) -> str:
        if cfg.get("oauth_client_secret"):
            return cfg["oauth_client_secret"]
        enc = cfg.get("oauth_client_secret_protected")
        if enc:
            from core.secrets import unprotect
            return unprotect(enc)
        return ""

    def problems(self) -> list:
        """What is missing before Discord login can work (shown on the login page)."""
        cfg = self.config()
        out = []
        if not cfg.get("oauth_client_id"):
            out.append("OAuth Client ID is not set")
        if not self.client_secret(cfg):
            out.append("OAuth Client Secret is not set")
        if not cfg.get("guild_id"):
            out.append("Discord server (guild) ID is not set")
        if not (cfg.get("web_admin_ids") or cfg.get("web_admin_role_ids") or cfg.get("web_operator_role_ids")):
            out.append("No admin users or roles are set, so nobody would be allowed in")
        return out

    def redirect_uri(self, cfg: dict) -> str:
        return (cfg.get("panel_url") or "").rstrip("/") + "/auth/callback"

    # -- OAuth flow ------------------------------------------------------------------
    def login_url(self) -> str:
        cfg = self.config()
        state = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._states = {k: v for k, v in self._states.items() if now - v < 600}
            self._states[state] = now
        q = urllib.parse.urlencode({
            "response_type": "code", "client_id": cfg.get("oauth_client_id", ""),
            "scope": "identify guilds.members.read", "state": state,
            "redirect_uri": self.redirect_uri(cfg),
        })
        return AUTHORIZE_URL + "?" + q

    def _http(self, url, data=None, headers=None):
        h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        h.update(headers or {})
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=h)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def complete_login(self, code: str, state: str) -> dict:
        """Exchange the code, check guild roles, create a session. Returns the session."""
        with self._lock:
            if self._states.pop(state, None) is None:
                raise PermissionError("Login expired or was not started here. Please try again.")
        cfg = self.config()
        basic = base64.b64encode(("%s:%s" % (cfg.get("oauth_client_id", ""), self.client_secret(cfg)))
                                 .encode()).decode()
        try:
            tok = self._http(DISCORD_API + "/oauth2/token",
                             data={"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": self.redirect_uri(cfg)},
                             headers={"Authorization": "Basic " + basic})
            auth = {"Authorization": "Bearer " + tok["access_token"]}
            me = self._http(DISCORD_API + "/users/@me", headers=auth)
        except urllib.error.HTTPError as e:
            raise PermissionError("Discord refused the login (%s). Check the Client ID/Secret and "
                                  "that the redirect URL is added in the Discord Developer Portal." % e.code)
        try:
            member = self._http(DISCORD_API + "/users/@me/guilds/%s/member" % cfg.get("guild_id"), headers=auth)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise PermissionError("You are not a member of this Discord server.")
            raise PermissionError("Could not read your Discord roles (%s)." % e.code)
        role = self.role_for(str(me["id"]), set(map(str, member.get("roles") or [])), cfg)
        if role is None:
            raise PermissionError("Your Discord account doesn't have a role that may use this panel. "
                                  "Ask the owner to give you an admin or operator role.")
        name = member.get("nick") or me.get("global_name") or me.get("username") or "user"
        avatar = None
        if me.get("avatar"):
            avatar = "https://cdn.discordapp.com/avatars/%s/%s.png?size=64" % (me["id"], me["avatar"])
        return self._new_session(str(me["id"]), name, role, avatar)

    @staticmethod
    def role_for(user_id: str, role_ids: set, cfg: dict) -> Optional[str]:
        if user_id in _ids(cfg.get("web_admin_ids")) or role_ids & _ids(cfg.get("web_admin_role_ids")):
            return "admin"
        if role_ids & _ids(cfg.get("web_operator_role_ids")):
            return "operator"
        return None

    # -- sessions ----------------------------------------------------------------------
    def _new_session(self, user_id, name, role, avatar) -> dict:
        sess = {"token": secrets.token_urlsafe(32), "csrf": secrets.token_urlsafe(24),
                "user_id": user_id, "name": name, "role": role, "avatar": avatar,
                "expires": time.time() + SESSION_TTL}
        with self._lock:
            now = time.time()
            self._sessions = {k: v for k, v in self._sessions.items() if v["expires"] > now}
            self._sessions[sess["token"]] = sess
        return sess

    def session(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if s and s["expires"] > time.time():
                return s
            self._sessions.pop(token, None)
        return None

    def logout(self, token: Optional[str]):
        with self._lock:
            self._sessions.pop(token or "", None)

    @staticmethod
    def allowed(sess: dict, action: str) -> bool:
        return action in ROLE_PERMS.get(sess.get("role"), set())
