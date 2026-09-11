extends RefCounted
##
## The planner, in GDScript. Replaces the frozen Python solver so the mod can ship
## as plain source with no binary.
##
## Two rules shape this file.
##
## First, it never reimplements what it can read: the score comes from the game's
## formula (see scoring.gd), the special cases from special.gd, the efficiency tags
## from TagLibrary. Only the SEARCH is ours.
##
## Second, the inner loop is written for GDScript, not transcribed from Python. A
## knight's six statistics are a PackedInt32Array, not a dictionary keyed by name -
## measured in this game, that one choice is worth a factor of six, and it is what
## makes the search finish faster here than it did as an external process.

const NSTATS := 6
const SPECIAL_SCORE := 99.0        # an unexpected outcome beats any ordinary score
const NO_TEAM_SCORE := -99.0

# ---------------------------------------------------------------- the snapshot
#
# Taken once, on the main thread, before any searching. Everything the search needs
# is copied into plain arrays here so the search itself never touches a game object
# it could accidentally write to.

var knights := []          # index -> knight record
var quests := []           # index -> quest record
var items := []            # index -> item record
var by_ref := {}           # Knight -> index
var for_sale := []         # item indices the shops have, never placed, only advised

var _special = null         # special.gd
var _scoring = null         # scoring.gd


func setup(special_mod, scoring_mod) -> void:
    _special = special_mod
    _scoring = scoring_mod


## Reads the round table into arrays. Returns a one-line summary for the log.
func snapshot() -> String:
    knights.clear()
    quests.clear()
    items.clear()
    by_ref.clear()
    # Cached values are keyed by indices into these arrays: a new snapshot renumbers
    # everything, so anything remembered from the last one is now nonsense.
    _qv_memo.clear()

    for k in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(k):
            continue
        # Read RAW, not through get_statistic_value_from_id(stat, false): that call
        # clamps to 0..15 before the equipment is added, and the game clamps only
        # once, at the end. A knight whose base is negative would start from zero
        # here and come out ahead - OLIVER was reading LUCK 13 where the game said
        # 11, and the planner was sending him out on the strength of it.
        var base := PackedInt32Array()
        base.resize(NSTATS)
        for s in range(NSTATS):
            base[s] = int(k.statistics_value[s]) + int(k.bonus_stats[s])
        by_ref[k] = knights.size()
        knights.append({
            "ref": k,
            "id": String(k.character_ink_id),
            "base": base,
            "chars": k.get_all_characteristics(false),
            "armor": int(k.current_armor),
            "affinity": float(k.current_affinity),
            "worn": [],          # filled below, in item indices
            "welded": [],        # items that cannot be taken off
            "pending": _pending_levels(k),
        })

    # Items: everything owned by a knight, plus everything in the vault. Each one is
    # measured against every knight, because get_stat_value() takes the knight and a
    # passive can make the same relic worth different things to two people.
    var seen := {}
    for rec in knights:
        for eq in rec["ref"].equipments:
            if is_instance_valid(eq) and not seen.has(eq):
                seen[eq] = _add_item(eq)
        for eq in rec["ref"].equipments:
            if not is_instance_valid(eq):
                continue
            rec["worn"].append(seen[eq])
            # An exclusive item is welded on: it can neither be taken off nor
            # handed to anyone else, and a plan that tries makes the mod look
            # broken (EDITH's sword, ARI's griffin).
            if eq.is_exclusive:
                rec["welded"].append(seen[eq])
    for eq in _vault_items():
        if is_instance_valid(eq) and not seen.has(eq):
            seen[eq] = _add_item(eq)
    # What the shops have. Measured like everything else, but deliberately kept OUT
    # of the pool: the planner must never place gear the player has not bought. It
    # only ever appears as advice, with its price attached.
    for_sale.clear()
    var owned_names := {}
    for it in range(items.size()):
        owned_names[items[it]["name"]] = true
    for row in _stock():
        var eq = row["ref"]
        # Already seen means the player HAS it - a shop keeps listing what you own,
        # and the shop entry is often the very same resource. Marking it as stock
        # took it out of the planner's pool: the pool fell from 47 items to 26 and
        # the plans got worse, which is how this surfaced.
        if seen.has(eq) or owned_names.has(String(eq.name)):
            continue
        seen[eq] = _add_item(eq)
        items[seen[eq]]["cost"] = int(row["cost"])
        for_sale.append(seen[eq])

    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var req: Dictionary = _scoring.requirements_of(q)
        var idx := PackedInt32Array()
        var val := PackedInt32Array()
        for stat in req:
            idx.append(int(stat))
            val.append(int(req[stat]))
        var eff: Dictionary = TagLibrary.get_efficiency_tags_for_quest(q)
        var base_d: int = q.base_duration if q.base_duration > 0 else q.duration
        if is_instance_valid(q.selected_modifier):
            base_d += q.selected_modifier.duration_modification
        # What the game stamps as quest.base_duration when the cycle is locked in:
        # the quest's OWN duration, without the cycle modifier. base_d above folds the
        # modifier in because that is what the team's reduction is subtracted from,
        # but SPEEDSTER is graded on base_duration - updated_duration, so on the raw
        # figure. The two are not interchangeable and the difference is half a point
        # per cycle the modifier moves.
        var raw_d: int = q.base_duration if q.base_duration > 0 else q.duration
        # quests_manager decrements the counter BEFORE judging the quest, so 1 means
        # this cycle is the last one this quest has.
        var last_chance: bool = (bool(q.has_deadline)
                                 and int(q.remaining_cycles_before_faillure) <= 1)
        # Only on the last cycle. Before that the quest is not lost, only postponed,
        # and pricing its rewards in would have the planner drop everything for it.
        var lost: float = (_reward_worth(q) if last_chance else 0.0)
        var loc = GameState.world_manager.get_location_from_ID(q.quest_location)
        quests.append({
            "ref": q,
            "id": String(q.quest_id),
            "req_idx": idx,
            "req_val": val,
            "nb": int(q.nb_requested_knights),
            "base_d": base_d,
            "raw_d": raw_d,
            "ultimatum": q.quest_type == Quest.QuestTypes.ULTIMATUM_QUEST,
            "coastal": is_instance_valid(loc) and loc.is_coastal,
            "eff_plus": eff["efficient_tags"],
            "eff_minus": eff["inefficient_tags"],
            "locked": _locked_knights(q),
            "deadline": bool(q.has_deadline),
            "remaining": int(q.remaining_cycles_before_faillure),
            "last_chance": last_chance,
            "lethal": bool(q.quest_can_be_lethal),
            "fail_cost": _fail_cost(q),
            "lost": lost,
            # Settled in the second pass below: the probe needs the quest records to
            # exist before it can score a single team against them.
            "winnable": false,
            "best_score": 0.0,
            # Folded in here so `_skipped_cost` and the search's own skip branch both
            # see it without being told twice.
            "skip_pen": _skip_penalty(q) + lost,
        })
    return "%d knight(s), %d quest(s), %d item(s)" % [knights.size(), quests.size(),
                                                      items.size()]


## Settles which expiring deadlines can still be won, and makes those come first.
##
## Runs inside the search, not in snapshot(): it has to judge on `free_knights` - the
## knights the search can actually place - and not on the whole round table. A quest
## declared winnable by a team that cannot be formed is the worst of both worlds, since
## the plan then pays the priority whatever it does.
func _settle_last_chance() -> void:
    _probe_left = PROBE_EVALS
    for qi in range(quests.size()):
        if not quests[qi]["last_chance"]:
            continue
        var best := _best_possible(qi)
        quests[qi]["best_score"] = best
        var win: bool = (best >= SPECIAL_SCORE
                         or _scoring.outcome_for_score(quests[qi]["ref"], best) > 0)
        quests[qi]["winnable"] = win
        if win:
            quests[qi]["skip_pen"] = LAST_CHANCE_PRIORITY
    # Teams were priced while `winnable` was still false everywhere.
    _qv_memo.clear()


## The best score any team could bring back from this quest.
##
## Every combination, not the ranked shortlist `_teams_for` keeps: a shortlist is a
## performance measure, and this answer decides whether a quest is given up for good.
## Judged with the gear the knights are ALREADY wearing - the gear climb runs later and
## can only improve on it - and without a meal, so a quest called winnable here is
## winnable with no further action from the player.
func _best_possible(qi: int) -> float:
    var forced := PackedInt32Array()
    for ki in quests[qi]["locked"]:
        forced.append(ki)
    var cands := PackedInt32Array()
    for ki in free_knights:
        if not ki in forced:
            cands.append(ki)
    var pick := PackedInt32Array()
    var size_max: int = mini(int(quests[qi]["nb"]), forced.size() + cands.size())
    return _best_walk(qi, cands, 0, forced, pick, size_max, NO_TEAM_SCORE)


func _best_walk(qi: int, cands: PackedInt32Array, start: int, forced: PackedInt32Array,
                pick: PackedInt32Array, size_max: int, best: float) -> float:
    # Bounded like every other search here. Running out means the quest keeps the
    # best score found so far, which can only make it look LESS winnable - the safe
    # direction: the mod may fail to prioritise a quest, it will never force a team
    # onto one nobody could have won.
    if _probe_left <= 0:
        return best
    var team := PackedInt32Array(forced)
    for x in pick:
        team.append(x)
    if team.size() > 0:
        _probe_left -= 1
        var r := score_team(qi, team, _probe_gear(team))
        # An unexpected outcome is not scored, and it is never a failure.
        if bool(r["special"]):
            return SPECIAL_SCORE
        if float(r["score"]) > best:
            best = float(r["score"])
    if team.size() >= size_max:
        return best
    for i in range(start, cands.size()):
        pick.append(cands[i])
        best = _best_walk(qi, cands, i + 1, forced, pick, size_max, best)
        pick.resize(pick.size() - 1)
        if best >= SPECIAL_SCORE:
            return best
    return best


## The gear the team is wearing right now, in the shape score_team() expects.
func _probe_gear(team: PackedInt32Array) -> Array:
    var gear := []
    for ki in team:
        var g := PackedInt32Array()
        for it in knights[ki]["worn"]:
            g.append(it)
        gear.append(g)
    return gear


