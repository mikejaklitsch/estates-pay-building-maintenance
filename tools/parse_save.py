#!/usr/bin/env python3
"""
Parse an EU5 save file and extract building maintenance data for a country.

Reads the mod's generated init effects to determine which buildings are
crown, estate-assigned, or shared pool. Extracts crown and shared pool
buildings (estate-assigned are ignored since estates pay those directly).

For each building, computes expected maintenance cost from the vanilla
maintenance PM's goods profile and market prices, and compares against
the engine's serialized upkeep value.

Usage:
    python parse_save.py <save_file> [--tag TAG] [--summary]

TAG defaults to the player country from the save metadata.
--summary suppresses per-building detail and shows only type-level summaries.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Discover mod root relative to this script
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_MOD_ROOT = _HERE.parent  # tools/ -> mod root
_INIT_EFFECTS = _MOD_ROOT / "in_game" / "common" / "scripted_effects" / "epbm_generated_init_effects.txt"
_PM_FILE = _MOD_ROOT / "in_game" / "common" / "production_methods" / "epbm_estate_maintenance_pms.txt"

# Import from the generator
sys.path.insert(0, str(_HERE / "epbm_generator"))
try:
    import epbm_generator_config as cfg
    from generate_building_hooks import (
        parse_production_methods, parse_all_buildings, classify,
        _resolve_vanilla_dir,
    )
    _HAS_GENERATOR = True
except ImportError:
    _HAS_GENERATOR = False


def load_building_categories():
    """Parse epbm_generated_init_effects.txt for crown_map, estate_map, tracked_types."""
    crown = set()
    estate_map = {}
    tracked = set()

    pat = re.compile(
        r'name\s*=\s*(\w+)\s+key\s*=\s*building_type:(\w+)'
        r'(?:\s+value\s*=\s*estate_type:(\w+))?'
    )
    with open(_INIT_EFFECTS, 'r', encoding='utf-8-sig') as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            map_name, btype = m.group(1), m.group(2)
            if map_name == 'epbm_crown_map':
                crown.add(btype)
            elif map_name == 'epbm_estate_map':
                estate_name = m.group(3) or 'unknown_estate'
                estate_map[btype] = estate_name.replace('_estate', '')
            elif map_name == 'epbm_tracked_types':
                tracked.add(btype)

    estate_set = set(estate_map.keys())
    shared = tracked - crown - estate_set
    return crown, estate_set, shared, tracked, estate_map


def _parse_epbm_estate_pms():
    """Parse epbm_estate_maintenance_pms.txt for simple good=qty PM definitions."""
    pm_file = _MOD_ROOT / "in_game" / "common" / "production_methods" / "epbm_estate_maintenance_pms.txt"
    if not pm_file.exists():
        return {}

    result = {}
    current_pm = None
    current_goods = {}
    known_non_goods = {'category', 'allow', 'always'}

    with open(pm_file, 'r', encoding='utf-8-sig') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            m = re.match(r'^(?:REPLACE:)?(\w+_maintenance)\s*=\s*\{', stripped)
            if m:
                if current_pm and current_goods:
                    result[current_pm] = current_goods
                current_pm = m.group(1)
                current_goods = {}
                continue

            if current_pm is not None:
                if stripped == '}':
                    if current_goods:
                        result[current_pm] = current_goods
                    current_pm = None
                    current_goods = {}
                    continue

                parts = stripped.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    if key not in known_non_goods and not key.startswith('{'):
                        try:
                            current_goods[key] = float(parts[1].strip())
                        except ValueError:
                            pass

    return result


def load_pm_goods():
    """Load all maintenance PM goods profiles from vanilla + mod via the generator."""
    if not _HAS_GENERATOR:
        print("Error: cannot import generator; PM goods profiles unavailable", file=sys.stderr)
        return {}

    vanilla_dir = _resolve_vanilla_dir()
    mod_in_game = _MOD_ROOT / "in_game"
    mod_pm_dir = mod_in_game / "common" / "production_methods"

    # Standalone PMs from production_methods/ directories
    raw_pms = parse_production_methods(vanilla_dir, mod_pm_dir if mod_pm_dir.exists() else None)
    result = {name: dict(data['goods']) for name, data in raw_pms.items() if data['goods']}

    # Inline PMs from building definitions (covers marketplace_maintenance etc.)
    buildings = parse_all_buildings(vanilla_dir, mod_in_game)
    for bname, bdata in buildings.items():
        for pm_name, pm_data in bdata.get('unique_pms', {}).items():
            goods = pm_data.get('goods', {})
            if goods and pm_name not in result:
                result[pm_name] = dict(goods)

    # EPBM estate maintenance PMs (simple good=qty format)
    estate_pms = _parse_epbm_estate_pms()
    for pm_name, goods in estate_pms.items():
        if pm_name not in result:
            result[pm_name] = goods

    return result


# ---------------------------------------------------------------------------
# Save file section indexing
# ---------------------------------------------------------------------------

def find_sections(path):
    """Single pass to find line numbers of key top-level sections."""
    sections = {}
    targets = {
        "metadata={",
        "countries={",
        "market_manager={",
        "building_manager={",
        "locations={",
    }
    found = set()
    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped in targets and stripped not in found:
                key = stripped.rstrip("={")
                sections[key] = i
                found.add(stripped)
                if len(found) == len(targets):
                    break
    return sections


def parse_player_tag(path, sections):
    start = sections.get("metadata", 1)
    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            if i > start + 100:
                break
            m = re.match(r'\s*flag="([A-Z]{3})=', line)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Country ID lookup
# ---------------------------------------------------------------------------

def parse_country(path, sections, tag):
    """Find the country's numeric ID."""
    start = sections.get("countries", 1)
    prev_id = None
    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()
            m = re.match(r'^(\d+)=\{', stripped)
            if m:
                prev_id = int(m.group(1))
            if stripped == f'country_name="{tag}"':
                return prev_id
    return None


