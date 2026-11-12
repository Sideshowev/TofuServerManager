# -*- mode: python ; coding: utf-8 -*-
# BaoBun Control Panel — PyInstaller build spec.
# Rebuild with:  pyinstaller BaoBunCP.spec   (run from this folder)
from PyInstaller.utils.hooks import collect_all

# Brand assets and the game plugins are bundled into the onefile exe; they are
# resolved at runtime via sys._MEIPASS (see ASSETS_DIR in app.py and
# core/paths.py). A 'plugins' folder next to the exe overrides bundled plugins.
datas = [('assets', 'assets'), ('presets.json', '.'), ('plugins', 'plugins'), ('ui/web', 'ui/web')]
binaries = []
hiddenimports = ['psutil', 'core.hosting', 'ui.home', 'ui.console', 'ui.wizard', 'core.secrets', 'core.a2s', 'core.game_catalog', 'core.engine', 'core.local_api', 'core.paths', 'core.plugin_bridge', 'baobun_agent', 'server_manager', 'smart_boot', 'activity_tracker', 'discord_bot', 'rcon', 'core', 'ui', 'core.plugin_system', 'core.config_engine', 'core.database_layer', 'core.process_manager', 'core.api_layer', 'core.preset_engine', 'core.health_check', 'ui.integration', 'ui.web_settings', 'core.web_panel', 'core.web_auth', 'core.diagnose', 'core.alerts', 'ui.alerts_settings', 'core.mods', 'core.mod_sources', 'core.mods_common', 'core.mods_ids', 'core.mods_workshop', 'core.mods_sites', 'core.history', 'core.players', 'core.network', 'core.quick_settings', 'core.importer', 'core.discord_status', 'core.quick_commands', 'core.minecraft', 'core.game_installers', 'core.rcon_config', 'core.battleye', 'ui.install_dialog', 'ui.settings_tab', 'ui.checkup_dialog', 'core.checkup', 'core.config_forms', 'core.config_catalog', 'core.mod_browse', 'core.port_plan', 'core.rcon_setup', 'ui.ports_dialog', 'core.nightly', 'core.boot_task', 'ui.nightly_dialog', 'core.ark_cluster', 'core.ark_ini', 'core.ark_catalog', 'ui.ark_window', 'core.hytale', 'core.discord_logic', 'core.self_update', 'core.heartbeat', 'ui.watchdog_dialog', 'ui.import_dialog', 'ui.connect_dialog', 'ui.quick_settings_tab', 'ui.mods_tab', 'ui.history', 'core.updates', 'core.version', 'core.bring_here', 'core.backup_check', 'core.first_run', 'core.player_access', 'core.diagnostics', 'core.discord_events', 'core.discord_extras', 'core.player_stats', 'core.mem_guard', 'core.status_page', 'core.mod_updates', 'core.hytale_install', 'core.img_cache', 'ui.first_run', 'ui.discord_bot_dialog', 'ui.players_dialog', 'ui.update_dialog', 'ui.bring_here_dialog', 'ui.restore_dialog', 'ui.mod_browser', 'ui.hytale_signin', 'ui.status_page_dialog', 'core.linked_pcs', 'ui.linked_pcs_dialog', 'ui.remote_server_window', 'toml', 'yaml']

for package in ('customtkinter', 'PIL', 'discord'):
    data, bins, hiddens = collect_all(package)
    datas += data
    binaries += bins
    hiddenimports += hiddens

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy"],          # Pillow can use it but BaoBun never does: 28 MB unpacked at every start
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BaoBunCP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