## True when failing this quest triggers a consequence of the given kind.
func _fails_into(q, kind: int) -> bool:
    for r in q.faillure_consequences:
        if is_instance_valid(r) and r.reward_type == kind:
            return true
    if is_instance_valid(q.selected_modifier):
        for r in q.selected_modifier.faillure_consequences_modification:
            if is_instance_valid(r) and r.reward_type == kind:
                return true
    return false


## What it costs the plan for a quest to RESOLVE AS A FAILURE.
##
## determine_rewards() picks its list with `success_rewards if outcome > 0 else
## faillure_consequences` and looks no closer, so a failure, a major failure and a
## critical failure all cost the SAME consequences - and an unstaffed quest whose
## deadline expires costs them too. One number covers all of it.
func _fail_cost(q) -> float:
    if q.quest_type == Quest.QuestTypes.ULTIMATUM_QUEST:
        return 100.0
    if _fails_into(q, QuestReward.RewardType.LOCATION_DESTROYED):
        return 100.0
    if _fails_into(q, QuestReward.RewardType.CHARACTER_DEATH):
        return 100.0
    if not q.extra_conditions.is_empty():
        return 30.0
    if q.quest_type == Quest.QuestTypes.MAJOR_QUEST:
        # A major quest has no scripted consequence, so the test below misses it - and
        # its content is gone for good, gold and relic included.
        return 8.0
    if not q.faillure_consequences.is_empty():
        return 6.0
    # Nothing scripted happens. What is lost is the reward that was promised.
    return 2.0


## What it costs to leave a quest to NOBODY this cycle.
##
## Almost every quest simply waits, and skipping one is a nudge at most. A quest whose
## deadline expires now does not wait: it resolves unstaffed, as a critical failure,
## consequences and all (Quest.determine_outcome, first branch). So skipping THAT one
## costs exactly what failing it costs - no more, and no less.
##
## No more matters as much as no less. Charging a flat refusal here was my own first
## fix and it was worse than the bug: a single knight dropped on a doomed emergency
## dodged the penalty, changed nothing the quest resolves with, and paid for the
## privilege in armour. The two costs have to be equal for the search to see that
## sending nobody and sending a team that fails are the same act.
func _skip_penalty(q) -> float:
    # quests_manager decrements the counter BEFORE judging the quest, so 1 means this
    # cycle is the last one it has.
    var deadline: bool = bool(q.has_deadline)
    var last_chance: bool = deadline and int(q.remaining_cycles_before_faillure) <= 1
    if q.quest_type == Quest.QuestTypes.ULTIMATUM_QUEST:
        return 100.0
    if last_chance:
        return _fail_cost(q)
    # Still time. These are urgency, not consequence: a looming deadline should pull
    # the quest forward without commandeering the cycle, and forcing one always cost
    # a whole cycle when two of them were open at once.
    if deadline and _fails_into(q, QuestReward.RewardType.LOCATION_DESTROYED):
        return 12.0
    if not q.extra_conditions.is_empty():
        return 30.0
    if deadline and q.quest_type == Quest.QuestTypes.MAJOR_QUEST:
        return 8.0
    if deadline and not q.faillure_consequences.is_empty():
        return 6.0
    return 0.5


## What winning this quest actually pays, in the same units as everything else.
##
## Only ever asked of a quest on its LAST cycle, where the question stops being "what
## does failing cost" and becomes "what is gone for good". `_fail_cost` answers the
## first and knows nothing of the second: quest_victoria_gank carries no failure
## consequence at all, so walking away from it was priced at 2 points - while winning
## it handed over two relics and a branch of the story.
func _reward_worth(q) -> float:
    var rewards := []
    for r in q.success_rewards:
        rewards.append(r)
    if is_instance_valid(q.selected_modifier):
        for r in q.selected_modifier.success_rewards_modification:
            rewards.append(r)
    var v := 0.0
    for r in rewards:
        if not is_instance_valid(r):
            continue
        # `amount` exists only on the variants that have one - the resource builds its
        # property list from `reward_type` - so the type is read first, never a field.
        var t: int = int(r.reward_type)
        if (t == QuestReward.RewardType.RELIC or t == QuestReward.RewardType.MOUNT
                or t == QuestReward.RewardType.QUEST_ITEM):
            v += 6.0
        elif t == QuestReward.RewardType.CONSUMABLE:
            v += 1.0
        elif t == QuestReward.RewardType.FUNDS:
            v += float(r.amount) * 0.02
        elif t == QuestReward.RewardType.SATISFACTION:
            v += float(r.amount)
        elif (t == QuestReward.RewardType.CHARACTER_TAG
                or t == QuestReward.RewardType.SOVEREIGN_TAG):
            v += 3.0
        else:
            # A story variable, a follow-up audience, a tax: worth having, and the mod
            # has no honest way to price a branch of the plot any finer than that.
            v += 2.0
    return v


## What the quests absent from a plan cost it.
func _skipped_cost(staffed: Dictionary) -> float:
    var c := 0.0
    for qi in range(quests.size()):
        if not staffed.has(qi):
            c += float(quests[qi]["skip_pen"])
    return c


func _add_item(eq) -> int:
    var per_knight := []
    for rec in knights:
        var arr := PackedInt32Array()
        arr.resize(NSTATS)
        for s in range(NSTATS):
            arr[s] = eq.get_stat_value(s, rec["ref"])
        per_knight.append(arr)
    var tags := []
    for t in eq.tags:
        tags.append(t)
    items.append({
        "ref": eq,
        "name": String(eq.name),
        "stats": per_knight,
        "tags": tags,
        "dur": int(eq.duration_reduction),
        "slot": int(eq.equipment_type),
        "exclusive": bool(eq.is_exclusive),
    })
    return items.size() - 1


## Level-ups the player has earned and not yet spent.
##
## Compared against the NEXT level's threshold, not the current one: a knight who
## has just reached level 10 is sitting on level 10's threshold by definition, so
## testing that would report every knight as owing a point.
func _pending_levels(k) -> int:
    var thr = LevelUpManager.level_xp_threshold
    if not (thr is Dictionary):
        return 0
    var lvl: int = int(k.current_level)
    var xp: int = int(k.current_xp)
    var n := 0
    while lvl < Knight.MAX_LEVEL and thr.has(lvl + 1) and int(thr[lvl + 1]) <= xp:
        lvl += 1
        n += 1
    return n


## Equipment sitting in the tower rather than on a knight.
func _vault_items() -> Array:
    return GameState.inventory_manager.get_all_available_items()


## Never advised, at the player's request.
const BANNED := ["WISH_GRANTING_LAMP"]


## What the shops are selling that the player could actually pay for.
##
## Stock lives on InventoryManager as one dictionary per shop and per act, keyed by
## the equipment itself. The value carries the requirement: when its `item` is set,
## the thing is bought with a QUEST ITEM and not with gold, and advising it as if it
## cost money sends the player after a demon heart they do not have.
## Whether the shop will actually sell this, or is showing it behind a padlock.
##
## The three tests are the game's own, lifted from shop_slot.gd - the very code that
## draws the lock - rather than reimplemented. KELPIE was being advised at 160 gold
## while the stable had it gated behind Scholars 15, which is exactly the kind of
## advice that is worse than none.
func _requirement_met(req) -> bool:
    if not is_instance_valid(req):
        return true
    # `population_category`, `county_id` and `sovereign_tag` only EXIST on their own
    # variant - the resource builds its property list from `type` - so the type is
    # read first, never the field.
    match int(req.type):
        EquipmentRequirement.Types.None:
            return true
        EquipmentRequirement.Types.Satisfaction:
            var sm = GameState.satisfaction_manager
            var cat: String = sm.convert_population_category_to_string(
                req.population_category)
            return sm.is_satisfaction_high_enough(cat, int(req.amount))
        EquipmentRequirement.Types.County:
            var wm = GameState.world_manager
            var county = wm.get_county_by_id(req.county_id)
            return is_instance_valid(county) and county in wm.rallied_counties
        EquipmentRequirement.Types.SovereignTag:
            var tag: String = TagManager.get_string_from_sovereign_tag(req.sovereign_tag)
            return GameState.sovereign_manager.does_sovereign_has_tag(tag, int(req.amount))
    return true


func _stock() -> Array:
    var im = GameState.inventory_manager
    var out := []
    # The game merges its own act tiers, in get_forge_items() and its two siblings.
    # Rebuilding that here was an off-by-one that emptied the shops outright: Acts.ONE
    # is 0, so the old `for a in range(1, act + 1)` ran ZERO iterations through the
    # whole of act I - the mod went a full playthrough without ever advising a
    # purchase - and act II silently missed its own tier. Asked, not rebuilt.
    var shelves := []
    for getter in ["get_forge_items", "get_stables_items", "get_witch_tower_items"]:
        if im.has_method(getter):
            shelves.append(im.call(getter))
    for d in shelves:
        if not (d is Dictionary):
            continue
        for eq in d:
            if not is_instance_valid(eq):
                continue
            if String(eq.name) in BANNED:
                continue
            var kind: int = int(eq.equipment_type)
            # Consumables are single use and the player arbitrates those; the mod
            # cannot place them anyway.
            if (kind != Equipment.EquipmentsTypes.RELIC
                    and kind != Equipment.EquipmentsTypes.MOUNT):
                continue
            # An entry whose cost is ANOTHER relic is a trade-in, and the mod has no
            # way to weigh what it would be giving up. Left to the player.
            var req = d[eq]
            if is_instance_valid(req) and "item" in req and int(req.item) != 0:
                continue
            if not _requirement_met(req):
                continue
            out.append({"ref": eq, "cost": int(eq.cost)})
    return out


## Knights the quest imposes. They are already on it and cannot be moved.
func _locked_knights(q) -> Array:
    var out := []
    for prop in ["locked_knights", "requested_knights"]:
        if prop in q:
            var v = q.get(prop)
            if v is Array:
                for k in v:
                    if is_instance_valid(k) and by_ref.has(k):
                        out.append(by_ref[k])
                break
    return out


# ------------------------------------------------------------------- evaluation

