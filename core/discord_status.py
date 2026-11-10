"""Text for the Discord status embed (kept separate so it can be tested without discord.py)."""


def status_lines(mgr, statuses, show_address=False):
    """One line per server for the status embed (players, update badge, join address)."""
    pub = None
    if show_address:
        try:
            from core.network import public_ip
            pub = public_ip()
        except Exception:
            pub = None
    lines = []
    for st in statuses:
        state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
        icon = {"running": "\U0001F7E2", "crashed": "\U0001F534"}.get(state, "\U0001F7E1" if state in (
            "starting", "updating", "stopping", "restarting") else "\u26AA")
        bits = ["**%s**" % st.get("name", "?")]
        if st.get("isRunning"):
            if st.get("playerCount") is not None:
                names = [p.get("name") for p in st.get("players") or [] if p.get("name")]
                who = (" (" + ", ".join(names[:5]) + (" +%d" % (len(names) - 5) if len(names) > 5 else "") + ")") if names else ""
                bits.append("%s/%s online%s" % (st["playerCount"], st.get("maxPlayers") or "?", who))
            else:
                bits.append("online")
        else:
            bits.append({"crashed": "crashed", "starting": "starting...", "updating": "updating...",
                         "stopping": "stopping...", "restarting": "restarting..."}.get(state, "offline"))
        if pub:
            srv = mgr.get_server(st["id"]) or {}
            try:
                from core.network import expected_ports
                ports = expected_ports(srv, mgr)
                if ports:
                    bits.append("`%s:%d`" % (pub, ports[0]["port"]))
            except Exception:
                pass
        if st.get("updateAvailable"):
            bits.append("update available")
        lines.append(icon + " " + " \u00b7 ".join(bits))
    return lines
