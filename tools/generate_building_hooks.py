#!/usr/bin/env python3
"""
Generate Paradox script files for Estates Pay Building Maintenance mod.

Parses base game building_types/ and production_methods/ to produce:
  - epbm_generated_inject.txt         (INJECT blocks: list management on build/destroy)
  - epbm_generated_replace.txt        (REPLACE blocks: same for buildings with existing on_built)
  - epbm_generated_init_effects.txt   (IO creation + parent profile map + init dispatch)
  - epbm_generated_ios.txt            (hidden international organizations as variable map hosts)
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
OUT_IOS = MOD_DIR / "in_game" / "common" / "international_organizations"
OUT_BIASES = MOD_DIR / "in_game" / "common" / "biases"
OUT_LOC = MOD_DIR / "main_menu" / "localization" / "english"

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
            # Extract garrison size from modifier block
            garrison_size = 0.0
            modifier_block = block.get('modifier', {})
            if isinstance(modifier_block, dict):
                gs = modifier_block.get('local_garrison_size')
                if gs:
                    try:
                        garrison_size = float(gs)
                    except (ValueError, TypeError):
                        pass

            # Extract fort_level from raw_modifier block (forts = buildings with fort_level)
            fort_level = 0
            raw_modifier_block = block.get('raw_modifier', {})
            if isinstance(raw_modifier_block, dict):
                fl = raw_modifier_block.get('fort_level')
                if fl:
                    try:
                        fort_level = int(fl)
                    except (ValueError, TypeError):
                        pass

            b = {
                'file': f,
                'estate': block.get('estate'),
                'is_foreign': block.get('is_foreign') == 'yes',
                'is_fort': fort_level > 0,
                'fort_level': fort_level,
                'garrison_size': garrison_size,
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
      qualifying: list of (building_name, pm_name, pm_source, is_fort) tuples
        pm_source = 'external' | 'inline'
        is_fort = True if building has fort_level > 0 in raw_modifier
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
            qualifying.append((bname, best_pm[0], best_pm[1], b['is_fort']))

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


def inject_on_built_hook(raw_text, building_name, list_name="epbm_building_types"):
    """
    For REPLACE buildings: insert our list-management hook into existing on_built,
    and add on_destroyed if it doesn't exist.
    Also renames inline unique_production_methods PM names to avoid duplicates.
    Returns modified building text.
    """
    add_code = (
        f"\n\t\t# EPBM: Track building for maintenance"
        f"\n\t\tlocation = {{ add_to_variable_list = {{ name = {list_name} target = prev }} }}"
        f"\n\t\tepbm_on_building_built = yes"
    )
    remove_code = (
        f"\n\t\t# EPBM: Untrack building"
        f"\n\t\tlocation = {{ remove_list_variable = {{ name = {list_name} target = prev }} }}"
        f"\n\t\tepbm_on_building_destroyed = yes"
    )

    # Rename inline PM names to avoid duplicate PM name errors
    upm_pattern = re.compile(r'unique_production_methods\s*=\s*\{')
    upm_match = upm_pattern.search(raw_text)
    if upm_match:
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
        upm_block = raw_text[upm_match.start():upm_end]
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
        brace_start = match.end() - 1
        depth = 0
        i = brace_start
        while i < len(raw_text):
            if raw_text[i] == '{':
                depth += 1
            elif raw_text[i] == '}':
                depth -= 1
                if depth == 0:
                    raw_text = raw_text[:i] + add_code + "\n\t" + raw_text[i:]
                    break
            i += 1

    # Add on_destroyed if not present
    if 'on_destroyed' not in raw_text:
        last_brace = raw_text.rindex('}')
        on_destroyed = f"\n\ton_destroyed = {{{remove_code}\n\t}}"
        raw_text = raw_text[:last_brace] + on_destroyed + "\n" + raw_text[last_brace:]
    else:
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
                        raw_text = raw_text[:i] + remove_code + "\n\t" + raw_text[i:]
                        break
                i += 1

    return raw_text


# ─────────────────────────────────────────────
# Code generation
# ─────────────────────────────────────────────

def generate_inject(qualifying, buildings):
    """Generate epbm_generated_inject.txt (INJECT blocks for buildings without on_built)."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# INJECT blocks: manage location tracking list on build/destroy",
        "",
    ]

    for bname, pm_name, _, is_fort in sorted(qualifying, key=lambda x: x[0]):
        b = buildings[bname]
        if b['has_on_built'] or b['has_on_destroyed']:
            continue  # These go in REPLACE file

        list_name = "epbm_fort_types" if is_fort else "epbm_building_types"

        lines.append(f"# {bname} uses {pm_name}{' (fort)' if is_fort else ''}")
        lines.append(f"INJECT:{bname} = {{")
        lines.append(f"\ton_built = {{")
        lines.append(f"\t\tlocation = {{ add_to_variable_list = {{ name = {list_name} target = prev }} }}")
        lines.append(f"\t\tepbm_on_building_built = yes")
        lines.append(f"\t}}")
        lines.append(f"\ton_destroyed = {{")
        lines.append(f"\t\tlocation = {{ remove_list_variable = {{ name = {list_name} target = prev }} }}")
        lines.append(f"\t\tepbm_on_building_destroyed = yes")
        lines.append(f"\t}}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def generate_replace(qualifying, buildings):
    """Generate epbm_generated_replace.txt (REPLACE blocks for buildings with existing on_built)."""
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# REPLACE blocks for buildings with existing on_built/on_destroyed hooks",
        "",
    ]

    for bname, pm_name, _, is_fort in sorted(qualifying, key=lambda x: x[0]):
        b = buildings[bname]
        if not b['has_on_built'] and not b['has_on_destroyed']:
            continue  # These go in INJECT file

        raw = read_raw_building_text(b['file'], bname)
        if raw is None:
            lines.append(f"# WARNING: Could not extract raw text for {bname}")
            lines.append("")
            continue

        list_name = "epbm_fort_types" if is_fort else "epbm_building_types"
        modified = inject_on_built_hook(raw, bname, list_name)
        lines.append(f"# {bname} uses {pm_name} (REPLACE due to existing on_built){' (fort)' if is_fort else ''}")
        lines.append(f"REPLACE:{modified}")
        lines.append("")

    return "\n".join(lines)