# ---------------------------------------------------------------------------
# Modifier reconstruction
# ---------------------------------------------------------------------------

def parse_game_date(path):
    """Extract the save date from metadata."""
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            m = re.match(r'\s*date=(\d+)\.(\d+)\.(\d+)', line.strip())
            if m:
                return int(m.group(1)), int(m.group(2)), int(m.group(3))
            if line.strip().startswith('countries='):
                break
    return None, None, None


def parse_game_rules(path):
    """Extract game rules from the save."""
    rules = set()
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            if line.strip().startswith('game_rules={'):
                break
        for line in f:
            stripped = line.strip()
            if stripped.startswith('setting={'):
                tokens = stripped.replace('setting={', '').replace('}', '').split()
                rules.update(tokens)
                break
    return rules


def parse_country_modifiers(path, sections, tag):
    """Extract inflation, societal values, and EPBM show_mod variables for a country."""
    start = sections.get("countries", 1)
    inflation = 0.0
    serfdom = 0.0
    epbm_vars = {}
    in_target = False
    found_tag = False

    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()

            if not found_tag:
                if stripped == f'country_name="{tag}"':
                    found_tag = True
                continue

            if stripped.startswith('inflation='):
                try:
                    inflation = float(stripped.split('=', 1)[1])
                except ValueError:
                    pass

            if stripped.startswith('serfdom_vs_free_subjects='):
                try:
                    serfdom = float(stripped.split('=', 1)[1])
                except ValueError:
                    pass

            # EPBM show_mod variables: flag=epbm_show_mod_X followed by data={type=value identity=N}
            if stripped.startswith('flag=epbm_show_mod_'):
                var_name = stripped.split('=', 1)[1]
                # Read ahead for identity
                for line2 in f:
                    s2 = line2.strip()
                    if s2.startswith('identity='):
                        try:
                            epbm_vars[var_name] = int(s2.split('=', 1)[1])
                        except ValueError:
                            pass
                        break
                    if s2 == '}':
                        epbm_vars[var_name] = 0
                        break

            # Stop at next country
            m = re.match(r'^(\d+)=\{$', stripped)
            if m and found_tag:
                break

    return inflation, serfdom, epbm_vars


def reconstruct_modifiers(inflation, serfdom, epbm_vars, game_rules, game_year):
    """Reconstruct building_upkeep_efficiency and building_upkeep_multiplier from sources."""

    sources_eff = []
    sources_mult = []

    # --- building_upkeep_efficiency ---

    # Societal value: serfdom_vs_free_subjects
    # Left (serfdom, negative) gives +0.5 efficiency at -100
    # Right (free subjects, positive) gives nothing for this modifier
    serfdom_eff = 0.0
    if serfdom < 0:
        serfdom_eff = 0.5 * min(abs(serfdom), 100) / 100
    sources_eff.append(("serfdom_vs_free_subjects (societal value)", serfdom_eff,
                        f"0.5 * {abs(serfdom):.0f}/100"))

    # inflation_impact static modifier: vanilla = -1.0 * inflation, mod injects +1.0 * inflation
    # Net: 0.0 per unit inflation (mod cancels it)
    infl_eff = (-1.0 + 1.0) * inflation
    sources_eff.append(("inflation_impact (vanilla -1.0 + mod +1.0)", infl_eff,
                        f"0.0 * {inflation:.5f}"))

    # Country-specific advances (AZT, ROM only)
    # Not parsed; Delhi won't have these

    total_eff = sum(s[1] for s in sources_eff)

    # --- building_upkeep_multiplier ---

    # Vanilla base: country_base_values = 0.2
    # Mod INJECT: +0.8 -> total base = 1.0
    base_mult = 0.2 + 0.8
    sources_mult.append(("country_base_values (vanilla 0.2 + mod 0.8)", base_mult, ""))

    # Game rule offset
    if 'epbm_baseline_50' in game_rules:
        sources_mult.append(("epbm_baseline_50_offset (static)", -0.5, ""))
    elif 'epbm_baseline_20' in game_rules:
        sources_mult.append(("epbm_baseline_20_offset (static)", -0.8, ""))
    # epbm_baseline_100 = no offset

    # years_since_game_start: +0.001 per year
    years = max(0, game_year - 1337)
    yearly_creep = 0.001 * years
    sources_mult.append((f"years_since_game_start ({years} years * 0.001)", yearly_creep, ""))

    # Yearly creep cancellation (if game rule disables it)
    if 'epbm_yearly_creep_disabled' in game_rules:
        sources_mult.append(("epbm_cancel_yearly_creep (auto_mod)", -yearly_creep,
                             f"-0.001 * {years}"))

    # inflation_impact: vanilla = 1.0 * inflation (from mod inject)
    infl_mult = 1.0 * inflation
    sources_mult.append((f"inflation_impact (mod inject, 1.0 * {inflation:.5f})", infl_mult, ""))

    # EPBM estate reductions (auto_modifiers scaled by show_mod variables)
    # The identity values are the raw multiplier contributions (fixed-point, /10000 scaling?)
    # Actually these auto_modifiers have building_upkeep_multiplier = 0 with scales_with,
    # so the contribution IS the script value itself. The identity is stored as integer
    # representation. Let me check the scaling.
    # In EU5 saves, variable identity values are stored * 1000 (fixedpoint).
    # EU5 save variables use 100000x fixed-point for identity values
    FP_SCALE = 100000

    estate_names = ['nobles', 'clergy', 'burghers', 'peasants', 'tribes', 'dhimmi', 'cossacks']
    for estate in estate_names:
        var_key = f'epbm_show_mod_{estate}'
        raw = epbm_vars.get(var_key, 0)
        value = raw / FP_SCALE
        if value != 0:
            sources_mult.append((f"epbm_{estate}_upkeep (auto_mod)", -value,
                                 f"identity={raw}"))

    total_mult = sum(s[1] for s in sources_mult)

    # Separate estate reductions (EPBM auto-mods that offset ledger cost, not real cost)
    estate_mult = sum(s[1] for s in sources_mult if 'epbm_' in s[0] and '_upkeep' in s[0])
    base_mult = total_mult - estate_mult

    return total_eff, sources_eff, total_mult, sources_mult, base_mult, estate_mult


