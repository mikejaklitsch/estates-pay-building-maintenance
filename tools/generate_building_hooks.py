#!/usr/bin/env python3
"""
Generate Paradox script files for Estates Pay Building Maintenance mod.

Parses base game building_types/ and production_methods/ to produce:
  - epbm_generated_pm_effects.txt     (per-PM add/remove scripted effects)
  - epbm_generated_inject.txt         (INJECT blocks for buildings without on_built)
  - epbm_generated_replace.txt        (REPLACE blocks for buildings with on_built)
  - epbm_generated_init_effects.txt   (init dispatch + per-PM init effects)
"""

import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

# Paths
GAME_DIR = Path(r"/mnt/c/SteamLibrary/steamapps/common/Europa Universalis V/game/in_game")
BUILDING_DIR = GAME_DIR / "common" / "building_types"
PM_FILE = GAME_DIR / "common" / "production_methods" / "unsorted_building_inputs.txt"
MOD_DIR = Path(r"/mnt/c/Users/Mjaklitsch/Documents/Paradox Interactive/Europa Universalis V/mod/Estates Pay Building Maintenance")
OUT_EFFECTS = MOD_DIR / "in_game" / "common" / "scripted_effects"
OUT_BUILDINGS = MOD_DIR / "in_game" / "common" / "building_types"

# Keys in PM definitions that are NOT goods
PM_META_KEYS = {"category", "no_upkeep", "potential", "produced", "output"}

# ─────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────

def strip_bom(text):
    return text.lstrip("\ufeff")


def strip_comments(text):
    """Remove # comments (but not inside quotes)."""
    lines = []
    for line in text.split("\n"):
        in_quote = False
        result = []
        for i, ch in enumerate(line):
            if ch == '"':
                in_quote = not in_quote
            elif ch == '#' and not in_quote:
                break
            result.append(ch)
        lines.append("".join(result))
    return "\n".join(lines)


def tokenize(text):
    """Simple tokenizer for Paradox script: yields tokens (strings, braces, =, values)."""
    text = strip_bom(text)
    text = strip_comments(text)
    i = 0
    n = len(text)
    while i < n:
        # Skip whitespace
        if text[i] in " \t\r\n":
            i += 1
            continue
        # Quoted string
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == '\\':
                    j += 1
                j += 1
            yield text[i+1:j]
            i = j + 1
            continue
        # Braces and equals
        if text[i] in '{}=':
            yield text[i]
            i += 1
            continue
        # Unquoted token
        j = i
        while j < n and text[j] not in " \t\r\n{}=\"":
            j += 1
        yield text[i:j]
        i = j


def parse_block(tokens, idx):
    """
    Parse a { ... } block starting at tokens[idx] which should be '{'.
    Returns (dict_or_list, next_idx).
    For simplicity, returns an OrderedDict of key=value pairs.
    Nested blocks become nested dicts. Duplicate keys get list values.
    """
    assert tokens[idx] == '{', f"Expected '{{' at index {idx}, got '{tokens[idx]}'"
    idx += 1
    result = OrderedDict()
    while idx < len(tokens) and tokens[idx] != '}':
        key = tokens[idx]
        idx += 1
        if idx < len(tokens) and tokens[idx] == '=':
            idx += 1  # skip =
            if idx < len(tokens) and tokens[idx] == '{':
                val, idx = parse_block(tokens, idx)
            else:
                val = tokens[idx]
                idx += 1
        else:
            # Bare value (no =), common in lists like possible_production_methods
            val = True
            # Don't advance idx - key is already consumed
            if key in result:
                if isinstance(result[key], list):
                    result[key].append(val)
                else:
                    result[key] = [result[key], val]
                continue
            result[key] = val
            continue
        # Store with duplicate handling
        if key in result:
            if isinstance(result[key], list):
                result[key].append(val)
            else:
                result[key] = [result[key], val]
        else:
            result[key] = val
    if idx < len(tokens):
        idx += 1  # skip '}'
    return result, idx


