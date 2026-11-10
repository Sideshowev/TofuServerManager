"""
BaoBun Control Panel - Discord bot

Lets your friends run the game servers from Discord:

* a control panel in ``channel_id``: a status summary plus one button per server
  (start it; admins can also stop it). The panel is edited in place when the bot
  restarts instead of being deleted and re-posted.
* slash commands (server names autocomplete; /access manages allow lists, bans and admins):
  everyone - /servers, /status, /start, /players, /join, /mods, /notify, /stats,
             /event, /events, /help
  admins   - /stop, /restart, /say, /access, /backup, /restore, /logs, /update, /checkup,
             /baobun-setup
* game nights: /event posts "let's play" with I'm in / Can't make it buttons, pings
  everyone going 30 minutes before, wakes the server and says when it starts
* "ping me" (/notify): a DM when a server is up, or when someone joins it
* crash alerts come with Restart / Show log buttons; restart warnings are posted
* "it's ready" pings: whoever starts a server gets told when people can join
* an activity feed in ``log_channel_id``: who started/stopped what, servers going to
  sleep when empty, players joining and leaving
* alerts (crashes, failed backups...) via post_alert(), used by core/alerts.py

discord_config.json (next to the exe; this file is never rewritten once it exists):
  token / token_protected, channel_id, log_channel_id, alert_channel_id,
  admin_ids, admin_role_ids  - may stop / restart servers and use /say
  start_role_ids             - only these roles may start servers (empty = everyone)
  log_max_age_hours (24)     - activity messages older than this are tidied away
  show_join_address (false)  - show the public IP:port in the panel and /join
  start_cooldown_seconds (20), max_running (0 = no limit),
  announce_players (true), announce_activity (true), notify_ready (true)

The bot reconnects by itself; if it crashes it is restarted with a growing delay.
"""

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    import discord
    from discord import app_commands
except ImportError:
    print("[ERROR] discord.py not installed. Run: pip install discord.py")
    discord = None
    app_commands = None

from core import discord_logic as L
from core import discord_events as EV
from core import discord_extras as X
from core.discord_status import status_lines

ALERT_FOOTER = "BaoBun alert"
UI_PURGE_LIMIT = 200
ROW_SIZE = 4
SERVERS_PER_MESSAGE = ROW_SIZE * 5
LOG_PURGE_INTERVAL = 3600.0
READY_TIMEOUT = 25 * 60

_app_instance = None
_client = None
_cfg = {}
_admin_ids = set()
_admin_role_ids = set()
_start_role_ids = set()
_log_channel_id = 0
_alert_channel_id = 0
_ui_channel_id = 0
_log_max_age_hours = 24
_show_address = False
_last_log_purge = 0.0
_grid_messages = {}          # message id -> [server ids]
_header_msg_id = None
_cooldown = L.Cooldowns(20)
_listener_added = False
_stop = threading.Event()
_event_channel_id = 0
_raw_config = {}
_events = None
_subs = None


# --------------------------------------------------------------------------- #
# config / permissions
# --------------------------------------------------------------------------- #
def _int_set(values):
    out = set()
    for v in values or []:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            pass
    return out


def _member_role_ids(user):
    return {r.id for r in getattr(user, "roles", []) or []}


def _is_admin(user):
    """Listed admins and admin roles, plus the Discord server's owner and anyone who may
    Manage Server (so a fresh install works before anything is configured)."""
    uid = getattr(user, "id", 0)
    guild = getattr(user, "guild", None)
    perms = getattr(user, "guild_permissions", None)
    manage = bool(perms is not None and (getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False)))
    return X.is_admin(uid, _member_role_ids(user), _admin_ids, _admin_role_ids,
                      getattr(guild, "owner_id", 0) or 0, manage)


def _can_start(user):
    """Anyone may start servers unless start_role_ids is set in discord_config.json."""
    return not _start_role_ids or _is_admin(user) or bool(_member_role_ids(user) & _start_role_ids)


def _opt(key, default):
    v = _cfg.get(key, default)
    return default if v is None else v


def _base_dir():
    from core.paths import base_dir
    return base_dir()


def get_server_manager():
    if _app_instance is None:
        return None
    mgr = getattr(_app_instance, "manager", None)
    if mgr is None:
        return None
    from core.linked_pcs import manager_for_bots     # includes servers on linked PCs
    return manager_for_bots(mgr)


def _visible(mgr, statuses):
    """Servers shown on Discord (hide one with "discordVisible": false in servers.json)."""
    out = []
    for st in statuses:
        srv = mgr.get_server(st["id"]) or {}
        if srv.get("discordVisible", True) is not False:
            out.append(st)
    return out


def _who(user):
    return "discord:" + str(user)


def _runtime(name):
    p = _base_dir() / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return str(p / name)


def _stores():
    global _events, _subs
    if _events is None:
        _events = EV.EventStore(_runtime("discord_events.json"))
    if _subs is None:
        _subs = X.Subscriptions(_runtime("discord_notify.json"))
    return _events, _subs


async def _dm(client, user_id, text):
    """DM someone; falls back to a mention in the activity channel."""
    try:
        user = client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
        await user.send(text)
        return True
    except Exception:
        return await _send_log(client, "<@%d> %s" % (int(user_id), text))


# --------------------------------------------------------------------------- #
# channels and messages
# --------------------------------------------------------------------------- #
async def _fetch_channel(client, channel_id):
    if not channel_id:
        return None
    try:
        ch = client.get_channel(int(channel_id))
        return ch or await client.fetch_channel(int(channel_id))
    except Exception:
        return None


async def _first_usable_channel(client):
    for g in client.guilds:
        for ch in g.text_channels:
            if ch.permissions_for(g.me).send_messages:
                return ch
    return None


def _is_alert(msg) -> bool:
    """Alerts are kept when the bot tidies its channels."""
    try:
        return any(e.footer and e.footer.text == ALERT_FOOTER for e in msg.embeds)
    except Exception:
        return False


async def _purge_own_messages(channel, client, limit=UI_PURGE_LIMIT):
    """Remove the bot's old panel messages: in bulk when it may, else one by one."""
    def mine(m):
        return m.author == client.user and not _is_alert(m)
    try:
        gone = await channel.purge(limit=limit, check=mine, bulk=True)
        return len(gone)
    except Exception:
        pass
    deleted = 0
    try:
        async for msg in channel.history(limit=limit):
            if mine(msg):
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
    except Exception as e:
        print("[WARN] failed to tidy #%s: %s" % (channel, e))
    return deleted


async def _purge_old_log_messages(channel, client, max_age_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    def old(m):
        created = m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=timezone.utc)
        return m.author == client.user and not _is_alert(m) and created < cutoff
    try:
        return len(await channel.purge(limit=UI_PURGE_LIMIT, check=old, bulk=True))
    except Exception:
        pass
    deleted = 0
    try:
        async for msg in channel.history(limit=UI_PURGE_LIMIT):
            if old(msg):
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
    except Exception as e:
        print("[WARN] failed to prune log channel #%s: %s" % (channel, e))
    return deleted