# ---------------------------------------------------------------------------
# Parse buildings
# ---------------------------------------------------------------------------

def parse_buildings(path, sections, owner_id, wanted_types):
    """Extract buildings of wanted types owned by the given country (or all owners if owner_id is None)."""
    start = sections.get("building_manager", 1)
    buildings = []
    current = None
    in_missing = False
    depth = 0

    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()

            if current is None:
                m = re.match(r'^(\d+)=\{$', stripped)
                if m:
                    current = {
                        'id': int(m.group(1)),
                        'type': None,
                        'level': None,
                        'location': None,
                        'owner': None,
                        'upkeep': None,
                        'employed': None,
                        'is_open': True,
                        'last_months_profit': None,
                        'pm_blocks': [],
                    }
                    depth = 1
                    in_missing = False
                continue

            depth += stripped.count('{') - stripped.count('}')

            # End of building entry
            if depth <= 0:
                owner_ok = owner_id is None or current['owner'] == owner_id
                if current['is_open'] and owner_ok and current['type'] in wanted_types:
                    buildings.append(current)
                current = None
                in_missing = False
                continue

            if stripped.startswith('type=') and current['type'] is None:
                current['type'] = stripped.split('=', 1)[1]
            elif stripped.startswith('level=') and current['level'] is None:
                try:
                    current['level'] = int(stripped.split('=', 1)[1])
                except ValueError:
                    pass
            elif stripped.startswith('location=') and current['location'] is None:
                try:
                    current['location'] = int(stripped.split('=', 1)[1])
                except ValueError:
                    pass
            elif stripped.startswith('owner=') and current['owner'] is None:
                try:
                    current['owner'] = int(stripped.split('=', 1)[1])
                except ValueError:
                    pass
            elif stripped == 'open=no':
                current['is_open'] = False
            elif stripped.startswith('upkeep='):
                try:
                    current['upkeep'] = float(stripped.split('=', 1)[1])
                except ValueError:
                    pass
            elif stripped.startswith('employed=') and current['employed'] is None:
                try:
                    current['employed'] = float(stripped.split('=', 1)[1])
                except ValueError:
                    pass
            elif stripped.startswith('last_months_profit='):
                try:
                    current['last_months_profit'] = float(stripped.split('=', 1)[1])
                except ValueError:
                    pass

            # Maintenance PM block
            pm_match = re.match(r'^(\w+_maintenance)=\{', stripped)
            if pm_match:
                current['pm_blocks'].append({
                    'name': pm_match.group(1),
                    'input': None,
                    'missing_goods': {},
                })
                in_missing = False
                continue

            if current['pm_blocks']:
                pm = current['pm_blocks'][-1]
                if stripped == 'missing={':
                    in_missing = True
                elif stripped == '}':
                    in_missing = False
                elif stripped.startswith('input='):
                    try:
                        pm['input'] = float(stripped.split('=', 1)[1])
                    except ValueError:
                        pass
                elif in_missing and '=' in stripped:
                    key, val = stripped.split('=', 1)
                    key = key.strip()
                    if key != 'demand':
                        try:
                            pm['missing_goods'][key] = float(val.strip())
                        except ValueError:
                            pass

            # Stop scanning past building_manager
            if stripped == 'population={':
                break

    return buildings


# ---------------------------------------------------------------------------
# Location -> market mapping
# ---------------------------------------------------------------------------

def parse_locations(path, sections, location_ids):
    start = sections.get("locations", 1)
    loc_to_market = {}
    needed = set(location_ids)
    current_loc_id = None

    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()

            m = re.match(r'^(\d+)=\{', stripped)
            if m:
                current_loc_id = int(m.group(1))
                continue

            if current_loc_id in needed and stripped.startswith('market='):
                val = stripped.split('=', 1)[1].strip()
                try:
                    loc_to_market[current_loc_id] = int(val)
                    needed.discard(current_loc_id)
                    if not needed:
                        break
                except ValueError:
                    pass

    return loc_to_market


# ---------------------------------------------------------------------------
# Market goods prices
# ---------------------------------------------------------------------------

def parse_market_prices(path, sections, market_ids):
    start = sections.get("market_manager", 1)
    prices = {}
    needed = set(market_ids)
    current_market_id = None
    in_goods = False
    goods_depth = 0
    current_good = None

    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()

            if in_goods:
                goods_depth += stripped.count('{') - stripped.count('}')

                if goods_depth < 0:
                    in_goods = False
                    goods_depth = 0
                    current_good = None
                    needed.discard(current_market_id)
                    if not needed:
                        break
                    continue

                gm = re.match(r'^(\w+)=\{$', stripped)
                if gm and goods_depth == 1:
                    current_good = gm.group(1)
                    continue

                if current_good and stripped.startswith('price='):
                    try:
                        prices[current_market_id][current_good] = float(stripped.split('=', 1)[1])
                    except ValueError:
                        pass
                    current_good = None

                continue

            m = re.match(r'^(\d+)=\{$', stripped)
            if m:
                mid = int(m.group(1))
                if mid in needed:
                    current_market_id = mid
                    prices[mid] = {}
                else:
                    current_market_id = None
                continue

            if current_market_id is not None and stripped == 'goods={':
                in_goods = True
                goods_depth = 0
                continue

            if stripped == 'building_manager={':
                break

    return prices


# ---------------------------------------------------------------------------
# Location name lookup
# ---------------------------------------------------------------------------

def parse_location_names(path):
    """Parse the locations={...} list from the save header to map ID -> name."""
    names = []
    capture = False
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            if not capture:
                if 'locations={' in line and 'locations_hash' not in line:
                    capture = True
                    text = line[line.index('locations={') + len('locations={'):]
                    names.extend(text.split())
                continue
            if '}' in line:
                names.extend(line[:line.index('}')].split())
                break
            names.extend(line.split())
    return names


