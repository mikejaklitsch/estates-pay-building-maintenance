# Building Maintenance Test Samples

Data collected during v4.1-4.2 debugging to reverse-engineer the engine's building maintenance formula.

## Key Findings

- **Formula**: `raw_goods_cost × building_upkeep_multiplier / max(0.5, 1 + building_upkeep_efficiency)`
- **Multiplier**: direct scalar (not `1 + modifier`). Vanilla base = 0.2, EPBM raises to 1.0.
- **Price rounding**: engine floors market prices to 0.01 before multiplying by quantity.
- **Intermediate rounding**: engine floors intermediate results at each calculation step.
- **Market access**: reduces goods demand but does NOT reduce maintenance cost.
- **Employment**: reduces goods demand AND maintenance cost.
- **Lacking goods**: behavior inconsistent (see samples 3 and 6).
- **Serfdom efficiency**: societal value `serfdom_vs_free_subjects` left_modifier gives `building_upkeep_efficiency = 0.5` at full slider. At 80% slider = 0.4 efficiency, eff_div = 1.4.

---

## System Overview

### What the mod does

EPBM shifts building maintenance costs from the crown to estates. In vanilla, the engine computes building maintenance as a single lump-sum deduction from the country's treasury. EPBM intercepts this by manipulating the `building_upkeep_multiplier` modifier: it raises the base multiplier to 1.0, then applies per-estate `building_upkeep_multiplier` auto_modifier reductions that lower the country's visible building maintenance proportionally to each estate's share. Each estate is then charged its share via `add_gold_to_estate`.

The net effect: the engine's displayed "building maintenance" shrinks by the amount the estates are covering, and estates pay the difference. The crown only pays for its own share.

### Building categories

Buildings are classified into three pools at game start, stored in global variable maps:

1. **Crown buildings** (`epbm_crown_map`): always paid by the crown (e.g. royal court, grand palace). Their costs go into `epbm_crown_total_cost`.
2. **Estate-assigned buildings** (`epbm_estate_map`): always paid by a specific estate (e.g. noble villa -> nobles). Their costs go into per-estate accumulators (`epbm_estate_<name>`).
3. **Shared pool** (everything else tracked in `epbm_tracked_types` but not in crown or estate maps): split proportionally by estate power. Their costs go into `epbm_total_cost`.

### Pool accumulation (the suspected bug area)

`epbm_calculate_maintenance` iterates `every_owned_building` and prices each one:

**Cached path** (`epbm_price_building_cached`): used when the building has full employment and is not lacking goods. Looks up the PM's goods profile from pre-registered pooled locations (`epbm_goods` variable maps), computes `sum(qty * floor(market_price * 100) / 100)` per good, caches the result per market. Final building cost = `cached_pm_cost * building_level`.

**Actual path** (`epbm_price_building_actual`): used when a building has reduced employment OR is lacking goods. Reads `building_goods_input(good)` for each good in the PM profile, which returns actual consumption quantities already reduced by employment and market access. Multiplies by floored market prices. Then **divides by market_access** to undo the access scaling (since the engine charges full cost regardless of access).

The actual path also applies a vanilla bug workaround: when a building has both reduced employment AND is lacking goods, the engine inverts the efficiency modifier (multiplies instead of dividing). The mod pre-compensates by multiplying the building's cost by `eff_div^2` so that the pool-level `/eff_div` produces the correct `*eff_div` net result.

### Scaling and modifier calculation

After accumulating all building costs into the three pools, the shared pool is scaled:
```
epbm_total_cost *= epbm_maint_burden    # game rule: 1.0, 0.5, or 0.2
epbm_total_cost *= epbm_inflation_factor  # 1 + inflation + yearly_creep
epbm_total_cost /= epbm_efficiency_divisor  # max(0.5, 1 + building_upkeep_efficiency)
```

The `epbm_shared_fraction` = `shared / (shared + crown)` (computed before scaling) captures what fraction of the non-estate pool is shared vs. crown.

Per-estate auto_modifier scales (`epbm_mod_<estate>`) are computed as:
```
estate_power * epbm_shared_fraction * epbm_maint_burden * epbm_inflation_factor
```
These feed into `building_upkeep_multiplier` auto_modifiers that reduce the country's displayed maintenance. The auto_modifiers do NOT include the `/eff_div` term because the engine applies efficiency separately.

