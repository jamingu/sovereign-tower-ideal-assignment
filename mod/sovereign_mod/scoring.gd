extends RefCounted
##
## Quest scoring, ported from the game's own Quest.determine_outcome().
##
## This is step 1 of replacing the Python solver: the mod has to be able to grade a
## team before it can choose one. st.py REIMPLEMENTED the formula from the data
## files, and had to approximate the parts that live in code - the tag library, the
## special cases, the protagonist rule. Running inside the game we can call those
## directly, so this is exact rather than close.
##
## determine_outcome() itself must never be called for a preview: it FREEZES the
## outcome on the quest and hands out damage and rewards. Only its scoring half is
## reproduced here, and nothing in this file writes to a game object.
##
## Loaded with load() rather than merged into main.gd on purpose: a parse error here
## returns null instead of taking the whole mod down with it.

# Quest.gd constants, repeated rather than read: they are compile-time constants of
# a class we do not extend.
const SUCCESS_SCORE := 0
const CRITICAL_FAILURE_SCORE := -10
const GREAT_SUCCESS_SCORE := 5
const CRITICAL_SUCCESS_SCORE := 10
const MAJOR_FAILURE_SCORE := -5


static func ping() -> String:
    return "scoring.gd loaded"


## The requirements actually in force: the quest's own, shifted by the modifier the
## cycle rolled. Mirrors the block at the top of determine_outcome().
static func requirements_of(quest) -> Dictionary:
    var req: Dictionary = quest.stats_requirements.duplicate()
    var mod = quest.selected_modifier
    if is_instance_valid(mod):
        for stat in mod.stats_requirements_modification.keys():
            if stat in req:
                req[stat] += mod.stats_requirements_modification[stat]
                if req[stat] < 0:
                    req[stat] = 0
            else:
                req[stat] = max(mod.stats_requirements_modification[stat], 0)
    return req


## How many knights the quest asks for, modifier included.
static func requested_knights(quest) -> int:
    var n: int = quest.nb_requested_knights
    var mod = quest.selected_modifier
    if is_instance_valid(mod):
        n = max(1, n + mod.nb_requested_knights_modification)
    return n


## True when one of the quest's special outcomes fires for this team. Such an
## outcome short-circuits scoring entirely, so a figure would be meaningless.
static func special_outcome_for(quest, team: Array):
    var pot: Array = quest.special_outcomes.duplicate()
    var mod = quest.selected_modifier
    if is_instance_valid(mod):
        pot.append_array(mod.unexpected_outcomes)
    if pot.is_empty():
        return null
    # are_conditions_met() takes an Array[Knight]. Handing it a plain Array made it
    # answer "no" on a quest that does trigger - the type has to be right, not just
    # the contents.
    var typed: Array[Knight] = []
    for k in team:
        typed.append(k)
    for so in pot:
        if not is_instance_valid(so):
            continue
        if so.are_conditions_met(typed):
            return so
    return null