def loc_name(names, loc_id):
    idx = loc_id - 1
    if names and 0 <= idx < len(names):
        return names[idx]
    return str(loc_id)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_pm_name(blist):
    """Find the maintenance PM name from a building's save data."""
    for b in blist:
        for pm in b['pm_blocks']:
            if pm['name'].endswith('_maintenance'):
                return pm['name']
    return None


def _compute_building_cost(b, goods_profile, loc_market, market_prices, engine_factor=1.0):
    """Compute cost for one building from goods profile and market prices."""
    loc = b['location']
    market_id = loc_market.get(loc)
    prices = market_prices.get(market_id, {}) if market_id is not None else {}
    level = b['level'] or 0
    total = 0.0
    breakdown = {}
    for good, qty in goods_profile.items():
        price = prices.get(good, 0)
        cost = qty * price * level * engine_factor
        breakdown[good] = (qty, price, cost)
        total += cost
    return total, breakdown


def _engine_factor(modifier_info):
    """Compute the engine's cost scaling factor using base multiplier (no estate reductions)."""
    total_eff = modifier_info['total_eff']
    base_mult = modifier_info['base_mult']
    eff_divisor = max(1 + total_eff, 0.5)
    return base_mult / eff_divisor if eff_divisor else 1.0


def _print_building_detail(b, goods_profile, pm_goods, loc_market, market_prices, location_names, indent="  ", ef=1.0):
    """Print one building's detail line."""
    loc = b['location']
    market_id = loc_market.get(loc)
    level = b['level'] or 0
    upkeep = b['upkeep'] or 0
    computed, breakdown = _compute_building_cost(b, goods_profile, loc_market, market_prices, ef)

    name = loc_name(location_names, loc)
    print(f"{indent}#{b['id']} {b['type']} lvl={level} loc={name}({loc}) mkt={market_id}  "
          f"upkeep={upkeep:.4f}  computed={computed:.4f}")
    if breakdown:
        parts = [f"{g}={c:.4f}" for g, (_, _, c) in sorted(breakdown.items())]
        print(f"{indent}  {', '.join(parts)}")

    epbm_pm = None
    for pm in b['pm_blocks']:
        if pm['name'] in pm_goods:
            epbm_pm = pm
            break
    if epbm_pm:
        print(f"{indent}  EPBM: input={epbm_pm['input']} missing={epbm_pm['missing_goods']}")


def _print_header(tag, country_id, crown_buildings, shared_buildings, modifier_info):
    print(f"=== Building Maintenance Report: {tag} (ID {country_id}) ===")
    print(f"  Crown buildings: {len(crown_buildings)} ({sum(b['level'] or 0 for b in crown_buildings)} total levels)")
    print(f"  Shared pool buildings: {len(shared_buildings)} ({sum(b['level'] or 0 for b in shared_buildings)} total levels)")
    print()

    # Modifier breakdown
    total_eff = modifier_info['total_eff']
    total_mult = modifier_info['total_mult']
    base_mult = modifier_info['base_mult']
    estate_mult = modifier_info['estate_mult']
    eff_divisor = max(1 + total_eff, 0.5)
    engine_factor = base_mult / eff_divisor if eff_divisor else 1.0

    print(f"  building_upkeep_efficiency: {total_eff:+.5f}  (divisor: 1 + {total_eff:.5f} = {eff_divisor:.5f})")
    for name, val, note in modifier_info['eff_sources']:
        if val != 0:
            print(f"    {val:+.5f}  {name}")
    print()

    print(f"  building_upkeep_multiplier: {total_mult:+.5f}  (base: {base_mult:+.5f}, estate offsets: {estate_mult:+.5f})")
    for name, val, note in modifier_info['mult_sources']:
        if val != 0:
            label = " [estate offset]" if 'epbm_' in name and '_upkeep' in name else ""
            print(f"    {val:+.5f}  {name}{label}")
    print()

    print(f"  Cost factor (base mult / eff divisor): {base_mult:.5f} / {eff_divisor:.5f} = {engine_factor:.5f}")
    print(f"  'computed cost' = PM goods qty * market price * level * {engine_factor:.5f}")
    print()


# ---------------------------------------------------------------------------
# Report: by pool then by type (default)
# ---------------------------------------------------------------------------

def report_by_type(tag, country_id, buildings, loc_market, market_prices,
                   crown_set, shared_set, pm_goods, location_names, summary_only,
                   modifier_info):
    crown_buildings = [b for b in buildings if b['type'] in crown_set]
    shared_buildings = [b for b in buildings if b['type'] in shared_set]
    _print_header(tag, country_id, crown_buildings, shared_buildings, modifier_info)
    ef = _engine_factor(modifier_info)

    for pool_name, pool_buildings in [("CROWN", crown_buildings), ("SHARED POOL", shared_buildings)]:
        by_type = defaultdict(list)
        for b in pool_buildings:
            by_type[b['type']].append(b)

        pool_engine = 0.0
        pool_computed = 0.0

        print(f"{'=' * 60}")
        print(f" {pool_name} BUILDINGS")
        print(f"{'=' * 60}")
        print()

        for btype in sorted(by_type.keys()):
            blist = by_type[btype]
            total_levels = sum(b['level'] or 0 for b in blist)
            total_upkeep = sum(b['upkeep'] or 0 for b in blist)
            pm_name = _find_pm_name(blist)
            goods_profile = pm_goods.get(pm_name, {}) if pm_name else {}

            type_computed = sum(
                _compute_building_cost(b, goods_profile, loc_market, market_prices, ef)[0]
                for b in blist
            )
            pool_engine += total_upkeep
            pool_computed += type_computed

            print(f"--- {btype} ({len(blist)} buildings, {total_levels} total levels) ---")
            print(f"  PM: {pm_name or '(none found)'}")
            if goods_profile:
                print(f"  Goods: {goods_profile}")
            print(f"  Engine upkeep total: {total_upkeep:.4f}")
            print(f"  Computed cost total: {type_computed:.4f}")

            if not summary_only:
                print()
                for b in blist:
                    _print_building_detail(b, goods_profile, pm_goods, loc_market, market_prices, location_names, ef=ef)
            print()

        print(f"  {pool_name} TOTAL: engine={pool_engine:.4f}  computed={pool_computed:.4f}  "
              f"delta={pool_engine - pool_computed:.4f}")
        print()


