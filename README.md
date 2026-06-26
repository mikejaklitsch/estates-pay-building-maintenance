# Estates Pay Building Maintenance

Estates contribute to building maintenance in proportion to their power. Each estate's share of the shared maintenance pool is determined by its relative estate power. Estate-assigned buildings (noble villas, parish churches, guild halls, etc.) are charged in full to their owning estate.

The crown pays full 100% maintenance cost (the real market price of goods consumed), up from the vanilla 20% discounted rate. Estates are charged their share directly, so the net cost to the crown is reduced by the total estate power.

## How It Works

### Maintenance Calculation

Every building with a tracked production method has its maintenance cost calculated from that PM's goods inputs priced against the local market. The mod iterates all owned buildings each recalculation cycle (monthly for players, yearly for AI) and routes each building's cost to either the shared pool or a specific estate.

Shared-pool costs are split across all estates proportional to estate power. Estate-assigned buildings bypass the shared pool and charge the owning estate directly. Government and military buildings (royal garden, forts, council halls) are crown-funded and excluded from estate maintenance.

### Engine-Side Upkeep Reduction

The crown's upkeep savings are handled natively by the engine, not by script. Each estate injects a `building_upkeep_multiplier` reduction into its `power` block, which the engine scales automatically by the estate's relative power. This means the crown's upkeep bill drops in proportion to total estate power with zero per-tick script overhead.

A crown-building offset auto-modifier corrects for the fact that estates should not reduce upkeep on crown-only buildings.

### Estate Charging

Estates are charged via `add_gold_to_estate` (which generates/destroys gold rather than transferring from the crown). The amount each estate pays matches the upkeep reduction they provide, so net gold destroyed equals vanilla upkeep.

### Production Method Profiles

Each tracked building type has a dedicated maintenance production method that defines its goods profile. These PMs are injected into the building's `possible_production_methods` so the engine always selects them. The goods inputs on each PM represent the maintenance cost profile for that building type.

For shared-pool buildings, goods profiles are stored on pre-allocated pooled locations (a set of 250 game locations reserved at game start). Each unique PM maps to a pooled location via `epbm_profiles`, and per-market price lookups are cached on the same location (`epbm_costs` variable map) to avoid redundant price calculations within a single recalculation cycle.

### Foreign Buildings

Foreign buildings are iterated via `every_owned_foreign_building` and use the same estate power split as domestic buildings.

## File Structure

```
in_game/
  common/
    auto_modifiers/          Crown upkeep multiplier and offset
    building_types/          INJECT stubs for estate-assigned and crown buildings
    estates/                 Per-estate power block upkeep reductions
    on_action/               Version tracking, rebuild triggers, monthly pulse
    production_methods/      Maintenance PMs with goods profiles
    script_values/           Generated dispatch values and manual helpers
    scripted_effects/        Calculation engine, charging, lifecycle, init
    scripted_triggers/       Building eligibility checks
  events/                   Orphan event placeholder
  gui/shared/               Government tooltip overrides
  localization/english/     GUI loc strings

loading_screen/
  common/defines/           Estate upkeep define override

main_menu/
  common/
    game_concepts/           In-game concept explanations
    modifier_icons/          Custom modifier icons
    modifier_type_definitions/  Custom modifier type registration
  gui/shared/               Building tooltip overrides
  localization/english/     Modifier loc strings

tools/
  epbm_generator/           Code generator for building hooks and init effects
```

### Generator

`tools/epbm_generator/` produces several generated files from a config-driven building list:

- `epbm_generated_init_effects.txt`: pool allocation effects and PM registration
- `epbm_generated_script_values.txt`: dispatch script values for per-building-type cost lookups
- `epbm_generated_crown_inject.txt`: crown building INJECT stubs

The generator supports a mod overlay system for cross-mod compatibility patches and has a check mode (`--check`) for CI validation.

## Version and Compatibility

- Built for Europa Universalis V 1.2.*/1.3.*
- Save-game safe: can be added to existing saves. Removal should also be safe, though some internal data may linger in the save file.
- Mods that add new buildings with maintenance production methods need a compatibility patch for estate contribution.
- Optional submods available for 50% or 20% (vanilla rate) maintenance multipliers.

## Submods

- [50% Building Maintenance](https://steamcommunity.com/sharedfiles/filedetails/?id=3671010689)
- [20% Building Maintenance (Vanilla)](https://steamcommunity.com/sharedfiles/filedetails/?id=3671010534)