## A team member's statistics once the loadout is on. Clamped 0..15 exactly as
## Knight.get_statistic_value_from_id() does - the cap is part of the rules, not a
## safety measure.
func stats_of(ki: int, gear: PackedInt32Array) -> PackedInt32Array:
    var out: PackedInt32Array = knights[ki]["base"].duplicate()
    for it in gear:
        var add: PackedInt32Array = items[it]["stats"][ki]
        for s in range(NSTATS):
            out[s] += add[s]
    for s in range(NSTATS):
        out[s] = clampi(out[s], 0, 15)
    return out


## Characteristics once the loadout is on: the knight's own, plus one entry per tag
## the gear grants. Gear tags always count as known.
func chars_of(ki: int, gear: PackedInt32Array) -> Dictionary:
    var out: Dictionary = knights[ki]["chars"].duplicate()
    for it in gear:
        for t in items[it]["tags"]:
            out[t] = true
    return out


## Quest.QuestsManager.calculate_updated_duration(), for a team that does not exist
## yet. The team keeps the SLOWEST member's reduction, not the sum - except that a
## BAYARD carrier imposes his own on everyone, which is the whole point of it.
func duration_of(qi: int, team: PackedInt32Array, gear: Array,
                 chars_by_member: Array = []) -> int:
    var q = quests[qi]
    if q["ultimatum"]:
        return 1
    # A reduction can be NEGATIVE - GUIGNOL carries -1 and lengthens the quest - so
    # "not set yet" cannot be spelled as -1. It was, and every negative reduction was
    # quietly rounded back up to zero: the planner and the shop advice both believed
    # a mount that costs the team a cycle was free, and the panel kept offering it.
    var slowest := 0
    var have := false
    var bayard := 0
    var has_bayard := false
    for j in range(team.size()):
        var red := 0
        var is_bayard := false
        var chars: Dictionary = (chars_by_member[j] if j < chars_by_member.size()
                                 else chars_of(team[j], gear[j]))
        for it in gear[j]:
            red += items[it]["dur"]
        for t in chars:
            if t == TagManager.CharacterTags.WOLF_HABITS:
                red += 1
            elif t == TagManager.CharacterTags.KELPIE and q["coastal"]:
                red += 1
            elif t == TagManager.CharacterTags.BAYARD:
                is_bayard = true
        # The game reads the Bayard carrier's reduction once the characteristics have
        # all been counted, not in the middle of counting them.
        if is_bayard:
            bayard = red
            has_bayard = true
        if not have or red < slowest:
            slowest = red
            have = true
    if has_bayard and slowest < bayard:
        slowest = bayard
    return maxi(1, q["base_d"] - slowest)


## Whether one of the quest's special outcomes fires. Ported from
## SpecialOutcome.are_conditions_met() so it can be asked about a team that is only
## being considered, with gear it is not wearing.
func special_fires(qi: int, team: PackedInt32Array, gear: Array,
                   stats_by_member: Array = [], chars_by_member: Array = []) -> bool:
    return special_hit(qi, team, gear, stats_by_member, chars_by_member) != null


## The outcome that fires, so it can be GRADED rather than merely counted. Returns
## null when none of them does.
func special_hit(qi: int, team: PackedInt32Array, gear: Array,
                 stats_by_member: Array = [], chars_by_member: Array = []):
    var q = quests[qi]["ref"]
    var pot: Array = q.special_outcomes.duplicate()
    if is_instance_valid(q.selected_modifier):
        pot.append_array(q.selected_modifier.unexpected_outcomes)
    if pot.is_empty():
        return null
    var refs := []
    for ki in team:
        refs.append(knights[ki]["ref"])
    for so in pot:
        if not is_instance_valid(so):
            continue
        if _outcome_met(so, team, gear, refs, stats_by_member, chars_by_member):
            return so
    return null


## What an unexpected outcome is actually worth.
##
## SPECIAL_SCORE for every one of them was the old answer, and `magpie_goberto` is
## why it could not stand: 100 damage, no reward whatsoever, and the search rated it
## above any critical success it could have had instead. An unexpected outcome is not
## a prize, it is just an outcome - and one of them is a funeral.
##
## Only the bad ones are re-priced. Anything that is not clearly a loss keeps the old
## value, so this can remove a catastrophe from a plan but never quietly reshuffle a
## good one.
func _outcome_worth(so, qi: int, team: PackedInt32Array) -> float:
    # The outcome brings its OWN damage range and it replaces the quest's
    # (Quest.determine_damages), dealt to everyone sent. Against a knight's armour,
    # that is the whole question.
    if is_instance_valid(so.damage_range) and quests[qi]["lethal"]:
        var hit: int = int(so.damage_range.max)
        for ki in team:
            if hit >= int(knights[ki]["armor"]):
                return -DEATH_PENALTY
    var net := 0.0
    for r in so.rewards:
        if not is_instance_valid(r):
            continue
        var t: int = r.reward_type
        if (t == QuestReward.RewardType.LOCATION_DESTROYED
                or t == QuestReward.RewardType.CHARACTER_DEATH
                or t == QuestReward.RewardType.CURRENT_KNIGHT_DEMISSION):
            return -DEATH_PENALTY
        elif t == QuestReward.RewardType.FUNDS:
            net += float(r.amount) * 0.02
        elif (t == QuestReward.RewardType.SATISFACTION
                or t == QuestReward.RewardType.AFFINITY):
            net += float(r.amount)
        else:
            # An item, a story variable, an audience: worth having.
            net += 2.0
    if net < 0.0:
        return net
    return SPECIAL_SCORE


func _outcome_met(so, team: PackedInt32Array, gear: Array, refs: Array,
                  stats_by_member: Array = [], chars_by_member: Array = []) -> bool:
    # The decompiled source flattens indentation, so where this test ends was a
    # guess. Read as a top-level test it fired on a quest the game leaves alone,
    # which settles it: the whole thing hangs off "the outcome names knights".
    # An outcome that names nobody never triggers.
    if so.knights.is_empty():
        return false
    # A TYPED outcome OVERRIDES are_conditions_met() outright - Gwendan's weighs
    # is_reformed, Arron's weighs his state - and everything below this point knows
    # nothing about that. It saw "the outcome names Gwendan", answered yes, and the
    # search went chasing a 99-point outcome the quest was never going to produce:
    # Gwendan came back nailed to a contract worth 0.69.
    #
    # None of those subclasses look at equipment, so the game can simply be asked -
    # which is what this file should have done from the start. Only the BASE class
    # needs the port below, because only it tests statistics and traits, and those do
    # move with gear a candidate team is not yet wearing.
    var scr = so.get_script()
    if scr != null and String(scr.resource_path).get_file() != "special_outcome.gd":
        # Array[Knight], not Array. Handed a plain one, are_conditions_met() answers
        # "no" on a quest that does trigger - this mod has paid for that twice.
        var typed: Array[Knight] = []
        for k in refs:
            typed.append(k)
        return so.are_conditions_met(typed)
    if so.for_traitor_plot:
        var traitor = GameState.character_manager.traitors_plot_manager.get_traitor()
        for k in so.knights:
            if k == traitor and k in refs:
                return true
        return false
    for k in so.knights:
        if not k in refs:
            return false
    if so.required_knight_characteristics.is_empty():
        return true
    if so.amount > 0:
        for j in range(team.size()):
            var st_: PackedInt32Array = (stats_by_member[j] if j < stats_by_member.size()
                                         else stats_of(team[j], gear[j]))
            var v: int = st_[so.stat]
            if so.requires_higher and v < so.amount:
                continue
            elif not so.requires_higher and v > so.amount:
                continue
            return true
        return false
    var present := {}
    for j in range(team.size()):
        var ch: Dictionary = (chars_by_member[j] if j < chars_by_member.size()
                              else chars_of(team[j], gear[j]))
        for t in ch:
            present[t] = true
    for c in so.required_knight_characteristics:
        if not c in present:
            return false
    return true


## The score a team would get, following Quest.determine_outcome() exactly.
##
## `team` holds knight indices, `gear` an equal-length Array of PackedInt32Array of
## item indices. `fed` holds the indices of knights who have eaten.
func score_team(qi: int, team: PackedInt32Array, gear: Array,
                fed: PackedInt32Array = PackedInt32Array()) -> Dictionary:
    var q = quests[qi]
    if team.is_empty():
        return {"score": NO_TEAM_SCORE, "special": false, "duration": q["base_d"]}
    # Statistics and characteristics were being rebuilt three times per evaluation -
    # once for the duration, once for the special outcomes, once for the score. Built
    # once here and passed down instead; that alone took the plan from 13 s to under 3.
    var stats_by := []
    var chars_by := []
    for j in range(team.size()):
        stats_by.append(stats_of(team[j], gear[j]))
        chars_by.append(chars_of(team[j], gear[j]))
    var dur := duration_of(qi, team, gear, chars_by)
    var hit = special_hit(qi, team, gear, stats_by, chars_by)
    if hit != null:
        return {"score": _outcome_worth(hit, qi, team), "special": true,
                "duration": dur}

    var nb: int = q["nb"]
    var divider: float = 1.0 + min(0, float(team.size() - 1)) / 2.0
    var missing: int = nb - team.size()
    var multiplier: float = 1.0 / divider
    var refs := []
    for ki in team:
        refs.append(knights[ki]["ref"])

    # Kept per knight rather than accumulated: the protagonist rule below compares
    # team-mates against each other, so it needs the individual figures first.
    var subtotal := PackedFloat64Array()
    subtotal.resize(team.size())
    var protagonist := -1
    for j in range(team.size()):
        var ki: int = team[j]
        var stats: PackedInt32Array = stats_by[j]
        var chars: Dictionary = chars_by[j]

        var presence: float = snappedf(10.0 / float(nb), 0.01)
        if nb == 3 and j % 3 == 0:
            presence += 0.01
        elif nb == 6 and j % 3 == 0:
            presence -= 0.01
        var stat_total := 0.0

        for r in range(q["req_idx"].size()):
            var need: int = q["req_val"][r]
            var requirement: float = float(need) + 1.0 * missing
            var sc: float = (float(stats[q["req_idx"][r]]) - requirement) * 0.66
            if is_equal_approx(sc, 0.0):
                sc = 0.22
            sc = sc / float(team.size()) * multiplier
            if sc > 0.0:
                sc *= 1 + need * 0.0275
            else:
                sc *= 1 + need * 0.01
            stat_total += snappedf(sc, 0.01)

        # KnightScore keeps FOUR dictionaries keyed by tag, and sums all of them.
        # Keyed by tag is the part that matters: a special case for a tag that is
        # also an efficiency tag OVERWRITES the +/-1 instead of adding to it. Summing
        # the two was worth a spurious +1 per tag, +3 on one quest here.
        var known_bonus := {}
        var unknown_bonus := {}
        var known_malus := {}
        var unknown_malus := {}
        for t in q["eff_plus"]:
            if t in chars:
                if chars[t]:
                    known_bonus[t] = 1.0
                else:
                    unknown_bonus[t] = 1.0
        for t in q["eff_minus"]:
            if t in chars:
                # Note the game files these under BONUSES too, with a value of -1.
                if chars[t]:
                    known_bonus[t] = -1.0
                else:
                    unknown_bonus[t] = -1.0

        var cases: Dictionary = _special.for_knight(knights[ki]["ref"], q["ref"], chars,
                                                    refs, q["raw_d"], dur, stats)
        for tag in cases:
            var sc2: float = float(cases[tag]["score"])
            if bool(cases[tag]["known"]):
                if sc2 > 0:
                    known_bonus[tag] = sc2
                else:
                    known_malus[tag] = sc2
            else:
                if sc2 > 0:
                    unknown_bonus[tag] = sc2
                else:
                    unknown_malus[tag] = sc2
        var mine := presence
        if ki in fed:
            mine += Meal.SCORE_GAIN
        mine += stat_total
        for d in [known_bonus, unknown_bonus, known_malus, unknown_malus]:
            for tag in d:
                mine += float(d[tag])
        subtotal[j] = mine
        if TagManager.CharacterTags.PROTAGONIST in chars:
            protagonist = j

    # The protagonist gets a point for being outshone by a team-mate. Applied after
    # everyone is graded, because it compares the totals.
    if protagonist >= 0:
        for j in range(subtotal.size()):
            if subtotal[j] > subtotal[protagonist]:
                subtotal[protagonist] += 1.0
                break

    var total := -10.0
    for v in subtotal:
        total += v
    for extra in q["ref"].extra_conditions:
        if is_instance_valid(extra) and extra.is_condition_met():
            total += 2.0

    return {"score": total, "special": false, "duration": dur,
            "per_knight": subtotal, "protagonist": protagonist}


