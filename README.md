# Estates Pay Building Maintenance

Estates contribute to building maintenance in proportion to their power. Estate-assigned buildings are charged in full to their owning estate. Government buildings are assigned to the country and paid in full by the crown. Everything else goes into a shared pool split by estate power.

The mod raises `building_upkeep_multiplier` from the vanilla 0.2 to 1.0, then applies per-estate auto modifier reductions scaled to each estate's share of total building costs. Each estate is charged a matching amount via `add_gold_to_estate`. Building costs are priced from production method goods inputs against the local market. Detection is dynamic, so modded buildings are picked up automatically. Fort buildings are the exception; they work but get folded into the shared pool.

## Game Rules

**Estate Building Maintenance** sets the baseline rate to 100% (default), 50%, or 20% (vanilla). **Yearly Maintenance Increase** lets you disable the vanilla yearly creep.

## File Structure

```
in_game/
  common/
    auto_modifiers/          Per-estate upkeep multiplier reductions
    building_types/          INJECT stubs for estate-assigned buildings
    on_action/               Monthly pulse, version tracking, rebuild triggers
    production_methods/      Maintenance PMs with goods profiles
    script_values/           Cost dispatch and helpers
    scripted_effects/        Calculation, charging, lifecycle
  events/                   Recalculation events
  gui/shared/               Government tooltip overrides
  localization/english/     GUI loc strings

loading_screen/
  common/defines/           Estate upkeep define override

main_menu/
  common/
    game_concepts/           In-game concept explanations
    game_rules/              Baseline maintenance and yearly creep rules
    static_modifiers/        Game rule modifier offsets
  localization/english/     Modifier and rule loc strings

tools/
  epbm_generator/           Code generator for building hooks and init effects
```

## Compatibility

Built for Europa Universalis V 1.3.*. Compatible with mods that add new buildings without a patch. Save-game safe.
