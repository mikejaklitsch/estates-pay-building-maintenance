#!/usr/bin/env python3
"""
Deploy mod files to a clean upload directory for Steam Workshop.
Copies only game-relevant directories, excluding dev files like tools/, docs/, .git/.
"""

import json
import shutil
import sys
from pathlib import Path

MOD_DIRS = [".metadata", "in_game", "loading_screen", "main_menu"]

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

    print(f"\nDeploy complete: {dst}")


if __name__ == "__main__":
    deploy()
