#!/usr/bin/env python3
"""
Deploy mod files to a clean upload directory for Steam Workshop.
Copies only game-relevant directories, excluding dev files like tools/, docs/, .git/.
Patches the rebuild version constant from metadata.json into the deployed on_actions.
"""

import json
import re
import shutil
import sys
from pathlib import Path

MOD_DIRS = [".metadata", "in_game", "loading_screen", "main_menu"]
ON_ACTIONS_REL = Path("in_game/common/on_action/epbm_on_actions.txt")


def version_to_rebuild(version_str):
    """Convert 'n.m' or 'n.m.p' version string to rebuild version (major*100 + minor*10)."""
    parts = version_str.strip().split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major * 100 + minor * 10


def deploy():
    src = Path(__file__).resolve().parent.parent
    # Dev directory ends with " Development", upload gets the real name
    name = src.name.removesuffix(" Development")
    dst = src.parent / name

    if dst.exists():
        shutil.rmtree(dst)
        print(f"Cleaned: {dst}")

    for d in MOD_DIRS:
        src_dir = src / d
        if src_dir.exists():
            shutil.copytree(src_dir, dst / d)
            print(f"Copied: {d}/")

    # Strip (Dev) tag from deployed metadata name
    meta_file = dst / ".metadata" / "metadata.json"
    if meta_file.exists():
        raw = meta_file.read_text(encoding="utf-8-sig")
        meta = json.loads(raw)
        if "(Dev)" in meta.get("name", ""):
            meta["name"] = meta["name"].replace(" (Dev)", "")
            meta_file.write_text(
                json.dumps(meta, indent=4) + "\n", encoding="utf-8-sig"
            )
            print(f"Stripped (Dev) from metadata name")

        # Patch rebuild version into on_actions
        version_str = meta.get("version", "0.0")
        rebuild_ver = version_to_rebuild(version_str)
        on_actions_file = dst / ON_ACTIONS_REL
        if on_actions_file.exists():
            text = on_actions_file.read_text(encoding="utf-8-sig")
            text, count = re.subn(
                r"@epbm_rebuild_version\s*=\s*\d+",
                f"@epbm_rebuild_version = {rebuild_ver}",
                text,
            )
            if count > 0:
                on_actions_file.write_text(text, encoding="utf-8-sig")
                print(f"Patched @epbm_rebuild_version = {rebuild_ver} (from version {version_str})")
            else:
                print(f"WARNING: @epbm_rebuild_version not found in {ON_ACTIONS_REL}")

    print(f"\nDeploy complete: {dst}")


if __name__ == "__main__":
    deploy()
