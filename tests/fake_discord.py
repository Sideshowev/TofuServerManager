"""A tiny stand-in for discord.py so the bot's logic can be tested without it."""
import sys
import types


def install():
    d = types.ModuleType("discord")
    ui = types.ModuleType("discord.ui")
    ac = types.ModuleType("discord.app_commands")

    class ButtonStyle:
        success, secondary, danger, primary = "success", "secondary", "danger", "primary"

    class Button:
        def __init__(self, style=None, label=None, disabled=False, custom_id=None, row=None):
            self.style, self.label, self.disabled, self.custom_id, self.row = style, label, disabled, custom_id, row

    class View:
        def __init__(self, timeout=180):
            self.children = []
            self.timeout = timeout

        def add_item(self, item):
            self.children.append(item)

        def stop(self):
            pass

    class Embed:
        def __init__(self, title=None, description=None, color=None, timestamp=None):
            self.title, self.description = title, description

        def set_footer(self, text=None):
            self.footer = text

    class AllowedMentions:
        def __init__(self, **k):
            self.k = k

        @classmethod
        def none(cls):
            return cls()

    class NotFound(Exception):
        pass

    class LoginFailure(Exception):
        pass

    class PrivilegedIntentsRequired(Exception):
        pass

    class TextChannel:
        pass

    class Role:
        pass

    class Game:
        def __init__(self, name=None):
            self.name = name

    class File:
        def __init__(self, fp, filename=None):
            self.fp, self.filename = fp, filename

    d.TextChannel, d.Role, d.Game, d.File = TextChannel, Role, Game, File
    ui.Button, ui.View = Button, View
    d.ui, d.ButtonStyle, d.Embed, d.AllowedMentions = ui, ButtonStyle, Embed, AllowedMentions
    d.NotFound, d.LoginFailure, d.PrivilegedIntentsRequired = NotFound, LoginFailure, PrivilegedIntentsRequired

    class Choice:
        def __init__(self, name, value):
            self.name, self.value = name, value

    class CommandTree:
        def __init__(self, client):
            self.commands = {}

        def command(self, name=None, description=None):
            def deco(fn):
                self.commands[name] = fn
                return fn
            return deco

    def passthrough(**k):
        return lambda fn: fn
    ac.Choice, ac.CommandTree, ac.describe, ac.autocomplete = Choice, CommandTree, passthrough, passthrough
    ac.choices = passthrough
    d.app_commands = ac
    sys.modules["discord"], sys.modules["discord.ui"], sys.modules["discord.app_commands"] = d, ui, ac
    return d
