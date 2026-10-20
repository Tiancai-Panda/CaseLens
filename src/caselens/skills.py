"""Bounded packaged Skill discovery and fixed trusted quantity binding."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path, PurePosixPath

import yaml

_BUNDLED_ROOT = Path(__file__).parent / "skill_bundles"
_MAX_BYTES = 65536
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    pairs = [(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value]
    return _unique_pairs(pairs)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Duplicate or invalid metadata field")
        result[key] = value
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _relative(path):
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError("Resource must be a relative POSIX path")
    if any(part in ("", ".", "..") or ":" in part for part in path.split("/")):
        raise ValueError("Resource path escapes bundle")
    relative = PurePosixPath(path)
    if relative.is_absolute():
        raise ValueError("Resource must be relative")
    return relative


def _file(root, path):
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError("Skill symlinks are forbidden")
    current = root
    for part in _relative(path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Skill symlinks are forbidden")
    resolved = current.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("Resource must be a file inside the bundle")
    return resolved


def _text(root, path):
    with _file(root, path).open("rb") as source:
        raw = source.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("Skill file exceeds byte limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Skill resources must be UTF-8 text") from error


class SkillRegistry:
    """Stateless catalog. The service owns activation/context deduplication.

    Default discovery never scans user directories. Explicit roots are trusted
    metadata fixtures, and can never redirect the executable quantity binding.
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else _BUNDLED_ROOT
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Skill root must be a real directory")
        self.root = self.root.resolve()
        self._entries = self._discover()

    def _discover(self):
        directories = sorted(self.root.iterdir())
        if len(directories) > 32:
            raise ValueError("Skill directory count exceeded")
        entries = {}
        for directory in directories:
            if directory.name == "__pycache__":
                continue
            if directory.is_symlink():
                raise ValueError("Skill symlinks are forbidden")
            if not directory.is_dir():
                continue
            if len(directory.name) > 64 or not _NAME.fullmatch(directory.name):
                raise ValueError("Invalid Skill directory name")
            count = size = 0
            for resource in directory.rglob("*"):
                if resource.is_symlink():
                    raise ValueError("Skill symlinks are forbidden")
                if "__pycache__" in resource.parts:
                    continue
                if resource.is_file():
                    count += 1
                    size += resource.stat().st_size
                if count > 64 or size > 524288:
                    raise ValueError("Skill file/byte count exceeded")
            content = _text(directory, "SKILL.md")
            lines = content.splitlines()
            if not lines or lines[0] != "---":
                raise ValueError("SKILL.md requires YAML frontmatter")
            try:
                closing = lines.index("---", 1)
                metadata = yaml.load("\n".join(lines[1:closing]), Loader=_UniqueLoader)
            except (ValueError, yaml.YAMLError) as error:
                raise ValueError("Malformed Skill YAML metadata") from error
            if not isinstance(metadata, dict):
                raise ValueError("Skill metadata must be a mapping")
            name, description = metadata.get("name"), metadata.get("description")
            if not isinstance(name, str) or name != directory.name:
                raise ValueError("Skill name must match directory")
            if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
                raise ValueError("Invalid Skill description")
            if not "\n".join(lines[closing + 1:]).strip():
                raise ValueError("Skill instructions are empty")
            try:
                manifest = json.loads(_text(directory, "manifest.json"),
                                      object_pairs_hook=_unique_pairs)
            except (ValueError, json.JSONDecodeError) as error:
                raise ValueError("Malformed Skill manifest") from error
            if not isinstance(manifest, dict):
                raise ValueError("Manifest must be a mapping")
            if manifest.get("name", name) != name:
                raise ValueError("Manifest name must match Skill")
            version = manifest.get("version")
            if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("Invalid Skill version")
            issues, resources = manifest.get("issues"), manifest.get("resources")
            for field, values, limit in (("issues", issues, 16), ("resources", resources, 32)):
                if not isinstance(values, list) or len(values) > limit or any(
                    not isinstance(value, str) or not value for value in values
                ) or len(set(values)) != len(values):
                    raise ValueError(f"Invalid or duplicate manifest {field}")
            if not issues:
                raise ValueError("Skill requires issues")
            for resource in resources:
                _file(directory, resource)
            tools = manifest.get("tools", {})
            if not isinstance(tools, dict) or any(
                key != "check_quantity" or name != "missing-quantity"
                or value != "scripts/check_quantity.py" for key, value in tools.items()
            ):
                raise ValueError("Unknown deterministic Skill tool binding")
            if tools and "scripts/check_quantity.py" not in resources:
                raise ValueError("Tool binding must be allowlisted")
            if name in entries:
                raise ValueError("Duplicate Skill name")
            entries[name] = {
                "name": name, "description": description, "version": version, "issues": issues,
                "resources": resources, "content": content, "directory": directory
            }
        return entries

    def catalog(self) -> list[dict]:
        return [
            {key: list(entry[key]) if key == "issues" else entry[key]
             for key in ("name", "description", "version", "issues")}
            for entry in self._entries.values()
        ]

    def _entry(self, name):
        if not isinstance(name, str) or name not in self._entries:
            raise ValueError("Unknown Skill name")
        return self._entries[name]

    def activate(self, name: str) -> dict:
        entry = self._entry(name)
        return {key: list(entry[key]) if key == "resources" else entry[key]
                for key in ("name", "version", "content", "resources")}

    def select(self, issues: list[str]) -> list[dict]:
        if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
            raise ValueError("Issues must be a list of strings")
        selected, seen = [], set()
        for issue in issues:
            for name, entry in self._entries.items():
                if (issue == name or issue in entry["issues"]) and name not in seen:
                    selected.append({key: entry[key] for key in ("name", "version", "content")})
                    seen.add(name)
        return selected

    def read_resource(self, name: str, path: str) -> dict:
        entry = self._entry(name)
        _relative(path)
        if path not in entry["resources"]:
            raise ValueError("Resource is not allowlisted")
        return {"name": name, "path": path, "content": _text(entry["directory"], path)}

    def check_quantity(self, data: dict) -> dict:
        # Never use self.root or a tool argument to choose executable code.
        script = _file(_BUNDLED_ROOT.resolve(), "missing-quantity/scripts/check_quantity.py")
        spec = importlib.util.spec_from_file_location("_caselens_trusted_quantity", script)
        if spec is None or spec.loader is None:
            raise ValueError("Trusted quantity helper unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check_quantity(data)