async def _maybe_prune_log_channel(client, channel):
    global _last_log_purge
    if time.time() - _last_log_purge < LOG_PURGE_INTERVAL:
        return
    _last_log_purge = time.time()
    await _purge_old_log_messages(channel, client, _log_max_age_hours)


async def _send_log(client, text=None, embed=None):
    """Post to the activity channel. Returns False when there is none."""
    ch = await _fetch_channel(client, _log_channel_id)
    if ch is None:
        return False
    try:
        await ch.send(content=text, embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False,
                                                                                          everyone=False))
    except Exception as e:
        print("[WARN] Discord activity message failed: %s" % e)
        return False
    await _maybe_prune_log_channel(client, ch)
    return True


def post_alert(title: str, text: str, color: int = 0xEF4444, server_id=None, kind=None) -> bool:
    """Post an alert embed from another thread. Returns True if it was sent.
    Goes to alert_channel_id, else the log channel, else the panel channel.
    Crash alerts get Restart / Show log buttons (admins only)."""
    client = _client
    if discord is None or client is None or not client.is_ready():
        return False

    async def send():
        for cid in (_alert_channel_id, _log_channel_id, _ui_channel_id):
            ch = await _fetch_channel(client, cid)
            if ch is None:
                continue
            embed = discord.Embed(title=title[:250], description=text[:3900], color=color,
                                  timestamp=datetime.now(timezone.utc))
            embed.set_footer(text=ALERT_FOOTER)
            view = _crash_view(server_id) if server_id and kind in ("crash", "gave_up") else None
            if view is not None:
                await ch.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            else:
                await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            return True
        return False
    try:
        return bool(asyncio.run_coroutine_threadsafe(send(), client.loop).result(timeout=20))
    except Exception as e:
        print("[WARN] Discord alert failed: " + str(e))
        return False


# --------------------------------------------------------------------------- #
# actions (shared by buttons and slash commands)
# --------------------------------------------------------------------------- #
async def _status_of(mgr, sid):
    statuses = await asyncio.to_thread(mgr.get_all_statuses)
    return next((s for s in statuses if s["id"] == sid), None), statuses


def _players_text(st):
    if not st or not st.get("isRunning"):
        return "offline"
    if st.get("playerCount") is None:
        return "online"
    names = [p.get("name") for p in st.get("players") or [] if p.get("name")]
    s = "%s/%s online" % (st["playerCount"], st.get("maxPlayers") or "?")
    return s + ((": " + ", ".join(names[:10])) if names else "")


async def _start(interaction, sid):
    """Start a server for whoever asked. Returns the text to show them."""
    mgr = get_server_manager()
    user = interaction.user
    if not _can_start(user):
        return "⚠ You don't have a role that can start servers."
    st, statuses = await _status_of(mgr, sid)
    if st is None:
        return "That server no longer exists."
    name = st.get("name", sid)
    if st.get("isRunning") or st.get("state") in L.BUSY:
        join = await asyncio.to_thread(L.join_text, mgr, sid, _show_address)
        return "**%s** is already %s. %s" % (name, "running - " + _players_text(st) if st.get("isRunning")
                                             else st.get("state"), join)
    left = _cooldown.remaining(user.id)
    if left and not _is_admin(user):
        return "⏳ Give it %d more seconds before starting another server." % int(left + 0.99)
    if L.too_many_running(statuses, int(_opt("max_running", 0) or 0)) and not _is_admin(user):
        return ("⚠ %d servers are already running - that's the limit. Ask an admin, or wait for one to "
                "go to sleep." % int(_opt("max_running", 0)))
    _cooldown.hit(user.id)
    try:
        result = await asyncio.to_thread(mgr.start_server, sid, _who(user))
    except Exception as e:
        return "❌ Couldn't start %s: %s" % (name, e)
    status = result.get("status")
    if status in ("already_running", "already_starting"):
        return "**%s** is already running." % name
    msg = ("⬇ Updating **%s** first - it starts when the update is done." % name if status == "updating"
           else "▶ Starting **%s**." % name)
    await _send_log(interaction.client, "▶ %s started **%s**." % (user.mention, name))
    if _opt("notify_ready", True):
        msg += " I'll let you know when it's ready to join."
        interaction.client.loop.create_task(_watch_ready(interaction, sid, name))
    return msg