### The crown offset problem

The goal is: `displayed_maintenance = crown_share_of_shared_pool + crown_building_costs`. The mod achieves this by subtracting each estate's portion from the `building_upkeep_multiplier` via auto_modifiers. The subtraction covers the shared pool fraction (`epbm_shared_fraction`) only; crown buildings are handled separately as a fixed cost the crown always pays.

The current discrepancy has two symptoms that compound:

1. **Pool overshoot**: EPBM's `raw_pool` (shared + crown before scaling) is consistently higher than the engine's actual maintenance pool. Sample 5 (Delhi): EPBM raw_pool = 169.70, engine-equivalent ~144, ratio ~0.849. The mod accumulates ~15% more cost than the engine.

2. **Final charge undershoot**: after auto_modifier reductions are applied, the country pays MORE than EPBM predicted. Because the reductions are computed from the overestimated pool, they over-subtract from the multiplier relative to what the engine's actual pool warrants. The engine's displayed maintenance ends up higher than the mod's prediction for the crown's share.

The two errors do not cancel: the pool overshoot inflates the estate reductions, but those reductions are applied to the engine's real (smaller) pool, so the final displayed maintenance diverges from the mod's target.

### Suspected root causes

1. **Actual-path quantity semantics**: `building_goods_input()` returns demand quantities already reduced by market access, employment, AND output percentage (which itself derives from employment and goods shortages). The mod divides by market_access to recover the demand that the engine uses for pricing (market access is the only factor that reduces demand without reducing cost). If `building_goods_input()` also folds in output_pct, the division by market_access alone correctly recovers `full_qty * employment * output_pct`. But if the function's handling of output_pct differs from what the engine uses for maintenance pricing, the recovered quantity will be wrong.

2. **Per-building vs. per-country rounding**: The engine floors intermediate results at each step. EPBM sums individual building costs without flooring at the country level between multiplication steps. The gap may compound across many buildings, consistently producing a higher sum than the engine's floored-per-step approach.

3. **Market access undo precision**: dividing by market_access to reverse the access scaling introduces floating-point error, especially for locations with non-round access values (73%, 99%, etc.). This always rounds up slightly.

4. **Cached path overcount**: the cached path uses full-capacity PM quantities and multiplies by building_level. If a building has slightly reduced capacity for a reason that doesn't trigger the `is_lacking_goods` or unemployment checks, the cached path overestimates.

### Diagnostic infrastructure

- **Debug logging**: `epbm_diag` variable triggers detailed per-building and per-pool logging to `error.log`/`debug.log` for BOH, DLH, FRA, TUR.
- **Save parser** (`tools/parse_save.py`): extracts building data from save files, computes expected costs, compares against serialized engine values.
- **In-game tooltip**: shows `epbm_show_*` snapshot variables (one month delayed from calculation). Cannot programmatically read the engine's final building maintenance value; must be checked by a human.

### Timing

- Monthly: player gets snapshot -> charge -> recalc. AI gets snapshot -> charge.
- Yearly: AI gets recalc.
- Auto_modifier scales use snapshotted (`epbm_show_mod_*`) values to stay synced with the one-month charge offset.

---

## Sample 1: Khambat Marketplaces (lacking goods, full market access)

- **Location**: Khambat
- **Building**: 6 marketplaces (6 building levels)
- **Market access**: 100%
- **Employment**: full
- **Lacking goods**: yes (cloth)
- **Output**: 79.68%
- **Country multiplier**: 30.82% (after EPBM estate reductions)
- **Efficiency**: 40% (eff_div = 1.4)
- **Final cost**: 0.73

### Per-building consumption (single marketplace)
| Good | Quantity | 
|------|----------|
| cloth | 0.28 |
| jewelry | 0.23 |
| paper | 0.23 |
| salt | 0.28 |

### Combined 6-building cost breakdown (full-capacity quantities)
| Good | Quantity | Cost |
|------|----------|------|
| cloth | 0.36 | 1.08 |
| jewelry | 0.30 | 1.56 |
| paper | 0.30 | 0.70 |
| salt | 0.36 | 0.90 |
| **Total** | | **4.24** |

