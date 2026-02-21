# Government Buildings Refactor Plan

## Goal
Exclude government/military buildings from EPBM. Add a visible "Crown Building" indicator on these buildings. Remove all fort-specific maintenance logic.

## Phase 1: Generator — Government Classification

**File: `tools/generate_building_hooks.py`**

1. Add a `GOVERNMENT_BUILDINGS` set containing all 139 building names (97 automated + 42 reclassified)
2. In `classify()`, buildings matching this set are tagged as "government" — no INJECT hooks generated
3. Remove the "fort" category entirely from classification logic
4. Generator no longer outputs `epbm_fort_types` list additions or fort-specific hooks

## Phase 2: Crown Building Modifier

**New file: `in_game/common/modifiers/epbm_modifiers.txt`** (or add to existing)

5. Define a static modifier `epbm_crown_building` — cosmetic only, just a visible flag
6. Description: "Crown Building — Maintenance paid by the crown, not reimbursed through estates"

**Localization:**

7. Add `epbm_crown_building` name and description to localization

**Generated building files:**

8. Generator injects the crown building modifier/flag into government building definitions so it shows in-game

> **Open question:** What's the best EU5 mechanism to show "Crown Building" on the building UI?
> - A building modifier added via the building definition
> - A scripted trigger that shows in tooltips
> - A building flag/tag in the building_type definition itself
>
> Need to check how vanilla buildings display similar labels.

## Phase 3: Rip Out Fort Logic from Effects

**File: `in_game/common/scripted_effects/epbm_effects.txt`**

9. Remove `@epbm_garrison_upkeep` constant
10. **`epbm_full_rebuild`**: Remove `epbm_fort_types` list clearing, `epbm_loc_f_*` variable zeroing, `epbm_fort_pay_*` zeroing in player block
11. **`epbm_calculate_maintenance`**: Remove:
    - Fort accumulators (`epbm_fort_nobles`, `epbm_fort_dhimmi`)
    - Fort building cost loop
    - Garrison maintenance calc
    - Fort maintenance slider scaling
    - Fort tax base computation (`epbm_fort_tax_total`, `epbm_fort_dhimmi_tax`)
    - Fort distribution to nobles+dhimmi
    - `epbm_loc_f_*` per-location vars
    - `epbm_fort_pay_*` persistent vars
    - Fort reimbursement modifier logic
12. **`epbm_charge_estates`**: Remove `epbm_fort_pay_nobles` and `epbm_fort_pay_dhimmi` from nobles/dhimmi charge blocks
13. **`epbm_snapshot_for_display`**: Remove `epbm_show_fort_nobles` and `epbm_show_fort_dhimmi` lines
14. Remove `epbm_maint_fort` per-location variable

## Phase 4: Clean Up GUI, Localization, Modifiers

**File: `in_game/gui/shared/epbm_government_tooltips.gui`**

15. Remove 2 fort `TooltipManualTableField` blocks (nobles + dhimmi fort lines)

**File: `in_game/gui/shared/epbm_location_tooltips.gui`**

16. Remove any fort maintenance references

**Localization:**

17. Remove `EPBM_FORT_MAINT_LABEL`

**Modifiers:**

18. Remove `epbm_fort_reimbursement` modifier definition

## Phase 5: Regenerate & Verify

19. Run the generator to produce new hook files without fort/government building hooks
20. Format all edited `.txt` and `.gui` files with `pdx-format`
21. Verify no stale `fort` references remain in the codebase

## Government Building List (139 total)

### Automated scan (97) — capital-specific, forts, soldier-employing
ablaq_palace, admiralty, alhambra, amsterdam_admiralty, armory, art_school, arts_academy, bailiff, bajang_ratu, barcelona_royal_shipyard, barracks, bastion, bavarian_academy_of_sciences, bey_fortress, brethren_marsh, calmecac, camara_comptos, cantonments, castel_sant_angelo, castle, cawa_barracks, chancery, city_guard, coastal_fort, conscription_center, copenhagen_dockyard, dock, dry_dock, enderun_academy, fortress, fortezza_di_sant_andrea, friesland_admiralty, gallowglass_sept, ghilman_barracks, grand_shipyard, great_enclosure, great_hill_complex, great_valley_complex, hexamilion_wall, house_of_parliament, hsa_burgtor, imperial_halic_shipyards, janissary_barracks, jurchen_barracks, kastellet, kilwan_shipwrights, korean_gunnery_coastal_defense, korean_gunnery_land_defense, kremlin, kronborg, kurmina_headquarter, kurultai, mamluk_barracks, naval_base, naval_battery, north_sea_shipyards, oma_nizwa_fort, order_headquarters, ostrog, peel_towers, pirate_stronghold, pirate_tavern, pukara_building, qalat_al_mashwar, rahdar, red_fort, regimental_camp, repaired_great_wall_of_china, republican_assembly, rotterdam_admiralty, royal_academy_of_arts, royal_atarazanas_seville, royal_court, royal_garden, royal_society, ruined_great_wall_of_china, segovia_artillery_academy, sergeantry, shipyard, sofa_barracks, star_fort, stockade, supreme_court, tambo, telpochcalli, the_bock_fortifications, theodosian_walls, thema_headquarters, tower_of_belem, training_fields, uffizi, venetian_arsenal, venetian_palaces, walls_of_benin, walls_of_ston, war_college, warrior_temple, west_friesland_admiralty, zazzau_walls, zeeland_admiralty, zwinger

### Reclassified from regular (42) — crown power, palaces, military, government admin
ambras_castle, belvedere_palace, berlin_palace, coastal_settlements, construction_center, counting_house, doges_palace, eghabho_nore_mansion, el_escorial_palace, forbidden_city, fortress_church, fortress_granary, galley_barracks, general_archive_of_simancas, grand_apartment, guich_garrison, imperial_city_of_hue, kalari, kings_manor, lieutenancy, local_governor, minting_office, moscow_artillery_yard, munich_residenz_founding, naval_governor, novodevichy_convent, oma_falaj, papal_archives, protected_harbor, quirinal_palace, ribat, ribeira_das_naus, rock_of_monaco, safaviyya_order_hall, sanssouci, schonbrunn_palace, sco_palace_of_holyroodhouse, seljuk_mint, the_cipher_secretary, versailles, viceroyalty, zhixian
