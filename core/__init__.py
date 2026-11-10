"""BaoBun Control Panel - core layer.

Submodules are imported on demand (``from core.api_layer import ApiLayer``) so
that one optional dependency missing (e.g. psutil, toml) can't stop the whole
app or the background agent from starting. The names below remain available
as ``core.<Name>`` for older code.
"""

import importlib

_EXPORTS = {
    "PluginBase": "plugin_system", "ServerPlugin": "plugin_system",
    "ConfigPlugin": "plugin_system", "ProcessPlugin": "plugin_system",
    "BackupPlugin": "plugin_system", "UpdatePlugin": "plugin_system",
    "MonitoringPlugin": "plugin_system", "PluginManager": "plugin_system",
    "PluginMetadata": "plugin_system", "PluginCapability": "plugin_system",
    "PluginError": "plugin_system", "ServerDefinition": "plugin_system",
    "ServerStatus": "plugin_system",
    "ConfigSchema": "config_engine", "ConfigParameter": "config_engine",
    "ConfigSection": "config_engine", "ConfigValue": "config_engine",
    "ParameterType": "config_engine", "ValidationRule": "config_engine",
    "ConfigEngine": "config_engine",
    "DatabaseLayer": "database_layer",
    "ProcessManager": "process_manager", "ProcessMetrics": "process_manager",
    "ApiLayer": "api_layer", "ApiRequest": "api_layer", "ApiResponse": "api_layer",
    "ApiError": "api_layer",
    "FileManager": "file_management",
    "PresetEngine": "preset_engine", "Preset": "preset_engine", "PresetParameter": "preset_engine",
    "ModPackManager": "mod_pack_manager", "ModInfo": "mod_pack_manager",
    "ModInstallResult": "mod_pack_manager", "ModSource": "mod_pack_manager",
    "HealthCheck": "health_check",
    "Engine": "engine", "build_engine": "engine",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(name)
    return getattr(importlib.import_module("core." + mod), name)