### Derived prices
| Good | Price (cost/qty) |
|------|-----------------|
| cloth | 3.00 |
| jewelry | 5.20 |
| paper | 2.33 |
| salt | 2.50 |

### Calculation
- Full cost: 4.24
- With output reduction (floor each step): 4.24 × 0.7968 → floor 3.37 → × 0.3082 → floor 1.03 → / 1.4 → floor **0.73** ✓
- Conclusion: lacking goods REDUCES cost by output percentage, floored at each step.

---

## Sample 2: Dhar Marketplaces (no lacking goods, reduced market access)

- **Location**: Dhar
- **Building**: 6 marketplaces (6 building levels)
- **Market access**: 73%
- **Employment**: full
- **Lacking goods**: no
- **Country multiplier**: 30.82% (same as Khambat)
- **Efficiency**: 40% (eff_div = 1.4)
- **Final cost**: 0.92

### Per-building consumption
| Good | Quantity |
|------|----------|
| cloth | 0.26 |
| jewelry | 0.21 |
| paper | 0.21 |
| salt | 0.26 |

### Cost breakdown
Same full quantities and prices as Khambat (0.36, 0.30, 0.30, 0.36). Total full cost = 4.24.

### Calculation
- Market access does NOT reduce cost: 4.24 × 0.3082 → floor 1.30 → / 1.4 → floor **0.92** ✓
- Demand quantities are reduced by market access (0.36 × 0.73 = 0.26), but cost uses full quantities.

---

## Sample 3: BOH Shared Pool (overall validation)

- **Country**: Bohemia
- **Shared buildings**: granary, marketplace, temple (only non-estate types)
- **Inflation**: 0%
- **Efficiency**: 40% (serfdom at 80%)
- **Yearly creep**: 1 year = 0.1% multiplier

### Building instances (typical month)
| Type | Instances | Level | Per-level cost |
|------|-----------|-------|---------------|
| granary | 3 | 1 | ~0.178 |
| granary | 1 | 2 | ~0.178 |
| marketplace | 4 | 2 | ~0.931 |
| temple | 4 | 1 | ~0.358 |

### Per-good marketplace data (single level-2 building, one specific month)
| Good | Qty (lvl2) | Cost | Derived price |
|------|-----------|------|--------------|
| cloth | 0.12 | 0.69 | 5.75 |
| jewelry | 0.10 | 0.47 | 4.70 |
| paper | 0.10 | 0.14 | 1.40 |
| salt | 0.12 | 0.36 | 3.00 |

### Game display values (various months)
6.27, 5.69, 6.40, 5.39, 7.09 (fluctuates with market prices)

---

## Sample 4: BOH Per-good Rounding Validation

- Single marketplace: raw total 1.94 (displayed), full precision ~1.97
- 40% efficiency applied
- Game result: 1.41

### Calculation
- Full precision: 1.9683 / 1.4 = 1.4059 ≈ 1.41 ✓
- Displayed 1.94 / 1.4 = 1.386 ≠ 1.41
- Conclusion: engine uses full-precision values internally, display truncates to 2 decimals. Formula `1/(1+eff)` is correct.

---

## Sample 5: Delhi (large nation, high inflation)

- **Country**: Delhi (DLH)
- **Inflation**: 99.9%
- **inf_factor**: 1.999
- **eff_div**: 1.0 (no serfdom)
- **Serfdom**: 80% (but DLH may have different societal values; eff_div logged as 1.0)
- **Reductions**: zeroed for testing
- **Game building maintenance**: 288.11
- **EPBM raw_pool**: 169.70
- **EPBM engine_expected**: 339.23

### Discrepancy
- EPBM predicts: 339.23 (raw × inf / eff_div = 169.70 × 1.999 / 1.0)
- Game shows: 288.11
- Ratio: 288.11 / 339.23 = 0.849 (EPBM overshoots by ~15%)
- Implies game's effective raw pool is ~144 vs EPBM's 170

---

## Sample 6: Granary Edge Case (employment + lacking goods)

- **Building**: 6 granaries (6 levels)
- **Employment**: 159/300 (53%)
- **Market access**: 99%
- **Output**: 84.24% efficiency
- **Lacking goods**: yes (masonry)
- **Country multiplier**: 199.9% (reductions zeroed, 99.9% inflation)
- **Efficiency**: none (eff_div = 1.0)
- **Final cost**: 1.43

