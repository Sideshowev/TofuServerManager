"""
BaoBun Control Panel - Configuration Engine

Universal configuration schema system that supports:
- JSON, JSONC, HJSON, INI, XML, YAML, CFG, Properties, TOML
- Parameter types: text, number, boolean, dropdown, multiselect, slider,
  color, path, password, advanced settings
- Validation rules with safe limits, tooltips, categories
- Auto-generated forms from schemas
- Preview before apply with rollback

Each server plugin defines its configuration files, parameter definitions,
validation rules, default values, documentation, and categories.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Parameter Types
# ---------------------------------------------------------------------------

class ParameterType(Enum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DROPDOWN = "dropdown"
    MULTISELECT = "multiselect"
    SLIDER = "slider"
    COLOR = "color"
    PATH = "path"
    PASSWORD = "password"
    ADVANCED = "advanced"   # Hidden by default, shown on request
    PORT = "port"             # Network port number (1-65535)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationRule:
    """A validation rule for a configuration parameter."""
    rule_type: str
    value: Any
    message: str = ""
    severity: str = "error"

    def check(self, value: Any) -> Optional[str]:
        v = value
        if self.rule_type == "min":
            try:
                if float(v) < float(self.value):
                    return self.message or f"Value must be >= {self.value}"
            except (TypeError, ValueError):
                return self.message or "Invalid number"
        elif self.rule_type == "max":
            try:
                if float(v) > float(self.value):
                    return self.message or f"Value must be <= {self.value}"
            except (TypeError, ValueError):
                return self.message or "Invalid number"
        elif self.rule_type == "min_length":
            if len(str(v)) < int(self.value):
                return self.message or f"Length must be >= {self.value}"
        elif self.rule_type == "max_length":
            if len(str(v)) > int(self.value):
                return self.message or f"Length must be <= {self.value}"
        elif self.rule_type == "pattern":
            if not re.match(str(self.value), str(v)):
                return self.message or f"Value does not match pattern {self.value}"
        elif self.rule_type == "choices":
            if str(v) not in str(self.value).split(","):
                return self.message or f"Value must be one of: {self.value}"
        elif self.rule_type == "file_exists":
            if not os.path.isfile(str(v)):
                return self.message or f"File not found: {v}"
        elif self.rule_type == "dir_exists":
            if not os.path.isdir(str(v)):
                return self.message or f"Directory not found: {v}"
        return None


class ConfigEngine:

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self._schemas = {}
        self._lock = threading.RLock()

    def register_schema(self, schema):
        with self._lock:
            self._schemas[schema.file_path] = schema

    def register_schemas(self, schemas):
        with self._lock:
            for s in schemas:
                self._schemas[s.file_path] = s

    def get_schema(self, file_path):
        with self._lock:
            return self._schemas.get(file_path)

    def list_schemas(self):
        with self._lock:
            return list(self._schemas.values())

    def read_config(self, server_dir, schema):
        server_dir = Path(server_dir)
        full_path = server_dir / schema.file_path
        if not full_path.exists():
            return self._defaults_from_schema(schema)
        fmt = schema.file_format.lower()
        try:
            if fmt == "json":
                data = self._read_json(full_path)
            elif fmt == "ini":
                data = self._read_ini(full_path)
            elif fmt == "yaml":
                data = self._read_yaml(full_path)
            elif fmt == "toml":
                data = self._read_toml(full_path)
            elif fmt == "xml":
                data = self._read_xml(full_path)
            elif fmt in ("cfg", "properties", "text"):
                data = self._read_kv(full_path)
            else:
                data = self._read_text(full_path)
        except Exception:
            data = {"raw_text": full_path.read_text(encoding="utf-8", errors="replace")}
        return self._map_to_schema(schema, data)

    def _defaults_from_schema(self, schema):
        sections = []
        for section in schema.sections:
            params = []
            for p in section.parameters:
                params.append({
                    "name": p.name, "label": p.label, "value": p.default,
                    "default": p.default, "param_type": p.param_type.value,
                    "description": p.description, "tooltip": p.tooltip,
                    "category": p.category, "advanced": p.advanced,
                    "read_only": p.read_only, "restart_required": p.restart_required,
                    "validation_errors": [],
                })
            sections.append({
                "name": section.name, "label": section.label,
                "description": section.description, "advanced": section.advanced,
                "parameters": params,
            })
        return {
            "file_path": schema.file_path, "file_format": schema.file_format,
            "label": schema.label, "description": schema.description,
            "sections": sections,
        }

    def _map_to_schema(self, schema, raw):
        sections_out = []
        raw_sections = raw.get("sections", {})
        raw_defaults = raw.get("defaults", {})
        for section in schema.sections:
            params_out = []
            section_data = raw_sections.get(section.name, {})
            for p in section.parameters:
                current = section_data.get(p.name, raw_defaults.get(p.name, p.default))
                errors = self._validate_parameter(p, current)
                params_out.append({
                    "name": p.name, "label": p.label, "value": current,
                    "default": p.default, "is_default": current == p.default,
                    "param_type": p.param_type.value, "description": p.description,
                    "tooltip": p.tooltip, "category": p.category,
                    "advanced": p.advanced, "read_only": p.read_only,
                    "restart_required": p.restart_required,
                    "validation_errors": errors, "choices": p.choices,
                    "min_value": p.min_value, "max_value": p.max_value,
                    "step": p.step,
                })
            sections_out.append({
                "name": section.name, "label": section.label,
                "description": section.description, "advanced": section.advanced,
                "parameters": params_out,
            })
        return {
            "file_path": schema.file_path, "file_format": schema.file_format,
            "label": schema.label, "description": schema.description,
            "sections": sections_out, "restart_required": schema.restart_required,
        }

    def _validate_parameter(self, param, value):
        errors = []
        if param.read_only:
            return errors
        for rule in param.validation:
            err = rule.check(value)
            if err:
                errors.append(err)
        if param.param_type == ParameterType.INTEGER:
            try: int(value)
            except (TypeError, ValueError): errors.append(f"{param.label} must be an integer")
        elif param.param_type == ParameterType.FLOAT:
            try: float(value)
            except (TypeError, ValueError): errors.append(f"{param.label} must be a number")
        elif param.param_type == ParameterType.BOOLEAN:
            if str(value).lower() not in ("true", "false", "1", "0", "yes", "no"):
                errors.append(f"{param.label} must be true or false")
        elif param.param_type == ParameterType.PORT:
            try:
                port = int(value)
                if port < 1 or port > 65535:
                    errors.append(f"{param.label} must be between 1 and 65535")
            except (TypeError, ValueError):
                errors.append(f"{param.label} must be a valid port number")
        return errors

    def write_config(self, server_dir, schema, values):
        server_dir = Path(server_dir)
        full_path = server_dir / schema.file_path
        backup_path = None
        if schema.backup_before_write and full_path.exists():
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_path = str(full_path) + f".bak.{ts}"
            shutil.copy2(full_path, backup_path)
        errors = self.validate_values(schema, values)
        if errors:
            return {"success": False, "errors": errors, "backup_path": backup_path}
        fmt = schema.file_format.lower()
        try:
            if fmt == "json":
                self._write_json(full_path, values, schema)
            elif fmt == "ini":
                self._write_ini(full_path, values, schema)
            elif fmt == "yaml":
                self._write_yaml(full_path, values, schema)
            elif fmt == "toml":
                self._write_toml(full_path, values, schema)
            elif fmt == "xml":
                self._write_xml(full_path, values, schema)
            elif fmt in ("cfg", "properties", "text"):
                self._write_kv(full_path, values, schema)
            else:
                full_path.write_text(str(values), encoding="utf-8")
        except Exception as e:
            return {"success": False, "errors": [f"Write failed: {e}"], "backup_path": backup_path}
        return {"success": True, "backup_path": backup_path, "file_path": str(full_path),
                "restart_required": schema.restart_required}

    def validate_values(self, schema, values):
        errors = []
        for section in schema.sections:
            section_values = values.get(section.name, {})
            for p in section.parameters:
                if p.read_only:
                    continue
                val = section_values.get(p.name, p.default)
                for err in self._validate_parameter(p, val):
                    errors.append(f"{section.label} > {p.label}: {err}")
        return errors

    def _read_json(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"sections": {"root": data} if isinstance(data, dict) else {}}

    def _write_json(self, path, values, schema):
        flat = {}
        for section in schema.sections:
            sec_vals = values.get(section.name, {})
            for p in section.parameters:
                flat[p.name] = sec_vals.get(p.name, p.default)
        path.write_text(json.dumps(flat, indent=2), encoding="utf-8")

    def _read_ini(self, path):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(path, encoding="utf-8")
        sections = {}
        for section in parser.sections():
            sections[section] = dict(parser.items(section))
        if parser.defaults():
            sections["DEFAULT"] = dict(parser.defaults())
        return {"sections": sections}

    def _write_ini(self, path, values, schema):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        for section in schema.sections:
            sec_vals = values.get(section.name, {})
            parser[section.name] = {p.name: str(sec_vals.get(p.name, p.default))
                                    for p in section.parameters}
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

    def _read_yaml(self, path):
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except ImportError:
            data = {}
        if isinstance(data, dict):
            return {"sections": {"root": data}}
        return {"sections": {}}

    def _write_yaml(self, path, values, schema):
        try:
            import yaml
            flat = {}
            for section in schema.sections:
                sec_vals = values.get(section.name, {})
                for p in section.parameters:
                    flat[p.name] = sec_vals.get(p.name, p.default)
            path.write_text(yaml.dump(flat, default_flow_style=False), encoding="utf-8")
        except ImportError:
            raise RuntimeError("PyYAML not installed")

    def _read_toml(self, path):
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except ImportError:
            try:
                import toml as _toml
                data = _toml.loads(path.read_text(encoding="utf-8"))
            except ImportError:
                data = {}
        if isinstance(data, dict):
            return {"sections": {"root": data}}
        return {"sections": {}}

    def _write_toml(self, path, values, schema):
        try:
            import toml as _toml
            flat = {}
            for section in schema.sections:
                sec_vals = values.get(section.name, {})
                for p in section.parameters:
                    flat[p.name] = sec_vals.get(p.name, p.default)
            path.write_text(_toml.dumps(flat), encoding="utf-8")
        except ImportError:
            raise RuntimeError("toml library not installed")

    def _read_xml(self, path):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            root = tree.getroot()
            data = {child.tag: child.text for child in root}
            return {"sections": {"root": data}}
        except Exception:
            return {"sections": {}}

    def _write_xml(self, path, values, schema):
        try:
            import xml.etree.ElementTree as ET
            root = ET.Element("config")
            for section in schema.sections:
                sec_vals = values.get(section.name, {})
                for p in section.parameters:
                    child = ET.SubElement(root, p.name)
                    child.text = str(sec_vals.get(p.name, p.default))
            ET.ElementTree(root).write(path, encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"XML write failed: {e}")

    def _read_kv(self, path):
        keys = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                keys[key.strip()] = val.strip()
        return {"sections": {"root": keys}}

    def _write_kv(self, path, values, schema):
        lines = []
        for section in schema.sections:
            sec_vals = values.get(section.name, {})
            for p in section.parameters:
                val = sec_vals.get(p.name, p.default)
                lines.append(f"{p.name}={val}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_text(self, path):
        return {"raw_text": path.read_text(encoding="utf-8", errors="replace")}

    def list_backups(self, file_path):
        full = Path(file_path)
        if not full.exists():
            return []
        backups = []
        for bak in sorted(full.parent.glob(f"{full.name}.bak.*")):
            stat = bak.stat()
            backups.append({
                "path": str(bak), "name": bak.name,
                "created": stat.st_mtime, "size": stat.st_size,
            })
        return sorted(backups, key=lambda b: b["created"], reverse=True)

    def rollback(self, file_path, backup_name):
        full = Path(file_path)
        bak = f


@dataclass
class ConfigParameter:
    name: str
    label: str
    param_type: ParameterType = ParameterType.TEXT
    default: Any = None
    description: str = ""
    tooltip: str = ""
    category: str = "General"
    validation: List[ValidationRule] = field(default_factory=list)
    choices: List[str] = field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    advanced: bool = False
    read_only: bool = False
    restart_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label,
            "param_type": self.param_type.value, "default": self.default,
            "description": self.description, "tooltip": self.tooltip,
            "category": self.category,
            "validation": [{"rule_type": r.rule_type, "value": r.value,
                           "message": r.message, "severity": r.severity}
                          for r in self.validation],
            "choices": self.choices, "min_value": self.min_value,
            "max_value": self.max_value, "step": self.step,
            "advanced": self.advanced, "read_only": self.read_only,
            "restart_required": self.restart_required,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfigParameter":
        return cls(
            name=data["name"],
            label=data.get("label", data["name"]),
            param_type=ParameterType(data.get("param_type", "text")),
            default=data.get("default"),
            description=data.get("description", ""),
            tooltip=data.get("tooltip", ""),
            category=data.get("category", "General"),
            validation=[ValidationRule(rule_type=v["rule_type"],
                                       value=v.get("value"),
                                       message=v.get("message", ""),
                                       severity=v.get("severity", "error"))
                       for v in data.get("validation", [])],
            choices=data.get("choices", []),
            min_value=data.get("min_value"),
            max_value=data.get("max_value"),
            step=data.get("step"),
            advanced=data.get("advanced", False),
            read_only=data.get("read_only", False),
            restart_required=data.get("restart_required", False),
        )


@dataclass
class ConfigSection:
    name: str
    label: str
    parameters: List[ConfigParameter] = field(default_factory=list)
    description: str = ""
    advanced: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label,
            "description": self.description, "advanced": self.advanced,
            "parameters": [p.to_dict() for p in self.parameters],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfigSection":
        return cls(
            name=data["name"],
            label=data.get("label", data["name"]),
            description=data.get("description", ""),
            advanced=data.get("advanced", False),
            parameters=[ConfigParameter.from_dict(p)
                       for p in data.get("parameters", [])],
        )


@dataclass
class ConfigSchema:
    file_path: str
    file_format: str
    label: str
    description: str = ""
    sections: List[ConfigSection] = field(default_factory=list)
    backup_before_write: bool = True
    restart_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path, "file_format": self.file_format,
            "label": self.label, "description": self.description,
            "sections": [s.to_dict() for s in self.sections],
            "backup_before_write": self.backup_before_write,
            "restart_required": self.restart_required,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfigSchema":
        return cls(
            file_path=data["file_path"],
            file_format=data.get("file_format", "text"),
            label=data.get("label", data["file_path"]),
            description=data.get("description", ""),
            sections=[ConfigSection.from_dict(s)
                     for s in data.get("sections", [])],
            backup_before_write=data.get("backup_before_write", True),
            restart_required=data.get("restart_required", False),
        )

    def get_parameter(self, section_name: str, param_name: str) -> Optional[ConfigParameter]:
        for section in self.sections:
            if section.name == section_name:
                for p in section.parameters:
                    if p.name == param_name:
                        return p
        return None

    def get_parameters_by_category(self, category: str) -> List[ConfigParameter]:
        result = []
        for section in self.sections:
            for p in section.parameters:
                if p.category == category:
                    result.append(p)
        return result

    def get_advanced_parameters(self) -> List[ConfigParameter]:
        result = []
        for section in self.sections:
            for p in section.parameters:
                if p.advanced:
                    result.append(p)
        return result

    def get_regular_parameters(self) -> List[ConfigParameter]:
        result = []
        for section in self.sections:
            for p in section.parameters:
                if not p.advanced:
                    result.append(p)
        return result


@dataclass
class ConfigValue:
    section: str
    parameter: str
    value: Any
    default: Any
    is_default: bool
    validation_errors: List[str] = field(default_factory=list)
    restart_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section, "parameter": self.parameter,
            "value": self.value, "default": self.default,
            "isDefault": self.is_default,
            "validationErrors": self.validation_errors,
            "restartRequired": self.restart_required,
        }