# --------------------------------------------------------------- self-checking

## Compares the INPUTS of the fast path against the game's own, for the board as it
## stands: the six statistics, and the set of characteristics. If a score disagrees
## it is either because the inputs differ or because the formula does, and there is
## no point arguing about the formula until this comes back empty.
func verify_inputs() -> Array:
    var problems := []
    for ki in range(knights.size()):
        var k = knights[ki]["ref"]
        var gear := PackedInt32Array()
        for it in knights[ki]["worn"]:
            gear.append(it)
        var mine := stats_of(ki, gear)
        for st_ in range(NSTATS):
            var theirs: int = k.get_statistic_value_from_id(st_, true)
            if mine[st_] != theirs:
                # The gap alone said nothing usable: with an empty board every knight
                # matched, so the fault is in what the gear contributes, not in the
                # base. So the line now carries the breakdown - base, then each worn
                # item and what it is credited with - which is the only way to see
                # WHICH item is being under-counted.
                var parts := PackedStringArray()
                parts.append("base %d" % int(knights[ki]["base"][st_]))
                for it in gear:
                    parts.append("%s %+d" % [items[it]["name"],
                                             int(items[it]["stats"][ki][st_])])
                var game_items := PackedStringArray()
                for eq in k.equipments:
                    if is_instance_valid(eq):
                        game_items.append("%s %+d" % [String(eq.resource_path).get_file(),
                                                      int(eq.get_stat_value(st_, k))])
                problems.append("%s: stat %s = %d, game says %d\n      mine: %s\n      game: %s" % [
                    knights[ki]["id"], Knight.Statistics.keys()[st_], mine[st_], theirs,
                    ", ".join(parts), ", ".join(game_items)])
        var my_chars := chars_of(ki, gear)
        var their_chars: Dictionary = k.get_all_characteristics()
        for t in my_chars:
            if not t in their_chars:
                problems.append("%s: tag %s invented" % [
                    knights[ki]["id"], TagManager.CharacterTags.keys()[t]])
        for t in their_chars:
            if not t in my_chars:
                problems.append("%s: tag %s missing" % [
                    knights[ki]["id"], TagManager.CharacterTags.keys()[t]])
    return problems


## The quest durations the planner works with.
##
## The game leaves base_duration and updated_duration at -1 until the cycle is
## resolved, so anything that reads them BEFORE that - and three special cases do,
## SPEEDSTER, PATIENT and OVERWORKED - gets an answer that has nothing to do with
## the quest. The planner computes them instead, which is what the game will end up
## with. scoring.gd, asking the game directly, is the one that cannot know yet.
const DURATION_TAGS := [TagManager.CharacterTags.SPEEDSTER,
                        TagManager.CharacterTags.PATIENT,
                        TagManager.CharacterTags.OVERWORKED]


## True when a knight carries a tag whose value depends on a duration the game has
## not computed yet, so a disagreement with scoring.gd is expected there.
func duration_tag_on(ki: int, gear: PackedInt32Array) -> bool:
    var chars := chars_of(ki, gear)
    for t in DURATION_TAGS:
        if t in chars:
            return true
    return false


## Grades the board as it actually stands, through the fast path, and hands back the
## figures so they can be held against scoring.gd - which asks the game directly.
## The two must agree before the search is allowed to trust this file.
func verify_against_scoring() -> Array:
    var rows := []
    for qi in range(quests.size()):
        var q = quests[qi]
        var team := PackedInt32Array()
        var gear := []
        var fed := PackedInt32Array()
        var refs := []
        for k in q["ref"].assigned_knights:
            if not is_instance_valid(k) or not by_ref.has(k):
                continue
            var ki: int = by_ref[k]
            team.append(ki)
            var g := PackedInt32Array()
            for it in knights[ki]["worn"]:
                g.append(it)
            gear.append(g)
            if k.has_eaten:
                fed.append(ki)
            refs.append(k)
        if team.is_empty():
            continue
        var fast := score_team(qi, team, gear, fed)
        var slow: Dictionary = _scoring.score(q["ref"], refs)
        var mine := PackedFloat64Array()
        if fast.has("per_knight"):
            mine = fast["per_knight"]
        var theirs := PackedFloat64Array()
        var names := PackedStringArray()
        for k in refs:
            names.append(String(k.character_ink_id))
            var per: Dictionary = slow.get("per_knight", {})
            theirs.append(0.0 if not per.has(k) else float(per[k].get_total_score()))
        var duration_tags := false
        for j in range(team.size()):
            if duration_tag_on(team[j], gear[j]):
                duration_tags = true
        rows.append({
            "id": q["id"],
            "duration_tags": duration_tags,
            "names": names,
            "fast_each": mine,
            "slow_each": theirs,
            "protagonist": int(fast.get("protagonist", -1)),
            "fast": float(fast["score"]),
            "slow": (SPECIAL_SCORE if bool(slow["special"]) else float(slow["score"])),
            "special_fast": bool(fast["special"]),
            "special_slow": bool(slow["special"]),
            "duration": int(fast["duration"]),
        })
    return rows


# ------------------------------------------------------------------- the search
#
# Objective, in the order the player asked for it:
#   1. bring a quest down to a single cycle - worth more than score, but never at
#      the price of dropping below a plain success;
#   2. the score itself;
#   3. affinity gained, purely to break ties.

## An outcome at or below -10 deals 100 damage to everyone sent
## (Quest.determine_damages), which on a quest that can be lethal is a funeral, not a
## setback. Nothing a plan can win is worth one, so this is a veto rather than a
## weight.
const DEATH_PENALTY := 200.0

const ONE_CYCLE_BONUS := 8.0
const DURATION_WEIGHT := 1.5
const AFFINITY_TIEBREAK := 0.0002

## How much a purchase has to be worth before it is mentioned at all. Below this it
## is noise: a tenth of a point for a hundred gold is not a recommendation.
const BUY_MIN_GAIN := 0.30

## How close an ultimatum has to be before its price is put aside. The player's own
## rule: two cycles out or less.
const ULTIMATUM_HORIZON := 2

## What it costs a plan to come home without a deadline it could still have won.
##
## Large enough that no ordinary quest outbids it: on its last cycle, a winnable quest
## comes first. It is charged identically whether the quest is left empty or handed a
## team that loses, so the only way out from under it is to actually win.
const LAST_CHANCE_PRIORITY := 100.0

## Ceiling on the winnability probe, in teams scored, for the whole cycle.
const PROBE_EVALS := 20000

var _probe_left := 0
# Kept PER TEAM SIZE, not overall. The one-cycle bonus is paid per knight, so a
# single ranked list is swept by the biggest teams and every solo falls off the end
# - and a solo is exactly what lets a fourth quest be staffed when ten knights have
# to cover twelve seats. The planner was quietly dropping a quest because of it.
const TEAMS_PER_SIZE := 16
# The candidate generator ranks with OPTIMISTIC gear, which promises the same mount
# to several quests at once - a bound, not a score. Over-optimism is the right error
# for a generator (it never hides a good plan), but only if enough finalists survive
# to be equipped for real and compared honestly. Twelve was too few and the planner
# shipped a three-quest plan with a knight left idle.
const PLANS_KEPT := 40
# Six quests times forty teams is four billion arrangements in the worst case. The
# disjointness test kills most of them early, but not all: a cap keeps a pathological
# board from hanging the plan.
const MAX_NODES := 400000
const MAX_CLIMB_PASSES := 40

var _nodes := 0
var _qv_memo := {}

## Free memory, in MB, below which the planner stops climbing and ships what it has.
##
## A minidump settled what guesswork could not: the process had committed 36 GB on a
## 32 GB machine, in private regions of 3.7, 6.3, 9.5 and 14.3 GB - something growing
## without bound and being copied on every reallocation - and died writing into a page
## the system had refused to commit. Until that growth is found and stopped, this turns
## a lost session into a slightly worse plan.
const MEMORY_FLOOR_MB := 3000