### Granary maintenance PM
- masonry: 0.2 per level
- Full demand: 6 × 0.2 = 1.2

### Demand scaling
- Market access (99%): 1.2 × 0.99 = 1.188
- Employment (53%): 1.188 × 0.53 = 0.630 ≈ **0.63** (matches breakdown)
- Actual consumption: 0.63 × 0.8424 = 0.531 ≈ **0.53** (matches "needs")

### Cost breakdown
| Good | Demand qty | Price | Per-good cost |
|------|-----------|-------|--------------|
| masonry | 0.63 | 0.95 | 0.61 |

### Calculation
- demand × price × multiplier = 0.63 × 0.95 × 1.999 = 1.196
- Game shows 1.43, ratio = 1.43 / 1.196 = 1.196 ≈ **1 / 0.8424**
- Formula: `demand × price × multiplier / output_pct = 0.63 × 0.95 × 1.999 / 0.8424 ≈ 1.43` ✓

### Contradiction with Sample 1
- Sample 1 (Khambat): lacking goods REDUCES cost (× output_pct)
- Sample 6 (Granary): lacking goods INCREASES cost (÷ output_pct)
- Key differences: Sample 1 had full employment + EPBM reductions active. Sample 6 has 53% employment + no reductions.
- Possible vanilla bug: engine applies output scaling inconsistently, or output percentage means different things in different contexts.

---

## Profiling Data (v4.2)

### Path split
| Path | Calls | Total time | Avg per call |
|------|-------|-----------|-------------|
| epbm_price_building_cached | 12,428 | 0.043s | 0.0035ms |
| epbm_price_building_actual | 10,085 | 0.056s | 0.0056ms |

45% of buildings take the actual (per-good) path. Combined overhead: 0.099s per session.

### Top EPBM effects
| Effect | Total time | Calls |
|--------|-----------|-------|
| epbm_effects.txt (estate iterator) | 0.601s | 82,600 |
| epbm_calculate.txt (main calc) | 0.458s | 2,340 |

---

## Sample 7: Cahokia Granary (0% output, full employment)

- **Location**: Cahokia
- **Building**: 1 granary, level 1
- **Market access**: 100%
- **Employment**: 50/50 (100%)
- **Output**: 0% (no masonry available)
- **Final cost**: 0.00
- **Cost potential**: +0.25
- **Multiplier**: +100.7% (1.007)

### Calculation
- Full cost: 0.2 × 1.26 × 1.007 = 0.2538, displayed as 0.25
- With 0% output: 0.25 × 0.0 = **0.00** ✓
- Proves: output MULTIPLIES cost (not divides). Division would give infinity at 0%.

---

## Sample 8: Cahokia Granary (partial output, full employment)

- **Location**: Cahokia
- **Building**: 1 granary, level 2
- **Market access**: 100%
- **Employment**: full (100%)
- **Output**: 18.92%
- **Actual input**: 0.07 masonry
- **Demands**: 0.4 masonry (0.2 × 2 levels)
- **Goods cost**: 0.51 (0.4 × ~1.275 per unit)
- **Multiplier**: 101.3% (1.013)
- **Final cost**: 0.09

### Calculation
- Actual input: 0.4 × 0.1892 = 0.076, floor to 0.07 ✓
- Cost: 0.51 × 0.1892 × 1.013 = 0.098, floor to **0.09** ✓
- Confirms: `goods_cost × output_pct × multiplier`, floored at each step.

---

## Sample 9: Reduced employment, no lacking goods (confirmed via gameplay)

- Employment reduces both goods demand AND cost.
- Cost breakdown shows employment-adjusted quantities (not full).
- Formula: `full_qty × employment_ratio × price × multiplier / eff_div`

---

## Vanilla Bug: Efficiency Inversion

### The bug
When a building has **reduced employment AND is lacking goods**, the engine applies `building_upkeep_efficiency` in the **wrong direction**: it multiplies instead of dividing. This makes maintenance MORE expensive instead of cheaper.

### Normal behavior
40% efficiency (eff_div = 1.4) means the engine divides cost by 1.4. The building pays ~71% of base cost. This is correct.

### Bugged behavior
With reduced employment + lacking goods, the engine multiplies by 1.4 instead of dividing. The building pays ~140% of the expected cost. Efficiency works backwards.

