# Estates Pay Building Maintenance

Estates pay their share of building maintenance. The crown is reimbursed accordingly.

## How It Works

Buildings are split into three categories, each with its own maintenance distribution:

- **Regular buildings** — Maintenance is split among estates based on local pop tax burden, weighted by inverted control.
- **Trade capacity buildings** (without output production methods) — Maintenance is split among estates based on global estate power, matching how trade profit is distributed. Not impacted by control.
- **Foreign buildings** — Maintenance is split among estates based on global estate power. Not impacted by control.

Government and military buildings (forts, barracks, admiralties, courts, etc.) are excluded from estate maintenance entirely. These buildings display a "Crown Building" modifier indicating their maintenance is paid directly by the crown.

## Performance, Implementation, and Coding Jargon

*(If you aren't a coder, you really don't need to care about this, but it was a lot of work so I'm telling you anyway.)*

- There is no way to grab a location's building maintenance cost in script, so it all has to be calculated at runtime. There also seems to be no way to get the exact quantity of each good used in a building's maintenance production method. Cue the litany of workarounds.

- Ideally you need building types that point to a set of goods that point to the quantities needed. But variable maps, as amazing as they are, don't support nesting. The workaround is to use lightweight International Organizations as variable containers. A global map points from each building type to an IO representing its production method, and each IO stores two variable maps:
  - **Goods map** — goods to quantity needed. Set once during initialization, never changes.
  - **Cost cache** — market to total cost per building level. Calculated lazily and cleared every month so prices stay current.

- A Python script collects all production methods and buildings that contribute to maintenance and hardcodes them into the initialization script. This handles generating the IOs, assigning their production method recipes to the goods maps, classifying buildings into the three categories, injecting the crown building modifier onto government buildings, and probably some other stuff I'm forgetting.

- On initialization, every location is checked for buildings that contribute to maintenance. Each country stores a variable list of its locations that have relevant buildings, and each of those locations stores variable lists of the buildings that require maintenance (one list per category: regular, trade). Foreign buildings are iterated directly from country scope and don't need location list tracking. Events that fire when the first level of a building is constructed or the last level is destroyed keep these lists updated. Ownership transfer is also handled; when a location changes hands, it is moved between the old and new owner's lists. All of this allows us to iterate over every relevant building without ever missing one or wasting time on locations and buildings we don't care about.

- As a safety net, the full location list is rebuilt from scratch every 10 years. This is still very lightweight compared to the full initialization, which only ever runs once.

- The player country's maintenance is recalculated monthly; AI countries are recalculated yearly. These values are saved to variables on the country and referenced by silent modifiers that pull gold from the estates and reimburse the crown accordingly.

- Mod state is tracked via a location modifier on an impassable location. When the mod is removed, the engine strips the modifier definition, and its absence triggers a full rebuild on re-add. A version variable on the same location detects mod updates.
