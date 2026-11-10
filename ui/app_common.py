"""Things the desktop app's modules share: data folders, the colour theme and the
image loader. (Split out of app.py so its windows can live in their own files.)"""

import sys
from pathlib import Path

import customtkinter as ctk

from core.paths import base_dir as _base_dir, bundle_dir as _bundle_dir

BASE_DIR = _base_dir()
BUNDLE_DIR = _bundle_dir()
APP_DIR = BASE_DIR
CONFIG_PATH = APP_DIR / "servers.json"
ASSETS_DIR = BUNDLE_DIR / "assets"

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    PIL_AVAILABLE = False

ACCENTS = {
    "cyan":    {"p": "#17607a", "s": "#7fb3c0", "ink": "#145066"},
    "blue":    {"p": "#2f517f", "s": "#8299b8", "ink": "#274271"},
    "violet":  {"p": "#574f88", "s": "#9690b8", "ink": "#474170"},
    "emerald": {"p": "#2f6e57", "s": "#82ab99", "ink": "#275a47"},
    "amber":   {"p": "#7a5b20", "s": "#a98f62", "ink": "#634a1a"},
    "rose":    {"p": "#7f414c", "s": "#a57e87", "ink": "#68343e"},
    "gold":    {"p": "#a67c00", "s": "#e6c87e", "ink": "#5c4410"},
}


def build_theme(accent="cyan", mode="dark"):
    """Return the full color theme for a given accent + light/dark mode."""
    a = ACCENTS.get(accent, ACCENTS["cyan"])
    if mode == "light":
        return {
            "bg_dark": "#eef1f5",
            "bg_card": "#ffffff",
            "bg_input": "#e6ecf3",
            "accent_blue": a["ink"],
            "accent_gold": a["ink"],
            "accent_green": "#2f7a58",
            "accent_red": "#a85a63",
            "text_light": "#1f2937",
            "text_muted": "#5b6570",
            "border": "#c9d3de",
            "code_bg": "#f6f8fa",
            "code_fg": "#24292f",
            "status_inactive": "#8a94a0",
            "success": "#15803d",
            "status_online": "#15803d",
            "danger": "#b91c1c",
            "warning": "#b45309",
            "info": a["ink"],
        }
    return {
        "bg_dark": "#0a0a0a",
        "bg_card": "#121212",
        "bg_input": "#1a1a1a",
        "accent_blue": a["p"],
        "accent_gold": a["s"],
        "accent_green": "#2f7a58",
        "accent_red": "#a85a63",
        "text_light": "#e8e0d0",
        "text_muted": "#9a8a70",
        "border": "#3d3d3d",
        "code_bg": "#0d1117",
        "code_fg": "#c9d1d9",
        "status_inactive": "#64748b",
        "success": "#15803d",
        "status_online": "#4ade80",
        "danger": "#b91c1c",
        "warning": "#b45309",
        "info": a["p"],
    }


THEME = build_theme("gold")


def later(widget, fn):
    """Run fn on the window's own thread - for background threads that finished their
    work and want to show it. Does nothing when the window is gone or BaoBun is closing
    (tkinter otherwise raises "main thread is not in main loop" inside the thread).
    Returns True when fn was handed over."""
    try:
        widget.after(0, fn)
        return True
    except Exception:       # RuntimeError (no main loop) or TclError (window destroyed)
        return False


def load_image(name, size=(24, 24)):
    if not PIL_AVAILABLE:
        return None
    path = ASSETS_DIR / name
    if not path.exists():
        return None
    img = Image.open(path).convert("RGBA")
    img = img.resize(size, Image.LANCZOS)
    return ctk.CTkImage(light_image=img, dark_image=img, size=size)