def generate_io_definitions(all_pm_goods):
    """
    Generate epbm_generated_ios.txt:
    Hidden international organizations, one per unique PM profile.
    Each IO hosts an epbm_goods variable map (goods_ref -> per_level_amount),
    populated at creation time via the create_international_organization block.
    """
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# Hidden international organizations used as variable map containers.",
        "# Each IO hosts an epbm_goods variable map for one PM profile.",
        "",
    ]

    for pm_name in sorted(all_pm_goods.keys()):
        io_name = f"epbm_pm_{pm_name}"
        lines.append(f"{io_name} = {{")
        lines.append("\tunique = yes")
        lines.append("\thas_target = no")
        lines.append("\tshow_on_diplomatic_map = no")
        lines.append("\tcreate_visible_trigger = { always = no }")
        lines.append("\tauto_disband_trigger = { always = no }")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def generate_io_biases(all_pm_goods):
    """
    Generate epbm_generated_biases.txt:
    Opinion biases for each IO (value = 0 since these are hidden data containers).
    Eliminates 'needs an opinion of other members' warnings.
    """
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# Opinion biases for hidden PM IOs (required by engine, value 0).",
        "",
    ]

    for pm_name in sorted(all_pm_goods.keys()):
        lines.append(f"io_opinion_epbm_pm_{pm_name} = {{")
        lines.append("\tvalue = 0")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def generate_io_localization(all_pm_goods):
    """
    Generate epbm_ios_l_english.yml:
    Localization entries for each IO to suppress auto-generated placeholder warnings.
    """
    lines = ["\ufeffl_english:"]

    for pm_name in sorted(all_pm_goods.keys()):
        io = f"epbm_pm_{pm_name}"
        lines.append(f' {io}: ""')
        lines.append(f' {io}_desc: ""')
        lines.append(f' diplomatic_status_{io}_name: ""')
        lines.append(f' diplomatic_status_{io}_tooltip: ""')
        lines.append(f' {io}_list_who_tt: ""')
        lines.append(f' io_opinion_{io}: ""')

    return "\n".join(lines)