def parse_file(filepath):
    """Parse a Paradox script file into a dict of top-level definitions."""
    text = filepath.read_text(encoding="utf-8-sig")
    toks = list(tokenize(text))
    result = OrderedDict()
    idx = 0
    while idx < len(toks):
        key = toks[idx]
        idx += 1
        if idx < len(toks) and toks[idx] == '=':
            idx += 1
            if idx < len(toks) and toks[idx] == '{':
                val, idx = parse_block(toks, idx)
            else:
                val = toks[idx]
                idx += 1
        else:
            val = True
        if key in result:
            if isinstance(result[key], list):
                result[key].append(val)
            else:
                result[key] = [result[key], val]
        else:
            result[key] = val
    return result


# ─────────────────────────────────────────────
# Parse production methods
# ─────────────────────────────────────────────

def parse_production_methods():
    """
    Parse unsorted_building_inputs.txt.
    Returns dict: pm_name -> { 'goods': OrderedDict(good->amount), 'no_upkeep': bool, 'has_output': bool, 'has_potential': bool }
    """
    data = parse_file(PM_FILE)
    pms = {}
    for name, block in data.items():
        if not isinstance(block, dict):
            continue
        pm = {
            'goods': OrderedDict(),
            'no_upkeep': 'no_upkeep' in block and block['no_upkeep'] == 'yes',
            'has_output': 'produced' in block or 'output' in block,
            'has_potential': 'potential' in block,
        }
        for k, v in block.items():
            if k not in PM_META_KEYS and isinstance(v, str):
                try:
                    pm['goods'][k] = float(v)
                except ValueError:
                    pass
        pms[name] = pm
    return pms


# ─────────────────────────────────────────────
# Parse building types
# ─────────────────────────────────────────────

def parse_all_buildings():
    """
    Parse all building files.
    Returns dict: building_name -> {
        'file': Path,
        'estate': str or None,
        'is_foreign': bool,
        'has_on_built': bool,
        'has_on_destroyed': bool,
        'possible_pms': [str],     (from possible_production_methods)
        'unique_pms': {pm_name: {goods}},  (from unique_production_methods)
        'raw': dict,  (full parsed block for REPLACE)
    }
    """
    buildings = OrderedDict()
    skip_files = {"readme.txt", "00_unique_buildings_to_make_obsolete.txt"}

    for f in sorted(BUILDING_DIR.iterdir()):
        if f.name in skip_files or not f.name.endswith(".txt"):
            continue
        data = parse_file(f)
        for bname, block in data.items():
            if not isinstance(block, dict):
                continue
            b = {
                'file': f,
                'estate': block.get('estate'),
                'is_foreign': block.get('is_foreign') == 'yes',
                'has_on_built': 'on_built' in block,
                'has_on_destroyed': 'on_destroyed' in block,
                'possible_pms': [],
                'unique_pms': OrderedDict(),
                'raw': block,
            }
            # Parse possible_production_methods (references to external PMs)
            ppm = block.get('possible_production_methods')
            if isinstance(ppm, dict):
                b['possible_pms'] = [k for k in ppm.keys()]
            elif isinstance(ppm, list):
                b['possible_pms'] = ppm

            # Parse unique_production_methods (inline PMs)
            upm = block.get('unique_production_methods')
            if isinstance(upm, dict):
                for pm_name, pm_block in upm.items():
                    if isinstance(pm_block, dict):
                        goods = OrderedDict()
                        for k, v in pm_block.items():
                            if k not in PM_META_KEYS and isinstance(v, str):
                                try:
                                    goods[k] = float(v)
                                except ValueError:
                                    pass
                        has_output = 'produced' in pm_block or 'output' in pm_block
                        no_upkeep = pm_block.get('no_upkeep') == 'yes'
                        is_maintenance = pm_block.get('category') == 'building_maintenance'
                        b['unique_pms'][pm_name] = {
                            'goods': goods,
                            'has_output': has_output,
                            'no_upkeep': no_upkeep,
                            'is_maintenance': is_maintenance,
                        }

            buildings[bname] = b
    return buildings


# ─────────────────────────────────────────────
# Classify qualifying buildings and PMs
# ─────────────────────────────────────────────