# ---------------------------------------------------------------------------
# Report: by market, then by type within each market
# ---------------------------------------------------------------------------

def report_by_market(tag, country_id, buildings, loc_market, market_prices,
                     crown_set, shared_set, pm_goods, location_names, market_centers,
                     summary_only, modifier_info):
    crown_buildings = [b for b in buildings if b['type'] in crown_set]
    shared_buildings = [b for b in buildings if b['type'] in shared_set]
    _print_header(tag, country_id, crown_buildings, shared_buildings, modifier_info)
    ef = _engine_factor(modifier_info)

    all_buildings = crown_buildings + shared_buildings

    # Group by market
    by_market = defaultdict(list)
    for b in all_buildings:
        mkt = loc_market.get(b['location'])
        by_market[mkt].append(b)

    grand_engine = 0.0
    grand_computed = 0.0

    for mkt_id in sorted(by_market.keys(), key=lambda x: (x is None, x)):
        mkt_buildings = by_market[mkt_id]
        center = market_centers.get(mkt_id)
        mkt_label = loc_name(location_names, center) if center is not None else "???"

        mkt_engine = sum(b['upkeep'] or 0 for b in mkt_buildings)
        mkt_levels = sum(b['level'] or 0 for b in mkt_buildings)

        print(f"{'=' * 60}")
        print(f" MARKET {mkt_id}: {mkt_label} ({len(mkt_buildings)} buildings, {mkt_levels} levels)")
        print(f"{'=' * 60}")

        # Show prices for goods used by buildings in this market
        prices = market_prices.get(mkt_id, {})
        used_goods = set()
        for b in mkt_buildings:
            pm_name = _find_pm_name([b])
            profile = pm_goods.get(pm_name, {}) if pm_name else {}
            used_goods.update(profile.keys())
        if used_goods:
            price_strs = [f"{g}={prices.get(g, 0):.4f}" for g in sorted(used_goods)]
            print(f"  Prices: {', '.join(price_strs)}")
        print()

        # Group by type within this market
        by_type = defaultdict(list)
        for b in mkt_buildings:
            by_type[b['type']].append(b)

        mkt_computed = 0.0

        for btype in sorted(by_type.keys()):
            blist = by_type[btype]
            pool = "crown" if btype in crown_set else "shared"
            total_levels = sum(b['level'] or 0 for b in blist)
            total_upkeep = sum(b['upkeep'] or 0 for b in blist)
            pm_name = _find_pm_name(blist)
            goods_profile = pm_goods.get(pm_name, {}) if pm_name else {}

            type_computed = sum(
                _compute_building_cost(b, goods_profile, loc_market, market_prices, ef)[0]
                for b in blist
            )
            mkt_computed += type_computed

            print(f"  --- {btype} [{pool}] ({len(blist)} buildings, {total_levels} levels) ---")
            print(f"    Engine: {total_upkeep:.4f}  Computed: {type_computed:.4f}")

            if not summary_only:
                for b in blist:
                    _print_building_detail(b, goods_profile, pm_goods, loc_market, market_prices, location_names, indent="    ", ef=ef)
            print()

        grand_engine += mkt_engine
        grand_computed += mkt_computed
        print(f"  MARKET TOTAL: engine={mkt_engine:.4f}  computed={mkt_computed:.4f}  "
              f"delta={mkt_engine - mkt_computed:.4f}")
        print()

    print(f"GRAND TOTAL: engine={grand_engine:.4f}  computed={grand_computed:.4f}  "
          f"delta={grand_engine - grand_computed:.4f}")


# ---------------------------------------------------------------------------
# Report: by market, then by location within each market, buildings listed
# ---------------------------------------------------------------------------

def report_by_location(tag, country_id, buildings, loc_market, market_prices,
                       crown_set, shared_set, pm_goods, location_names, market_centers,
                       summary_only, modifier_info):
    crown_buildings = [b for b in buildings if b['type'] in crown_set]
    shared_buildings = [b for b in buildings if b['type'] in shared_set]
    _print_header(tag, country_id, crown_buildings, shared_buildings, modifier_info)
    ef = _engine_factor(modifier_info)

    all_buildings = crown_buildings + shared_buildings

    # Group by market, then by location
    by_market = defaultdict(lambda: defaultdict(list))
    for b in all_buildings:
        mkt = loc_market.get(b['location'])
        by_market[mkt][b['location']].append(b)

    grand_engine = 0.0
    grand_computed = 0.0

    for mkt_id in sorted(by_market.keys(), key=lambda x: (x is None, x)):
        locations = by_market[mkt_id]
        center = market_centers.get(mkt_id)
        mkt_label = loc_name(location_names, center) if center is not None else "???"
        mkt_buildings = [b for loc_blds in locations.values() for b in loc_blds]
        mkt_levels = sum(b['level'] or 0 for b in mkt_buildings)

        print(f"{'=' * 60}")
        print(f" MARKET {mkt_id}: {mkt_label} ({len(mkt_buildings)} buildings, {mkt_levels} levels)")
        print(f"{'=' * 60}")

        prices = market_prices.get(mkt_id, {})
        used_goods = set()
        for b in mkt_buildings:
            pm_name = _find_pm_name([b])
            profile = pm_goods.get(pm_name, {}) if pm_name else {}
            used_goods.update(profile.keys())
        if used_goods:
            price_strs = [f"{g}={prices.get(g, 0):.4f}" for g in sorted(used_goods)]
            print(f"  Prices: {', '.join(price_strs)}")
        print()

        mkt_engine = 0.0
        mkt_computed = 0.0

        for loc_id in sorted(locations.keys()):
            loc_blds = locations[loc_id]
            name = loc_name(location_names, loc_id)
            loc_levels = sum(b['level'] or 0 for b in loc_blds)
            loc_upkeep = sum(b['upkeep'] or 0 for b in loc_blds)

            loc_computed = 0.0
            for b in loc_blds:
                pm_name = _find_pm_name([b])
                profile = pm_goods.get(pm_name, {}) if pm_name else {}
                loc_computed += _compute_building_cost(b, profile, loc_market, market_prices, ef)[0]

            mkt_engine += loc_upkeep
            mkt_computed += loc_computed

            print(f"  --- {name} ({loc_id}) : {len(loc_blds)} buildings, {loc_levels} levels ---")
            print(f"    Engine: {loc_upkeep:.4f}  Computed: {loc_computed:.4f}")

            if not summary_only:
                for b in sorted(loc_blds, key=lambda x: x['type']):
                    pm_name = _find_pm_name([b])
                    profile = pm_goods.get(pm_name, {}) if pm_name else {}
                    _print_building_detail(b, profile, pm_goods, loc_market, market_prices, location_names, indent="    ", ef=ef)
            print()

        grand_engine += mkt_engine
        grand_computed += mkt_computed
        print(f"  MARKET TOTAL: engine={mkt_engine:.4f}  computed={mkt_computed:.4f}  "
              f"delta={mkt_engine - mkt_computed:.4f}")
        print()

    print(f"GRAND TOTAL: engine={grand_engine:.4f}  computed={grand_computed:.4f}  "
          f"delta={grand_engine - grand_computed:.4f}")


