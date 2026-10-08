"""Where things live: the two engine projects, the shared asset folder, and Unity's guid -> path table.

A twinscene.json at the repository root configures it (all keys optional):

    {
      "unity": "unity",                 Unity project folder (holds Assets/)
      "godot": "godot",                 Godot project folder (holds project.godot)
      "shared": {"unity": "Assets", "godot": ""},   models, textures, audio and fonts under these folders are
                                                    mirrored, at the same paths ("" is the Godot project root)
      "scenes": [{"unity": "Assets/Scenes/Main.unity", "godot": "scenes/main.tscn"}],
      "scripts": {"Assets/Scripts/Enemy.cs": "res://ai/enemy_brain.gd"}   script pairs whose names differ
    }                                                                     (see scripts.py)

Asset references in the neutral model are "shared:<path inside the shared folder>" when both engines have the file,
or "unity:Assets/..." / "godot:res://..." when only one does (those don't sync).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_GUID = re.compile(r"^guid:\s*([0-9a-f]{32})\s*$", re.M)


@dataclass
class Config:
    root: Path
    unity: Path
    godot: Path
    shared_unity: str = "Assets"
    shared_godot: str = ""
    scenes: list[dict] = field(default_factory=list)
    scripts: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | str) -> "Config":
        root = Path(root)
        data = {}
        f = root / "twinscene.json"
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
        shared = data.get("shared", {})
        return cls(
            root=root,
            unity=root / data.get("unity", "unity"),
            godot=root / data.get("godot", "godot"),
            shared_unity=shared.get("unity", "Assets").strip("/"),
            shared_godot=shared.get("godot", "").strip("/"),
            scenes=data.get("scenes", []),
            scripts=data.get("scripts", {}),
        )


class UnityProject:
    """A Unity project folder: maps asset guids to paths and back."""

    def __init__(self, path: Path, shared: str = "Assets/Shared"):
        self.path = Path(path)
        self.shared = shared
        self._by_guid: dict[str, str] | None = None

    def _scan(self) -> dict[str, str]:
        if self._by_guid is None:
            out = {}
            assets = self.path / "Assets"
            for meta in assets.rglob("*.meta"):
                try:
                    m = _GUID.search(meta.read_text(encoding="utf-8", errors="replace")[:400])
                except OSError:
                    continue
                if m:
                    out[m.group(1)] = meta.relative_to(self.path).as_posix()[:-5]
            self._by_guid = out
        return self._by_guid

    def register(self, asset_path: str, guid: str) -> None:
        """Record an asset TwinScene just wrote (its .meta is new, so the cached table doesn't have it)."""
        self._scan()[guid] = asset_path

    def path_of(self, guid: str) -> str | None:
        return self._scan().get(guid)

    def guid_of(self, asset_path: str) -> str | None:
        for g, p in self._scan().items():
            if p == asset_path:
                return g
        return None

    def ref(self, asset_path: str) -> str:
        """Unity asset path -> neutral asset reference."""
        prefix = self.shared + "/"
        if asset_path.startswith(prefix):
            return "shared:" + asset_path[len(prefix):]
        return "unity:" + asset_path

    def asset_path(self, ref: str) -> str | None:
        """Neutral asset reference -> Unity asset path (None if Unity can't have it)."""
        if ref.startswith("shared:"):
            return f"{self.shared}/{ref[7:]}"
        if ref.startswith("unity:"):
            return ref[6:]
        return None


class GodotProject:
    def __init__(self, path: Path, shared: str = ""):
        self.path = Path(path)
        self.shared = shared
        self._extracted: dict | None = None

    def forget_extracted(self) -> None:
        self._extracted = None

    def extracted(self) -> dict:
        """Mesh resources saved out of model files: res path -> (model res path, mesh name)."""
        if self._extracted is None:
            from .models import extracted_meshes
            self._extracted = extracted_meshes(self.path)
        return self._extracted

    def ref(self, res_path: str) -> str:
        prefix = f"res://{self.shared}/" if self.shared else "res://"
        if res_path.startswith(prefix) and not res_path.startswith(("res://.godot/", "res://addons/")):
            return "shared:" + res_path[len(prefix):]
        return "godot:" + res_path

    def res_path(self, ref: str) -> str | None:
        if ref.startswith("shared:"):
            return f"res://{self.shared}/{ref[7:]}" if self.shared else f"res://{ref[7:]}"
        if ref.startswith("godot:"):
            return ref[6:]
        return None