def classify(buildings, pms):
    """
    Determine which buildings qualify for maintenance tracking.
    A building qualifies if:
      - No estate = X assignment
      - Not is_foreign = yes
      - Has at least one qualifying PM (category=building_maintenance, has goods, no no_upkeep, no output)

    Returns:
      qualifying: list of (building_name, pm_name, pm_source) tuples
        pm_source = 'external' | 'inline'
      all_pm_goods: dict pm_name -> OrderedDict(good->amount)
    """
    qualifying = []
    all_pm_goods = OrderedDict()

    for bname, b in buildings.items():
        if b['estate'] is not None:
            continue
        if b['is_foreign']:
            continue

        # Check external PMs
        best_pm = None
        for pm_name in b['possible_pms']:
            if pm_name not in pms:
                continue
            pm = pms[pm_name]
            if pm['no_upkeep'] or pm['has_output']:
                continue
            if not pm['goods']:
                continue
            if best_pm is None:
                best_pm = (pm_name, 'external')
                all_pm_goods[pm_name] = pm['goods']

        # Check inline PMs
        for pm_name, pm_data in b['unique_pms'].items():
            if not pm_data.get('is_maintenance', False):
                continue
            if pm_data.get('no_upkeep', False) or pm_data.get('has_output', False):
                continue
            if not pm_data['goods']:
                continue
            if best_pm is None:
                best_pm = (pm_name, 'inline')
                all_pm_goods[pm_name] = pm_data['goods']

        if best_pm:
            qualifying.append((bname, best_pm[0], best_pm[1]))

    return qualifying, all_pm_goods


# ─────────────────────────────────────────────
# Read raw building text for REPLACE blocks
# ─────────────────────────────────────────────

