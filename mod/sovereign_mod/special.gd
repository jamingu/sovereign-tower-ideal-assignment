extends RefCounted
##
## Port of TagSpecialCasesController.check_for_special_cases_for_score().
##
## The game's own function reads knight.get_all_characteristics(), which already
## includes whatever the knight is CARRYING. A planner has to grade loadouts the
## knight is not wearing yet, so this version takes the characteristics as an
## argument instead of reading them off the knight.
##
## Everything else is faithful to the original, including the order of the checks
## and the exact constants. `verify()` holds it against the game's own answer for
## the real board: if the two ever diverge, the port is wrong, not the game.
##
## Each tag contributes independently - the game loops over characteristics and
## writes one entry per tag - so a loadout's contribution is the sum over the tags
## it adds. That is what makes an equipment search affordable.

const NO_SCORE := 0.0


## tag -> {"score": float, "known": bool}, for one knight on one quest.
##
## `characteristics` is tag -> known(bool), as get_all_characteristics() returns.
## `team` is the knights that would go along (used by LONER, TIMID, LOYAL).
## `updated_duration` is the quest's duration AFTER mounts, which the caller knows
## and the quest object does not while a plan is only hypothetical.
## `stats` lets the caller grade a loadout the knight is not wearing: four of the
## tags below read a statistic, and reading it off the knight would answer for the
## gear currently on him. Empty means "ask the knight".
static func for_knight(knight, quest, characteristics: Dictionary, team: Array,
                       base_duration: int, updated_duration: int,
                       stats: PackedInt32Array = PackedInt32Array()) -> Dictionary:
    var out := {}
    var sat = GameState.satisfaction_manager.current_satisfaction
    for tag in characteristics.keys():
        var known: bool = characteristics[tag]
        var score := NO_SCORE
        var keep := true
        match tag:
            TagManager.CharacterTags.LONER:
                if team.size() != 1:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.DEADLY_WEAPON:
                if quest.quest_category != TagManager.QuestTags.ASSASSINATION:
                    keep = false
                else:
                    score = 100.0
            TagManager.CharacterTags.WISH_GRANTING_LAMP:
                score = 100.0
            TagManager.CharacterTags.AMBER_EYE:
                if not quest.quest_id in GameState.quests_manager.all_time_completed_quests:
                    keep = false
                else:
                    score = 8.0
            TagManager.CharacterTags.KIND_HEARTED:
                score = float(clampi(_reward_for(SatisfactionManager.PopulationCategory.PEOPLE,
                                                 quest), -1, 1))
                keep = score != 0.0
            TagManager.CharacterTags.NOBLE_SOUL:
                score = float(clampi(_reward_for(SatisfactionManager.PopulationCategory.NOBLES,
                                                 quest), -1, 1))
                keep = score != 0.0
            TagManager.CharacterTags.TRUE_NOBLE_SOUL:
                var total := 0
                for cat_string in sat.keys():
                    var cat = GameState.satisfaction_manager.get_population_category_from_string(cat_string)
                    total += _reward_for(cat, quest)
                score = float(clampi(total, -1, 1))
                keep = score != 0.0
            TagManager.CharacterTags.SYPHON:
                # Edith's kill counter. Nobody else can carry this one.
                if not knight is Edith:
                    keep = false
                else:
                    score = float((knight as Edith).bonus_for_kills)
                    keep = score > 0.0
            TagManager.CharacterTags.CHEESE_LOVER:
                # The only tag whose value depends on ANOTHER tag being present.
                if not TagManager.CharacterTags.CHEESY in characteristics:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.SADDISTIC:
                if not quest.involve_killing:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.LASTING_IMPRESSION:
                if not knight is Gideon:
                    keep = false
                elif not quest.quest_location in (knight as Gideon).successfuls_locations:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.TIME_PERCEPTION:
                if not knight is Epicrate:
                    keep = false
                elif not quest.quest_id in (knight as Epicrate).completed_quests_ids:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.REVOLUTIONAR:
                score = clampf((float(sat["people"]) - float(sat["nobles"])) * 0.2, -2.0, 2.0)
            TagManager.CharacterTags.PATIENT:
                if updated_duration <= 1:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.TIMID:
                if team.size() > 1:
                    keep = false
                else:
                    score = -1.0
            TagManager.CharacterTags.TRUE_DRAGON_KNIGHT:
                score = 0.5
                if quest.quest_category in _MARTIAL:
                    score *= 2.0
            TagManager.CharacterTags.BRUTAL:
                score = 1.5
                if not quest.quest_category in _MARTIAL:
                    score *= -1.0
            TagManager.CharacterTags.LOYAL:
                if team.size() <= 1:
                    keep = false
                else:
                    var total_affinity := 0.0
                    for other in team:
                        if other == knight:
                            continue
                        total_affinity += other.current_affinity
                    # Divided by the WHOLE team, not by the others: that is what the
                    # game does, self included in the divisor.
                    score = clampf(total_affinity / float(team.size()) * 0.25, -2.0, 2.0)
            TagManager.CharacterTags.NOBILITY_PRIMES:
                score = clampf((float(sat["nobles"]) - float(sat["people"])) * 0.2, -2.0, 2.0)
            TagManager.CharacterTags.SPEEDSTER:
                var gained := base_duration - updated_duration
                if gained <= 0:
                    keep = false
                else:
                    score = float(gained) * 0.5
            TagManager.CharacterTags.OVERWORKED:
                if updated_duration <= 1:
                    keep = false
                else:
                    score = -float(updated_duration - 1) * 0.5
            TagManager.CharacterTags.BELIEVER:
                var scholars := float(sat["scholars"])
                if scholars < 10.0:
                    keep = false
                else:
                    score = scholars * 0.025
            TagManager.CharacterTags.MUTE:
                if quest.quest_category != TagManager.QuestTags.DIPLOMACY:
                    keep = false
                else:
                    score = -3.0
            TagManager.CharacterTags.TANK:
                score = float(knight.current_armor) * 0.08
            TagManager.CharacterTags.RESOURCEFULL:
                score = float(_stat(knight, stats, Knight.Statistics.WITS)) * 0.08
            TagManager.CharacterTags.GAMBLER:
                score = float(_stat(knight, stats, Knight.Statistics.LUCK)) * 0.1
            TagManager.CharacterTags.PROBLEM_SOLVER:
                score = float(_stat(knight, stats, Knight.Statistics.STRENGTH)) * 0.1
            TagManager.CharacterTags.BRIZH_CONNOISSEUR:
                var loc = GameState.world_manager.get_location_from_ID(quest.quest_location)
                var county = GameState.world_manager.get_county_for_location(loc)
                if county == null or String(county.ink_id) != "brizh":
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.COASTAL:
                var loc2 = GameState.world_manager.get_location_from_ID(quest.quest_location)
                if loc2 == null or not loc2.is_coastal:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.SERRATED_BLADE:
                if not quest.involve_killing:
                    keep = false
                else:
                    score = 1.0
            TagManager.CharacterTags.GRANNYS_HERBAL_TEA:
                score = float(clampi(_reward_for(SatisfactionManager.PopulationCategory.PEOPLE,
                                                 quest), -1, 1))
                keep = score != 0.0
            TagManager.CharacterTags.FINE_WINE:
                score = float(clampi(_reward_for(SatisfactionManager.PopulationCategory.NOBLES,
                                                 quest), -1, 1))
                keep = score != 0.0
            TagManager.CharacterTags.DEMON_DECOCTION:
                if not quest.quest_id in GameState.quests_manager.all_time_completed_quests:
                    keep = false
                else:
                    score = 100.0
            _:
                keep = false
        if keep:
            out[tag] = {"score": score, "known": known}
    return out


