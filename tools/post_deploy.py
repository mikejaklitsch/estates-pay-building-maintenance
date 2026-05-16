#!/usr/bin/env python3
"""Post-deploy hook: patch @epbm_rebuild_version from metadata version."""

import re
from pathlib import Path

ON_ACTIONS_REL = Path("in_game/common/on_action/epbm_on_actions.txt")


def version_to_rebuild(version_str):
    parts = version_str.strip().split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major * 100 + minor * 10


def post_deploy(src, dst, meta):
    if not meta:
        return
    version_str = meta.get("version", "0.0")
    rebuild_ver = version_to_rebuild(version_str)
    on_actions_file = dst / ON_ACTIONS_REL
    if not on_actions_file.exists():
        return
    text = on_actions_file.read_text(encoding="utf-8-sig")
    text, count = re.subn(
        r"@epbm_rebuild_version\s*=\s*\d+",
        f"@epbm_rebuild_version = {rebuild_ver}",
        text,
    )
    if count > 0:
        on_actions_file.write_text(text, encoding="utf-8-sig")
        print(f"  Patched @epbm_rebuild_version = {rebuild_ver} (from {version_str})")
    else:
        print(f"  WARNING: @epbm_rebuild_version not found in {ON_ACTIONS_REL}")
