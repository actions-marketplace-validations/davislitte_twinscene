"""Keeps the shared asset folders (by default Unity Assets/ <-> Godot assets/) holding the same media files.

Each side is compared with the file hashes from the last sync (sync/assets.json). A file added, changed or deleted
on one side is copied to (or deleted from) the other. Engine bookkeeping files (.meta, .import) stay engine-only.
Git stores identical files once, so the copy costs no repository space.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path

from .project import Config

# Only engine-neutral media is copied across. Engine formats (scenes, prefabs, materials, scripts...) are translated
# by TwinScene or stay engine-only; bookkeeping files (.meta, .import) are each engine's own.
MEDIA = (".fbx", ".obj", ".gltf", ".glb", ".bin", ".dae", ".blend", ".3ds",
         ".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr", ".hdr", ".bmp", ".tif", ".tiff", ".webp", ".ktx",
         ".wav", ".ogg", ".mp3", ".aiff", ".aif", ".flac",
         ".ttf", ".otf")
UNITY_META = "fileFormatVersion: 2\nguid: {guid}\n"


def _files(folder: Path) -> dict[str, str]:
    out = {}
    if not folder.is_dir():
        return out
    for p in folder.rglob("*"):
        rel = p.relative_to(folder).parts
        if rel and rel[0] in (".godot", "addons", "Library", "Temp", "Packages"):
            continue  # engine caches and plugins
        if p.is_file() and p.suffix.lower() in MEDIA and not p.name.startswith("."):
            out[p.relative_to(folder).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def mirror_shared(cfg: Config, prefer: str | None = None, dry_run: bool = False, rng: random.Random | None = None):
    from .sync import Conflict, Report  # (sync imports this module lazily)

    rng = rng or random.Random()
    report = Report("Shared assets")
    ufolder = cfg.unity / cfg.shared_unity
    gfolder = cfg.godot / cfg.shared_godot
    state_file = cfg.root / "sync" / "assets.json"
    base: dict[str, str] = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    U, G = _files(ufolder), _files(gfolder)
    result: dict[str, str] = {}

    for rel in sorted(set(U) | set(G) | set(base)):
        hu, hg, hb = U.get(rel), G.get(rel), base.get(rel)
        if hu == hg:
            if hu is not None:
                result[rel] = hu
            continue
        u_changed, g_changed = hu != hb, hg != hb
        if u_changed and g_changed and prefer is None:
            report.conflicts.append(Conflict(rel, "file", _desc(hu), _desc(hg)))
            if hb is not None:
                result[rel] = hb
            continue
        from_unity = (u_changed and not g_changed) or (u_changed and prefer == "unity") or \
                     (not u_changed and not g_changed)
        if from_unity:
            src, dst, h, side = ufolder / rel, gfolder / rel, hu, report.to_godot
        else:
            src, dst, h, side = gfolder / rel, ufolder / rel, hg, report.to_unity
        if h is None:
            side.append(f"deleted {rel}")
            if not dry_run and dst.exists():
                dst.unlink()
                if dst.parent == ufolder or ufolder in dst.parents:
                    meta = dst.with_name(dst.name + ".meta")
                    if meta.exists():
                        meta.unlink()
            continue
        side.append(f"{'updated' if dst.exists() else 'added'} {rel}")
        result[rel] = h
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            report.files.append(str(dst))
            if not from_unity:
                _ensure_meta(dst, ufolder, rng)

    if not dry_run and (result != base or not state_file.exists()):
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report


def _ensure_meta(path: Path, ufolder: Path, rng: random.Random) -> None:
    """Unity scenes refer to assets by the guid in their .meta, so a copied-in file gets one straight away (and its
    folders too). Unity fills in the import settings when it opens the project."""
    for p in [path] + [d for d in path.parents if ufolder in d.parents]:
        meta = p.with_name(p.name + ".meta")
        if not meta.exists():
            text = UNITY_META.format(guid="%032x" % rng.getrandbits(128))
            if p.is_dir():
                text += "folderAsset: yes\n"
            meta.write_text(text, encoding="utf-8", newline="\n")


def _desc(h: str | None) -> str:
    return "deleted" if h is None else f"changed ({h[:8]})"