### Evidence

**Khambat (full employment, lacking goods)**: efficiency DIVIDES (correct)
```
4.24 × 0.3082 → floor 1.30
1.30 / 1.4 → floor 0.92
0.92 × 0.7968 → floor 0.73 ✓
```

**Granary (53% employment, lacking goods)**: efficiency MULTIPLIES (bug)
```
0.61 × 1.999 → floor 1.21
1.21 × 1.4 → floor 1.69
1.69 × 0.8424 → floor 1.43 ✓
```

### Trigger
The exact trigger is not fully confirmed. Candidates:
- Reduced employment combined with lacking goods
- Building upkeep multiplier being above 1.0 (Khambat had 0.3082, granary had 1.999)
- Some interaction between employment ratio and the efficiency calculation

More samples needed to isolate the exact condition.

---

## Engine Behavior Summary

| Factor | Affects demand | Affects cost | Notes |
|--------|---------------|-------------|-------|
| Market access | Yes | **No** | Vanilla bug: demand reduced but full price charged |
| Employment | Yes | Yes | Cost scales with employment ratio |
| Lacking goods (output) | Yes | Yes | Cost × output_pct (confirmed at 0% and 18.92%) |
| Efficiency modifier | No | Yes | Normally divides. **Inverts (multiplies) when employment is reduced + goods lacking** |
| Inflation | Yes (via multiplier) | Yes | Adds to building_upkeep_multiplier via static modifier inject |

### Confirmed formula (normal case)
```
cost = full_qty × employment_ratio × price × output_pct × multiplier / eff_div
```
Floored to 0.01 at each intermediate step.

### Known vanilla bugs
1. **Market access**: reduces goods demand but not maintenance cost
2. **Efficiency inversion**: efficiency multiplies instead of divides when building has reduced employment + lacking goods

---

## v4.3 Debugging Session Findings

### Save file `upkeep` field
The engine serializes per-building maintenance cost as `upkeep=X` in the building_manager section of the save file. Buildings with 0 upkeep omit the field. This is engine ground truth, not mod-computed. The `parse_save.py --compare` mode uses this for per-building validation.

### Save file `employed` field
The `employed` field is in thousands of workers (not a ratio). `employed=0.300` = 300 workers. Buildings with 0 workers omit the field entirely. Employment per level varies by building type (granary = 50, marketplace = 100, etc.), defined via `employment_size` script values in `default_values.txt`.

### Save file location names
The `locations={...}` list in the save header is 1-indexed. Use `names[loc_id - 1]` to get the correct name.

### `building_goods_input()` behavior
Returns demand quantity, NOT consumption. Includes market_access and employment reductions, but does NOT include output_pct (goods shortage) reduction. Verified: fully-staffed building with lacking goods returns the same demand as a non-lacking building.

### `location_unemployed_population_for_building_type` behavior
Returns positive when there are surplus workers at the location, negative when there's a labor shortage. Most populated locations have surplus workers, so `value > 0` is true for almost all buildings regardless of their staffing level.

### Price flooring
The engine uses full-precision market prices for maintenance calculation, not floored to 0.01. EPBM's price flooring was removed in v4.3 to match engine behavior.

### Fixes applied (v4.3)
1. **Efficiency bug condition**: changed from `value > 0` (surplus workers, always true) to checking actual labor shortage via employment ratio < 1.0. The original condition was backwards.
2. **Price flooring removed**: engine uses full precision prices; EPBM now matches.
3. **Employment-per-level in maps**: `epbm_tracked_types` map values now store the building type's employment-per-level (in thousands), scraped from vanilla `default_values.txt` by the generator. At runtime, this enables computing employment ratio from `location_unemployed_population_for_building_type`.
4. **Output_pct derivation**: for lacking-goods buildings, output_pct is derived as `actual_cost / expected_cost` where expected = cached_cost * employment_ratio. The actual path cost (from building_goods_input) divided by the expected full-employment cost gives the output fraction.

### Remaining investigation
- The output_pct derivation needs validation against engine upkeep values from a new save
- `building_efficiency` (location scope trigger) is unexplored; may provide output_pct directly
- The eff_bug vanilla behavior (efficiency inversion with reduced employment + lacking goods) still needs more samples to confirm the exact trigger condition