def generate_init_effects(qualifying, all_pm_goods, buildings):
    """
    Generate epbm_generated_init_effects.txt:
    1. epbm_stamp_globals: creates PM IOs + stamps parent building_type->IO map
    2. epbm_init_building: dispatch that adds building_type to location list (game start)
    """
    lines = [
        "# Auto-generated by tools/generate_building_hooks.py",
        "# IO creation + parent profile lookup + init dispatch",
        "",
    ]

    # ── Part 1: Create IOs and stamp parent profile map ──
    lines.append("# Create PM international organizations and populate their goods maps,")
    lines.append("# then stamp global parent map epbm_profiles (building_type -> IO scope).")
    lines.append("# Called once at game start.")
    lines.append("epbm_stamp_globals = {")
    lines.append("\t# Create all PM IOs and populate goods maps inside creation scope")
    lines.append("\trandom_country = {")
    lines.append("\t\tlimit = { is_real_country = yes }")

    for pm_name in sorted(all_pm_goods.keys()):
        goods = all_pm_goods[pm_name]
        io_type = f"international_organization_type:epbm_pm_{pm_name}"
        goods_str = ", ".join(f"{g} {a}" for g, a in goods.items())
        lines.append(f"\t\t# PM: {pm_name} ({goods_str})")
        lines.append(f"\t\tcreate_international_organization = {{")
        lines.append(f"\t\t\ttype = {io_type}")
        for good, amount in goods.items():
            lines.append(f"\t\t\tadd_to_variable_map = {{ name = epbm_goods key = goods:{good} value = {amount} }}")
        lines.append("\t\t}")

    lines.append("\t}")
    lines.append("")
    lines.append("\t# Parent map: building_type -> IO scope")

    for bname, pm_name, _, is_fort in sorted(qualifying, key=lambda x: x[0]):
        bt_ref = f"building_type:{bname}"
        io_ref = f"international_organization:epbm_pm_{pm_name}"
        lines.append(f"\tadd_to_global_variable_map = {{ name = epbm_profiles key = {bt_ref} value = {io_ref} }}")

    lines.append("")
    lines.append("\t# Global list of all PM IOs (for monthly cache clearing)")
    for pm_name in sorted(all_pm_goods.keys()):
        io_ref = f"international_organization:epbm_pm_{pm_name}"
        lines.append(f"\tadd_to_global_variable_list = {{ name = epbm_all_ios target = {io_ref} }}")

    lines.append("}")
    lines.append("")

    # ── Part 2: Init dispatch for pre-existing buildings ──
    lines.append("# Init dispatch: add building instance to location list for pre-existing buildings")
    lines.append("# Scope: building (called via every_buildings_in_location)")
    lines.append("# Fort buildings (fort_level > 0) go to epbm_fort_types, others to epbm_building_types")
    lines.append("epbm_init_building = {")
    lines.append("\tsave_temporary_scope_as = epbm_bldg")

    first = True
    for bname, pm_name, _, is_fort in sorted(qualifying, key=lambda x: x[0]):
        keyword = "if" if first else "else_if"
        first = False
        bt_ref = f"building_type:{bname}"
        list_name = "epbm_fort_types" if is_fort else "epbm_building_types"
        lines.append(f"\t{keyword} = {{")
        lines.append(f"\t\tlimit = {{ building_type = {bt_ref} }}")
        lines.append(f"\t\tlocation = {{ add_to_variable_list = {{ name = {list_name} target = scope:epbm_bldg }} }}")
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
    print(f"  Unique PM goods profiles: {len(all_pm_goods)}")

    # Build reverse map: pm_name -> list of building names
    pm_to_buildings = {}
    for bname, pm_name, _, _ in qualifying:
        pm_to_buildings.setdefault(pm_name, []).append(bname)

    # Count INJECT vs REPLACE
    inject_count = sum(1 for b, _, _, _ in qualifying
                       if not buildings[b]['has_on_built'] and not buildings[b]['has_on_destroyed'])
    replace_count = sum(1 for b, _, _, _ in qualifying
                        if buildings[b]['has_on_built'] or buildings[b]['has_on_destroyed'])
    fort_count = sum(1 for _, _, _, is_fort in qualifying if is_fort)
    print(f"  INJECT buildings: {inject_count}")
    print(f"  REPLACE buildings: {replace_count}")
    print(f"  Fort buildings (fort_level > 0): {fort_count}")

    if replace_count > 0:
        replace_buildings = [b for b, _, _, _ in qualifying
                            if buildings[b]['has_on_built'] or buildings[b]['has_on_destroyed']]
        print(f"  REPLACE candidates: {', '.join(replace_buildings)}")

    # Generate files
    print("\nGenerating files...")

    inject_code = generate_inject(qualifying, buildings)
    out_path = OUT_BUILDINGS / "epbm_generated_inject.txt"
    out_path.write_text(inject_code, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    replace_code = generate_replace(qualifying, buildings)
    out_path = OUT_BUILDINGS / "epbm_generated_replace.txt"
    out_path.write_text(replace_code, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    io_defs = generate_io_definitions(all_pm_goods)
    OUT_IOS.mkdir(parents=True, exist_ok=True)
    out_path = OUT_IOS / "epbm_generated_ios.txt"
    out_path.write_text(io_defs, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    init_effects = generate_init_effects(qualifying, all_pm_goods, buildings)
    out_path = OUT_EFFECTS / "epbm_generated_init_effects.txt"
    out_path.write_text(init_effects, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    biases = generate_io_biases(all_pm_goods)
    OUT_BIASES.mkdir(parents=True, exist_ok=True)
    out_path = OUT_BIASES / "epbm_generated_biases.txt"
    out_path.write_text(biases, encoding="utf-8-sig")
    print(f"  Wrote {out_path.name}")

    loc = generate_io_localization(all_pm_goods)
    OUT_LOC.mkdir(parents=True, exist_ok=True)
    out_path = OUT_LOC / "epbm_ios_l_english.yml"
    out_path.write_text(loc, encoding="utf-8")
    print(f"  Wrote {out_path.name}")

    # Summary
    print("\n=== Summary ===")
    print(f"Total qualifying buildings: {len(qualifying)}")
    print(f"  Regular buildings: {len(qualifying) - fort_count}")
    print(f"  Fort buildings: {fort_count}")
    print(f"Unique PM goods profiles: {len(all_pm_goods)}")
    print(f"INJECT blocks: {inject_count}")
    print(f"REPLACE blocks: {replace_count}")

    # Count unique goods across all PMs
    all_goods = set()
    for goods in all_pm_goods.values():
        all_goods.update(goods.keys())
    print(f"Distinct maintenance goods: {len(all_goods)} ({', '.join(sorted(all_goods))})")

    if fort_count > 0:
        fort_buildings = [(b, buildings[b]['fort_level'], buildings[b]['garrison_size'])
                         for b, _, _, is_fort in qualifying if is_fort]
        print(f"\nFort buildings (fort_level, garrison_size):")
        for b, fl, gs in sorted(fort_buildings, key=lambda x: x[0]):
            print(f"  {b}: level={fl}, garrison={gs}")

    print("\n=== PM to Buildings Mapping ===")
    for pm_name in sorted(all_pm_goods.keys()):
        blist = pm_to_buildings.get(pm_name, [])
        goods = all_pm_goods[pm_name]
        goods_str = ", ".join(f"{g}={a}" for g, a in goods.items())
        print(f"  {pm_name} ({len(blist)} buildings): [{goods_str}]")
        for b in sorted(blist):
            src = "inline" if any(bn == b and s == 'inline' for bn, _, s, _ in qualifying) else "external"
            fort_tag = " (fort)" if buildings[b]['is_fort'] else ""
            print(f"    - {b} ({src}){fort_tag}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