## Free physical memory in MB, or -1 when the engine will not say.
func _free_mb() -> int:
    if not OS.has_method("get_memory_info"):
        return -1
    var info: Dictionary = OS.get_memory_info()
    # "available" is what this process may still commit; "free" is the machine's idle
    # RAM. The first is the one that runs out.
    var v = info.get("available", info.get("free", -1))
    if typeof(v) != TYPE_INT and typeof(v) != TYPE_FLOAT:
        return -1
    if float(v) < 0.0:
        return -1
    return int(float(v) / 1048576.0)


## Ceiling on that cache, in entries.
##
## It had none, and that is what killed the game: the equipment climb asks about a
## different loadout every time, so with 42 items in the pool it minted a fresh key
## per attempt and the dictionary grew without end. The trace showed it plainly -
## climbs slowing from 70 ms to 5.8 s, then the process gone without an error line,
## which is what running out of memory looks like. Emptying it costs a few repeated
## evaluations; not emptying it costs the session.
const QV_MEMO_MAX := 200000
# The level-up advice raises a statistic without touching the team or the gear, so
# the cache key would not change and every trial would get the same stale answer.
var _memo_off := false
# Branch and bound. _suffix[at] is the most the quests from `at` onwards could add if
# they never competed for a knight; _cut is what a plan must beat to be worth keeping.
# Without them the search walked 400 000 arrangements, hit its own ceiling and still
# took ten seconds - and being cut off at a ceiling means the answer is arbitrary.
var _suffix := PackedFloat64Array()
var _cut := -INF

# Outcome ranks, so "never below a success" can be tested.
const RANK_FAIL := 0
const RANK_SUCCESS := 1

var free_knights := PackedInt32Array()
var pool := PackedInt32Array()          # movable items, by index


## Knights that can be placed, and equipment that can be moved.
func _prepare() -> void:
    free_knights = PackedInt32Array()
    for ki in range(knights.size()):
        var k = knights[ki]["ref"]
        if k.is_dead:
            continue
        # Busy on a quest that is not on the board any more: out of reach.
        if is_instance_valid(k.assigned_quest) and not _quest_index(k.assigned_quest) >= 0:
            continue
        free_knights.append(ki)
    var welded := {}
    for rec in knights:
        for it in rec["welded"]:
            welded[it] = true
    var on_sale := {}
    for it in for_sale:
        on_sale[it] = true
    pool = PackedInt32Array()
    for it in range(items.size()):
        if items[it]["exclusive"] or welded.has(it):
            continue
        # Shop stock is measured, never placed. Letting it into the pool would have
        # the planner hand out relics the player has not bought.
        if on_sale.has(it):
            continue
        if items[it]["slot"] != Equipment.EquipmentsTypes.RELIC                 and items[it]["slot"] != Equipment.EquipmentsTypes.MOUNT:
            continue
        pool.append(it)


## Items worth trying on a given quest: anything that saves a cycle, carries a tag,
## or adds to a statistic the quest actually asks for.
func _relevant_items(qi: int, team: PackedInt32Array) -> PackedInt32Array:
    var q = quests[qi]
    var out := PackedInt32Array()
    for it in pool:
        var item = items[it]
        if item["dur"] != 0 or not item["tags"].is_empty():
            out.append(it)
            continue
        var useful := false
        for ki in team:
            var add: PackedInt32Array = item["stats"][ki]
            for r in range(q["req_idx"].size()):
                if add[q["req_idx"][r]] != 0:
                    useful = true
                    break
            if useful:
                break
        if useful:
            out.append(it)
    return out


func _quest_index(q) -> int:
    for qi in range(quests.size()):
        if quests[qi]["ref"] == q:
            return qi
    return -1


## What a quest contributes to the plan's value. The score alone would trade a
## cycle for a fraction of a point, which is the opposite of what the player wants.
func quest_value(qi: int, team: PackedInt32Array, gear: Array,
                 fed: PackedInt32Array = PackedInt32Array()) -> float:
    if team.is_empty():
        return 0.0
    # The equipment climb asks about the same quest again and again, changing one
    # item at a time. A packed array hashes by content, so the key is cheap to build
    # and the repeats cost nothing.
    var key := PackedInt32Array([qi])
    key.append_array(team)
    for g in gear:
        key.append(-1)
        key.append_array(g)
    key.append(-2)
    key.append_array(fed)
    var hit = (null if _memo_off else _qv_memo.get(key))
    if hit != null:
        return float(hit)
    var r := score_team(qi, team, gear, fed)
    var base_d: int = quests[qi]["base_d"]
    var real_d: int = int(r["duration"])
    var v: float = float(r["score"])
    # An unexpected outcome is not scored at all, and it is never a failure.
    var outcome: int = (Quest.QuestOutcomes.UNEXPECTED_OUTCOME if bool(r["special"])
                        else _scoring.outcome_for_score(quests[qi]["ref"],
                                                        float(r["score"])))
    if outcome > 0:
        # Cycles saved only count on a quest that is going to be WON. Three cycles cut
        # off a failure save nothing, and paying a mount for them is precisely how one
        # knight alone on a doomed emergency came to look like a good idea: -4.73 for
        # the quest, +4.5 for the mount that shortened it, and the search called it
        # progress.
        v += DURATION_WEIGHT * float(team.size()) * float(base_d - real_d)
        if base_d > 1 and real_d <= 1:
            v += ONE_CYCLE_BONUS * float(team.size())
    else:
        # Sending a team that FAILS spares the quest nothing: determine_rewards() hands
        # out the same consequences an empty quest would have triggered. Charging it
        # here too makes walking away and failing cost the same, which is what stops a
        # knight being posted to dodge a penalty he does not actually dodge.
        if bool(quests[qi]["winnable"]):
            # Exactly what walking away costs, to the point. A quest that CAN be won
            # on its last cycle has to be won: a team that loses it forfeits precisely
            # what an empty quest forfeits, so the search can never buy its way out by
            # posting someone doomed - the shape of the worst bug this mod has had.
            v -= LAST_CHANCE_PRIORITY
        else:
            # Nothing left to save. What is forfeited is forfeited either way, so the
            # two costs stay equal and walking away stays honest.
            v -= float(quests[qi]["fail_cost"]) + float(quests[qi]["lost"])
        if outcome == Quest.QuestOutcomes.CRITICAL_FAILURE and quests[qi]["lethal"]:
            v -= DEATH_PENALTY * float(team.size())
    v += AFFINITY_TIEBREAK * _affinity_gain(team, r)
    if not _memo_off:
        if _qv_memo.size() >= QV_MEMO_MAX:
            _qv_memo.clear()
        _qv_memo[key] = v
    return v


## Affinity the team would come home with. Last tie-break, nothing more.
func _affinity_gain(team: PackedInt32Array, r: Dictionary) -> float:
    var per := 0.0
    if bool(r["special"]):
        per = 0.0
    elif float(r["score"]) >= 10.0:
        per = 1.5
    elif float(r["score"]) > 5.0:
        per = 0.75
    if per == 0.0:
        return 0.0
    var total := 0.0
    for ki in team:
        var cur: float = knights[ki]["affinity"]
        total += minf(10.0, cur + per) - cur
    return total


## The loadout a team wears if nothing is moved: welded items only.
func _welded_gear(team: PackedInt32Array) -> Array:
    var gear := []
    for ki in team:
        var g := PackedInt32Array()
        for it in knights[ki]["welded"]:
            g.append(it)
        gear.append(g)
    return gear


## The gear a team would wear at best: what is welded on, plus the fastest mount
## still free for anyone without one.
##
## Ranking on welded gear alone was a real mistake, not a shortcut: reaching one
## cycle is worth eight points PER KNIGHT, so a trio that only gets there once it is
## mounted scored far below a lone knight who already owns a griffin. The planner
## sent ARI out by himself and left two knights idle. The ranking has to see what a
## team could become, not only what it is wearing right now.
func _optimistic_gear(team: PackedInt32Array) -> Array:
    var mounts := []
    for it in pool:
        if items[it]["slot"] == Equipment.EquipmentsTypes.MOUNT:
            mounts.append(it)
    mounts.sort_custom(func(a, b): return items[a]["dur"] > items[b]["dur"])
    var gear := []
    var next_mount := 0
    for ki in team:
        var g := PackedInt32Array()
        var has_mount := false
        for it in knights[ki]["welded"]:
            g.append(it)
            if items[it]["slot"] == Equipment.EquipmentsTypes.MOUNT:
                has_mount = true
        if not has_mount and next_mount < mounts.size():
            g.append(mounts[next_mount])
            next_mount += 1
        gear.append(g)
    return gear


## Every team a quest could field, ranked on what it could become once mounted.
func _teams_for(qi: int, allowed: PackedInt32Array) -> Array:
    var q = quests[qi]
    var forced := PackedInt32Array()
    for ki in q["locked"]:
        forced.append(ki)
    var candidates := PackedInt32Array()
    for ki in allowed:
        if not ki in forced:
            candidates.append(ki)
    var raw := []
    var size_max: int = mini(q["nb"], forced.size() + candidates.size())
    var pick := PackedInt32Array()
    _combine(candidates, 0, forced, size_max, pick, raw, qi)
    var by_size := {}
    for cand in raw:
        var n: int = cand["team"].size()
        if not by_size.has(n):
            by_size[n] = []
        by_size[n].append(cand)
    var out := []
    for n in by_size:
        var list: Array = by_size[n]
        list.sort_custom(func(a, b): return a["v"] > b["v"])
        if list.size() > TEAMS_PER_SIZE:
            list.resize(TEAMS_PER_SIZE)
        out.append_array(list)
    out.sort_custom(func(a, b): return a["v"] > b["v"])
    return out


func _combine(cands: PackedInt32Array, start: int, forced: PackedInt32Array,
              size_max: int, pick: PackedInt32Array, out: Array, qi: int) -> void:
    var team := PackedInt32Array(forced)
    for x in pick:
        team.append(x)
    if team.size() > 0:
        out.append({"team": team, "v": quest_value(qi, team, _optimistic_gear(team))})
    if team.size() >= size_max:
        return
    for i in range(start, cands.size()):
        pick.append(cands[i])
        _combine(cands, i + 1, forced, size_max, pick, out, qi)
        pick.resize(pick.size() - 1)