## The score a team would get on a quest, and the outcome it lands in.
##
## `team` is an Array[Knight]. Returns
##   {score: float, outcome: int, special: bool, per_knight: Dictionary}
## with `outcome` a Quest.QuestOutcomes value, or -99 in `score` when the team is
## empty (the game has no answer for that either).
static func score(quest, team: Array) -> Dictionary:
    var out := {"score": 0.0, "outcome": Quest.QuestOutcomes.UNDEFINED,
                "special": false, "per_knight": {}}
    if team.is_empty():
        out["score"] = -99.0
        return out

    var so = special_outcome_for(quest, team)
    if so != null:
        out["special"] = true
        out["outcome"] = Quest.QuestOutcomes.UNEXPECTED_OUTCOME
        return out

    var efficiency: Dictionary = TagLibrary.get_efficiency_tags_for_quest(quest)
    var requirements := requirements_of(quest)
    var nb_requested: int = quest.nb_requested_knights

    # The game divides by the team size AND by this, so a team below strength is
    # penalised twice. min(0, ...) means a full or over-full team divides by 1.
    var divider: float = 1.0 + min(0, float(team.size() - 1)) / 2.0
    var missing: int = nb_requested - team.size()
    var stat_multiplier: float = 1.0 / divider

    # TYPED, and it matters: check_for_protagonist() below takes a
    # Dictionary[Knight, KnightScore]. Handed a plain Dictionary it quietly does
    # nothing, and the protagonist's point never lands - the same trap as
    # are_conditions_met() further up. Godot does not warn about either.
    var scores: Dictionary[Knight, KnightScore] = {}
    for i in range(team.size()):
        var knight = team[i]
        var ks := KnightScore.new()
        ks.knight = knight
        ks.presence_score = snappedf(
            float(SUCCESS_SCORE - CRITICAL_FAILURE_SCORE) / float(nb_requested), 0.01)
        # A rounding correction the game applies so three or six shares add back up
        # to ten. Reproduced because it is worth a hundredth, and a hundredth is
        # exactly what separates a critical success from a great one at 10.00.
        if nb_requested == 3 and i % 3 == 0:
            ks.presence_score += 0.01
        elif nb_requested == 6 and i % 3 == 0:
            ks.presence_score -= 0.01
        if knight.has_eaten:
            ks.meal_score += Meal.SCORE_GAIN

        var characteristics: Dictionary = knight.get_all_characteristics()
        for stat in requirements:
            var quest_requirement: float = float(requirements[stat]) + 1.0 * missing
            var stat_score: float = (knight.get_statistic_value_from_id(stat)
                                     - quest_requirement) * 0.66
            # Meeting the requirement exactly is worth a token 0.22, not nothing.
            if is_equal_approx(stat_score, 0.0):
                stat_score = 0.22
            stat_score = stat_score / float(team.size()) * stat_multiplier
            if stat_score > 0.0:
                stat_score *= 1 + requirements[stat] * 0.0275
            else:
                stat_score *= 1 + requirements[stat] * 0.01
            ks.stats_score[stat] = snappedf(stat_score, 0.01)

        # A tag the player has not discovered still counts: the game separates known
        # from unknown only to decide what to DISPLAY, and sums all four.
        for tag in efficiency["efficient_tags"]:
            if tag in characteristics:
                if characteristics[tag]:
                    ks.known_bonuses[tag] = 1
                else:
                    ks.unknown_bonuses[tag] = 1
        for tag in efficiency["inefficient_tags"]:
            if tag in characteristics:
                if characteristics[tag]:
                    ks.known_bonuses[tag] = -1
                else:
                    ks.unknown_bonuses[tag] = -1

        var special_cases: Dictionary = \
            TagLibrary.tag_special_cases_controller.check_for_special_cases_for_score(
                knight, quest)
        for tag in special_cases.keys():
            var known: bool = special_cases[tag]["known"]
            var sc: float = float(special_cases[tag]["score"])
            if known:
                if sc > 0:
                    ks.known_bonuses[tag] = sc
                else:
                    ks.known_maluses[tag] = sc
            else:
                if sc > 0:
                    ks.unknown_bonuses[tag] = sc
                else:
                    ks.unknown_maluses[tag] = sc
        scores[knight] = ks

    TagLibrary.tag_special_cases_controller.check_for_protagonist(scores)

    var total: float = -10.0
    for extra_condition in quest.extra_conditions:
        if is_instance_valid(extra_condition) and extra_condition.is_condition_met():
            total += 2
    for ks2 in scores.values():
        total += ks2.get_total_score()

    out["score"] = total
    out["outcome"] = outcome_for_score(quest, total)
    out["per_knight"] = scores
    return out


## Quest.get_outcome_for_score(). Note the thresholds are not symmetric: 10.00 flat
## IS a critical success, but 5.00 flat is NOT a great one.
static func outcome_for_score(quest, s: float) -> int:
    if s >= CRITICAL_SUCCESS_SCORE:
        return Quest.QuestOutcomes.CRITICAL_SUCCESS
    elif s > GREAT_SUCCESS_SCORE:
        return Quest.QuestOutcomes.GREAT_SUCCESS
    elif s <= CRITICAL_FAILURE_SCORE:
        return Quest.QuestOutcomes.CRITICAL_FAILURE
    elif s > SUCCESS_SCORE:
        return Quest.QuestOutcomes.SUCCESS
    else:
        # A quest with extra conditions has no middle ground: missing the bar at all
        # is a critical failure.
        if not quest.extra_conditions.is_empty():
            return Quest.QuestOutcomes.CRITICAL_FAILURE
        elif s <= MAJOR_FAILURE_SCORE:
            return Quest.QuestOutcomes.MAJOR_FAILURE
        else:
            return Quest.QuestOutcomes.FAILURE


## FORTUNATE gives a team one chance in two of climbing a tier, if a single point
## would have got them there. It is a coin flip at resolution time, so a planner can
## only report it, never count on it.
static func fortunate_would_lift(quest, s: float, per_knight: Dictionary) -> bool:
    if per_knight.is_empty():
        return false
    if outcome_for_score(quest, s) == outcome_for_score(quest, s + 1.0):
        return false
    return TagLibrary.tag_special_cases_controller.is_fortunate(per_knight)
