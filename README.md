# Estates Pay Building Maintenance

Estates contribute to building maintenance in proportion to their power. Each estate's share of the shared maintenance pool is set by its relative estate power. Estate-assigned buildings (noble villas, parish churches, guild halls, etc.) are charged in full to their owning estate.

By default the crown pays the full market price of the goods a building consumes (100%), up from the vanilla 20% discounted rate. The 50% and 20% rates are selectable through an in-game game rule. Estates are charged their share directly, which lowers the crown's net cost.

## How It Works

### Maintenance Calculation

Each building's maintenance cost is derived from an input-only production method priced against the building's local market. The calculation runs per country: monthly for players, yearly for AI. It iterates every owned building (domestic and foreign) and routes each building's cost to the shared pool or to a specific estate.

- Shared-pool costs are split across all estates in proportion to estate power. Government-category buildings are part of the shared pool.
- The crown's power share of the pool is not charged by script. The crown pays it through the engine's building maintenance line.
- Estate-assigned buildings bypass the pool and charge the owning estate in full.
- Forts and walls (stockade, castle, bastion, star_fort, fortress, city_walls, and named wonder walls such as the Theodosian Walls) are excluded from the calculation entirely via a hard-coded blacklist (`epbm_blacklist`).

Entry point: `epbm_calculate_maintenance` in `in_game/common/scripted_effects/epbm_calculate.txt`.

### Pricing and Caching

For each building the engine selects an input-only production method (at least one required good, no produced goods) and prices its goods against the local market. Market access and building efficiency are divided out to recover a per-level base cost, then reapplied at the building's actual level. The per-level cost is cached on the market-center location in the `epbm_pm_cache` variable map (keyed by production method), and `epbm_bt_display` (keyed by building type) feeds the building tooltip. Only domestic buildings with staff and market access write the cache. Foreign buildings read `building_efficiency` as 0, so they are priced at efficiency 1 and do not write the cache. The production method cache is cleared on the global monthly pulse so prices track the market. There are no pooled or reserved locations; goods are read directly from the engine each cycle.

### Crown Rate and Estate Reductions

The crown's upkeep rate and the estate reductions are applied through country-scoped modifiers, not through gold transfers from the crown:

- `INJECT:country_base_values` raises `building_upkeep_multiplier` by 0.8 (vanilla 0.2 to 1.0), so the crown pays the full goods price by default. The `epbm_baseline_50` / `epbm_baseline_20` game rules apply an offsetting static modifier (`-0.5` / `-0.8`) to bring the rate back to 50% or 20%.
- Seven auto modifiers (`epbm_nobles_upkeep` through `epbm_cossacks_upkeep`), one per estate, each apply `building_upkeep_multiplier = -1.0` scaled by a per-estate script value. An eighth auto modifier, `epbm_other_upkeep`, covers estates added by other mods. The calculation engine computes each estate's scale (its estate power times `epbm_base_multiplier`) and writes it to the variables those script values read. The AI and the player both snapshot these scales after each calculation. This is where the crown's bill drops in proportion to estate power.
- `epbm_cancel_yearly_creep` subtracts the engine's `years_since_game_start` upkeep creep when the `epbm_yearly_creep_disabled` game rule is set.
- `epbm_starting_maintenance` (`building_upkeep_multiplier = -1.0`) is given to every start-time country from 1337.4.1 to 1337.6.1 by the setup file `main_menu/setup/start/99_epbm_starting_maintenance.txt`. The estate refunds do not apply for the first month or two, and without this relief many nations declare bankruptcy on day 1.

### Estate Charging

Estates are charged via `add_gold_to_estate` with a negative value (`epbm_charge_estates_dynamic`), which destroys the estate's gold rather than transferring it to the crown. Each estate's charge is the sum of its shared-pool share and its assigned-building costs, scaled by the country charge factor (`epbm_base_multiplier` divided by `epbm_efficiency_divisor`). `epbm_base_multiplier` is the engine's non-estate `building_upkeep_multiplier`: 1.0, less the game rule offset, plus the yearly creep when it is enabled. Charging runs monthly for both players and AI; recalculation runs monthly for players and yearly for AI. Both country events (`epbm.1` monthly, `epbm.2` yearly) are listed directly on the engine pulses, so the engine checks their triggers in parallel.

### Production Method Profiles

Estate-assigned buildings (villas, churches, guild halls, and similar) normally consume no goods, so each is given a dedicated maintenance production method that defines a goods profile. These PMs are defined in `production_methods/epbm_estate_maintenance_pms.txt` and injected into each building's `possible_production_methods` in `building_types/epbm_estate_building_maintenance.txt`. Ordinary economic buildings are priced from their own existing input PMs and need no injected profile.

### Foreign Buildings

Foreign buildings are iterated with `every_owned_foreign_building` and priced the same way, then added to the shared pool (they are not estate-assigned).

### Game Rules

Two game rules are exposed in the new-game setup (`main_menu/common/game_rules/epbm_game_rules.txt`):

- `epbm_baseline_maintenance`: 100% (default), 50%, or 20% of the goods market price. 20% matches the vanilla upkeep fraction.
- `epbm_yearly_creep`: whether building maintenance rises slightly each year (vanilla behavior) or stays flat (default: disabled).

## File Structure

```
in_game/
  common/
    auto_modifiers/          Crown base rate, yearly-creep cancel, 7 per-estate upkeep-share reductions
    building_types/          INJECT maintenance PMs onto estate-assigned buildings
    on_action/               Game-start init and monthly/yearly pulses
    production_methods/      Maintenance goods profiles for estate-assigned buildings
    script_values/           Base multiplier, efficiency divisor, per-estate modifier scales
    scripted_effects/        Calculation engine, charging, lifecycle, blacklist init
  events/                    Hidden AI/player pulse events and an orphan warning-suppression event
  gui/shared/                Expense and government tooltip overrides
  localization/english/      GUI loc strings

loading_screen/
  common/defines/            NEstate.UPKEEP_PER_BUILDING = 0

main_menu/
  common/
    game_concepts/           Estate building maintenance concept
    game_rules/              Baseline-rate and yearly-creep game rules
    static_modifiers/        Baseline-rate offset modifiers (50% / 20%), starting maintenance relief
  setup/start/               Generated: starting maintenance relief for every start-time country
  gui/shared/                Building tooltip overrides
  localization/english/      Modifier and game-rule loc strings

tools/                       Developer utilities: building/PM parsing (epbm_generator/), save parsing
                             (parse_save.py), and log analysis. Not part of the runtime; the mod
                             detects buildings dynamically. The one generated file is the starting
                             maintenance setup file: rerun
                             tools/epbm_generator/generate_starting_maintenance.py after each patch.
```

## Version and Compatibility

- Version 4.3.0. Metadata supported game version: 1.3.*.
- Save-game safe: can be added to an existing save. A version stamp (`epbm_version` / `@epbm_rebuild_version`) forces a full recalculation when the mod updates. Removal should also be safe, though some variables may linger in the save file.
- Buildings from other mods are priced automatically if they have an input-only production method. An estate-assigned building added by another mod is charged to its estate only if it is given a maintenance production method.