## Picks one team per quest, all disjoint, maximising the total. Quests are taken
## in order of how much their best team is worth, so the search commits to the
## decisions that matter first and prunes early.
func _assign(order: Array, teams: Dictionary, at: int, used: Dictionary,
             current: Array, running: float, best: Array) -> void:
    if at >= order.size():
        # Several candidates are kept, not just the leader. Teams are ranked here on
        # WELDED gear only - working out the best loadout for every candidate team
        # would cost more than the whole plan - so this ranking is a rough guide. The
        # real ordering only appears once each finalist is equipped, and the first
        # version of this kept one candidate and shipped a visibly worse plan.
        if current.is_empty():
            return
        var copy := []
        for c in current:
            copy.append({"quest": c["quest"], "team": PackedInt32Array(c["team"])})
        best.append({"value": running, "picks": copy})
        if best.size() > PLANS_KEPT * 4:
            best.sort_custom(func(a, b): return float(a["value"]) > float(b["value"]))
            best.resize(PLANS_KEPT)
            _cut = float(best[best.size() - 1]["value"])
        return
    _nodes += 1
    if _nodes > MAX_NODES:
        return
    # Nothing reachable from here can enter the shortlist.
    if running + _suffix[at] <= _cut:
        return
    var qi: int = order[at]
    for cand in teams[qi]:
        var clash := false
        for ki in cand["team"]:
            if used.has(ki):
                clash = true
                break
        if clash:
            continue
        for ki in cand["team"]:
            used[ki] = true
        current.append({"quest": qi, "team": cand["team"]})
        _assign(order, teams, at + 1, used, current, running + float(cand["v"]), best)
        current.resize(current.size() - 1)
        for ki in cand["team"]:
            used.erase(ki)
    # Leaving a quest empty is still allowed - a bad team is often worse than none -
    # but it is no longer free. See _skip_penalty(): a deadline expiring this cycle
    # makes walking away cost more than any team could be worth.
    _assign(order, teams, at + 1, used, current,
            running - float(quests[qi]["skip_pen"]), best)


# ------------------------------------------------------------ equipment

## Hands out relics and mounts by hill-climbing: try every single move, take the
## best one, repeat until nothing improves.
##
## A single-move climb is enough here because the objective rewards a whole team's
## cycle, so the moves that matter (giving the last member of a slow team a mount)
## show up as improvements on their own. Anything cleverer would cost more than the
## quarter of a second the whole plan is allowed.
func _optimise_gear(picks: Array) -> Array:
    var gear := []
    for p in picks:
        gear.append(_welded_gear(p["team"]))
    if pool.is_empty():
        return gear
    # Only the items that could possibly change this quest's answer are tried. A
    # relic that touches none of the required statistics, carries no tag and saves
    # no cycle cannot move the score by definition, and with the vault full the pool
    # is eighty items where a dozen matter.
    var relevant := []
    for p in picks:
        relevant.append(_relevant_items(p["quest"], p["team"]))

    # item -> [pick, member], or absent when the item is in the vault.
    var holder := {}
    var value := PackedFloat64Array()
    value.resize(picks.size())
    for pi in range(picks.size()):
        value[pi] = quest_value(picks[pi]["quest"], picks[pi]["team"], gear[pi])

    # Hard ceiling. The climb only ever takes a strictly improving move, so it
    # cannot cycle in theory - but "in theory" is not a good enough reason to leave
    # an unbounded loop running inside a game's main loop.
    var passes := 0
    while passes < MAX_CLIMB_PASSES:
        passes += 1
        var best_gain := 0.0001
        var best_move := []
        for pi in range(picks.size()):
            var team: PackedInt32Array = picks[pi]["team"]
            for j in range(team.size()):
                for it in relevant[pi]:
                    var from = holder.get(it)
                    if from != null and from[0] == pi and from[1] == j:
                        continue
                    var move := _try_move(picks, gear, value, holder, it, pi, j)
                    if move.is_empty():
                        continue
                    if float(move[0]) > best_gain:
                        best_gain = float(move[0])
                        best_move = move
        if best_move.is_empty():
            break
        _apply_move(picks, gear, value, holder, best_move)
    return gear


## Evaluates giving item `it` to member `j` of pick `pi`, without committing.
## Returns [gain, ...state needed to apply it], or [] when the move is illegal.
func _try_move(picks: Array, gear: Array, value: PackedFloat64Array, holder: Dictionary,
               it: int, pi: int, j: int) -> Array:
    var slot: int = items[it]["slot"]
    var team: PackedInt32Array = picks[pi]["team"]
    var ki: int = team[j]
    # A knight who wears a welded item of that type cannot take another.
    for w in knights[ki]["welded"]:
        if items[w]["slot"] == slot:
            return []

    var from = holder.get(it)
    var new_here := _without_slot(gear[pi][j], slot)
    new_here.append(it)
    var displaced := _slot_item(gear[pi][j], slot)

    var gain := 0.0
    var old_gear_here: PackedInt32Array = gear[pi][j]
    gear[pi][j] = new_here
    var from_pi := -1
    var old_gear_there := PackedInt32Array()
    if from != null:
        from_pi = int(from[0])
        var fj: int = int(from[1])
        old_gear_there = gear[from_pi][fj]
        # Straight swap when both are in the same slot; otherwise the giver simply
        # loses the item.
        var replacement := _without_slot(old_gear_there, slot)
        if displaced >= 0:
            replacement.append(displaced)
        gear[from_pi][fj] = replacement
    var new_here_value := quest_value(picks[pi]["quest"], team, gear[pi])
    gain = new_here_value - value[pi]
    var new_there_value := 0.0
    if from_pi >= 0 and from_pi != pi:
        new_there_value = quest_value(picks[from_pi]["quest"], picks[from_pi]["team"],
                                      gear[from_pi])
        gain += new_there_value - value[from_pi]
    elif from_pi == pi:
        gain = new_here_value - value[pi]
    # Roll back: this was only a look.
    gear[pi][j] = old_gear_here
    if from_pi >= 0:
        gear[from_pi][int(from[1])] = old_gear_there
    return [gain, it, pi, j, from_pi, (-1 if from == null else int(from[1])), displaced]


func _apply_move(picks: Array, gear: Array, value: PackedFloat64Array,
                 holder: Dictionary, move: Array) -> void:
    var it: int = int(move[1])
    var pi: int = int(move[2])
    var j: int = int(move[3])
    var from_pi: int = int(move[4])
    var from_j: int = int(move[5])
    var displaced: int = int(move[6])
    var slot: int = items[it]["slot"]
    var g := _without_slot(gear[pi][j], slot)
    g.append(it)
    gear[pi][j] = g
    if from_pi >= 0:
        var replacement := _without_slot(gear[from_pi][from_j], slot)
        if displaced >= 0:
            replacement.append(displaced)
            holder[displaced] = [from_pi, from_j]
        gear[from_pi][from_j] = replacement
    elif displaced >= 0:
        holder.erase(displaced)
    holder[it] = [pi, j]
    value[pi] = quest_value(picks[pi]["quest"], picks[pi]["team"], gear[pi])
    if from_pi >= 0 and from_pi != pi:
        value[from_pi] = quest_value(picks[from_pi]["quest"], picks[from_pi]["team"],
                                     gear[from_pi])


func _without_slot(g: PackedInt32Array, slot: int) -> PackedInt32Array:
    var out := PackedInt32Array()
    for x in g:
        if items[x]["slot"] != slot:
            out.append(x)
    return out


func _slot_item(g: PackedInt32Array, slot: int) -> int:
    for x in g:
        if items[x]["slot"] == slot and not items[x]["exclusive"]:
            return x
    return -1


# --------------------------------------------------------------------- shopping

## What is worth buying, given the plan we just made.
##
## Not "what raises the score" - the player is not interested in a tenth of a point
## for fifty gold. Only two things earn a recommendation: an item that lifts a quest
## into a better outcome, and one that brings a quest down to a single cycle. That is
## the same bar the meal has to clear.
## Why the buying advice said what it said.
##
## "You never told me to buy anything" has two possible causes and they need telling
## apart: either the shops hold nothing that helps, or the mod cannot SEE the stock.
## This prints both - the stock as the mod reads it, and what the best purchase would
## actually do to the board as it stands.
func shop_report() -> String:
    var out := PackedStringArray()
    var gold := 0
    var fm = GameState.funds_manager
    if "current_funds" in fm:
        gold = int(fm.current_funds)
    out.append("gold %d | %d for sale | %d in the pool" % [gold, for_sale.size(),
                                                           pool.size()])
    # The reserve either fires or it does not, and "it did not" has three possible
    # causes. They are printed rather than guessed at.
    var um = GameState.ultimatums_manager
    if um == null or not ("has_current_ultimatum" in um) or not um.has_current_ultimatum:
        out.append("ultimatum: none registered")
    else:
        var ult = um.current_ultimatum
        if not is_instance_valid(ult):
            out.append("ultimatum: flagged, but no resource")
        else:
            var conds := PackedStringArray()
            for c in ult.selected_conditions_set:
                if not is_instance_valid(c):
                    continue
                var t: int = int(c.type)
                if t == QuestExtraCondition.Types.MIN_FUNDS:
                    conds.append("MIN_FUNDS %d" % int(c.amount))
                elif t == QuestExtraCondition.Types.MIN_RALLIED_COUNTIES:
                    conds.append("MIN_RALLIED_COUNTIES")
                else:
                    conds.append("SATISFACTION")
            out.append("ultimatum: %s | targeted cycle %d, now %d | %s | reserve %d" % [
                String(ult.ultimatum_id), int(ult.targeted_cycle_index),
                int(GameState.current_cycle_index),
                ("no condition" if conds.is_empty() else ", ".join(conds)),
                gold_floor()])
    for it in for_sale:
        out.append("  %-30s %5d gold%s" % [items[it]["name"],
                   int(items[it].get("cost", 0)),
                   ("" if int(items[it].get("cost", 0)) <= gold else "   (too dear)")])
    # The board as it stands, not the last plan: the question is about what is on
    # screen right now.
    for qi in range(quests.size()):
        var team := PackedInt32Array()
        var gear := []
        for k in quests[qi]["ref"].assigned_knights:
            if is_instance_valid(k) and by_ref.has(k):
                var ki: int = by_ref[k]
                team.append(ki)
                var g := PackedInt32Array()
                for x in knights[ki]["worn"]:
                    g.append(x)
                gear.append(g)
        if team.is_empty():
            continue
        var now := score_team(qi, team, gear)
        out.append("%s  now %.2f" % [String(quests[qi]["id"]).substr(0, 40),
                                     float(now["score"])])
        var best := {}
        for it in for_sale:
            var slot: int = items[it]["slot"]
            for j in range(team.size()):
                if _slot_welded(team[j], slot):
                    continue
                var trial := []
                for g2 in gear:
                    trial.append(g2)
                var replaced := _without_slot(trial[j], slot)
                replaced.append(it)
                trial[j] = replaced
                var after := score_team(qi, team, trial)
                var delta: float = float(after["score"]) - float(now["score"])
                if best.is_empty() or delta > float(best["delta"]):
                    best = {"delta": delta, "name": items[it]["name"],
                            "who": knights[team[j]]["id"],
                            "cost": int(items[it].get("cost", 0))}
        if best.is_empty():
            out.append("    nothing on sale fits")
        else:
            out.append("    best buy: %s for %s, %d gold -> %+.2f (%.2f)" % [
                String(best["name"]), String(best["who"]).to_upper(),
                int(best["cost"]), float(best["delta"]),
                float(now["score"]) + float(best["delta"])])
    return "\n".join(out)