def read_raw_building_text(filepath, building_name):
    """
    Extract the raw text of a building definition from a file.
    Returns the text between building_name = { ... } including braces.
    """
    text = filepath.read_text(encoding="utf-8-sig")
    text = strip_bom(text)

    # Find the building definition start
    pattern = re.compile(r'^(' + re.escape(building_name) + r')\s*=\s*\{', re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None

    start = match.start()
    # Find matching closing brace
    brace_start = text.index('{', match.start())
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
        i += 1
    return None


def inject_on_built_hook(raw_text, building_name, pm_name):
    """
    For REPLACE buildings: insert our hook into the existing on_built block,
    and add on_destroyed if it doesn't exist.
    Also renames inline unique_production_methods PM names to avoid duplicates
    (prefix with epbm_) since REPLACE redefines the building but the engine
    still sees the original PM name.
    Returns modified building text.
    """
    add_effect = f"epbm_add_{pm_name}"
    remove_effect = f"epbm_remove_{pm_name}"

    # Rename inline PM names to avoid duplicate PM name errors
    # Find unique_production_methods block and prefix PM names with epbm_
    upm_pattern = re.compile(r'unique_production_methods\s*=\s*\{')
    upm_match = upm_pattern.search(raw_text)
    if upm_match:
        # Find all PM name definitions inside the block (name = {)
        brace_start = upm_match.end() - 1
        depth = 0
        i = brace_start
        while i < len(raw_text):
            if raw_text[i] == '{':
                depth += 1
            elif raw_text[i] == '}':
                depth -= 1
                if depth == 0:
                    upm_end = i + 1
                    break
            i += 1
        else:
            upm_end = len(raw_text)
        # Extract the UPM block and rename PM names within it
        upm_block = raw_text[upm_match.start():upm_end]
        # Find PM definitions: word followed by = {
        pm_def_pattern = re.compile(r'(\t\t)(\w+)(\s*=\s*\{)')
        def rename_pm(m):
            name = m.group(2)
            if name in ('unique_production_methods', 'category', 'potential', 'no_upkeep'):
                return m.group(0)
            return f"{m.group(1)}epbm_{name}{m.group(3)}"
        new_upm_block = pm_def_pattern.sub(rename_pm, upm_block)
        raw_text = raw_text[:upm_match.start()] + new_upm_block + raw_text[upm_end:]

    # Find on_built block and inject before its closing brace
    on_built_pattern = re.compile(r'(on_built\s*=\s*\{)')
    match = on_built_pattern.search(raw_text)
    if match:
        # Find the matching closing brace for on_built
        brace_start = match.end() - 1
        depth = 0
        i = brace_start
        while i < len(raw_text):
            if raw_text[i] == '{':
                depth += 1
            elif raw_text[i] == '}':
                depth -= 1
                if depth == 0:
                    # Insert our hook before the closing brace
                    inject = f"\n\t\t# EPBM: Track maintenance goods\n\t\tlocation = {{ {add_effect} = yes }}"
                    raw_text = raw_text[:i] + inject + "\n\t" + raw_text[i:]
                    break
            i += 1

    # Add on_destroyed if not present
    if 'on_destroyed' not in raw_text:
        # Find the final closing brace of the building
        last_brace = raw_text.rindex('}')
        on_destroyed = f"\n\ton_destroyed = {{\n\t\t# EPBM: Remove maintenance goods\n\t\tlocation = {{ {remove_effect} = yes }}\n\t}}"
        raw_text = raw_text[:last_brace] + on_destroyed + "\n" + raw_text[last_brace:]
    else:
        # Inject into existing on_destroyed
        on_destroyed_pattern = re.compile(r'(on_destroyed\s*=\s*\{)')
        match = on_destroyed_pattern.search(raw_text)
        if match:
            brace_start = match.end() - 1
            depth = 0
            i = brace_start
            while i < len(raw_text):
                if raw_text[i] == '{':
                    depth += 1
                elif raw_text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        inject = f"\n\t\t# EPBM: Remove maintenance goods\n\t\tlocation = {{ {remove_effect} = yes }}"
                        raw_text = raw_text[:i] + inject + "\n\t" + raw_text[i:]
                        break
                i += 1

    return raw_text


# ─────────────────────────────────────────────
# Code generation
# ─────────────────────────────────────────────

def _emit_add_good(lines, good, amount, indent="\t"):
    """Emit inlined add-good logic for a single hardcoded good."""
    i = indent
    lines.append(f"{i}if = {{")
    lines.append(f"{i}\tlimit = {{ has_variable_map = epbm_maint is_key_in_variable_map = {{ name = epbm_maint target = goods:{good} }} }}")
    lines.append(f'{i}\tset_local_variable = {{ name = epbm_temp value = "variable_map(epbm_maint|goods:{good})" }}')
    lines.append(f"{i}\tchange_local_variable = {{ name = epbm_temp add = {amount} }}")
    lines.append(f"{i}\tadd_to_variable_map = {{ name = epbm_maint key = goods:{good} value = local_var:epbm_temp }}")
    lines.append(f"{i}}}")
    lines.append(f"{i}else = {{")
    lines.append(f"{i}\tadd_to_variable_map = {{ name = epbm_maint key = goods:{good} value = {amount} }}")
    lines.append(f"{i}}}")


def _emit_remove_good(lines, good, amount, indent="\t"):
    """Emit inlined remove-good logic for a single hardcoded good."""
    i = indent
    lines.append(f"{i}if = {{")
    lines.append(f"{i}\tlimit = {{ has_variable_map = epbm_maint is_key_in_variable_map = {{ name = epbm_maint target = goods:{good} }} }}")
    lines.append(f'{i}\tset_local_variable = {{ name = epbm_temp value = "variable_map(epbm_maint|goods:{good})" }}')
    lines.append(f"{i}\tchange_local_variable = {{ name = epbm_temp subtract = {amount} }}")
    lines.append(f"{i}\tif = {{ limit = {{ local_var:epbm_temp < 0 }} set_local_variable = {{ name = epbm_temp value = 0 }} }}")
    lines.append(f"{i}\tadd_to_variable_map = {{ name = epbm_maint key = goods:{good} value = local_var:epbm_temp }}")
    lines.append(f"{i}}}")
    lines.append(f"{i}else = {{")
    lines.append(f"{i}\tset_variable = {{ name = epbm_needs_recalc value = 1 }}")
    lines.append(f"{i}}}")


def generate_pm_effects(all_pm_goods, pm_to_buildings):
    """Generate epbm_generated_pm_effects.txt with inlined variable map logic."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# Per-PM scripted effects for add/remove maintenance goods",
        "# Uses hardcoded goods names to avoid $PARAM$ issues with variable_map()",
        "",
    ]

    for pm_name, goods in sorted(all_pm_goods.items()):
        buildings_using = pm_to_buildings.get(pm_name, [])
        goods_comment = ", ".join(f"{g} {a}" for g, a in goods.items())

        lines.append(f"# {pm_name}")
        if buildings_using:
            lines.append(f"# Used by: {', '.join(sorted(buildings_using))}")
        lines.append(f"# Goods: {goods_comment}")

        # Add effect
        lines.append(f"epbm_add_{pm_name} = {{")
        for good, amount in goods.items():
            _emit_add_good(lines, good, amount)
        lines.append("}")
        lines.append("")

        # Remove effect
        lines.append(f"epbm_remove_{pm_name} = {{")
        for good, amount in goods.items():
            _emit_remove_good(lines, good, amount)
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def generate_inject(qualifying, buildings):
    """Generate epbm_generated_inject.txt (INJECT blocks for buildings without on_built)."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# INJECT blocks for building on_built/on_destroyed hooks",
        "",
    ]

    for bname, pm_name, _ in sorted(qualifying, key=lambda x: x[0]):
        b = buildings[bname]
        if b['has_on_built'] or b['has_on_destroyed']:
            continue  # These go in REPLACE file

        add_effect = f"epbm_add_{pm_name}"
        remove_effect = f"epbm_remove_{pm_name}"

        lines.append(f"# {bname} uses {pm_name}")
        lines.append(f"INJECT:{bname} = {{")
        lines.append(f"\ton_built = {{ location = {{ {add_effect} = yes }} }}")
        lines.append(f"\ton_destroyed = {{ location = {{ {remove_effect} = yes }} }}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def generate_replace(qualifying, buildings, all_pm_goods):
    """Generate epbm_generated_replace.txt (REPLACE blocks for buildings with existing on_built)."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# REPLACE blocks for buildings with existing on_built/on_destroyed hooks",
        "",
    ]

    for bname, pm_name, _ in sorted(qualifying, key=lambda x: x[0]):
        b = buildings[bname]
        if not b['has_on_built'] and not b['has_on_destroyed']:
            continue  # These go in INJECT file

        raw = read_raw_building_text(b['file'], bname)
        if raw is None:
            lines.append(f"# WARNING: Could not extract raw text for {bname}")
            lines.append("")
            continue

        modified = inject_on_built_hook(raw, bname, pm_name)
        lines.append(f"# {bname} uses {pm_name} (REPLACE due to existing on_built)")
        lines.append(f"REPLACE:{modified}")
        lines.append("")

    return "\n".join(lines)


def _emit_init_good(lines, good, amount, indent="\t"):
    """Emit inlined init-good logic: multiply amount by building level, add to map."""
    i = indent
    # prev = the building being iterated
    # Calculate amount * building_level, accumulate with existing value
    # (multiple buildings at same location can share goods)
    lines.append(f"{i}set_local_variable = {{")
    lines.append(f"{i}\tname = epbm_temp")
    lines.append(f"{i}\tvalue = {{ value = {amount} multiply = prev.building_level }}")
    lines.append(f"{i}}}")
    lines.append(f"{i}if = {{")
    lines.append(f"{i}\tlimit = {{ has_variable_map = epbm_maint is_key_in_variable_map = {{ name = epbm_maint target = goods:{good} }} }}")
    lines.append(f"{i}\tchange_local_variable = {{ name = epbm_temp add = \"variable_map(epbm_maint|goods:{good})\" }}")
    lines.append(f"{i}}}")
    lines.append(f"{i}add_to_variable_map = {{ name = epbm_maint key = goods:{good} value = local_var:epbm_temp }}")


def generate_init_effects(qualifying, all_pm_goods, buildings):
    """Generate epbm_generated_init_effects.txt with inlined variable map logic."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# Initialization effects for game start",
        "# Uses hardcoded goods names to avoid $PARAM$ issues with variable_map()",
        "",
    ]

    # Per-PM init effects (multiply goods by building level)
    generated_pms = set()
    for bname, pm_name, _ in qualifying:
        if pm_name in generated_pms:
            continue
        generated_pms.add(pm_name)

        goods = all_pm_goods[pm_name]
        lines.append(f"# Init effect for {pm_name}")
        lines.append(f"epbm_init_{pm_name} = {{")
        for good, amount in goods.items():
            _emit_init_good(lines, good, amount)
        lines.append("}")
        lines.append("")

    # Dispatch effect: routes building_type -> correct init
    lines.append("# Dispatch effect: called per building during init scan")
    lines.append("# Scope: building, location accessible via 'location'")
    lines.append("epbm_init_building_maintenance = {")

    first = True
    for bname, pm_name, _ in sorted(qualifying, key=lambda x: x[0]):
        keyword = "if" if first else "else_if"
        first = False
        lines.append(f"\t{keyword} = {{")
        lines.append(f"\t\tlimit = {{ building_type = building_type:{bname} }}")
        lines.append(f"\t\tlocation = {{ epbm_init_{pm_name} = yes }}")
        lines.append("\t}")

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print("Parsing production methods...")
    pms = parse_production_methods()
    print(f"  Found {len(pms)} production methods")
    qualifying_pms = {k: v for k, v in pms.items()
                      if not v['no_upkeep'] and not v['has_output'] and v['goods']}
    print(f"  Qualifying PMs (has goods, no no_upkeep, no output): {len(qualifying_pms)}")

    print("\nParsing building types...")
    buildings = parse_all_buildings()
    print(f"  Found {len(buildings)} buildings")

    print("\nClassifying qualifying buildings...")
    qualifying, all_pm_goods = classify(buildings, pms)
    print(f"  Qualifying buildings: {len(qualifying)}")
    print(f"  Unique PM effect pairs needed: {len(all_pm_goods)}")

    # Build reverse map: pm_name -> list of building names
    pm_to_buildings = {}
    for bname, pm_name, _ in qualifying:
        pm_to_buildings.setdefault(pm_name, []).append(bname)

    # Count INJECT vs REPLACE
    inject_count = sum(1 for b, _, _ in qualifying
                       if not buildings[b]['has_on_built'] and not buildings[b]['has_on_destroyed'])
    replace_count = sum(1 for b, _, _ in qualifying
                        if buildings[b]['has_on_built'] or buildings[b]['has_on_destroyed'])
    print(f"  INJECT buildings: {inject_count}")
    print(f"  REPLACE buildings: {replace_count}")

    # List REPLACE buildings
    if replace_count > 0:
        replace_buildings = [b for b, _, _ in qualifying
                            if buildings[b]['has_on_built'] or buildings[b]['has_on_destroyed']]
        print(f"  REPLACE candidates: {', '.join(replace_buildings)}")

    # Generate files
    print("\nGenerating files...")

    pm_effects = generate_pm_effects(all_pm_goods, pm_to_buildings)
    out_path = OUT_EFFECTS / "epbm_generated_pm_effects.txt"
    out_path.write_text(pm_effects, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    inject_code = generate_inject(qualifying, buildings)
    out_path = OUT_BUILDINGS / "epbm_generated_inject.txt"
    out_path.write_text(inject_code, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    replace_code = generate_replace(qualifying, buildings, all_pm_goods)
    out_path = OUT_BUILDINGS / "epbm_generated_replace.txt"
    out_path.write_text(replace_code, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    init_effects = generate_init_effects(qualifying, all_pm_goods, buildings)
    out_path = OUT_EFFECTS / "epbm_generated_init_effects.txt"
    out_path.write_text(init_effects, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    # Summary
    print("\n=== Summary ===")
    print(f"Total qualifying buildings: {len(qualifying)}")
    print(f"PM effect pairs generated: {len(all_pm_goods)}")
    print(f"INJECT blocks: {inject_count}")
    print(f"REPLACE blocks: {replace_count}")

    # Show PM -> buildings mapping
    print("\n=== PM to Buildings Mapping ===")
    for pm_name in sorted(all_pm_goods.keys()):
        blist = pm_to_buildings.get(pm_name, [])
        goods = all_pm_goods[pm_name]
        goods_str = ", ".join(f"{g}={a}" for g, a in goods.items())
        print(f"  {pm_name} ({len(blist)} buildings): [{goods_str}]")
        for b in sorted(blist):
            src = "inline" if any(bn == b and s == 'inline' for bn, _, s in qualifying) else "external"
            print(f"    - {b} ({src})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