async def _watch_ready(interaction, sid, name):
    """Tell the person who started a server when it's joinable (or that it failed)."""
    mgr = get_server_manager()
    started = time.time()
    state = "waiting"
    while time.time() - started < READY_TIMEOUT:
        await asyncio.sleep(10)
        try:
            state = await asyncio.to_thread(L.ready_state, mgr, sid, started)
        except Exception:
            state = "waiting"
        if state != "waiting":
            break
    user = interaction.user
    if state == "ready":
        join = await asyncio.to_thread(L.join_text, mgr, sid, _show_address)
        text = "✅ %s **%s** is ready! %s" % (user.mention, name, join)
    elif state == "failed":
        text = "❌ %s **%s** didn't start - an admin can check the console in BaoBun." % (user.mention, name)
    else:
        text = "⏳ %s **%s** is still starting after %d minutes - it may be updating or loading a big " \
               "world." % (user.mention, name, READY_TIMEOUT // 60)
    if await _send_log(interaction.client, text):
        return
    try:     # no activity channel: tell them where they asked (removed again after 30 minutes)
        ch = interaction.channel
        if ch is not None:
            await ch.send(text, delete_after=1800,
                          allowed_mentions=discord.AllowedMentions(users=[user], roles=False, everyone=False))
    except Exception:
        try:
            await interaction.followup.send(text, ephemeral=True)
        except Exception:
            pass


async def _stop_or_restart(interaction, sid, restart=False):
    mgr = get_server_manager()
    user = interaction.user
    if not _is_admin(user):
        return "⚠ Only admins can %s servers." % ("restart" if restart else "stop")
    st, _ = await _status_of(mgr, sid)
    if st is None:
        return "That server no longer exists."
    name = st.get("name", sid)
    if not st.get("isRunning") and not restart:
        return "**%s** isn't running." % name
    try:
        if restart:
            await asyncio.to_thread(mgr.restart_server, sid, _who(user))
        else:
            r = await asyncio.to_thread(mgr.stop_server, sid, _who(user))
    except Exception as e:
        return "❌ Couldn't %s %s: %s" % ("restart" if restart else "stop", name, e)
    if restart:
        await _send_log(interaction.client, "\U0001F504 %s restarted **%s**." % (user.mention, name))
        return "\U0001F504 Restarting **%s**." % name
    forced = isinstance(r, dict) and r.get("graceful") is False
    await _send_log(interaction.client, "⏹ %s stopped **%s**%s." % (user.mention, name,
                                                                         " (it had to be forced)" if forced else ""))
    return "⏹ Stopped **%s**%s." % (name, " - it didn't exit in time, so it was force-stopped" if forced else "")


# --------------------------------------------------------------------------- #
# crash alert buttons, game nights, "ping me"
# --------------------------------------------------------------------------- #
def _crash_view(sid):
    view = discord.ui.View(timeout=None)
    r = discord.ui.Button(style=discord.ButtonStyle.primary, label="Restart it")
    lg = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Show log")

    async def restart_cb(interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await _stop_or_restart(interaction, sid, restart=True), ephemeral=True)

    async def log_cb(interaction):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("⚠ Only admins can see server logs.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await _log_message(sid), ephemeral=True)
    r.callback, lg.callback = restart_cb, log_cb
    view.add_item(r)
    view.add_item(lg)
    return view


async def _log_message(sid, lines=30):
    mgr = get_server_manager()
    text = await asyncio.to_thread(X.log_tail, mgr, sid, lines)
    if not text:
        return "No log for that server yet."
    return "```\n%s\n```" % text[-1900:]


def _event_view(ev):
    view = discord.ui.View(timeout=None)
    if ev.get("cancelled"):
        return view
    yes = discord.ui.Button(style=discord.ButtonStyle.success, label="I'm in", custom_id="baobun:ev:in:" + ev["id"])
    no = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Can't make it",
                           custom_id="baobun:ev:out:" + ev["id"])
    eid = ev["id"]

    async def rsvp(interaction, going):
        events, _s = _stores()
        e = events.rsvp(eid, interaction.user.id, going)
        if e is None:
            await interaction.response.send_message("That event is over or was cancelled.", ephemeral=True)
            return
        try:
            await interaction.response.edit_message(content=_event_text(e), view=_event_view(e),
                                                    allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.response.send_message("Saved.", ephemeral=True)

    async def yes_cb(interaction):
        await rsvp(interaction, True)

    async def no_cb(interaction):
        await rsvp(interaction, False)
    yes.callback, no.callback = yes_cb, no_cb
    view.add_item(yes)
    view.add_item(no)
    return view


def _event_text(ev):
    return ("\U0001F3B2 **%s**\n" % ev["title"]) + EV.describe(ev)


async def _event_channel(client, fallback=None):
    for cid in (_event_channel_id, _log_channel_id):
        ch = await _fetch_channel(client, cid)
        if ch is not None:
            return ch
    return fallback


async def _event_loop(client):
    """Every 30 s: reminders, waking servers and "starting now" for game nights."""
    events, _s = _stores()
    while not client.is_closed():
        await asyncio.sleep(30)
        try:
            due = await asyncio.to_thread(events.due)
            mgr = get_server_manager()
            for ev in due["remind"]:
                events.update(ev["id"], reminded=True)
                ch = await _fetch_channel(client, ev.get("channel")) or await _event_channel(client)
                if ch is None or not ev.get("going"):
                    continue
                who = " ".join("<@%d>" % u for u in ev["going"])
                await ch.send("⏰ **%s** starts %s! %s" % (ev["title"], EV.stamp(ev["when"], "R"), who),
                              allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            for ev in due["wake"]:
                events.update(ev["id"], woke=True)
                sid = ev.get("server_id")
                if not sid or mgr is None:
                    continue
                st, _all = await _status_of(mgr, sid)
                if st and not st.get("isRunning") and st.get("state") not in L.BUSY:
                    try:
                        await asyncio.to_thread(mgr.start_server, sid, "discord:event")
                        await _send_log(client, "▶ Starting **%s** for game night \"%s\"." %
                                        (ev.get("server_name") or sid, ev["title"]))
                    except Exception as e:
                        await _send_log(client, "❌ Couldn't start **%s** for \"%s\": %s" %
                                        (ev.get("server_name") or sid, ev["title"], e))
            for ev in due["announce"]:
                events.update(ev["id"], announced=True)
                ch = await _fetch_channel(client, ev.get("channel")) or await _event_channel(client)
                if ch is None:
                    continue
                join = ""
                if ev.get("server_id") and mgr is not None:
                    join = await asyncio.to_thread(L.join_text, mgr, ev["server_id"], _show_address)
                who = " ".join("<@%d>" % u for u in ev.get("going") or [])
                await ch.send("\U0001F3AE **%s** is starting now!%s %s %s" % (
                    ev["title"], (" **%s**." % ev["server_name"]) if ev.get("server_name") else "", join, who),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            await asyncio.to_thread(events.prune)
        except Exception as e:
            print("[WARN] Discord game nights: %s" % e)


def _restore_view(sid, backups):
    view = discord.ui.View(timeout=120)
    for b in backups[:5]:
        label = b["name"][:-4] if b["name"].endswith(".zip") else b["name"]
        if b.get("location") == "copy":
            label += " (other PC)"
        btn = discord.ui.Button(style=discord.ButtonStyle.danger, label=("Restore " + label)[:80])
        path = b["path"]

        async def cb(interaction, path=path, label=label):
            if not _is_admin(interaction.user):
                await interaction.response.send_message("⚠ Only admins can restore backups.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            mgr = get_server_manager()
            try:
                await asyncio.to_thread(mgr.restore_server_world, sid, path)
            except Exception as e:
                await interaction.followup.send("❌ %s" % e, ephemeral=True)
                return
            await _send_log(interaction.client, "↩ %s restored a backup of **%s** (%s)." %
                            (interaction.user.mention, (mgr.get_server(sid) or {}).get("name", sid), label))
            await interaction.followup.send("✅ Restored %s. The world from before is saved as a "
                                            "pre_restore backup. Start the server when you're ready." % label,
                                            ephemeral=True)
        btn.callback = cb
        view.add_item(btn)
    return view


def _modpack_view(sid, name):
    view = discord.ui.View(timeout=300)
    btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Download mod pack")

    async def cb(interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        mgr = get_server_manager()
        r = await asyncio.to_thread(X.make_modpack, mgr, sid, _runtime("modpacks"))
        if r.get("error"):
            await interaction.followup.send(r["error"], ephemeral=True)
            return
        try:
            await interaction.followup.send("Mod pack for **%s** (%d mods) - unzip it into your game's Mods folder."
                                            % (name, r["count"]),
                                            file=discord.File(r["path"], filename="%s mods.zip" % name[:40]),
                                            ephemeral=True)
        except Exception as e:
            await interaction.followup.send("Couldn't upload the mod pack: %s" % e, ephemeral=True)
    btn.callback = cb
    view.add_item(btn)
    return view


async def _notify_changes(client, prev_up, statuses, prev_players, snap, names):
    """DMs for /notify: servers that came up, and people joining watched servers."""
    _e, subs = _stores()
    if prev_up is not None:
        for sid in X.came_up(prev_up, statuses):
            users = subs.take_up(sid)
            if users:
                join = await asyncio.to_thread(L.join_text, get_server_manager(), sid, _show_address)
                for u in users:
                    await _dm(client, u, "✅ **%s** is up! %s" % (names.get(sid, sid), join))
    if prev_players is not None:
        for sid, cur in snap.items():
            if sid not in prev_players:
                continue
            joined = sorted(cur - prev_players[sid])
            watchers = subs.who(sid, "join") if joined else []
            for u in watchers:
                await _dm(client, u, "\U0001F3AE %s joined **%s**." % (", ".join(joined)[:300], names.get(sid, sid)))


# --------------------------------------------------------------------------- #
# the panel
# --------------------------------------------------------------------------- #
def _status_embed(mgr, statuses):
    lines = status_lines(mgr, statuses, _show_address)
    embed = discord.Embed(title="BaoBun servers", description="\n".join(lines)[:4000] or "No servers yet.",
                          color=0xE6C87E, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text="Press a button to start a server · /help for commands")
    return embed


def _make_button(st, row):
    label, style, disabled = L.button_spec(st)
    btn = discord.ui.Button(style=getattr(discord.ButtonStyle, style), label=label, disabled=disabled,
                            custom_id="baobun:toggle:" + str(st["id"]), row=row)
    sid = st["id"]

    async def callback(interaction):
        await _on_button(interaction, sid)
    btn.callback = callback
    return btn


def _build_grid_view(servers):
    view = discord.ui.View(timeout=None)
    for i, st in enumerate(servers):
        view.add_item(_make_button(st, i // ROW_SIZE))
    return view


async def _on_button(interaction, sid):
    mgr = get_server_manager()
    if mgr is None:
        await interaction.response.send_message("BaoBun isn't ready yet - try again in a minute.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    st, _ = await _status_of(mgr, sid)
    if st is None:
        await interaction.followup.send("That server no longer exists.", ephemeral=True)
        return
    if st.get("isRunning"):
        name = st.get("name", sid)
        join = await asyncio.to_thread(L.join_text, mgr, sid, _show_address)
        info = "**%s** is running - %s. %s" % (name, _players_text(st), join)
        if not _is_admin(interaction.user):
            await interaction.followup.send(info, ephemeral=True)
            return
        await interaction.followup.send(info + "\nStop it?", view=_ConfirmStop(sid, name, st.get("playerCount")),
                                        ephemeral=True)
        return
    text = await _start(interaction, sid)
    await interaction.followup.send(text, ephemeral=True)
    await _refresh_message(interaction.message, mgr)


class _ConfirmStopBase:
    pass


if discord is not None:
    class _ConfirmStop(discord.ui.View):
        """Admins confirm a stop (with a warning when people are playing)."""

        def __init__(self, sid, name, players):
            super().__init__(timeout=60)
            self.sid = sid
            label = "Stop it (%s playing)" % players if players else "Stop it"
            b = discord.ui.Button(style=discord.ButtonStyle.danger, label=label)
            r = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Restart")

            async def stop_cb(interaction):
                await interaction.response.defer(ephemeral=True, thinking=True)
                await interaction.followup.send(await _stop_or_restart(interaction, sid), ephemeral=True)
                self.stop()

            async def restart_cb(interaction):
                await interaction.response.defer(ephemeral=True, thinking=True)
                await interaction.followup.send(await _stop_or_restart(interaction, sid, restart=True),
                                                ephemeral=True)
                self.stop()
            b.callback, r.callback = stop_cb, restart_cb
            self.add_item(b)
            self.add_item(r)
else:
    _ConfirmStop = _ConfirmStopBase


def _chunk(statuses):
    return [statuses[i:i + SERVERS_PER_MESSAGE] for i in range(0, len(statuses), SERVERS_PER_MESSAGE)]


async def _refresh_message(message, mgr):
    ids = _grid_messages.get(getattr(message, "id", None))
    if not ids:
        return
    statuses = await asyncio.to_thread(mgr.get_all_statuses)
    by_id = {s["id"]: s for s in statuses}
    ordered = [by_id[i] for i in ids if i in by_id]
    if ordered:
        try:
            await message.edit(view=_build_grid_view(ordered))
        except Exception:
            pass


def _panel_file():
    p = _base_dir() / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p / "discord_panel.json"


def _save_panel(channel_id):
    try:
        _panel_file().write_text(json.dumps({"channel": channel_id, "header": _header_msg_id,
                                             "grids": [[mid, ids] for mid, ids in _grid_messages.items()]}),
                                 encoding="utf-8")
    except Exception:
        pass


async def _reuse_panel(channel, mgr, statuses) -> bool:
    """Edit the panel posted last time instead of deleting and re-posting it."""
    global _header_msg_id
    try:
        saved = json.loads(_panel_file().read_text(encoding="utf-8"))
    except Exception:
        return False
    chunks = _chunk(statuses)
    grids = saved.get("grids") or []
    if saved.get("channel") != channel.id or not saved.get("header") or len(grids) != len(chunks):
        return False
    try:
        header = await channel.fetch_message(int(saved["header"]))
        msgs = [await channel.fetch_message(int(mid)) for mid, _ids in grids]
        await header.edit(content=None, embed=_status_embed(mgr, statuses))
        _grid_messages.clear()
        for msg, chunk in zip(msgs, chunks):
            await msg.edit(view=_build_grid_view(chunk))
            _grid_messages[msg.id] = [s["id"] for s in chunk]
        _header_msg_id = header.id
        return True
    except Exception:
        _grid_messages.clear()
        return False


async def _build_control_panel(client, channel, mgr):
    global _header_msg_id
    statuses = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
    if await _reuse_panel(channel, mgr, statuses):
        print("[INFO] Discord panel updated in place")
        return
    await _purge_own_messages(channel, client)
    _grid_messages.clear()
    try:
        _header_msg_id = (await channel.send(embed=_status_embed(mgr, statuses))).id
    except Exception as e:
        print("[WARN] could not post the status embed: " + str(e))
        _header_msg_id = None
    for chunk in _chunk(statuses):
        try:
            msg = await channel.send(view=_build_grid_view(chunk))
            _grid_messages[msg.id] = [s["id"] for s in chunk]
        except Exception as e:
            print("[WARN] could not post control panel buttons: " + str(e))
    _save_panel(channel.id)


async def _keep_fresh(client, channel, mgr):
    """Every 20 s: update the panel when anything changed (started outside Discord,
    players, crashes...), announce who joined/left, rebuild when servers are added."""
    last = None
    players = None
    ups = None
    presence = None
    while not client.is_closed():
        await asyncio.sleep(20)
        try:
            statuses = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
            names = {s["id"]: s.get("name", s["id"]) for s in statuses}
            snap = L.player_snapshot(statuses)
            if players is not None and _opt("announce_players", True):
                lines = L.player_changes(players, snap, names)
                if lines:
                    await _send_log(client, "\n".join(lines)[:1900])
            try:
                await _notify_changes(client, ups, statuses, players, snap, names)
            except Exception as e:
                print("[WARN] Discord notifications: %s" % e)
            ups = X.up_map(statuses)
            players = snap
            text = X.presence_text(statuses)
            if text != presence:
                presence = text
                try:
                    await client.change_presence(activity=discord.Game(name=text))
                except Exception:
                    pass
            key = tuple((s["id"], s.get("name"), s.get("state"), bool(s.get("isRunning")), s.get("playerCount"),
                         tuple(sorted(p.get("name") or "" for p in s.get("players") or [])),
                         bool(s.get("updateAvailable"))) for s in statuses)
            if key == last:
                continue
            shown = [i for ids in _grid_messages.values() for i in ids]
            if last is not None and shown != [s["id"] for s in statuses]:
                await _build_control_panel(client, channel, mgr)    # servers added / removed / renamed order
                last = key
                continue
            last = key
            if _header_msg_id:
                try:
                    hmsg = await channel.fetch_message(_header_msg_id)
                    await hmsg.edit(embed=_status_embed(mgr, statuses))
                except discord.NotFound:
                    await _build_control_panel(client, channel, mgr)     # someone deleted the panel
                    continue
                except Exception:
                    pass
            for mid in list(_grid_messages):
                try:
                    msg = await channel.fetch_message(mid)
                    await _refresh_message(msg, mgr)
                except discord.NotFound:
                    await _build_control_panel(client, channel, mgr)
                    break
                except Exception:
                    pass
        except Exception as e:
            print("[WARN] Discord panel refresh: " + str(e))


def _on_mgr_event(event, server_id, info):
    """Things that happen outside Discord show up in the activity channel."""
    client = _client
    if client is None or not client.is_ready() or not _opt("announce_activity", True):
        return
    mgr = get_server_manager()
    srv = (mgr.get_server(server_id) if mgr else None) or {}
    if srv.get("discordVisible", True) is False:
        return
    text = L.event_message(event, info or {}, srv)
    if text:
        try:
            asyncio.run_coroutine_threadsafe(_send_log(client, text), client.loop)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# slash commands
# --------------------------------------------------------------------------- #
def _setup_commands(client):
    tree = app_commands.CommandTree(client)

    async def server_ac(interaction, current: str):
        mgr = get_server_manager()
        if mgr is None:
            return []
        servers = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
        return [app_commands.Choice(name=n, value=v) for n, v in L.autocomplete(servers, current)]

    async def resolve(interaction, text):
        mgr = get_server_manager()
        if mgr is None:
            await interaction.followup.send("BaoBun isn't ready yet - try again in a minute.", ephemeral=True)
            return None, None
        servers = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
        srv, cands = L.find_server(servers, text)
        if srv is None:
            hint = (" Did you mean: " + ", ".join(c.get("name") for c in cands[:5]) + "?") if cands else \
                " Try /servers to see the names."
            await interaction.followup.send("I don't know a server called \"%s\".%s" % (text, hint), ephemeral=True)
        return mgr, srv

    @tree.command(name="servers", description="What's online and who's playing")
    async def servers_cmd(interaction):
        mgr = get_server_manager()
        if mgr is None:
            await interaction.response.send_message("BaoBun isn't ready yet.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        statuses = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
        await interaction.followup.send(embed=_status_embed(mgr, statuses), ephemeral=True)

    @tree.command(name="start", description="Start a game server")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def start_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        mgr, srv = await resolve(interaction, server)
        if srv:
            await interaction.followup.send(await _start(interaction, srv["id"]), ephemeral=True)

    @tree.command(name="stop", description="Stop a game server (admins)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def stop_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        mgr, srv = await resolve(interaction, server)
        if srv:
            await interaction.followup.send(await _stop_or_restart(interaction, srv["id"]), ephemeral=True)

    @tree.command(name="restart", description="Restart a game server (admins)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def restart_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        mgr, srv = await resolve(interaction, server)
        if srv:
            await interaction.followup.send(await _stop_or_restart(interaction, srv["id"], restart=True),
                                            ephemeral=True)

    @tree.command(name="players", description="Who's playing on a server")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def players_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True)
        mgr, srv = await resolve(interaction, server)
        if srv:
            await interaction.followup.send("**%s**: %s" % (srv.get("name"), _players_text(srv)), ephemeral=True)

    @tree.command(name="join", description="How to connect to a server")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def join_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True)
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        join = await asyncio.to_thread(L.join_text, mgr, srv["id"], _show_address)
        state = "running" if srv.get("isRunning") else "offline - press its Start button first"
        await interaction.followup.send("**%s** is %s. %s" % (srv.get("name"), state, join or
                                        "Ask an admin for the address (it isn't shown on Discord)."),
                                        ephemeral=True)

    @tree.command(name="say", description="Send a message to everyone on a server (admins)")
    @app_commands.describe(server="Which server", message="What to say")
    @app_commands.autocomplete(server=server_ac)
    async def say_cmd(interaction, server: str, message: str):
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send("⚠ Only admins can do that.", ephemeral=True)
            return
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        ok = await asyncio.to_thread(mgr.broadcast, srv["id"], message[:200])
        await interaction.followup.send("Sent." if ok else "That server can't receive messages right now.",
                                        ephemeral=True)

    @tree.command(name="access", description="Allow list, bans and admins of a server (admins)")
    @app_commands.describe(server="Which server", what="What to do", player="Player name (or Steam / EOS ID)",
                           reason="Why (for bans)", everywhere="Do it on all servers of this game")
    @app_commands.choices(what=[app_commands.Choice(name=n, value=v) for n, v in (
        ("show the lists", "list"), ("allow to join", "allow:add"), ("remove from allow list", "allow:remove"),
        ("ban", "ban:add"), ("unban", "ban:remove"), ("make admin", "admin:add"), ("remove admin", "admin:remove"))])
    @app_commands.autocomplete(server=server_ac)
    async def access_cmd(interaction, server: str, what: str, player: str = "", reason: str = "",
                         everywhere: bool = False):
        from core import player_access as PA
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send("⚠ Only admins can do that.", ephemeral=True)
            return
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        if what == "list":
            info = await asyncio.to_thread(PA.overview, mgr, srv["id"])
            await interaction.followup.send(PA.discord_text(info), ephemeral=True)
            return
        if not player.strip():
            await interaction.followup.send("Say which player (the player option).", ephemeral=True)
            return
        lst, action = what.split(":")
        by = "discord:%s" % getattr(interaction.user, "name", "?")
        try:
            if everywhere:
                sids = await asyncio.to_thread(PA.same_game, mgr, srv["id"])
                res = await asyncio.to_thread(PA.change_many, mgr, sids, lst, action, player, reason, by)
                text = "\n".join(("✓ " + r["text"]) if "text" in r else ("⚠ " + r.get("error", "")) for r in
                                  res.values())
            else:
                r = await asyncio.to_thread(PA.change, mgr, srv["id"], lst, action, player, reason, by)
                text = "✓ " + r["text"]
        except PA.AccessError as e:
            text = "⚠ %s" % e
        await interaction.followup.send(text[:1990], ephemeral=True)

    # ---------------------------------------------------------------- everyone
    @tree.command(name="status", description="Every server at a glance (posted for everyone to see)")
    async def status_cmd(interaction):
        mgr = get_server_manager()
        if mgr is None:
            await interaction.response.send_message("BaoBun isn't ready yet.", ephemeral=True)
            return
        await interaction.response.defer()
        statuses = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
        embed = _status_embed(mgr, statuses)
        try:
            from core import status_page
            c = status_page.config(mgr)
            if c["enabled"] and c["address"]:
                embed.description = (embed.description or "") + "\n\n\U0001F310 Live page: http://%s:%d/" % (
                    c["address"].split(":")[0], c["port"])
        except Exception:
            pass
        await interaction.followup.send(embed=embed)

    @tree.command(name="mods", description="Which mods a server uses (with links and a mod pack)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def mods_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True)
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        try:
            info = await asyncio.to_thread(X.mods_info, mgr, srv["id"])
        except Exception as e:
            await interaction.followup.send("Couldn't read the mods: %s" % e, ephemeral=True)
            return
        text = X.mods_text(info, srv.get("name") or srv["id"])
        if info.get("pack_possible") and any(not m["server_only"] for m in info["mods"]):
            await interaction.followup.send(text, view=_modpack_view(srv["id"], srv.get("name") or srv["id"]),
                                            ephemeral=True)
        else:
            await interaction.followup.send(text, ephemeral=True)

    @tree.command(name="notify", description="Get a DM when a server is up, or when someone joins it")
    @app_commands.describe(server="Which server", when="When to tell you")
    @app_commands.choices(when=[app_commands.Choice(name="when it's up and ready", value="up"),
                                app_commands.Choice(name="every time someone joins", value="join"),
                                app_commands.Choice(name="stop telling me", value="off")])
    @app_commands.autocomplete(server=server_ac)
    async def notify_cmd(interaction, server: str, when: str = "up"):
        await interaction.response.defer(ephemeral=True)
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        when = getattr(when, "value", when)
        _e, subs = _stores()
        uid = interaction.user.id
        name = srv.get("name") or srv["id"]
        if when == "off":
            subs.set(srv["id"], uid, "up", False)
            subs.set(srv["id"], uid, "join", False)
            await interaction.followup.send("OK - no more messages about **%s**." % name, ephemeral=True)
            return
        if when == "up" and srv.get("isRunning") and srv.get("state") in (None, "running"):
            join = await asyncio.to_thread(L.join_text, mgr, srv["id"], _show_address)
            await interaction.followup.send("**%s** is already up! %s" % (name, join), ephemeral=True)
            return
        subs.set(srv["id"], uid, when, True)
        await interaction.followup.send(
            ("\U0001F514 I'll DM you when **%s** is up and ready." % name) if when == "up" else
            ("\U0001F514 I'll DM you whenever someone joins **%s**. `/notify %s stop telling me` to stop."
             % (name, name)), ephemeral=True)

    @tree.command(name="stats", description="Who played the most, busiest servers and times")
    @app_commands.describe(server="Only this server (optional)", days="How many days back (default 7)")
    @app_commands.autocomplete(server=server_ac)
    async def stats_cmd(interaction, server: str = "", days: int = 7):
        await interaction.response.defer()
        mgr = get_server_manager()
        srv = None
        if server:
            mgr, srv = await resolve(interaction, server)
            if not srv:
                return
        from core.player_stats import PlayerStats, format_summary
        days = max(1, min(int(days or 7), 90))
        s = await asyncio.to_thread(PlayerStats(_runtime("player_stats.json")).summary, days, None,
                                    srv["id"] if srv else None)
        await interaction.followup.send(format_summary(s, srv.get("name") if srv else None)[:1990],
                                        allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------- game nights
    @tree.command(name="event", description="Plan a game night - people sign up and get pinged before")
    @app_commands.describe(title="What you're playing, e.g. 'ARK boss fight'",
                           when="e.g. 'sat 8pm', 'tomorrow 7:30pm', '11/14 21:00', 'in 2h'",
                           server="Server to start for it (optional)",
                           start_server="Start that server 10 minutes before (default yes)")
    @app_commands.autocomplete(server=server_ac)
    async def event_cmd(interaction, title: str, when: str, server: str = "", start_server: bool = True):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ts = EV.parse_when(when)
        if ts is None:
            await interaction.followup.send("I couldn't read \"%s\" as a time. Try `sat 8pm`, `tomorrow 7:30pm`, "
                                            "`11/14 21:00` or `in 2h` (server PC's time zone)." % when,
                                            ephemeral=True)
            return
        srv = None
        if server:
            mgr, srv = await resolve(interaction, server)
            if not srv:
                return
        events, _s = _stores()
        ch = await _event_channel(interaction.client, interaction.channel)
        try:
            ev = events.create(title, ts, interaction.user.id, getattr(interaction.user, "display_name", None) or
                               str(interaction.user), getattr(ch, "id", 0) or 0,
                               srv["id"] if srv else None, srv.get("name") if srv else None, start_server)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        if ch is None:
            await interaction.followup.send("I can't find a channel to post in.", ephemeral=True)
            return
        try:
            msg = await ch.send(_event_text(ev), view=_event_view(ev),
                                allowed_mentions=discord.AllowedMentions.none())
            events.update(ev["id"], message=msg.id, channel=ch.id)
        except Exception as e:
            events.cancel(ev["id"])
            await interaction.followup.send("Couldn't post the event: %s" % e, ephemeral=True)
            return
        await interaction.followup.send("✅ Posted in %s for %s - you're signed up." %
                                        (getattr(ch, "mention", "#events"), EV.stamp(ts, "F")), ephemeral=True)

    async def event_ac(interaction, current: str):
        events, _s = _stores()
        out = []
        for e in events.upcoming():
            if not e.get("announced"):
                label = "%s (%s)" % (e["title"], datetime.fromtimestamp(e["when"]).strftime("%a %d %b %H:%M"))
                if current.lower() in label.lower():
                    out.append(app_commands.Choice(name=label[:100], value=e["id"]))
        return out[:25]

    @tree.command(name="events", description="Upcoming game nights")
    async def events_cmd(interaction):
        events, _s = _stores()
        up = [e for e in events.upcoming() if not e.get("announced")]
        if not up:
            await interaction.response.send_message("No game nights planned. Make one with `/event`.", ephemeral=True)
            return
        lines = ["\U0001F3B2 **%s** - %s (%s) · %d going%s" % (
            e["title"], EV.stamp(e["when"], "F"), EV.stamp(e["when"], "R"), len(e.get("going") or []),
            (" · " + e["server_name"]) if e.get("server_name") else "") for e in up[:15]]
        await interaction.response.send_message("\n".join(lines)[:1990], ephemeral=True)

    @tree.command(name="event-cancel", description="Cancel a game night (whoever made it, or admins)")
    @app_commands.describe(event="Which one")
    @app_commands.autocomplete(event=event_ac)
    async def event_cancel_cmd(interaction, event: str):
        events, _s = _stores()
        ev = events.get(event)
        if not ev or ev.get("cancelled"):
            await interaction.response.send_message("I don't know that event.", ephemeral=True)
            return
        if not EV.can_manage(ev, interaction.user.id, _is_admin(interaction.user)):
            await interaction.response.send_message("Only whoever made it (or an admin) can cancel it.",
                                                    ephemeral=True)
            return
        ev = events.cancel(event)
        try:
            ch = await _fetch_channel(interaction.client, ev.get("channel"))
            if ch is not None and ev.get("message"):
                msg = await ch.fetch_message(int(ev["message"]))
                await msg.edit(content=_event_text(ev), view=None, allowed_mentions=discord.AllowedMentions.none())
                if ev.get("going"):
                    await ch.send("❌ **%s** was cancelled. %s" % (ev["title"], " ".join(
                        "<@%d>" % u for u in ev["going"] if u != interaction.user.id)),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except Exception:
            pass
        await interaction.response.send_message("Cancelled **%s**." % ev["title"], ephemeral=True)

    # ------------------------------------------------------------------ admins
    async def admin_only(interaction):
        if _is_admin(interaction.user):
            return True
        await interaction.followup.send("⚠ Only admins can do that.", ephemeral=True)
        return False

    @tree.command(name="backup", description="Back up a server's world now (admins)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def backup_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await admin_only(interaction):
            return
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        try:
            r = await asyncio.to_thread(mgr.backup_server_world, srv["id"], None, None, "discord")
        except Exception as e:
            await interaction.followup.send("❌ Backup failed: %s" % e, ephemeral=True)
            return
        size = 0
        try:
            import os
            size = os.path.getsize(r["zip_path"]) / 1048576
        except Exception:
            pass
        note = (" (%s)" % r["warning"]) if r.get("warning") else ""
        await interaction.followup.send("\U0001F4BE Backed up **%s** - %d files, %.1f MB.%s" %
                                        (srv.get("name"), r.get("files", 0), size, note), ephemeral=True)

    @tree.command(name="restore", description="Put back an earlier backup of a server's world (admins)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def restore_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True)
        if not await admin_only(interaction):
            return
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        backups = await asyncio.to_thread(mgr.list_server_backups, srv["id"], True)
        if not backups:
            await interaction.followup.send("No backups of **%s** yet." % srv.get("name"), ephemeral=True)
            return
        warn = " Stop it first - it's running." if srv.get("isRunning") else ""
        await interaction.followup.send("Pick a backup of **%s** to put back (newest first). The current world is "
                                        "saved first, just in case.%s" % (srv.get("name"), warn),
                                        view=_restore_view(srv["id"], backups), ephemeral=True)

    @tree.command(name="logs", description="The last lines of a server's console (admins)")
    @app_commands.describe(server="Which server", lines="How many lines (default 30)")
    @app_commands.autocomplete(server=server_ac)
    async def logs_cmd(interaction, server: str, lines: int = 30):
        await interaction.response.defer(ephemeral=True)
        if not await admin_only(interaction):
            return
        mgr, srv = await resolve(interaction, server)
        if srv:
            await interaction.followup.send(await _log_message(srv["id"], lines), ephemeral=True)

    @tree.command(name="update", description="Update a server's game files and mods (admins)")
    @app_commands.describe(server="Which server")
    @app_commands.autocomplete(server=server_ac)
    async def update_cmd(interaction, server: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await admin_only(interaction):
            return
        mgr, srv = await resolve(interaction, server)
        if not srv:
            return
        name = srv.get("name") or srv["id"]
        client = interaction.client
        user = interaction.user

        async def run():
            lines = []
            try:
                full = mgr.get_server(srv["id"]) or {}
                if full.get("appId"):
                    r = await asyncio.to_thread(mgr.update_server, srv["id"], _who(user))
                    lines.append("game: %s" % (r.get("status") or "done"))
            except Exception as e:
                lines.append("game update failed: %s" % e)
            try:
                from core.mods import ModManager
                r = await asyncio.to_thread(ModManager(mgr).update, srv["id"], None, _who(user))
                ups = [u.get("mod") for u in r.get("updated") or []]
                lines.append(("mods: " + ", ".join(ups)) if ups else "mods: up to date")
            except Exception as e:
                lines.append("mods: %s" % e)
            await _send_log(client, "⬆ %s updated **%s** - %s." % (user.mention, name, "; ".join(lines)))
        client.loop.create_task(run())
        await interaction.followup.send("⬆ Updating **%s** (players get a warning first). I'll post in the "
                                        "activity channel when it's done." % name, ephemeral=True)

    @tree.command(name="checkup", description="What needs attention on the servers (admins)")
    async def checkup_cmd(interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await admin_only(interaction):
            return
        mgr = get_server_manager()
        statuses = _visible(mgr, await asyncio.to_thread(mgr.get_all_statuses))
        text = await asyncio.to_thread(X.checkup_text, mgr, {s["id"] for s in statuses})
        await interaction.followup.send(text, ephemeral=True)

    @tree.command(name="baobun-setup", description="Set up the bot: channels and roles (admins)")
    @app_commands.describe(panel_channel="Where the server buttons go", activity_channel="Who started what, joins",
                           alert_channel="Crashes and problems", event_channel="Game nights",
                           admin_role="Can stop/restart servers and use admin commands",
                           start_role="Only this role may start servers (leave out = everyone)",
                           show_address="Show the public join address in Discord")
    async def setup_cmd(interaction, panel_channel: discord.TextChannel = None,
                        activity_channel: discord.TextChannel = None, alert_channel: discord.TextChannel = None,
                        event_channel: discord.TextChannel = None, admin_role: discord.Role = None,
                        start_role: discord.Role = None, show_address: bool = None):
        await interaction.response.defer(ephemeral=True)
        if not await admin_only(interaction):
            return
        mgr = get_server_manager()
        vals = {}
        for key, obj in (("channel_id", panel_channel), ("log_channel_id", activity_channel),
                         ("alert_channel_id", alert_channel), ("event_channel_id", event_channel)):
            if obj is not None:
                vals[key] = obj.id
        if admin_role is not None:
            vals["admin_role_ids"] = [admin_role.id]
        if start_role is not None:
            vals["start_role_ids"] = [start_role.id]
        if show_address is not None:
            vals["show_join_address"] = bool(show_address)
        setup = await asyncio.to_thread(X.save_setup, mgr, **vals) if vals else X.setup_values(mgr)
        _apply_config(X.merged_config(_raw_config, setup))
        text = X.setup_summary(_cfg)
        if "channel_id" in vals:
            text += "\n\nMoving the control panel - give me a moment."
            ch = await _fetch_channel(interaction.client, _ui_channel_id)
            if ch is not None:
                try:
                    await _build_control_panel(interaction.client, ch, mgr)
                except Exception as e:
                    text += " (couldn't post it: %s)" % e
        await interaction.followup.send(text[:1990], ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="help", description="What this bot can do")
    async def help_cmd(interaction):
        await interaction.response.send_message(L.HELP, ephemeral=True)

    return tree


# --------------------------------------------------------------------------- #
# start-up
# --------------------------------------------------------------------------- #
def _load_config():
    """Read discord_config.json (creates a template only when the file is missing)."""
    path = _base_dir() / "discord_config.json"
    if not path.exists():
        template = {"token": "YOUR_DISCORD_BOT_TOKEN", "channel_id": 0, "log_channel_id": 0, "alert_channel_id": 0,
                    "event_channel_id": 0,
                    "admin_ids": [], "admin_role_ids": [], "start_role_ids": [], "log_max_age_hours": 24,
                    "show_join_address": False, "start_cooldown_seconds": 20, "max_running": 0,
                    "announce_players": True, "announce_activity": True, "notify_ready": True}
        path.write_text(json.dumps(template, indent=2), encoding="utf-8")
        print("[INFO] Created discord_config.json template (channels and roles can also be set with /baobun-setup).")
        return template
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print("[WARN] Could not read discord_config.json: " + str(e))
        return None


def _apply_config(raw):
    global _cfg, _log_channel_id, _alert_channel_id, _ui_channel_id, _show_address, _log_max_age_hours
    global _admin_ids, _admin_role_ids, _start_role_ids, _cooldown, _event_channel_id
    _cfg = dict(raw)
    _event_channel_id = raw.get("event_channel_id") or 0
    _ui_channel_id = raw.get("channel_id") or 0
    _log_channel_id = raw.get("log_channel_id") or 0
    _alert_channel_id = raw.get("alert_channel_id") or 0
    _show_address = bool(raw.get("show_join_address"))
    try:
        _log_max_age_hours = max(1, int(raw.get("log_max_age_hours", 24)))
    except (TypeError, ValueError):
        _log_max_age_hours = 24
    try:
        _cooldown = L.Cooldowns(max(0, float(raw.get("start_cooldown_seconds", 20))))
    except (TypeError, ValueError):
        _cooldown = L.Cooldowns(20)
    _admin_ids = _int_set(raw.get("admin_ids"))
    _admin_role_ids = _int_set(raw.get("admin_role_ids"))
    _start_role_ids = _int_set(raw.get("start_role_ids"))
    print("[INFO] Discord admins: %d user(s), %d role(s); start roles: %s"
          % (len(_admin_ids), len(_admin_role_ids), sorted(_start_role_ids) if _start_role_ids else "everyone"))


def _check_permissions(channel, what):
    try:
        perms = channel.permissions_for(channel.guild.me)
    except Exception:
        return
    need = {"view_channel": "View Channel", "send_messages": "Send Messages", "embed_links": "Embed Links",
            "read_message_history": "Read Message History"}
    missing = [label for attr, label in need.items() if not getattr(perms, attr, True)]
    if missing:
        print("[WARN] Discord: the bot is missing %s in #%s (%s channel)" % (", ".join(missing), channel, what))
    if what == "panel" and not getattr(perms, "manage_messages", False):
        print("[INFO] Discord: give the bot Manage Messages in #%s so it can tidy old messages faster" % channel)


def _make_client(token):
    global _client
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = _setup_commands(client)
    state = {"panel": False, "synced": False}

    @client.event
    async def on_ready():
        print("[INFO] BaoBun Discord bot connected as %s" % client.user)
        if not state["synced"]:
            state["synced"] = True
            for g in client.guilds:
                try:
                    tree.copy_global_to(guild=g)
                    await tree.sync(guild=g)
                except Exception as e:
                    print("[WARN] Discord slash commands not added in %s: %s (re-invite the bot with the "
                          "applications.commands scope)" % (g, e))
        ui_channel = await _fetch_channel(client, _ui_channel_id) or await _first_usable_channel(client)
        if ui_channel is None:
            print("[WARN] The bot can't see any channel it may post in - invite it and set channel_id.")
            return
        _check_permissions(ui_channel, "panel")
        log_ch = await _fetch_channel(client, _log_channel_id)
        if _log_channel_id and log_ch is None:
            print("[WARN] Discord: log_channel_id %s not found or not visible to the bot" % _log_channel_id)
        elif log_ch is not None:
            _check_permissions(log_ch, "activity")
        mgr = get_server_manager()
        if mgr is None:
            return
        if not state["panel"]:
            state["panel"] = True
            try:
                await _build_control_panel(client, ui_channel, mgr)
            except Exception as e:
                print("[WARN] Discord panel: %s" % e)
            client.loop.create_task(_keep_fresh(client, ui_channel, mgr))
            events, _s = _stores()
            for ev in events.upcoming():       # keep the I'm in / Can't make it buttons working
                if ev.get("message") and not ev.get("cancelled"):
                    try:
                        client.add_view(_event_view(ev), message_id=int(ev["message"]))
                    except Exception:
                        pass
            client.loop.create_task(_event_loop(client))

    @client.event
    async def on_guild_join(guild):
        try:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        except Exception:
            pass

    _client = client
    return client


def start_bot(app_instance=None):
    """Start the bot on a background thread (keeps running, reconnects, restarts on crashes)."""
    global _app_instance, _listener_added
    _app_instance = app_instance
    if discord is None:
        print("[ERROR] Cannot start Discord bot - discord.py not installed")
        return
    raw = _load_config()
    if raw is None:
        return
    from core.secrets import bot_token
    mgr0 = get_server_manager()
    token = bot_token(mgr0.config_path.parent if mgr0 is not None else _base_dir(), raw)
    if not token:
        print("[INFO] Discord bot token not configured; skipping bot startup.")
        return
    global _raw_config
    _raw_config = dict(raw)
    mgr = get_server_manager()
    try:      # channels / roles chosen with /baobun-setup (kept in settings, not in discord_config.json)
        raw = X.merged_config(raw, X.setup_values(mgr)) if mgr is not None else raw
    except Exception:
        pass
    _apply_config(raw)
    if mgr is not None and hasattr(mgr, "add_listener") and not _listener_added:
        mgr.add_listener(_on_mgr_event)
        _listener_added = True

    def run_bot():
        delay = 15
        while not _stop.is_set():
            client = _make_client(token)
            began = time.time()
            try:
                client.run(token, log_handler=None)
            except discord.LoginFailure:
                print("[ERROR] Discord login failed - the bot token is wrong or was reset (paste it again in BaoBun: Settings > Discord bot)")
                return
            except discord.PrivilegedIntentsRequired as e:
                print("[ERROR] Discord: %s" % e)
                return
            except Exception as e:
                print("[ERROR] Discord bot stopped: %s" % e)
            if _stop.is_set():
                return
            delay = 15 if time.time() - began > 600 else min(delay * 2, 600)
            print("[INFO] Discord bot reconnecting in %d s" % delay)
            _stop.wait(delay)

    global _thread
    _thread = threading.Thread(target=run_bot, daemon=True, name="baobun-discord")
    _thread.start()
    print("[INFO] Discord bot thread started.")


_thread = None


def restart_bot(app_instance=None, wait: float = 20.0) -> bool:
    """Reconnect with the current token and settings (after the token was changed in
    BaoBun's Discord bot window). True when a token is set and the bot is starting."""
    stop_bot()
    t = _thread
    if t is not None and t.is_alive():
        t.join(wait)
    _stop.clear()
    start_bot(app_instance=app_instance if app_instance is not None else _app_instance)
    return _thread is not None and _thread.is_alive()


def stop_bot():
    _stop.set()
    client = _client
    if client is not None and not client.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(client.close(), client.loop)
        except Exception:
            pass