# The four categories the game calls martial, for TRUE_DRAGON_KNIGHT and BRUTAL.
const _MARTIAL := [TagManager.QuestTags.HUNT, TagManager.QuestTags.CONFRONTATION,
                   TagManager.QuestTags.DUEL, TagManager.QuestTags.ASSASSINATION]


static func _stat(knight, stats: PackedInt32Array, id: int) -> int:
    if stats.size() > id:
        return stats[id]
    return knight.get_statistic_value_from_id(id)


static func _reward_for(category, quest) -> int:
    var amount := 0
    for reward in quest.success_rewards:
        if not is_instance_valid(reward):
            continue
        if reward.reward_type != QuestReward.RewardType.SATISFACTION:
            continue
        if reward.affected_category != category:
            continue
        amount += reward.amount
    return amount


## Holds this port against the game's own function for the knights actually on the
## board. Returns an empty array when they agree.
static func verify(quest, team: Array) -> Array:
    var problems := []
    for knight in team:
        var theirs: Dictionary = \
            TagLibrary.tag_special_cases_controller.check_for_special_cases_for_score(
                knight, quest)
        var mine := for_knight(knight, quest, knight.get_all_characteristics(), team,
                               quest.base_duration, quest.updated_duration)
        for tag in theirs.keys():
            if not tag in mine:
                problems.append("%s: %s missing (game says %.3f)" % [
                    knight.character_ink_id, TagManager.CharacterTags.keys()[tag],
                    float(theirs[tag]["score"])])
            elif not is_equal_approx(float(mine[tag]["score"]), float(theirs[tag]["score"])):
                problems.append("%s: %s = %.3f, game says %.3f" % [
                    knight.character_ink_id, TagManager.CharacterTags.keys()[tag],
                    float(mine[tag]["score"]), float(theirs[tag]["score"])])
        for tag in mine.keys():
            if not tag in theirs:
                problems.append("%s: %s invented (%.3f), game has none" % [
                    knight.character_ink_id, TagManager.CharacterTags.keys()[tag],
                    float(mine[tag]["score"])])
    return problems
