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

var _special = null         # special.gd
var _scoring = null         # scoring.gd


## Diagnostic trail, opened and closed on every line so it survives a hard crash -
## print() sits in a buffer that a crash takes with it, which is exactly when the
## last line matters. Off unless someone sets trace_on.
var trace_on := false


func _trace(msg: String) -> void:
    if not trace_on:
        return
    var f := FileAccess.open("user://sovereign_mod/trace.txt", FileAccess.READ_WRITE)
    if f == null:
        f = FileAccess.open("user://sovereign_mod/trace.txt", FileAccess.WRITE)
    if f == null:
        return
    f.seek_end()
    f.store_line("%d  %s" % [Time.get_ticks_msec(), msg])
    f.close()


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
        var loc = GameState.world_manager.get_location_from_ID(q.quest_location)
        quests.append({
            "ref": q,
            "id": String(q.quest_id),
            "req_idx": idx,
            "req_val": val,
            "nb": int(q.nb_requested_knights),
            "base_d": base_d,
            "ultimatum": q.quest_type == Quest.QuestTypes.ULTIMATUM_QUEST,
            "coastal": is_instance_valid(loc) and loc.is_coastal,
            "eff_plus": eff["efficient_tags"],
            "eff_minus": eff["inefficient_tags"],
            "locked": _locked_knights(q),
        })
    return "%d knight(s), %d quest(s), %d item(s)" % [knights.size(), quests.size(),
                                                      items.size()]


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


## Equipment sitting in the tower rather than on a knight.
func _vault_items() -> Array:
    return GameState.inventory_manager.get_all_available_items()


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
    var slowest := -1
    var bayard := -1
    for j in range(team.size()):
        var red := 0
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
                bayard = red
        if slowest < 0 or red < slowest:
            slowest = red
    if slowest < 0:
        slowest = 0
    if slowest < bayard:
        slowest = bayard
    return maxi(1, q["base_d"] - slowest)


## Whether one of the quest's special outcomes fires. Ported from
## SpecialOutcome.are_conditions_met() so it can be asked about a team that is only
## being considered, with gear it is not wearing.
func special_fires(qi: int, team: PackedInt32Array, gear: Array,
                   stats_by_member: Array = [], chars_by_member: Array = []) -> bool:
    var q = quests[qi]["ref"]
    var pot: Array = q.special_outcomes.duplicate()
    if is_instance_valid(q.selected_modifier):
        pot.append_array(q.selected_modifier.unexpected_outcomes)
    if pot.is_empty():
        return false
    var refs := []
    for ki in team:
        refs.append(knights[ki]["ref"])
    for so in pot:
        if not is_instance_valid(so):
            continue
        if _outcome_met(so, team, gear, refs, stats_by_member, chars_by_member):
            return true
    return false


func _outcome_met(so, team: PackedInt32Array, gear: Array, refs: Array,
                  stats_by_member: Array = [], chars_by_member: Array = []) -> bool:
    # The decompiled source flattens indentation, so where this test ends was a
    # guess. Read as a top-level test it fired on a quest the game leaves alone,
    # which settles it: the whole thing hangs off "the outcome names knights".
    # An outcome that names nobody never triggers.
    if so.knights.is_empty():
        return false
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
    if special_fires(qi, team, gear, stats_by, chars_by):
        return {"score": SPECIAL_SCORE, "special": true, "duration": dur}

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
                                                    refs, q["base_d"], dur, stats)
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
                problems.append("%s: stat %s = %d, game says %d" % [
                    knights[ki]["id"], Knight.Statistics.keys()[st_], mine[st_], theirs])
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

const ONE_CYCLE_BONUS := 8.0
const DURATION_WEIGHT := 1.5
const AFFINITY_TIEBREAK := 0.0002
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
    pool = PackedInt32Array()
    for it in range(items.size()):
        if items[it]["exclusive"] or welded.has(it):
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
    var hit = _qv_memo.get(key)
    if hit != null:
        return float(hit)
    var r := score_team(qi, team, gear, fed)
    var base_d: int = quests[qi]["base_d"]
    var real_d: int = int(r["duration"])
    var v: float = float(r["score"])
    v += DURATION_WEIGHT * float(team.size()) * float(base_d - real_d)
    # Winning a cycle while failing the quest is not winning anything.
    if base_d > 1 and real_d <= 1 and (bool(r["special"]) or float(r["score"]) > 0.0):
        v += ONE_CYCLE_BONUS * float(team.size())
    v += AFFINITY_TIEBREAK * _affinity_gain(team, r)
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
    # Leaving a quest empty is always allowed: a bad team is worse than none.
    _assign(order, teams, at + 1, used, current, running, best)


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


# ------------------------------------------------------------------ entry point

## Works out the cycle. Everything above is pure computation on the snapshot, so
## this can be called off the main thread once the snapshot is taken.
func plan() -> Dictionary:
    var t0 := Time.get_ticks_usec()
    _trace("plan: start")
    _prepare()
    _trace("plan: prepared, %d free knight(s), pool %d" % [free_knights.size(), pool.size()])
    var teams := {}
    var order := []
    var t_teams := Time.get_ticks_usec()
    for qi in range(quests.size()):
        var list := _teams_for(qi, free_knights)
        if list.is_empty():
            continue
        teams[qi] = list
        order.append(qi)
        _trace("plan: quest %d -> %d team(s)" % [qi, list.size()])
    order.sort_custom(func(a, b): return float(teams[a][0]["v"]) > float(teams[b][0]["v"]))
    t_teams = Time.get_ticks_usec() - t_teams

    var t_search := Time.get_ticks_usec()
    _trace("plan: search begins over %d quest(s)" % order.size())
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
    if best.is_empty():
        return {"assignments": [], "value": 0.0, "us": Time.get_ticks_usec() - t0}

    t_search = Time.get_ticks_usec() - t_search
    _trace("plan: search done, %d node(s), %d candidate(s)" % [_nodes, best.size()])
    best.sort_custom(func(a, b): return float(a["value"]) > float(b["value"]))
    if best.size() > PLANS_KEPT:
        best.resize(PLANS_KEPT)
    var t_gear := Time.get_ticks_usec()
    var climbs := 0

    # Every finalist gets its equipment worked out, and only then are they compared.
    var picks: Array = []
    var gear := []
    var best_value := -INF
    var climbed := {}
    for cand in best:
        # Several finalists differ only in a quest nobody was sent on. Climbing the
        # same arrangement twice costs as much as climbing a new one.
        var sig := ""
        for c in cand["picks"]:
            sig += "%d:%s;" % [int(c["quest"]), String(c["team"])]
        if climbed.has(sig):
            continue
        climbed[sig] = true
        climbs += 1
        _trace("plan: climb %d" % climbs)
        var g := _optimise_gear(cand["picks"])
        var v := 0.0
        for pi2 in range(cand["picks"].size()):
            v += quest_value(cand["picks"][pi2]["quest"], cand["picks"][pi2]["team"], g[pi2])
        if v > best_value:
            best_value = v
            picks = cand["picks"]
            gear = g

    var out := []
    var total := 0.0
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
    t_gear = Time.get_ticks_usec() - t_gear
    _trace("plan: done")
    return {"assignments": out, "value": total, "us": Time.get_ticks_usec() - t0,
            "us_teams": t_teams, "us_search": t_search, "us_gear": t_gear,
            "climbs": climbs, "nodes": _nodes, "pool": pool.size(),
            "evals": _qv_memo.size()}