## Gold that must not be spent.
##
## An ultimatum can carry a MIN_FUNDS extra condition: hold that much on the day and
## the quest is worth +2, miss it and it is not. The mod was recommending relics with
## the very gold the ultimatum was going to ask for. The Python planner called this
## its GOLD_FLOOR; this is the same idea, applied only once the ultimatum is close
## enough for the money to be genuinely spoken for.
func gold_floor() -> int:
    var um = GameState.ultimatums_manager
    if um == null or not ("has_current_ultimatum" in um):
        return 0
    if not um.has_current_ultimatum:
        return 0
    var ult = um.current_ultimatum
    if not is_instance_valid(ult):
        return 0
    var due: int = int(ult.targeted_cycle_index) - int(GameState.current_cycle_index)
    if due > ULTIMATUM_HORIZON:
        return 0
    var kept := 0
    for c in ult.selected_conditions_set:
        # `amount` only exists on the MIN_FUNDS variant - the class builds its
        # property list from `type` - so the type is checked first, not after.
        if is_instance_valid(c) and int(c.type) == QuestExtraCondition.Types.MIN_FUNDS:
            kept = maxi(kept, int(c.amount))
    return kept


func _buy_advice(picks: Array, gear: Array) -> Array:
    var gold := 0
    var fm = GameState.funds_manager
    if "current_funds" in fm:
        gold = int(fm.current_funds)
    gold = maxi(0, gold - gold_floor())
    var found := []
    for it in for_sale:
        var cost: int = int(items[it].get("cost", 0))
        if cost > gold:
            continue
        var slot: int = items[it]["slot"]
        for pi in range(picks.size()):
            var qi: int = picks[pi]["quest"]
            var team: PackedInt32Array = picks[pi]["team"]
            var before := score_team(qi, team, gear[pi])
            var tier_before := (99 if bool(before["special"])
                                else _tier_of(qi, float(before["score"])))
            for j in range(team.size()):
                if _slot_welded(team[j], slot):
                    continue
                var trial := []
                for g in gear[pi]:
                    trial.append(g)
                var replaced := _without_slot(trial[j], slot)
                replaced.append(it)
                trial[j] = replaced
                var after := score_team(qi, team, trial)
                var tier_after := (99 if bool(after["special"])
                                   else _tier_of(qi, float(after["score"])))
                var cycles_saved: int = int(before["duration"]) - int(after["duration"])
                var gain: float = 0.0
                if not bool(after["special"]) and not bool(before["special"]):
                    gain = float(after["score"]) - float(before["score"])
                # A tier crossed or a cycle saved is still the strongest reason to
                # buy. But requiring one of those was the CONSUMABLE rule applied to
                # permanent gear, and it kept a whole shop quiet: on a board sitting
                # at 8.69 and 9.11, the best relics were worth +0.69 and +0.36 and
                # the panel said "nothing to buy". A relic is bought once and kept,
                # so a lasting gain earns its own mention.
                # An item that LENGTHENS the quest is never advice, whatever it
                # adds to the score. GUIGNOL costs 15 gold, carries a
                # duration_reduction of -1, and the panel was offering it for +2.25
                # on a quest that was going to take ONE cycle and would then have
                # taken two. The team's duration is set by its slowest knight, so a
                # single such mount costs every knight on the quest a cycle - and
                # cycles come before score.
                if cycles_saved < 0:
                    continue
                if tier_after <= tier_before and cycles_saved <= 0 and gain < BUY_MIN_GAIN:
                    continue
                # EVERY carrier is kept, not just this item's best one. Keeping only
                # the best made CLAYMORE vanish outright: its best carrier was ARI,
                # ARI's relic slot went to the hunting bow, and the claymore was only
                # offered to GIDEON on the NEXT press - after the bow had actually
                # been bought. Which knight carries what is the selection's business,
                # further down, because that is where the free slots are known.
                found.append({
                    "item": it,
                    "knight": knights[team[j]]["id"],
                    "quest_id": quests[qi]["id"],
                    "tiers": tier_after - tier_before,
                    "cycles": cycles_saved,
                    "gain": gain,
                    "slot": slot,
                    "to": _tier_name(tier_after),
                    "name": items[it]["name"],
                    "cost": cost,
                    # `for` and `path` are the names the panel already reads, so the
                    # line renders the same whichever planner produced it.
                    "for": knights[team[j]]["id"],
                    "path": String(items[it]["ref"].resource_path),
                })
    # Cheapest first among equal gains, and never advise more than the purse holds.
    found.sort_custom(func(a, b):
        if int(a["cycles"]) != int(b["cycles"]):
            return int(a["cycles"]) > int(b["cycles"])
        if int(a["tiers"]) != int(b["tiers"]):
            return int(a["tiers"]) > int(b["tiers"])
        if not is_equal_approx(float(a["gain"]), float(b["gain"])):
            return float(a["gain"]) > float(b["gain"])
        return int(a["cost"]) < int(b["cost"]))
    var spent := 0
    var affordable := []
    # One knight, one relic and one mount. Advising three swords for ARI - which is
    # exactly what the first version did, 265 gold for a single slot - is not advice,
    # it is a shopping list nobody can use. The best gain per knight and slot wins;
    # the others are dropped rather than re-ranked, because their figures were all
    # measured against the gear he is wearing NOW, not against each other.
    var taken := {}
    var bought := {}
    for f in found:
        # One copy of each item, and one item per knight and slot. The list is sorted
        # best-first, so the first carrier reached for an item is the best one whose
        # slot is still free.
        if bought.has(int(f["item"])):
            continue
        var slot_key := "%s/%d" % [String(f.get("knight", "")), int(f.get("slot", -1))]
        if taken.has(slot_key):
            continue
        if spent + int(f["cost"]) > gold:
            continue
        bought[int(f["item"])] = true
        taken[slot_key] = true
        spent += int(f["cost"])
        affordable.append(f)
    return affordable


## Whether a knight has something welded into that slot, which no purchase can move.
func _slot_welded(ki: int, slot: int) -> bool:
    for w in knights[ki]["welded"]:
        if items[w]["slot"] == slot:
            return true
    return false


# ------------------------------------------------------------------- level-ups

## Which statistic each knight should raise with the points he has banked.
##
## Only the statistics the quest actually ASKS FOR are tried. A point elsewhere
## looks good on the sheet and changes nothing about the quest he is going on, and
## the player spends these by hand, one at a time, in the tower.
## `force` pretends every knight has that many points banked. Nothing in the game
## changes; it exists so the advice can be exercised on a save where nobody happens
## to owe a level-up, which is most of them once the roster reaches the cap.
func _level_advice(picks: Array, gear: Array, force: int = 0) -> Dictionary:
    var out := {}
    # The cache is keyed on team and gear, neither of which a level-up changes, so
    # every trial would come back with the same stale figure.
    _memo_off = true
    for pi in range(picks.size()):
        var qi: int = picks[pi]["quest"]
        var team: PackedInt32Array = picks[pi]["team"]
        var req: PackedInt32Array = quests[qi]["req_idx"]
        if req.is_empty():
            continue
        for j in range(team.size()):
            var ki: int = team[j]
            var n: int = (force if force > 0 else int(knights[ki]["pending"]))
            if n <= 0:
                continue
            var original: PackedInt32Array = knights[ki]["base"]
            var best_v := quest_value(qi, team, gear[pi])
            var best_combo := PackedInt32Array()
            for combo in _stat_combos(req, n):
                var trial := original.duplicate()
                for st_ in combo:
                    trial[st_] += 1
                knights[ki]["base"] = trial
                var v := quest_value(qi, team, gear[pi])
                if v > best_v:
                    best_v = v
                    best_combo = combo
            knights[ki]["base"] = original
            if best_combo.size() > 0:
                var names := PackedStringArray()
                for st_ in best_combo:
                    names.append(String(Knight.Statistics.keys()[st_]))
                out[knights[ki]["id"]] = names
    _memo_off = false
    return out


## Every way of spending `n` points across `stats`. Order does not matter, and the
## same statistic may be raised twice.
func _stat_combos(stats: PackedInt32Array, n: int) -> Array:
    var out := []
    _combos_rec(stats, n, 0, PackedInt32Array(), out)
    return out


func _combos_rec(stats: PackedInt32Array, n: int, start: int,
                 pick: PackedInt32Array, out: Array) -> void:
    if pick.size() == n:
        out.append(PackedInt32Array(pick))
        return
    for i in range(start, stats.size()):
        pick.append(stats[i])
        _combos_rec(stats, n, i, pick, out)
        pick.resize(pick.size() - 1)


# ------------------------------------------------------------------------ meals