# ---------------------------------------------------------------------------
# Report: EPBM vs engine comparison
# ---------------------------------------------------------------------------

def _cached_cost(goods_profile, prices):
    """Compute per-level cached-path cost: sum(qty * floor(price, 0.01))."""
    total = 0.0
    for good, qty in goods_profile.items():
        raw_price = prices.get(good, 0)
        total += qty * raw_price
    return total


def report_compare(tag, country_id, buildings, loc_market, market_prices,
                   crown_set, estate_map, shared_set, pm_goods, location_names,
                   modifier_info, summary_only):
    """Compare EPBM cached-path computation against engine upkeep per building."""

    eff_div = max(1 + modifier_info['total_eff'], 0.5)
    inf_factor = modifier_info['base_mult']

    print(f"=== EPBM vs Engine Comparison: {tag} (ID {country_id}) ===")
    print(f"  inf_factor={inf_factor:.4f}  eff_div={eff_div:.4f}")
    print(f"  engine formula: upkeep = raw * {inf_factor:.4f} / {eff_div:.4f}")
    print()

    pools = defaultdict(list)
    for b in buildings:
        btype = b['type']
        if btype in crown_set:
            pool = 'CROWN'
        elif btype in estate_map:
            pool = f"ESTATE {estate_map[btype]}"
        elif btype in shared_set:
            pool = 'SHARED'
        else:
            continue

        pm_name = None
        is_lacking = False
        for pm in b['pm_blocks']:
            if pm['name'] in pm_goods:
                pm_name = pm['name']
                if pm.get('missing_goods'):
                    is_lacking = True
                break

        goods_profile = pm_goods.get(pm_name, {}) if pm_name else {}
        loc = b['location']
        market_id = loc_market.get(loc)
        prices = market_prices.get(market_id, {}) if market_id is not None else {}
        level = b['level'] or 0

        per_level = _cached_cost(goods_profile, prices)
        cached = per_level * level
        engine_upkeep = b['upkeep'] or 0.0
        employed = b.get('employed')
        emp_str = f"{employed * 1000:.0f}" if employed is not None else "0"

        predicted = cached * inf_factor / eff_div

        lacking_goods = []
        if is_lacking:
            for pm in b['pm_blocks']:
                if pm['name'] == pm_name:
                    lacking_goods = sorted(pm['missing_goods'].keys())
                    break

        pools[pool].append({
            'b': b,
            'pm_name': pm_name,
            'cached': cached,
            'predicted': predicted,
            'engine_upkeep': engine_upkeep,
            'employed': employed,
            'emp_str': emp_str,
            'is_lacking': is_lacking,
            'lacking_goods': lacking_goods,
            'market_id': market_id,
            'per_level': per_level,
        })

    pool_order = ['SHARED', 'CROWN'] + sorted(
        k for k in pools if k.startswith('ESTATE')
    )

    grand_epbm = 0.0
    grand_predicted = 0.0
    grand_upkeep = 0.0
    grand_count = 0

    for pool_name in pool_order:
        entries = pools.get(pool_name, [])
        if not entries:
            continue

        pool_cached = sum(e['cached'] for e in entries)
        pool_predicted = sum(e['predicted'] for e in entries)
        pool_upkeep = sum(e['engine_upkeep'] for e in entries)
        pool_levels = sum((e['b']['level'] or 0) for e in entries)

        print(f"{'=' * 100}")
        print(f" {pool_name} ({len(entries)} buildings, {pool_levels} levels)")
        print(f" Pool totals: cached_raw={pool_cached:.4f}  "
              f"predicted={pool_predicted:.4f}  engine={pool_upkeep:.4f}  "
              f"delta={pool_predicted - pool_upkeep:+.4f}")
        print(f"{'=' * 100}")

        if not summary_only:
            by_type = defaultdict(list)
            for e in entries:
                by_type[e['b']['type']].append(e)

            for btype in sorted(by_type.keys()):
                type_entries = by_type[btype]
                type_cached = sum(e['cached'] for e in type_entries)
                type_predicted = sum(e['predicted'] for e in type_entries)
                type_upkeep = sum(e['engine_upkeep'] for e in type_entries)
                type_levels = sum((e['b']['level'] or 0) for e in type_entries)
                pm = type_entries[0]['pm_name'] or '(none)'

                print(f"\n  --- {btype} ({len(type_entries)} bldgs, {type_levels} lvls) "
                      f"PM: {pm} ---")
                print(f"  {'Location':<20} {'Lvl':>3} {'Wrk':>5} {'Lack':>5} "
                      f"{'Mkt':>4} {'$/lvl':>6} | "
                      f"{'Cached':>8} {'Predict':>8} {'Engine':>8} {'Delta':>8}")
                print(f"  {'-' * 90}")

                for e in sorted(type_entries,
                                key=lambda x: -(x['engine_upkeep'] or 0)):
                    b = e['b']
                    name = loc_name(location_names, b['location'])
                    lack = ','.join(e['lacking_goods']) if e['is_lacking'] else '-'
                    delta = e['predicted'] - e['engine_upkeep']

                    print(f"  {name:<20} {b['level'] or 0:>3} "
                          f"{e['emp_str']:>5} {lack:>5} "
                          f"{e['market_id'] or '?':>4} "
                          f"{e['per_level']:>6.3f} | "
                          f"{e['cached']:>8.4f} {e['predicted']:>8.4f} "
                          f"{e['engine_upkeep']:>8.4f} {delta:>+8.4f}")

                type_cached = sum(e['cached'] for e in type_entries)
                print(f"  {'SUBTOTAL':<20} {type_levels:>3} "
                      f"{'':>5} {'':>5} {'':>4} {'':>6} | "
                      f"{type_cached:>8.4f} {type_predicted:>8.4f} "
                      f"{type_upkeep:>8.4f} "
                      f"{type_predicted - type_upkeep:>+8.4f}")

        print()
        grand_epbm += pool_cached
        grand_predicted += pool_predicted
        grand_upkeep += pool_upkeep
        grand_count += len(entries)

    print(f"{'=' * 100}")
    print(f" GRAND TOTAL ({grand_count} buildings)")
    print(f"   epbm_raw={grand_epbm:.4f}  predicted={grand_predicted:.4f}  "
          f"engine={grand_upkeep:.4f}  delta={grand_predicted - grand_upkeep:+.4f}")
    print(f"{'=' * 100}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_market_centers(path, sections, market_ids):
    """Extract center location ID for each market."""
    start = sections.get("market_manager", 1)
    centers = {}
    needed = set(market_ids)
    current_market_id = None

    with open(path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            if i < start:
                continue
            stripped = line.strip()

            m = re.match(r'^(\d+)=\{$', stripped)
            if m:
                mid = int(m.group(1))
                current_market_id = mid if mid in needed else None
                continue

            if current_market_id is not None and stripped.startswith('center='):
                try:
                    centers[current_market_id] = int(stripped.split('=', 1)[1])
                    needed.discard(current_market_id)
                    if not needed:
                        break
                except ValueError:
                    pass

            if stripped == 'building_manager={':
                break

    return centers


def main():
    parser = argparse.ArgumentParser(description="Parse EU5 save for building maintenance data")
    parser.add_argument("save_file", help="Path to .eu5 save file")
    parser.add_argument("--tag", help="Country tag (default: player country)", default=None)
    parser.add_argument("--summary", action="store_true", help="Type-level summaries only, no per-building detail")
    parser.add_argument("--compare", action="store_true",
                        help="Compare EPBM cached-path costs against engine upkeep per building")
    parser.add_argument("--effbug", action="store_true",
                        help="Scan all nations for buildings with lacking maintenance goods AND partial employment")
    parser.add_argument("--view", choices=["type", "market", "location"], default="type",
                        help="Group by: type (default), market (type within market), "
                             "location (location within market)")
    args = parser.parse_args()

    path = args.save_file
    if not Path(path).exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    # Load mod data
    if not _INIT_EFFECTS.exists():
        print(f"Error: {_INIT_EFFECTS} not found", file=sys.stderr)
        sys.exit(1)
    crown_set, estate_set, shared_set, tracked_set, estate_map = load_building_categories()
    print(f"Mod data: {len(tracked_set)} tracked, {len(crown_set)} crown, "
          f"{len(estate_set)} estate, {len(shared_set)} shared", file=sys.stderr)

    pm_goods = load_pm_goods()
    print(f"  {len(pm_goods)} maintenance PM goods profiles loaded", file=sys.stderr)

    if args.compare or args.effbug:
        wanted = tracked_set
    else:
        wanted = crown_set | shared_set

    print("Indexing save file sections...", file=sys.stderr)
    sections = find_sections(path)
    for name, line in sorted(sections.items(), key=lambda x: x[1]):
        print(f"  {name}: line {line}", file=sys.stderr)

    tag = args.tag
    if not tag and not args.effbug:
        tag = parse_player_tag(path, sections)
        if not tag:
            print("Error: could not determine player tag. Use --tag.", file=sys.stderr)
            sys.exit(1)
    if tag:
        print(f"Target country: {tag}", file=sys.stderr)

    if args.effbug:
        print("Scanning for effbug candidates (all nations)...", file=sys.stderr)
        all_buildings = parse_buildings(path, sections, None, wanted)
        print(f"  Found {len(all_buildings)} tracked buildings across all nations", file=sys.stderr)

        location_names = parse_location_names(path)
        location_ids = {b['location'] for b in all_buildings if b['location'] is not None}
        loc_market = parse_locations(path, sections, location_ids)
        market_ids = set(loc_market.values())
        market_prices = parse_market_prices(path, sections, market_ids)

        emp_per_level = {
            'granary': 50, 'marketplace': 100, 'temple': 50,
            'irrigation_systems': 250, 'bridge_infrastructure': 250,
            'toll_castle': 10, 'nobles_mansion': 10, 'noble_courts': 10,
            'nobled_sponsored_temple': 50, 'noble_villa': 10, 'burgher_mansion': 10,
            'rotten_borough': 10, 'nobles_recruitment_center': 10,
            'local_shrine': 50, 'parish_church': 50, 'spiritual_retreat': 50,
            'local_estates': 20, 'local_markets': 20, 'local_assembly': 20,
            'nobility_fortifications': 10, 'cathedral': 100,
            'warrior_monks_training_grounds': 500, 'kurultai': 5000,
            'local_noble_delegation': 20, 'gallowglass_sept': 50,
            'guild_hall': 100, 'artisan_guilds': 100, 'merchants_guild': 100,
        }

        candidates = []
        for b in all_buildings:
            pm_name = None
            has_maint_missing = False
            for pm in b['pm_blocks']:
                if pm['name'] in pm_goods:
                    pm_name = pm['name']
                    if pm.get('missing_goods'):
                        has_maint_missing = True
                    break
            if not has_maint_missing:
                continue

            emp = b.get('employed')
            if emp is None or emp == 0:
                continue
            workers = emp * 1000
            epl = emp_per_level.get(b['type'], 10)
            max_w = epl * (b['level'] or 1)
            if workers >= max_w:
                continue

            goods_profile = pm_goods.get(pm_name, {}) if pm_name else {}
            loc = b['location']
            market_id = loc_market.get(loc)
            prices = market_prices.get(market_id, {}) if market_id is not None else {}
            cached = _cached_cost(goods_profile, prices) * (b['level'] or 1)
            emp_ratio = workers / max_w

            candidates.append({
                'b': b, 'pm_name': pm_name, 'cached': cached,
                'emp_ratio': emp_ratio, 'workers': workers, 'max_w': max_w,
                'missing': pm['missing_goods'] if pm else {},
            })

        print(f"  Effbug candidates (lacking maint goods + partial employment): {len(candidates)}", file=sys.stderr)

        print(f"{'Location':<22} {'Type':<28} {'Lvl':>3} {'Wrk':>5}/{'':<4} {'EmpR':>5} "
              f"{'Cached':>7} {'Upkeep':>7} {'Up/Cch':>6} {'Missing'}")
        print('-' * 120)
        for c in sorted(candidates, key=lambda x: -(x['b'].get('upkeep') or 0)):
            b = c['b']
            name = loc_name(location_names, b['location'])
            upkeep = b.get('upkeep') or 0
            ratio = upkeep / c['cached'] if c['cached'] > 0.001 else 0
            miss = ','.join(f"{k}={v:.3f}" for k, v in c['missing'].items())
            print(f"{name:<22} {b['type']:<28} {b['level'] or 0:>3} "
                  f"{c['workers']:>5.0f}/{c['max_w']:<4.0f} {c['emp_ratio']:>5.3f} "
                  f"{c['cached']:>7.4f} {upkeep:>7.4f} {ratio:>6.3f} {miss}")
        return

    print("Finding country...", file=sys.stderr)
    country_id = parse_country(path, sections, tag)
    if country_id is None:
        print(f"Error: country {tag} not found", file=sys.stderr)
        sys.exit(1)
    print(f"  Country ID: {country_id}", file=sys.stderr)

    print("Parsing modifiers...", file=sys.stderr)
    game_year, _, _ = parse_game_date(path)
    game_rules = parse_game_rules(path)
    inflation, serfdom, epbm_vars = parse_country_modifiers(path, sections, tag)
    total_eff, eff_sources, total_mult, mult_sources, base_mult, estate_mult = reconstruct_modifiers(
        inflation, serfdom, epbm_vars, game_rules, game_year or 1337)
    print(f"  building_upkeep_efficiency: {total_eff:.5f}", file=sys.stderr)
    print(f"  building_upkeep_multiplier: {total_mult:.5f}", file=sys.stderr)

    print("Parsing buildings...", file=sys.stderr)
    buildings = parse_buildings(path, sections, country_id, wanted)
    label = "tracked" if args.compare else "crown+shared"
    print(f"  Found {len(buildings)} {label} buildings", file=sys.stderr)

    location_ids = {b['location'] for b in buildings if b['location'] is not None}
    print(f"  Across {len(location_ids)} locations", file=sys.stderr)

    print("Mapping locations to markets...", file=sys.stderr)
    loc_market = parse_locations(path, sections, location_ids)
    market_ids = set(loc_market.values())
    print(f"  {len(loc_market)} locations mapped to {len(market_ids)} markets", file=sys.stderr)

    print("Parsing market prices...", file=sys.stderr)
    market_prices = parse_market_prices(path, sections, market_ids)
    for mid in sorted(market_prices):
        print(f"  Market {mid}: {len(market_prices[mid])} goods", file=sys.stderr)

    print("Parsing location names and market centers...", file=sys.stderr)
    location_names = parse_location_names(path)
    market_centers = parse_market_centers(path, sections, market_ids)
    for mid in sorted(market_centers):
        ctr = market_centers[mid]
        print(f"  Market {mid}: {loc_name(location_names, ctr)}", file=sys.stderr)

    print(file=sys.stderr)

    modifier_info = dict(
        total_eff=total_eff, eff_sources=eff_sources,
        total_mult=total_mult, mult_sources=mult_sources,
        base_mult=base_mult, estate_mult=estate_mult,
    )
    if args.compare:
        report_compare(
            tag=tag, country_id=country_id, buildings=buildings,
            loc_market=loc_market, market_prices=market_prices,
            crown_set=crown_set, estate_map=estate_map, shared_set=shared_set,
            pm_goods=pm_goods, location_names=location_names,
            modifier_info=modifier_info, summary_only=args.summary,
        )
        return

    common = dict(tag=tag, country_id=country_id, buildings=buildings,
                  loc_market=loc_market, market_prices=market_prices,
                  crown_set=crown_set, shared_set=shared_set, pm_goods=pm_goods,
                  location_names=location_names, summary_only=args.summary,
                  modifier_info=modifier_info)

    if args.view == "type":
        report_by_type(**common)
    elif args.view == "market":
        report_by_market(**common, market_centers=market_centers)
    elif args.view == "location":
        report_by_location(**common, market_centers=market_centers)


if __name__ == '__main__':
    main()