## Who should get the meal, if anyone.
##
## Only ever advised when it CHANGES AN OUTCOME. A meal is one use and the player
## decides those personally; suggesting one for a fraction of a point wastes it, so
## a knight who is already comfortably critical is not a candidate however much the
## number would move.
func _meal_advice(picks: Array, gear: Array) -> Dictionary:
    var best := {}
    for pi in range(picks.size()):
        var qi: int = picks[pi]["quest"]
        var team: PackedInt32Array = picks[pi]["team"]
        var plain := score_team(qi, team, gear[pi])
        if bool(plain["special"]):
            continue
        var plain_tier := _tier_of(qi, float(plain["score"]))
        for ki in team:
            var fed := PackedInt32Array([ki])
            var with_meal := score_team(qi, team, gear[pi], fed)
            if bool(with_meal["special"]):
                continue
            var lifted := _tier_of(qi, float(with_meal["score"]))
            if lifted <= plain_tier:
                continue
            var gain: float = float(with_meal["score"]) - float(plain["score"])
            if best.is_empty() or lifted - plain_tier > int(best["tiers"])                     or (lifted - plain_tier == int(best["tiers"]) and gain > float(best["gain"])):
                best = {"knight": knights[ki]["id"], "quest_id": quests[qi]["id"],
                        "tiers": lifted - plain_tier, "gain": gain,
                        "from": _tier_name(plain_tier), "to": _tier_name(lifted)}
    return best


## Outcome rank, so "does the meal lift a tier" has an answer.
func _tier_of(qi: int, s: float) -> int:
    match _scoring.outcome_for_score(quests[qi]["ref"], s):
        Quest.QuestOutcomes.CRITICAL_SUCCESS: return 4
        Quest.QuestOutcomes.GREAT_SUCCESS: return 3
        Quest.QuestOutcomes.SUCCESS: return 2
        Quest.QuestOutcomes.FAILURE: return 1
        Quest.QuestOutcomes.MAJOR_FAILURE: return 0
    return -1


func _tier_name(t: int) -> String:
    match t:
        4: return "critical success"
        3: return "great success"
        2: return "success"
        1: return "failure"
        0: return "major failure"
    return "critical failure"


# ------------------------------------------------------------------ entry point

## Works out the cycle. Everything above is pure computation on the snapshot, so
## this can be called off the main thread once the snapshot is taken.
## Planning, in pieces, so the game keeps drawing.
##
## The whole thing takes about three seconds, and doing it in one call froze the
## game for all of it - which is exactly the complaint that got the external solver
## made asynchronous in the first place. The candidate search is quick and runs in
## one go; the equipment climbs are forty independent passes and are handed out a
## few milliseconds at a time.
var _stage := 0                 # 0 idle, 1 climbing, 2 done
var _finalists := []
var _finalist_at := 0
var _pick_best := []
var _gear_best := []
var _value_best := -INF
var _t_start := 0
var _t_teams := 0
var _t_search := 0
var _t_gear := 0
var _climbs := 0
var _climbed := {}


func planning() -> bool:
    return _stage == 1


## Takes the snapshot and finds the candidate arrangements. Half a second at worst.
func plan_start() -> void:
    _stage = 1
    _finalists = []
    _finalist_at = 0
    _pick_best = []
    _gear_best = []
    _value_best = -INF
    _climbs = 0
    _climbed = {}
    _t_gear = 0
    var found := _search()
    _finalists = found
    if _finalists.is_empty():
        _stage = 2


## Climbs finalists until the budget runs out. True once there is a plan.
func plan_step(budget_ms: int) -> bool:
    if _stage != 1:
        return _stage == 2
    var until := Time.get_ticks_msec() + budget_ms
    var t0 := Time.get_ticks_usec()
    var free_mb := _free_mb()
    # The floor is checked before any work, not after: once the allocator is against
    # the wall the next copy is the one that kills the process.
    if free_mb >= 0 and free_mb < MEMORY_FLOOR_MB:
        _stage = 2
        return true
    while _finalist_at < _finalists.size():
        var cand = _finalists[_finalist_at]
        _finalist_at += 1
        # Built int by int rather than through String(PackedInt32Array). That
        # conversion asks the engine to size a buffer from the array's own header,
        # and it is exactly where the process died allocating 14 GB - so it is the
        # one call in this loop worth not making. Cheaper too: no intermediate
        # "[1, 2, 3]" per team.
        var sig := ""
        for c in cand["picks"]:
            sig += str(int(c["quest"]))
            sig += ":"
            for ki in c["team"]:
                sig += str(int(ki))
                sig += ","
            sig += ";"
        if _climbed.has(sig):
            continue
        _climbed[sig] = true
        _climbs += 1
        var g := _optimise_gear(cand["picks"])
        var v := 0.0
        var staffed := {}
        for pi in range(cand["picks"].size()):
            staffed[int(cand["picks"][pi]["quest"])] = true
            v += quest_value(cand["picks"][pi]["quest"], cand["picks"][pi]["team"], g[pi])
        # The search charged every quest left empty; this comparison has to charge for
        # them as well, or the finalist that walks away from the emergency wins here
        # after losing there.
        v -= _skipped_cost(staffed)
        if v > _value_best:
            _value_best = v
            _pick_best = cand["picks"]
            _gear_best = g
        if Time.get_ticks_msec() >= until:
            break
    _t_gear += Time.get_ticks_usec() - t0
    if _finalist_at >= _finalists.size():
        _stage = 2
        return true
    return false


func plan() -> Dictionary:
    plan_start()
    while not plan_step(100000):
        pass
    return plan_result()


func _search() -> Array:
    var t0 := Time.get_ticks_usec()
    _t_start = t0
    _prepare()
    _settle_last_chance()
    var teams := {}
    var order := []
    var t_teams := Time.get_ticks_usec()
    for qi in range(quests.size()):
        var list := _teams_for(qi, free_knights)
        if list.is_empty():
            continue
        teams[qi] = list
        order.append(qi)
    order.sort_custom(func(a, b): return float(teams[a][0]["v"]) > float(teams[b][0]["v"]))
    t_teams = Time.get_ticks_usec() - t_teams

    var t_search := Time.get_ticks_usec()
    var best := []
    _nodes = 0
    _cut = -INF
    # Suffix bound: the best a quest can offer, ignoring who else wants those knights.
    # Skipping a quest is always allowed, so a negative best contributes nothing.
    _suffix = PackedFloat64Array()
    _suffix.resize(order.size() + 1)
    _suffix[order.size()] = 0.0
    for i in range(order.size() - 1, -1, -1):
        _suffix[i] = _suffix[i + 1] + maxf(0.0, float(teams[order[i]][0]["v"]))
    _assign(order, teams, 0, {}, [], 0.0, best)
    _t_teams = t_teams
    _t_search = Time.get_ticks_usec() - t_search
    best.sort_custom(func(a, b): return float(a["value"]) > float(b["value"]))
    if best.size() > PLANS_KEPT:
        best.resize(PLANS_KEPT)
    return best

    t_search = Time.get_ticks_usec() - t_search
    best.sort_custom(func(a, b): return float(a["value"]) > float(b["value"]))
    if best.size() > PLANS_KEPT:
        best.resize(PLANS_KEPT)


## The level-up advice as it would read if every knight had a point to spend.
## Read only, and used by the test bench alone.
func level_advice_test(force: int) -> Dictionary:
    return _level_advice(_pick_best, _gear_best, force)


## The finished plan, once the climbs are done.
func plan_result() -> Dictionary:
    var picks: Array = _pick_best
    var gear: Array = _gear_best
    var meal := _meal_advice(picks, gear)
    var buy := _buy_advice(picks, gear)
    var levels := _level_advice(picks, gear)

    var out := []
    var total := 0.0
    var staffed := {}
    for p in picks:
        staffed[int(p["quest"])] = true
    # A quest whose deadline expires this cycle and that the plan still leaves empty
    # is a deliberate choice, and it must not be a silent one. It can be either of two
    # very different choices - no team could have succeeded, or the knights were worth
    # more elsewhere - so each one is measured below rather than assumed.
    var missed := []
    for qi2 in range(quests.size()):
        if not quests[qi2]["last_chance"] or staffed.has(qi2):
            continue
        # WHY it was given up, measured. "No team could succeed" was printed over
        # every abandoned deadline, including the ones abandoned because the knights
        # were simply worth more elsewhere - a different statement, and here a false
        # one: four knights scored a great success on the quest the panel had just
        # declared unwinnable.
        missed.append({"id": quests[qi2]["id"],
                       "best": float(quests[qi2]["best_score"]),
                       "winnable": bool(quests[qi2]["winnable"])})
    for pi in range(picks.size()):
        var qi: int = picks[pi]["quest"]
        var team: PackedInt32Array = picks[pi]["team"]
        var r := score_team(qi, team, gear[pi])
        var v := quest_value(qi, team, gear[pi])
        total += v
        var names := PackedStringArray()
        for ki in team:
            names.append(knights[ki]["id"])
        var loadout := []
        var place := []          # what actually has to be handed over
        var krefs := []
        for j in range(team.size()):
            var worn := PackedStringArray()
            var give := []
            # Untyped on purpose: := cannot infer a type out of a plain Dictionary,
            # and the mod has lost a whole file to that before.
            var already = knights[team[j]]["worn"]
            for x in gear[pi][j]:
                worn.append(items[x]["name"])
                # Welded gear, and anything the knight already wears, is left alone:
                # asking the game to equip it again is refused and would be counted
                # as a failure.
                if not x in already:
                    give.append(items[x]["ref"])
            loadout.append(worn)
            place.append(give)
            krefs.append(knights[team[j]]["ref"])
        out.append({
            "quest_id": quests[qi]["id"],
            "quest_ref": quests[qi]["ref"],
            "knights": names,
            "knight_refs": krefs,
            "gear": loadout,
            "place": place,
            "score": float(r["score"]),
            "special": bool(r["special"]),
            "duration": int(r["duration"]),
            "base_duration": quests[qi]["base_d"],
            "value": v,
        })
    return {"assignments": out, "buy": buy, "levels": levels, "missed": missed,
            "gold_floor": gold_floor(),
            "meal": (null if meal.is_empty() else meal["knight"]),
            "meal_info": meal, "value": total,
            "us": Time.get_ticks_usec() - _t_start,
            "us_teams": _t_teams, "us_search": _t_search, "us_gear": _t_gear,
            "climbs": _climbs, "nodes": _nodes, "pool": pool.size(),
            "evals": _qv_memo.size()}
