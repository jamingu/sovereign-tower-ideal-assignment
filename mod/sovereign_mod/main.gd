extends Node
##
## "Ideal assignment" mod - Sovereign Tower
##
## Loaded as the `SovereignMod` autoload through override.cfg, FROM DISK: this file
## does not live inside sovereign_tower.pck, the game archive is untouched.
## To uninstall: delete override.cfg and this folder.
##
## The mod computes NOTHING. st.py remains the solver; the mod runs the script,
## reads the JSON it produces and applies the result to the interface.
##
## It only applies what is FREE and located at the round table: assignments and
## equipment. Meals and purchases cost gold and live in other rooms - calling them
## from here would hand out free items, which is cheating, and would invalidate the
## gold budget st.py computed.
##

const MOD_DIR := "user://sovereign_mod/"
const SETTINGS_PATH := MOD_DIR + "settings.json"
const PLAN_PATH := MOD_DIR + "plan.json"

## Test bench. The mod re-reads cmd.txt, expects ONE NAME from the fixed list below
## (no expression evaluation: an unknown name is rejected) and writes the result to
## out.txt. Lets the mod be checked from a terminal, on a real loaded save, without
## pulling the player in for every trial.
## Setting `test_bench`, to be turned off once tuning is over.
const CMD_PATH := MOD_DIR + "cmd.txt"
const OUT_PATH := MOD_DIR + "out.txt"

## Exchange files for the live score: the mod writes the real board state, st.py
## grades it and returns the scores.
const LIVE_IN := MOD_DIR + "live_in.json"
const LIVE_OUT := MOD_DIR + "live_out.json"

## Same idea for audience choices: the mod sends the option labels, st.py digs the
## rewards out of the ink script.
const HINTS_IN := MOD_DIR + "hints_in.json"
const HINTS_OUT := MOD_DIR + "hints_out.json"
# Ticks (of 0.3 s) before a spawned solver is considered lost.
const MAX_WAIT := 40

## The full plan is a different animal from the live score: it searches every
## combination of knights, quests and equipment, and takes about twenty seconds on
## a full round table. It gets its own, far longer, leash.
const PLAN_MAX_WAIT := 300         # ticks of 0.3 s -> 90 s
const SPINNER := ["|", "/", "-", "\\"]

## Every screen the ending runs through. It is not one scene but a chain of them -
## the knights' epilogues, the closing dialogues, then the credits - so they are all
## listed rather than guessed at from a parent node.
const ENDING_SCRIPTS := [
    "scenes/cutscene/servant_ending_cutscene_container.gd",
    "scenes/rooms/dialogue_rooms/arlin_ending_dialogue_container.gd",
    "scenes/rooms/dialogue_rooms/arlin_ending_dialogue.gd",
    "scenes/rooms/dialogue_rooms/demon_ending_dialogue.gd",
    "scenes/credits/credits.gd",
]

const COMMANDS := ["state", "assign", "report", "clear", "score2", "detail", "spec", "fast", "inputs", "plan2", "inkprobe", "probe2", "hints2", "levels2", "scene", "quests", "knights", "away", "shop",
                   "load", "table", "tree", "achievements", "clean_achievements",
                   "test_lock", "test_required", "wheel", "outcomes", "test_outcome",
                   "scores", "options", "options_state", "choices", "test_choice",
                   "why"]

## Achievements fired by mistake during tuning: a "populate" command (since removed)
## recruited the whole round table at once, which unlocked the recruitment
## achievements on Steam. Restoring a save file does NOT undo them: they live on
## Steam's servers.
const ACHIEVEMENTS_TO_CLEAR := ["KNIGHTHOOD", "THE_STRONGEST_KNIGHT", "A_KNIGHTS_GAME"]

## Every feature is an independent switch: the player wants to turn them on one by
## one (see MOD_BACKLOG.md, point 5).
const DEFAULTS := {
    # Left empty, the mod finds sovereign_mod/solver/st.exe by itself: there is
    # nothing to configure, wherever Steam put the game. Fill these in only to run
    # the solver from its Python sources ("python" -> python.exe, "solver" -> st.py).
    "python": "",
    "solver": "",
    "slot": 1,
    "assignment_button": true,
    "clear_button": true,
    "test_bench": false,
    "wheel_font_size": 32,
    "unexpected_outcomes": true,
    "wheel_numbers": true,
    "buying_advice": true,
    "live_score": true,
    "audience_outcomes": true,
    "audience_rewards": true,
    "reward_names": true,
    "meal_likes": true,
    "fast_results": true,
    "fast_ending": true,
    "ending_speed": 3.0,
    "fix_ghost_portrait": true,
    "result_speed": 4.0,
}

## What the options screen offers to tick: setting key -> label, in display order.
const OPTIONS := [
    ["assignment_button", "Show the \"Auto-assignement\" button"],
    ["clear_button", "Show the \"Clear all\" button"],
    ["wheel_numbers", "Show numbers on the difficulty wheel"],
    ["unexpected_outcomes", "Show unexpected outcomes"],
    ["live_score", "Live quest score"],
    ["audience_outcomes", "Audience: show a quest's unexpected outcomes"],
    ["audience_rewards", "Audience: name the relic / mount / consumable on offer"],
    ["reward_names", "Name the relic / mount / consumable a quest promises"],
    ["meal_likes", "Kitchen: flag the dishes a knight likes and dislikes"],
    ["fast_results", "Speed up the end-of-cycle results screen"],
    ["fast_ending", "Speed up the ending sequence"],
    ["fix_ghost_portrait", "Round table: clear a portrait left behind by the swipe"],
    ["buying_advice", "Buying and meal advice"],
    ["test_bench", "Remote control (lets the assistant drive the game to test it)"],
]

var settings := {}

var _layer: CanvasLayer
var _panel: Panel
var _button: Button
var _clear_button: Button
var _meal_button: Button
var _train_button: Button
var _status: Label
var _outcome_label: Label
var _quest_label: Label           # name of the quest the score belongs to
var _score_label: Label
var _advice_label: Label          # "to buy" section
var _meal_label: Label
var _levels_label: Label
var _score_sep: HSeparator
var _meal_sep: HSeparator
var _levels_sep: HSeparator
var _buy_sep: HSeparator
var _section: Node = null          # current QuestPresentationSection
var _roundtable: Node = null       # current RoundtableContainer
var _home: Node = null             # main menu
var _tower: Node = null            # TowerViewContainer
var _kitchen: Node = null          # Kitchen (meal selection)
var _cycle_end: Node = null        # CycleTransitionContainer (end-of-cycle results)
var _endings: Array[Node] = []     # the ending sequence, epilogues and credits
var _wheels: Array[Node] = []      # difficulty wheels to annotate
var _choices: Array[Node] = []     # audience choice buttons to annotate
var _rewards_shown: Array[Node] = []   # reward chips on the quest card
var _choice_sig := ""              # labels on screen at the last ink lookup
var _choice_pending := ""          # signature of the lookup currently running
var _choice_wait := 0              # ticks spent waiting for that answer
var _score_pending := false        # a score computation is running
var _score_wait := 0               # ticks spent waiting for it
var _rewards := {}                 # label -> reward text, kept across audiences
var _tips := {}                    # button id -> {base, full} tooltip we wrote
var _signature := ""               # board state at the last score computation
var _scores := {}                  # quest_id -> score text
var _last_plan := {}               # last plan applied, for the live advice
var _plan_board := ""              # quests on the board when that plan was made
var _last_report := ""             # result of the last action, for the test bench
var _plan_pending := false         # a plan is being computed right now
var _plan_wait := 0                # ticks spent waiting for it
var _plan_freed := 0               # what the pre-clear removed, kept for the report
var _plan_stripped := 0
var _spin := 0                     # current frame of the loader


func _ready() -> void:
    _load_settings()
    get_tree().node_added.connect(_on_node_added)
    get_tree().node_removed.connect(_on_node_removed)
    # Everything is built and started unconditionally; each feature checks its own
    # setting at run time. That is what lets a checkbox take effect mid-game without
    # restarting.
    _start_bench()
    _start_display()
    _build_ui()
    _build_options()
    # The planner runs here now. The external solver is only woken if the GDScript
    # one cannot be loaded, so a missing st.exe is the normal case and not worth a
    # word in the log.
    if _new_solver() != null:
        _log("ready (planning in GDScript)")
    else:
        _ensure_cache()
        _log("ready (external solver: %s)"
             % (_solver_exe if _solver_exe != "" else "NOT FOUND"))


# ------------------------------------------------- numbers on the difficulty wheel
#
# The wheel (DifficultyHintWheel) shows six slices, one per statistic, each with a
# Low / Mid / High / Max label. The exact required value is computed right next to
# it, in DifficultyHint.define_difficulty(), but never displayed.
#
# We add the number WITHOUT touching the original display: an extra Label placed on
# each slice. A requirement the game considers unknown stays "?" - showing its value
# would hand the player information they have not earned.

const LABEL_NAME := "SovModValue"


func _start_display() -> void:
    var t := Timer.new()
    t.wait_time = 0.3
    t.autostart = true
    t.timeout.connect(_update_display)
    add_child(t)


func _update_display() -> void:
    # A plan being computed in the background: check whether it has landed.
    _poll_solver()
    # Visibility follows the setting AND the presence of the round table, re-read
    # every tick: unticking the box hides the panel immediately.
    if is_instance_valid(_options_button):
        _options_button.visible = is_instance_valid(_home) and _home.is_visible_in_tree()
        if _options_button.visible:
            _place_options_button()
    if is_instance_valid(_button):
        _button.visible = settings.get("assignment_button", true)
    if is_instance_valid(_clear_button):
        _clear_button.visible = settings.get("clear_button", true)
    if is_instance_valid(_panel):
        # The panel lives as long as it has anything to show: either button, or any
        # of the read-outs.
        var has_content: bool = (settings.get("assignment_button", true)
                                 or settings.get("clear_button", true)
                                 or settings.get("live_score", false)
                                 or settings.get("buying_advice", false)
                                 or settings.get("unexpected_outcomes", false))
        # `is_instance_valid` alone was not enough: the game PRELOADS the round table
        # and merely hides it when you leave, so the node stays alive and the panel
        # lingered on screen well after the assignment was validated.
        #
        # But keying on the QUEST SECTION alone was too tight the other way: that
        # section only appears once a quest is picked, so the panel stayed invisible
        # for the whole first half of the round table. The container is the right
        # unit - the panel belongs to the round table, not to one selection.
        var board_up: bool = (is_instance_valid(_roundtable)
                              and _roundtable.is_visible_in_tree())
        if not board_up:
            board_up = is_instance_valid(_section) and _section.is_visible_in_tree()
        # The end-of-cycle results are drawn over the round table, which stays
        # visible underneath: without this the panel comes back during the recap.
        if is_instance_valid(_cycle_end) and _cycle_end.is_visible_in_tree():
            board_up = false
        _panel.visible = has_content and board_up
        # First run: no stored position yet. Centre it horizontally and lift it
        # ~300 px off the bottom so it clears the knight's name. Only computable
        # once the viewport size is known.
        if _panel.visible:
            _fit_height()
        if _panel.visible and _panel.position.x < 0:
            var screen := _panel.get_viewport_rect().size
            _panel.position = Vector2(screen.x * 0.5 - _panel.size.x * 0.5,
                                      screen.y - _panel.size.y - 140.0)
            _keep_on_screen()
    if settings.get("wheel_numbers", false):
        _update_wheels()
    if settings.get("unexpected_outcomes", false):
        _update_outcome()
    if settings.get("live_score", false):
        _update_score()
    # Re-filtered every tick: coming back from the forge clears the "buy this" line
    # on its own, no need to press the button again.
    # Le plan survit a la fermeture de la table ronde, ce qui est voulu : revenir de la
    # forge doit rafraichir le conseil d'achat sans re-cliquer. Mais il survivait AUSSI
    # au changement de cycle : le panneau affichait encore les montees de niveau du tour
    # precedent alors que le joueur n'avait rien demande. On le perime des que la liste
    # des quetes du plateau change.
    if not _last_plan.is_empty() and _quest_set() != _plan_board:
        _last_plan = {}
        _plan_board = ""
        if is_instance_valid(_advice_label):
            _advice_label.text = ""
            _meal_label.text = ""
            _levels_label.text = ""
    if settings.get("buying_advice", false) and not _last_plan.is_empty():
        _update_advice(_last_plan)
    _refresh_rules()
    if settings.get("fix_ghost_portrait", false):
        _fix_ghost_portraits()
    if settings.get("meal_likes", false):
        _update_meal_likes()
    if settings.get("reward_names", false):
        _update_reward_chips()
    if settings.get("audience_rewards", false):
        _refresh_rewards()
    if settings.get("audience_outcomes", false) or settings.get("audience_rewards", false):
        _update_choices()


func _update_wheels() -> void:
    for i in range(_wheels.size() - 1, -1, -1):
        var w: Node = _wheels[i]
        if not is_instance_valid(w):
            _wheels.remove_at(i)
            continue
        _annotate_wheel(w)


func _annotate_wheel(w: Node) -> void:
    var quest = w.current_quest
    if not is_instance_valid(quest):
        return
    if w.portions == null:
        return
    var required := _requirements(quest, w.current_quest_modifiers)
    for slice in w.portions.get_children():
        if not ("statistic_id" in slice):
            continue
        # The game's own verdict tells apart "no requirement at all" (NONE, nothing
        # to print) from "requirement the player has not discovered yet" (UNKNOWN,
        # shown as "?" on the wheel). We never re-decide that: replicating
        # define_difficulty() once printed a number where the game shows "?".
        #
        # An UNKNOWN requirement DOES have a value, and this setting reveals it -
        # the player asked for it explicitly, under this same checkbox rather than
        # one of its own. Unticking "numbers on the wheel" restores the game's
        # discovery mechanic whole.
        var text := ""
        if slice.difficulty != DifficultyHint.Difficulties.NONE:
            var stat = slice.statistic_id
            text = str(int(required[stat])) if required.has(stat) else ""
        _place_label(slice, text)


## Mirrors DifficultyHint.define_difficulty(): base requirements, then the change
## brought by the modifier picked at the audience.
func _requirements(quest, mods) -> Dictionary:
    var required: Dictionary = quest.stats_requirements.duplicate()
    if is_instance_valid(mods):
        for stat in mods.stats_requirements_modification.keys():
            if required.has(stat):
                required[stat] = max(0, required[stat] + mods.stats_requirements_modification[stat])
            else:
                required[stat] = max(mods.stats_requirements_modification[stat], 0)
    return required


func _place_label(slice: Node, text: String) -> void:
    var lab: Label = slice.get_node_or_null(LABEL_NAME)
    if text == "":
        if lab != null:
            lab.visible = false
        return
    if lab == null:
        lab = Label.new()
        lab.name = LABEL_NAME
        lab.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
        lab.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
        lab.set_anchors_preset(Control.PRESET_FULL_RECT)
        lab.mouse_filter = Control.MOUSE_FILTER_IGNORE
        lab.add_theme_color_override("font_color", Color(1, 1, 1))
        lab.add_theme_color_override("font_outline_color", Color(0, 0, 0))
        var size: int = int(settings.get("wheel_font_size", 32))
        lab.add_theme_font_size_override("font_size", size)
        # Outline scales with the font: too thin and the digit vanishes on pale slices.
        lab.add_theme_constant_override("outline_size", max(4, size / 4))
        slice.add_child(lab)
    lab.visible = true
    lab.text = text
    # Wheel slices are rotated; without compensation the label follows and a 6 placed
    # at the bottom reads as a 9 - the player hit exactly that. Cancel the inherited
    # rotation so every number stays upright.
    lab.pivot_offset = lab.size / 2.0
    lab.rotation = -slice.get_global_transform().get_rotation()


# ------------------------------------------------------------------- live score
#
# The score comes from st.py, never from the game: `quest.determine_outcome()`
# FREEZES the outcome and would trigger damage and rewards (see below).
#
# st.py rebuilds knights from the SAVE FILE, which lags behind the running game. So
# we hand it the team AND the equipment actually in place, through `st.py live`; it
# only keeps from the save what does not move.
#
# The call blocks (~0.2 s), so we only fire it when the board actually changed: the
# signature below sums up the state, and while it is unchanged nothing runs.

## The quests currently on the board, ignoring who is assigned to them. Identifies
## the cycle for the purpose of expiring a stale plan.
func _quest_set() -> String:
    var ids := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if is_instance_valid(q):
            ids.append(String(q.quest_id))
    ids.sort()
    return ",".join(ids)


func _board_signature() -> String:
    var parts := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var names := PackedStringArray()
        for k in q.assigned_knights:
            if not is_instance_valid(k):
                continue
            var items := PackedStringArray()
            for eq in k.equipments:
                if is_instance_valid(eq):
                    items.append(String(eq.name))
            items.sort()
            # The stat total is part of the signature: without it a level-up or a
            # training session changed nothing here, and the score stayed frozen.
            var total := 0
            for id in Knight.Statistics.values():
                total += k.get_statistic_value_from_id(id, false)
            names.append("%s[%s]%s#%d" % [String(k.character_ink_id), ",".join(items),
                                          "M" if k.has_eaten else "", total])
        names.sort()
        if names.size() > 0:
            parts.append("%s:%s" % [String(q.quest_id), ",".join(names)])
    parts.sort()
    return "|".join(parts)


func _update_score() -> void:
    if not is_instance_valid(_score_label):
        return
    if not is_instance_valid(_section):
        _quest_label.text = ""
        _score_label.text = ""
        return
    # Same rule as the audience lookup: never block the frame on Python. The panel
    # keeps the previous figure until the new one lands, one or two ticks later.
    # scoring.gd answers in microseconds by asking the game itself, so there is
    # nothing to wait for. The pending/timeout dance below only applies to the
    # external solver, kept as a fallback.
    if _load_scoring() != null:
        var sig_now := _board_signature()
        if sig_now != _signature:
            _signature = sig_now
            _score_live()
    elif _score_pending:
        _score_wait += 1
        if _score_wait > MAX_WAIT:
            _log("st.py live timed out")
            _score_pending = false
        else:
            _collect_scores()
    else:
        var sig := _board_signature()
        if sig != _signature:
            _signature = sig
            _recompute_scores()
    var q = _section.selected_quest
    if not is_instance_valid(q):
        _quest_label.text = ""
        _score_label.text = ""
        return
    # Duration next to the name: a knight sent on a 3-cycle quest is gone for three
    # cycles, and nothing in this panel said so. calculate_updated_duration() is the
    # game's own function and only reads - it applies the team's mount reduction.
    var cycles: int = GameState.quests_manager.calculate_updated_duration(q)
    var title := "%s - %d cycle%s" % [tr(q.quest_name), cycles,
                                      ("" if cycles == 1 else "s")]
    # A deadline is the one thing on this panel there is no recovering from: a quest
    # left to nobody on the cycle its deadline expires resolves as a CRITICAL FAILURE
    # before anything is scored, consequences included. The game files it under
    # "urgent" and says nothing more, so it is spelled out here.
    if q.has_deadline:
        var left: int = int(q.remaining_cycles_before_faillure)
        title += ("  -  LAST CYCLE" if left <= 1 else "  -  deadline in %d" % left)
    _quest_label.text = title
    # A special outcome whose conditions are met SHORT-CIRCUITS the whole thing:
    # `determine_outcome()` returns UNEXPECTED_OUTCOME before scoring anything. The
    # figure would be meaningless, so we do not show one.
    if _outcome_triggered(q):
        _score_label.text = "??/10 - UNEXPECTED OUTCOME"
    else:
        _score_label.text = _scores.get(String(q.quest_id), "no knight assigned")


## Sends the real board state to st.py and collects the scores.
func _recompute_scores() -> void:
    # Keep the figures on screen while the new ones are computed: clearing here made
    # the panel blink empty at every change.
    var quests := []
    # The knights' CURRENT statistics, equipment excluded (st.py adds the gear back
    # from `equip`). The save does not know about a level-up made this cycle, so the
    # score used to stay frozen after a "+1 WITS".
    var stats := {}
    for k in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(k):
            continue
        var row := {}
        for id in Knight.Statistics.values():
            row[Knight.Statistics.keys()[id]] = k.get_statistic_value_from_id(id, false)
        stats[String(k.character_ink_id)] = row
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var names := []
        var equip := {}
        var fed := []
        for k in q.assigned_knights:
            if not is_instance_valid(k):
                continue
            var name := String(k.character_ink_id)
            names.append(name)
            var items := []
            for eq in k.equipments:
                if is_instance_valid(eq):
                    items.append(String(eq.name))
            equip[name] = items
            if k.has_eaten:
                fed.append(name)
        if names.size() > 0:
            quests.append({"quest_id": String(q.quest_id), "knights": names,
                           "equip": equip, "has_eaten": fed})
    if quests.is_empty():
        _scores.clear()
        return

    var f := FileAccess.open(LIVE_IN, FileAccess.WRITE)
    if f == null:
        return
    f.store_string(JSON.stringify({"slot": settings.get("slot", 1),
                                   "stats": stats, "quests": quests}))
    f.close()

    if not _ensure_cache():
        return
    # The answer's arrival is signalled by the file appearing, so remove the old one.
    DirAccess.remove_absolute(ProjectSettings.globalize_path(LIVE_OUT))
    var pid := OS.create_process(_solver_exe, _solver_argv(["live",
        "--in=" + ProjectSettings.globalize_path(LIVE_IN),
        "--out=" + ProjectSettings.globalize_path(LIVE_OUT)]))
    if pid <= 0:
        _log("the live score could not start")
        return
    _score_pending = true
    _score_wait = 0


## Picks the scores up once Python has renamed the file into place.
func _collect_scores() -> void:
    if not FileAccess.file_exists(LIVE_OUT):
        return
    _score_pending = false
    _scores.clear()
    var g := FileAccess.open(LIVE_OUT, FileAccess.READ)
    if g == null:
        return
    var parsed = JSON.parse_string(g.get_as_text())
    g.close()
    if typeof(parsed) != TYPE_DICTIONARY:
        return
    for row in parsed.get("scores", []):
        var qid := String(row.get("quest_id", ""))
        if row.has("error"):
            _scores[qid] = String(row["error"])
        elif row.get("score") == null:
            _scores[qid] = _english_outcome(String(row.get("outcome", "-")))
        else:
            _scores[qid] = "%.2f/10 - %s" % [
                float(row["score"]), _english_outcome(String(row.get("outcome", "")))]


## st.py speaks French (it is an older tool, fully commented that way); this panel
## does not. Translate the outcome tiers on the way in.
##
## Order matters: "REUSSITE CRITIQUE" has to be replaced before "REUSSITE", or the
## longer label would be mangled into "CRITICAL SUCCESS CRITIQUE".
## Statistic names as the GAME shows them, not as the engine names them. The wheel
## reads FOR / AGI / CHA / MAG / INT / FRT, so "WITS" in our own panel had the player
## looking for a stat that does not exist on screen.
const STAT_LABEL := {
    "STRENGTH": "FOR", "AGILITY": "AGI", "CHARISMA": "CHA",
    "MAGIC": "MAG", "WITS": "INT", "LUCK": "FRT",
}


func _stat_label(name: String) -> String:
    return String(STAT_LABEL.get(name, name))


const OUTCOME_FR_EN := [
    ["REUSSITE CRITIQUE", "CRITICAL SUCCESS"],
    ["GRANDE REUSSITE", "GREAT SUCCESS"],
    ["ECHEC CRITIQUE", "CRITICAL FAILURE"],
    ["ECHEC MAJEUR", "MAJOR FAILURE"],
    ["REUSSITE", "SUCCESS"],
    ["ECHEC", "FAILURE"],
    ["FORTUNE", "LUCK"],
]


func _english_outcome(text: String) -> String:
    var out := text
    for pair in OUTCOME_FR_EN:
        out = out.replace(pair[0], pair[1])
    return out


# ------------------------------------------------------------ audience choices
#
# A choice that grants a quest carries its id (`related_quest_id`), so the quest
# resource can be loaded and inspected before the choice is even made. We append the
# unexpected outcomes AND the knights that trigger them to the button tooltip.
#
# Unlike the round-table read-out, this one names names: the player asked for it
# explicitly, precisely to decide whether a quest is worth taking.

func _update_choices() -> void:
    for i in range(_choices.size() - 1, -1, -1):
        var b: Node = _choices[i]
        if not is_instance_valid(b):
            _choices.remove_at(i)
            continue
        _annotate_choice(b)


## ChoiceButton EXTENDS Button, so the option text is its own `text` property
## (`dialogue_container.gd`: `choice_buttons[i].text = ... choices[i].text`).
## Walking the subtree instead picked up the decorative hint labels, and the mod
## kept asking the solver about "+", "-" and "->".
func _choice_label(n: Node) -> String:
    if not ("text" in n):
        return ""
    return String(n.text).strip_edges()


## Asks st.py what each visible option grants.
##
## Runs the solver WITHOUT blocking. `OS.execute` froze the game for the whole
## Python start-up on every new line of dialogue - the player saw a stutter before
## each set of choices. We now spawn the process, and pick the answer up from the
## output file on a later tick.
func _refresh_rewards() -> void:
    # An answer may be waiting from a previous tick.
    if _choice_pending != "":
        _choice_wait += 1
        # A crashed or missing interpreter would otherwise leave us waiting forever,
        # and no tooltip would ever be annotated again.
        if _choice_wait > MAX_WAIT:
            _log("st.py hints timed out")
            _choice_sig = _choice_pending
            _choice_pending = ""
        else:
            _collect_rewards()
        return
    var labels := []
    for b in _choices:
        if not is_instance_valid(b) or not b.is_visible_in_tree():
            continue
        var lab := _choice_label(b)
        if lab != "" and not labels.has(lab):
            labels.append(lab)
    if labels.is_empty():
        _choice_sig = ""
        return
    var sig := "|".join(PackedStringArray(labels))
    if sig == _choice_sig:
        return

    # The cache belongs to ONE set of choices, not to the session. st.py resolves a
    # label inside the knot that covers the whole set, so the same wording can mean
    # something else in another audience - keeping it would show a stale reward.
    _rewards.clear()
    var ask := labels

    # Read here rather than by an external process: about 50 ms to load the script
    # once, then 20 to 35 ms per set of choices, which is why the whole spawn-and-
    # wait dance below is now only a fallback.
    if _read_rewards_native(ask):
        _choice_sig = sig
        return

    if not _ensure_cache():
        _choice_sig = sig
        return
    var f := FileAccess.open(HINTS_IN, FileAccess.WRITE)
    if f == null:
        return
    f.store_string(JSON.stringify({"choices": ask}))
    f.close()
    # Remove the previous answer: its presence is the signal that the new one landed.
    DirAccess.remove_absolute(ProjectSettings.globalize_path(HINTS_OUT))
    var pid := OS.create_process(_solver_exe, _solver_argv(["hints",
        "--in=" + ProjectSettings.globalize_path(HINTS_IN),
        "--out=" + ProjectSettings.globalize_path(HINTS_OUT)]))
    if pid <= 0:
        _log("the audience lookup could not start")
        _choice_sig = sig
        return
    _choice_pending = sig
    _choice_wait = 0


## Reads the answer once st.py has written it. The file is renamed into place by
## Python, so its mere existence means it is complete.
func _collect_rewards() -> void:
    if not FileAccess.file_exists(HINTS_OUT):
        return
    var sig := _choice_pending
    _choice_pending = ""
    _choice_sig = sig
    var g := FileAccess.open(HINTS_OUT, FileAccess.READ)
    if g == null:
        return
    var parsed = JSON.parse_string(g.get_as_text())
    g.close()
    if typeof(parsed) != TYPE_DICTIONARY:
        return
    for row in parsed.get("hints", []):
        var lines := PackedStringArray()
        for e in row.get("equipment", []):
            lines.append("Reward: %s (%s)" % [String(e.get("name", "?")),
                                              String(e.get("kind", "?"))])
        for q in row.get("quests", []):
            lines.append("Unlocks quest: %s" % String(q.get("name", "?")))
        # "" is a real answer: this option grants nothing, do not ask again.
        _rewards[String(row.get("label", ""))] = "
".join(lines)


func _annotate_choice(b: Node) -> void:
    var parts := PackedStringArray()
    if settings.get("audience_rewards", false):
        var reward: String = _rewards.get(_choice_label(b), "")
        if reward != "":
            parts.append(reward)
    var qid := String(b.related_quest_id) if ("related_quest_id" in b) else ""
    if settings.get("audience_rewards", false) and qid != "":
        var loot := _quest_reward_lines(qid)
        if loot != "":
            parts.append(loot)
    if settings.get("audience_outcomes", false) and qid != "":
        var t := _choice_tooltip(qid)
        if t != "":
            parts.append(t)
    var key0 := b.get_instance_id()
    if parts.is_empty():
        # Nothing to add for THIS choice. The buttons are a reused pool, so leaving
        # our previous text in place made an old audience's tooltip haunt a new one
        # ("Unlocks quest: ..." on a choice that unlocks nothing). Put the game's own
        # tooltip back and forget the button.
        var stale: Dictionary = _tips.get(key0, {})
        if not stale.is_empty() and String(b.tooltip_text) == String(stale.get("full", "")):
            b.tooltip_text = String(stale.get("base", ""))
        _tips.erase(key0)
        return
    var extra := "
".join(parts)
    # Keep whatever the game already put there, and add ours below it once.
    #
    # Remembering the last tooltip we wrote is what tells our own text apart from the
    # game's, without needing a visible marker: a "---" separator was showing on its
    # own above our lines whenever the game had put no tooltip at all.
    var key := key0
    var base := String(b.tooltip_text)
    var known: Dictionary = _tips.get(key, {})
    if String(known.get("full", "")) == base:
        base = String(known.get("base", ""))
    var full := extra if base == "" else base + "
---
" + extra
    b.tooltip_text = full
    _tips[key] = {"base": base, "full": full}


# ------------------------------------------------------------------- the kitchen
#
# The game already draws a green thumb on a dish, but only once the knight has been
# served it: `slot.likes.visible = meal_ID in knight.known_liked_meals`. The full
# list is right beside it in `get_liked_meals()`, so we light the same thumb on the
# dishes he likes but has never been offered - the player asked for exactly the
# indicator the game uses when it already knows.

func _update_meal_likes() -> void:
    if not is_instance_valid(_kitchen):
        return
    var knight = _kitchen._current_selected_knight
    if not is_instance_valid(knight):
        return
    var shop = _kitchen.kitchen_shop
    if not is_instance_valid(shop) or not is_instance_valid(shop.equipment_container):
        return
    # Disliked dishes are the other half of the same information: the game knows the
    # full list, and hiding it only means serving a bad meal to find out. Anything a
    # knight does not like is disliked - `liked_meals` is the whole of what he wants.
    var liked: Array = knight.get_liked_meals()
    for slot in shop.equipment_container.get_children():
        if not ("equipment" in slot) or not ("likes" in slot):
            continue
        var item = slot.equipment
        if not is_instance_valid(item) or not (item is Meal):
            continue
        var ok: bool = item.meal_ID in liked
        if is_instance_valid(slot.likes) and ok:
            slot.likes.visible = true
        if is_instance_valid(slot.dislikes) and not ok:
            slot.dislikes.visible = true


## The quest card shows "Mount" with no name. Each chip is a RewardDisplay holding
## its own `QuestReward`, so the item is one hop away - we just put it in the
## tooltip. Reading the resource only; nothing is triggered.
func _update_reward_chips() -> void:
    for i in range(_rewards_shown.size() - 1, -1, -1):
        var chip: Node = _rewards_shown[i]
        if not is_instance_valid(chip):
            _rewards_shown.remove_at(i)
            continue
        var text := _reward_name(chip.quest_reward)
        if text == "":
            continue
        if chip.tooltip_text != text:
            chip.tooltip_text = text
            # A PanelContainer ignores the mouse by default, so no tooltip would ever
            # show: it has to be told to catch it.
            chip.mouse_filter = Control.MOUSE_FILTER_STOP
        # The tooltip on its own was useless on the quest card: the chip sits under
        # the audience popup, and moving the pointer onto it CLOSES that popup, so
        # the player never gets to read it. RewardDisplay writes a generic
        # "NEW_MOUNT" / "NEW_RELIC" into its label - we overwrite it with the real
        # item name, which is the information the player actually wants.
        #
        # Nothing to restore when the setting is unticked: RewardDisplay rewrites the
        # label from scratch every time a quest card is built.
        var lab = chip.reward_label
        if not is_instance_valid(lab):
            continue
        var want := _reward_item_name(chip.quest_reward)
        if want != "" and String(lab.text) != want:
            lab.text = want


## The item's name alone, for the chip itself. The stats stay in the tooltip: the
## chip is a few characters wide and the card lays them out in a FlowContainer.
func _reward_item_name(r) -> String:
    var item = _reward_item(r)
    if not is_instance_valid(item):
        return ""
    return tr(item.name)


## The relic / mount / consumable / quest item a reward hands over, null otherwise.
func _reward_item(r):
    if not is_instance_valid(r):
        return null
    match r.reward_type:
        QuestReward.RewardType.RELIC:
            return r.relic
        QuestReward.RewardType.MOUNT:
            return r.mount
        QuestReward.RewardType.CONSUMABLE:
            return r.consumable
        QuestReward.RewardType.QUEST_ITEM:
            return r.quest_item
    return null


## "Paul (mount, STR +1, CHA -2)" for a reward that hands over an item, "" otherwise.
func _reward_name(r) -> String:
    if not is_instance_valid(r):
        return ""
    var item = null
    var kind := ""
    match r.reward_type:
        QuestReward.RewardType.RELIC:
            item = r.relic
            kind = "relic"
        QuestReward.RewardType.MOUNT:
            item = r.mount
            kind = "mount"
        QuestReward.RewardType.CONSUMABLE:
            item = r.consumable
            kind = "consumable"
        QuestReward.RewardType.QUEST_ITEM:
            item = r.quest_item
            kind = "quest item"
        _:
            return ""
    if not is_instance_valid(item):
        return ""
    var bits := PackedStringArray([kind])
    for id in item.statistics_value:
        var v: int = int(item.statistics_value[id])
        if v != 0:
            bits.append("%s %+d" % [_stat_label(Knight.Statistics.keys()[id]), v])
    if item.bonus_armor != 0:
        bits.append("armor %+d" % item.bonus_armor)
    return "%s (%s)" % [tr(item.name), ", ".join(bits)]


## Names the relic / mount / consumable a quest promises.
##
## The quest card shows a generic "Mount" icon and the game gives no way to hover it
## - hovering closes the audience popup. The item is right there in the resource
## though: `QuestReward` carries `relic` / `mount` / `consumable` / `quest_item`.
##
## Reads `success_rewards` only, which is plain exported data. Never call
## `determine_rewards()`: it FIRES the rewards.
func _quest_reward_lines(qid: String) -> String:
    var quest = GameState.quests_manager.get_quest_from_id(qid)
    if quest == null:
        return ""
    var lines := PackedStringArray()
    for r in quest.success_rewards:
        if not is_instance_valid(r):
            continue
        var item = null
        var kind := ""
        match r.reward_type:
            QuestReward.RewardType.RELIC:
                item = r.relic
                kind = "relic"
            QuestReward.RewardType.MOUNT:
                item = r.mount
                kind = "mount"
            QuestReward.RewardType.CONSUMABLE:
                item = r.consumable
                kind = "consumable"
            QuestReward.RewardType.QUEST_ITEM:
                item = r.quest_item
                kind = "quest item"
            _:
                continue
        if is_instance_valid(item):
            lines.append("Reward: %s (%s)" % [tr(item.name), kind])
    return "
".join(lines)


func _choice_tooltip(qid: String) -> String:
    var quest = GameState.quests_manager.get_quest_from_id(qid)
    if quest == null:
        return ""
    var outcomes: Array = quest.special_outcomes
    if outcomes.is_empty():
        return "No unexpected outcome."
    # One line per outcome, no header and no count: the quest name and duration are
    # already on the card the player is looking at.
    var lines := PackedStringArray()
    for special in outcomes:
        if not is_instance_valid(special):
            continue
        _add_once(lines, "Unexpected outcome: %s" % _outcome_condition(special))
    return "
".join(lines)


## Appends a line unless it is already there.
##
## A quest can carry SEVERAL special outcomes sharing the same trigger - Villador's
## false coins has two, both keyed on the same knight, the game picking between them
## on story state the player cannot see. Listing both printed the very same sentence
## twice. Only IDENTICAL lines collapse: two outcomes with different requirements
## still get a line each.
func _add_once(lines: PackedStringArray, line: String) -> void:
    if not lines.has(line):
        lines.append(line)


## What it takes to trigger an unexpected outcome, in the shortest readable form.
## Named knights first, then a required trait, then a statistic threshold.
## What an unexpected outcome actually does, in one line.
##
## "UNEXPECTED OUTCOME" on its own reads like a jackpot, and it is not: the magpie
## contract carries one that deals 100 damage and hands over nothing at all. The
## player has to be able to tell those two apart BEFORE sending anyone.
func _outcome_effect(special, quest) -> String:
    var bits := PackedStringArray()
    # The outcome brings its OWN damage range and it REPLACES the quest's, so the
    # "1-2" printed on the card says nothing about it.
    if is_instance_valid(special.damage_range):
        var lo: int = int(special.damage_range.min)
        var hi: int = int(special.damage_range.max)
        if hi > 0:
            var dmg := ("%d damage" % hi) if lo == hi else ("%d-%d damage" % [lo, hi])
            # Held against the armour of the knights actually assigned: that is the
            # difference between a scratch and a funeral.
            if quest.quest_can_be_lethal and _would_kill(hi, quest):
                dmg = "DEADLY, " + dmg
            bits.append(dmg)
    for r in special.rewards:
        if not is_instance_valid(r):
            continue
        match r.reward_type:
            QuestReward.RewardType.FUNDS:
                bits.append("%d gold" % int(r.amount))
            QuestReward.RewardType.SATISFACTION:
                bits.append("%+d satisfaction" % int(r.amount))
            QuestReward.RewardType.AFFINITY:
                bits.append("%+d affinity" % int(r.amount))
            QuestReward.RewardType.RELIC:
                bits.append("a relic")
            QuestReward.RewardType.MOUNT:
                bits.append("a mount")
            QuestReward.RewardType.CONSUMABLE:
                bits.append("a consumable")
            QuestReward.RewardType.QUEST_ITEM:
                bits.append("a quest item")
            QuestReward.RewardType.CURRENT_KNIGHT_DEMISSION:
                bits.append("A KNIGHT RESIGNS")
            QuestReward.RewardType.LOCATION_DESTROYED:
                bits.append("A LOCATION IS DESTROYED")
            QuestReward.RewardType.CHARACTER_DEATH:
                bits.append("A DEATH")
    # Story variables and special instructions leave no trace here on purpose: they
    # are invisible to the player anyway, and naming them would spoil the scene.
    if bits.is_empty():
        return "no material effect"
    return ", ".join(bits)


## True when that much damage reaches the armour of anyone currently assigned.
func _would_kill(damage: int, quest) -> bool:
    for k in quest.assigned_knights:
        if is_instance_valid(k) and damage >= int(k.current_armor):
            return true
    return false


func _outcome_condition(special) -> String:
    var who := PackedStringArray()
    for k in special.knights:
        if is_instance_valid(k):
            who.append(tr(k.name))
    if who.size() > 0:
        return ", ".join(who)
    if special.required_knight_characteristics.size() > 0:
        var traits := PackedStringArray()
        for t in special.required_knight_characteristics:
            traits.append(TagManager.CharacterTags.keys()[t])
        return "trait " + ", ".join(traits)
    if special.amount > 0:
        return "%s %s %d" % [_stat_label(Knight.Statistics.keys()[special.stat]),
            (">=" if special.requires_higher else "<="), special.amount]
    return "unknown conditions"


# ---------------------------------------------------------- unexpected outcomes
#
# A quest can hide an unexpected outcome: a special ending that fires when the
# assigned team meets certain conditions (a specific knight, a trait, a statistic
# above a threshold).
#
# That is what explains a plan sending ONE knight on a quest meant for two or three:
# the unexpected outcome beats a plain success.
#
# We read `quest.special_outcomes` and call `are_conditions_met()`, which is a PURE
# function (reads only). Not to be confused with `quest.determine_outcome()`, which
# FREEZES the outcome and triggers damage and rewards: calling it for a preview
# would lock in the quest result before the player confirms.
#
# We announce the fact, never the recipe: conditions sometimes hinge on traits the
# player has not discovered yet.

func _outcomes_of(quest) -> Array:
    var list: Array = []
    if not is_instance_valid(quest):
        return list
    list.append_array(quest.special_outcomes)
    if is_instance_valid(quest.selected_modifier):
        list.append_array(quest.selected_modifier.unexpected_outcomes)
    return list


func _update_outcome() -> void:
    if not is_instance_valid(_outcome_label):
        return
    if not is_instance_valid(_section):
        _outcome_label.text = ""
        return
    _outcome_label.text = _outcome_text(_section.selected_quest)


## Will the team on this quest trigger a special outcome as it stands?
func _outcome_triggered(quest) -> bool:
    if not is_instance_valid(quest):
        return false
    for special in _outcomes_of(quest):
        if is_instance_valid(special) and special.are_conditions_met(quest.assigned_knights):
            return true
    return false


func _outcome_text(quest) -> String:
    var list := _outcomes_of(quest)
    if list.is_empty():
        return "Unexpected outcome: none on this quest"
    # The player asked for the condition itself, not just a count: "possible,
    # conditions not met (1)" told them nothing they could act on.
    var lines := PackedStringArray()
    for special in list:
        if not is_instance_valid(special):
            continue
        if special.are_conditions_met(quest.assigned_knights):
            _add_once(lines, "Unexpected outcome: TRIGGERED - %s"
                             % _outcome_effect(special, quest))
        else:
            var txt := "Unexpected outcome: needs %s" % _outcome_condition(special)
            # Naming a knight who is ALREADY on the quest reads as "it is armed", and
            # it is not. Several outcomes are typed subclasses - Gwendan's tests
            # is_reformed, Arron's tests his state - which weigh something on top of
            # the knight's presence, and this panel only ever read the generic
            # `knights` list. So the line said "needs Gwendan" with Gwendan sitting
            # right there, one line under a score that said FAILURE.
            #
            # What the extra condition IS stays unsaid, on purpose: it often hangs on
            # a trait the player has not discovered, and the panel announces the fact,
            # never the recipe.
            if _named_knights_present(special, quest):
                txt += " - assigned, but another condition on them is not met"
            txt += " (%s)" % _outcome_effect(special, quest)
            _add_once(lines, txt)
    return "
".join(lines)


# ------------------------------------------------------------------ test bench

func _start_bench() -> void:
    var t := Timer.new()
    t.wait_time = 0.25
    t.autostart = true
    t.timeout.connect(_read_command)
    add_child(t)


func _read_command() -> void:
    if not settings.get("test_bench", false):
        return
    if not FileAccess.file_exists(CMD_PATH):
        return
    var f := FileAccess.open(CMD_PATH, FileAccess.READ)
    if f == null:
        return
    var name := f.get_as_text().strip_edges()
    f.close()
    DirAccess.remove_absolute(ProjectSettings.globalize_path(CMD_PATH))
    var reply := ""
    if not COMMANDS.has(name):
        reply = "unknown command: %s\nknown: %s" % [name, ", ".join(COMMANDS)]
    else:
        reply = _run_command(name)
    var o := FileAccess.open(OUT_PATH, FileAccess.WRITE)
    if o != null:
        o.store_string(reply)
        o.close()


func _run_command(name: String) -> String:
    match name:
        "scene":
            var sc := get_tree().current_scene
            return "scene: %s\nsection: %s\nroundtable: %s" % [
                ("(none)" if sc == null else sc.name),
                ("absent" if not is_instance_valid(_section) else "present"),
                ("absent" if not is_instance_valid(_roundtable) else "present")]
        "assign":
            _on_pressed()
            # The solver now runs in the background: the report is not ready yet.
            # Ask again with "report" once the panel stops spinning.
            return ("\"Auto-assignement\" pressed - computing in the background"
                    if _plan_pending else "\"Auto-assignement\" pressed\n" + _last_report)
        "report":
            return ("still computing (%ds)" % int(_plan_wait * 0.3)
                    if _plan_pending else _last_report)
        "score2":
            return _score2()
        "detail":
            return _score_detail()
        "spec":
            return _spec_verify()
        "fast":
            return _fast_verify()
        "inputs":
            var sv = _new_solver()
            if sv == null:
                return "solver.gd could not be loaded"
            sv.snapshot()
            var bad: Array = sv.verify_inputs()
            if bad.is_empty():
                return "inputs match the game for every knight"
            var only_quests := true
            for b in bad:
                if not String(b).begins_with("QUEST "):
                    only_quests = false
            if only_quests:
                return "knights match.
  %s" % "
  ".join(bad)
            return "%d difference(s):
  %s" % [bad.size(), "
  ".join(bad)]
        "plan2":
            return _plan2()
        "inkprobe":
            return _ink_probe()
        "probe2":
            return _probe_shops_and_levels()
        "hints2":
            return _hints_native()
        "levels2":
            return _levels_test()
        "clear":
            _on_clear_pressed()
            return "\"Clear all\" pressed\n" + _last_report
        "load":
            if not is_instance_valid(_home):
                return "main menu absent (already in game?)"
            _home._on_slot_to_resume_selected(0)
            return "loading started from %s (%s)" % [
                _home.name, String(_home.get_script().resource_path)]
        "table":
            if not is_instance_valid(_tower):
                return "tower view absent"
            _tower._open_roundtable_container()
            return "round table opening started"
        "tree":
            return _dump_tree()
        "wheel":
            return _dump_wheel()
        "outcomes":
            return _dump_outcomes()
        "scores":
            return _dump_scores()
        "options":
            if not is_instance_valid(_options_panel):
                return "options screen not built"
            if _options_panel.visible:
                _close_options()
            else:
                _open_options()
            return "options screen: %s" % ("visible" if _options_panel.visible else "hidden")
        "options_state":
            return _dump_options_state()
        "choices":
            return _dump_choices()
        "test_choice":
            return _choice_tooltip("contract_almora_new_ramparts_building")
        "test_outcome":
            return _test_outcome()
        "test_lock":
            return _test_lock()
        "test_required":
            return _test_required()
        "achievements":
            return _dump_achievements()
        "clean_achievements":
            return _clean_achievements()
        "state":
            return _dump_state()
        "quests":
            return _dump_quests()
        "knights":
            return _dump_knights()
        "away":
            return _dump_ongoing()
        "shop":
            var sv2 = _new_solver()
            if sv2 == null:
                return "solver.gd could not be loaded"
            sv2.snapshot()
            return sv2.shop_report()
        "why":
            return _dump_why()
    return "not implemented: " + name


## Audience choice buttons currently on screen, with what we appended to them.
func _dump_choices() -> String:
    if _choices.is_empty():
        return "no choice button on screen (not in an audience?)"
    var out := PackedStringArray()
    for b in _choices:
        if not is_instance_valid(b):
            continue
        var qid := String(b.related_quest_id) if ("related_quest_id" in b) else "-"
        out.append("--- %s | quest: %s ---" % [b.name, ("(none)" if qid == "" else qid)])
        out.append(String(b.tooltip_text))
    return "
".join(out)


func _dump_options_state() -> String:
    var lines := PackedStringArray()
    lines.append("screen visible: %s" % (
        "yes" if is_instance_valid(_options_panel) and _options_panel.visible else "no"))
    for pair in OPTIONS:
        lines.append("  %-24s %s" % [pair[0], settings.get(pair[0], false)])
    return "\n".join(lines)


## Real board state, as the game sees it. This is the reference used to check that
## an application did what it claimed.
func _dump_state() -> String:
    var out := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var names := PackedStringArray()
        for k in q.assigned_knights:
            if is_instance_valid(k):
                var required := " (REQUIRED)" if k in q.requested_knights else ""
                names.append(String(k.character_ink_id) + required)
        if names.size() > 0:
            out.append("%s: %s" % [String(q.quest_id), ", ".join(names)])
    if out.size() == 0:
        out.append("no assignment")
    out.append("--- equipment worn ---")
    for kn in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(kn):
            continue
        var items := PackedStringArray()
        for eq in kn.equipments:
            if is_instance_valid(eq):
                items.append(String(eq.name) + (" [LOCKED]" if eq.is_exclusive else ""))
        out.append("%s: %s" % [String(kn.character_ink_id),
                               ("nothing" if items.size() == 0 else ", ".join(items))])
    return "\n".join(out)


## Exercises detection on a quest that really owns an unexpected outcome.
##
## The tuning save offers none, so we load the resource from disk and query its
## conditions. Read-only: the quest is never added to the game, nothing is modified.
func _test_outcome() -> String:
    var path := "res://content/quests/contract_almora_new_ramparts_building.tres"
    var quest = load(path)
    if quest == null:
        return "test quest not found: " + path
    var out := PackedStringArray()
    out.append("quest: %s (%d slot(s))" % [String(quest.quest_id), quest.nb_requested_knights])
    out.append("declared outcomes: %d" % quest.special_outcomes.size())

    var empty: Array[Knight] = []
    for special in quest.special_outcomes:
        if not is_instance_valid(special):
            continue
        out.append("  named knights: %d | required traits: %d | stat %s %s %d" % [
            special.knights.size(), special.required_knight_characteristics.size(),
            Knight.Statistics.keys()[special.stat],
            (">=" if special.requires_higher else "<="), special.amount])
        out.append("  conditions with an empty team: %s" % str(special.are_conditions_met(empty)))
        for kn in GameState.character_manager.roundtable_knights:
            if not is_instance_valid(kn):
                continue
            var solo: Array[Knight] = [kn]
            out.append("  with %s alone: %s" % [String(kn.character_ink_id),
                                                str(special.are_conditions_met(solo))])
        # Positive path: replay the condition with exactly the knights it demands.
        # Without this check, a detector that always says false would look healthy.
        var wanted := PackedStringArray()
        var ideal_team: Array[Knight] = []
        for k in special.knights:
            if is_instance_valid(k):
                wanted.append(String(k.character_ink_id))
                ideal_team.append(k)
        out.append("  knight(s) demanded: %s" % (
            "none" if wanted.size() == 0 else ", ".join(wanted)))
        out.append("  with the demanded team: %s" % str(
            special.are_conditions_met(ideal_team)))
    return "\n".join(out)


## Live scores, exactly as the panel shows them.
func _dump_scores() -> String:
    if not settings.get("live_score", false):
        return "live_score setting is false"
    _signature = ""          # force a recomputation
    _update_score()
    if _scores.is_empty():
        return "no score (no knight assigned?)"
    var out := PackedStringArray()
    for qid in _scores:
        out.append("%-46s %s" % [qid, _scores[qid]])
    return "\n".join(out)


## Unexpected outcome of every live quest, with the team assigned to it.
func _dump_outcomes() -> String:
    var out := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var list := _outcomes_of(q)
        var team := PackedStringArray()
        for k in q.assigned_knights:
            if is_instance_valid(k):
                team.append(String(k.character_ink_id))
        out.append("%-46s %d outcome(s) | team: %s" % [
            String(q.quest_id), list.size(),
            ("nobody" if team.size() == 0 else ", ".join(team))])
        out.append("    -> %s" % _outcome_text(q))
    return ("no quest" if out.size() == 0 else "\n".join(out))


## Reports what the mod actually placed on the difficulty wheels.
func _dump_wheel() -> String:
    if not settings.get("wheel_numbers", false):
        return "wheel_numbers setting is false"
    if _wheels.size() == 0:
        return "no wheel spotted (is the round table open?)"
    var out := PackedStringArray()
    for w in _wheels:
        if not is_instance_valid(w):
            continue
        var q = w.current_quest
        out.append("--- wheel: quest %s ---" % (
            "(none)" if not is_instance_valid(q) else String(q.quest_id)))
        if w.portions == null:
            out.append("  no slices")
            continue
        for slice in w.portions.get_children():
            if not ("statistic_id" in slice):
                continue
            var stat_name: String = Knight.Statistics.keys()[slice.statistic_id]
            var lab: Label = slice.get_node_or_null(LABEL_NAME)
            var placed := "(no label)"
            if lab != null:
                placed = ("hidden" if not lab.visible else "\"%s\"" % lab.text)
            var diff: String = DifficultyHint.Difficulties.keys()[slice.difficulty]
            var turned := "%.0f deg" % rad_to_deg(lab.rotation) if lab != null else "-"
            out.append("  %-10s difficulty=%-8s visible=%-5s placed=%-8s rotation=%s" % [
                stat_name, diff, str(slice.visible), placed, turned])
    return "\n".join(out)


## Checks that "Clear all" spares a locked item.
##
## Lends Ari's griffin (is_exclusive) to whichever knight comes first, runs the
## global strip, verifies the item is still worn, then puts everything back.
## No recruitment, no gold change: nothing that could fire a Steam achievement -
## achievement_manager listens to no equipment signal.
func _test_lock() -> String:
    var cm = GameState.character_manager
    if cm.roundtable_knights.size() == 0:
        return "round table empty: load a save first"
    var knight = cm.roundtable_knights[0]
    var griffin = load("res://content/equipment/mounts/ari_griffin.tres")
    if griffin == null:
        return "griffin not found"
    if not griffin.is_exclusive:
        return "WARNING: this griffin is not flagged is_exclusive, test is meaningless"

    var mount_before = knight.mount
    knight.mount = griffin
    var out := PackedStringArray()
    out.append("lent to %s: %s (exclusive)" % [String(knight.character_ink_id),
                                               String(griffin.name)])

    var returned := _unequip_all()
    var held: bool = knight.mount == griffin
    out.append("global strip: %d item(s) returned" % returned)
    out.append("VERDICT: %s" % ("lock holds, griffin still worn" if held
                                else "FAILED - the griffin was torn off"))

    knight.mount = mount_before
    out.append("original state restored (mount: %s)" % (
        "none" if not is_instance_valid(mount_before) else String(mount_before.name)))
    return "\n".join(out)


## Checks that "Clear all" spares a knight REQUIRED by the quest.
##
## Temporarily marks a knight as required, runs the global clear and verifies they
## stayed in place, then undoes the marking.
func _test_required() -> String:
    var cm = GameState.character_manager
    if cm.roundtable_knights.size() == 0:
        return "round table empty: load a save first"
    var quest = null
    for q in GameState.quests_manager.current_quests:
        if is_instance_valid(q):
            quest = q
            break
    if quest == null:
        return "no quest available"

    var knight = cm.roundtable_knights[0]
    var out := PackedStringArray()

    _section.update_quests_panel(quest, false)
    _section.assign_knight_to_quest(knight)
    quest.requested_knights.append(knight)
    out.append("%s marked REQUIRED on %s" % [String(knight.character_ink_id),
                                             String(quest.quest_id)])

    var removed := _clear_assignments()
    var held: bool = knight in quest.assigned_knights
    out.append("global clear: %d knight(s) removed" % removed)
    out.append("VERDICT: %s" % ("lock holds, required knight stayed" if held
                                else "FAILED - the required knight was torn off"))

    quest.requested_knights.erase(knight)
    if knight in quest.assigned_knights:
        _section._unassign_knight_from_quest(knight)
    out.append("marking undone, board reset")
    return "\n".join(out)


## Lists unlocked Steam achievements with their date. The timestamp is what tells
## the ones earned by playing from the ones fired by mistake.
func _dump_achievements() -> String:
    if not Steam.isSteamRunning():
        return "Steam is not running"
    var out := PackedStringArray()
    for key in CurrentPlatformManager.achievements_steam_name:
        var name: String = String(CurrentPlatformManager.achievements_steam_name[key])
        var d: Dictionary = Steam.getAchievementAndUnlockTime(name)
        if not bool(d.get("achieved", false)):
            continue
        var t: int = int(d.get("unlocked", 0))
        out.append("%-34s %s" % [name,
            ("unknown date" if t == 0 else Time.get_datetime_string_from_unix_time(t, true))])
    if out.size() == 0:
        return "no achievement unlocked"
    return "%d achievement(s) unlocked:\n%s" % [out.size(), "\n".join(out)]


## Clears achievements fired by mistake. Touches ONLY the named list.
func _clean_achievements() -> String:
    if not Steam.isSteamRunning():
        return "Steam is not running - cannot write"
    var out := PackedStringArray()
    for name in ACHIEVEMENTS_TO_CLEAR:
        var before: Dictionary = Steam.getAchievementAndUnlockTime(name)
        if not bool(before.get("achieved", false)):
            out.append("%s: already absent, nothing done" % name)
            continue
        Steam.clearAchievement(name)
        out.append("%s: cleared" % name)
    Steam.storeStats()
    out.append("--- verification ---")
    for name in ACHIEVEMENTS_TO_CLEAR:
        var after: Dictionary = Steam.getAchievementAndUnlockTime(name)
        out.append("%s: %s" % [name,
            ("STILL PRESENT" if bool(after.get("achieved", false)) else "removed")])
    return "\n".join(out)


## Lists script-bearing nodes: used to find a screen's real file name when suffix
## detection fails.
func _dump_tree() -> String:
    var root := get_tree().current_scene
    if root == null:
        return "no current scene"
    var out := PackedStringArray()
    _walk(root, 0, out)
    return "scene: %s\n%s" % [root.name, "\n".join(out)]


func _walk(n: Node, depth: int, out: PackedStringArray) -> void:
    if depth > 8 or out.size() > 200:
        return
    var s = n.get_script()
    if s != null:
        out.append("%s%s  <- %s" % ["  ".repeat(depth), n.name,
                                    String(s.resource_path).get_file()])
    for c in n.get_children():
        _walk(c, depth + 1, out)


func _dump_quests() -> String:
    var out := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if is_instance_valid(q):
            out.append("%s (slots: %d)" % [String(q.quest_id), q.nb_requested_knights])
    return ("no quest" if out.size() == 0 else "\n".join(out))


func _dump_knights() -> String:
    var out := PackedStringArray()
    for kn in GameState.character_manager.roundtable_knights:
        if is_instance_valid(kn):
            out.append("%s (quest: %s)" % [String(kn.character_ink_id),
                ("none" if not is_instance_valid(kn.assigned_quest)
                 else String(kn.assigned_quest.quest_id))])
    return ("round table empty" if out.size() == 0 else "\n".join(out))


## Why a quest went to the knight it went to.
##
## Every knight, alone on every quest, with the value the planner puts on him and
## whether the quest's unexpected outcome fires for him. A plan that looks wrong is
## either a bug or a knight who was worth more elsewhere, and nothing short of the
## two columns side by side tells the two apart.
func _dump_why() -> String:
    var sv = _new_solver()
    if sv == null:
        return "solver.gd could not be loaded"
    sv.snapshot()
    if sv.quests.is_empty():
        return "no quest on the board"
    var out := PackedStringArray()
    for qi in range(sv.quests.size()):
        var q: Dictionary = sv.quests[qi]
        out.append("%s  (%d slot(s)%s)" % [String(q["id"]), int(q["nb"]),
                                           (", lethal" if bool(q["lethal"]) else "")])
        for ki in range(sv.knights.size()):
            var team := PackedInt32Array()
            team.append(ki)
            var g := PackedInt32Array()
            # Judged in the gear he is WEARING: that is the state the planner starts
            # its own climb from, so anything else would answer a different question.
            for it in sv.knights[ki]["worn"]:
                g.append(it)
            var gear := [g]
            var hit = sv.special_hit(qi, team, gear)
            var r: Dictionary = sv.score_team(qi, team, gear)
            out.append("    %-10s value %9.2f   score %7.2f   %d cycle(s)%s" % [
                String(sv.knights[ki]["id"]), float(sv.quest_value(qi, team, gear)),
                float(r["score"]), int(r["duration"]),
                ("   <- UNEXPECTED OUTCOME" if hit != null else "")])
    return "\n".join(out)


## The quests already under way, with the cycles they still owe and who is on them.
##
## Nothing outside the game can answer this: an ongoing quest has left
## current_quests, and the save records neither its countdown nor its team. So the
## question "how much longer is he away" had no answer at all until now.
func _dump_ongoing() -> String:
    var out := PackedStringArray()
    for q in GameState.quests_manager.ongoing_quests:
        if not is_instance_valid(q):
            continue
        var who := PackedStringArray()
        for k in q.assigned_knights:
            if is_instance_valid(k):
                who.append(String(k.character_ink_id))
        out.append("%-46s %d cycle(s) left  %s" % [
            String(q.quest_id).substr(0, 46), _cycles_left(q), ", ".join(who)])
    return ("no quest under way" if out.size() == 0 else "\n".join(out))


## How many more cycle ends this quest needs.
##
## Simulated rather than derived: update_quests_duration() decrements FIRST and only
## then re-applies the cycle modifier, with a max(0, ...) in between, so a quest with
## a +1 modifier outlives its own counter by a cycle. Bounded, like every loop in
## this mod - an unbounded one once took the game down with no log at all.
func _cycles_left(q) -> int:
    var d: int = int(q.duration)
    var m := 0
    if is_instance_valid(q.selected_modifier):
        m = int(q.selected_modifier.duration_modification)
    var n := 0
    while n < 20:
        n += 1
        d -= 1
        var total: int = d
        if m != 0:
            total = maxi(0, total + m)
        if total <= 0:
            return n
    return n


## True when every knight the outcome names is already on the quest.
##
## An outcome that names nobody is left alone: it never fires anyway, and its real
## condition is a trait or a statistic that this test says nothing about.
func _named_knights_present(special, quest) -> bool:
    if special.knights.is_empty():
        return false
    for k in special.knights:
        if not is_instance_valid(k):
            continue
        if not k in quest.assigned_knights:
            return false
    return true


# ------------------------------------------------------------------ settings

func _load_settings() -> void:
    settings = DEFAULTS.duplicate(true)
    var abs_dir := ProjectSettings.globalize_path(MOD_DIR)
    if not DirAccess.dir_exists_absolute(abs_dir):
        DirAccess.make_dir_recursive_absolute(abs_dir)
    if not FileAccess.file_exists(SETTINGS_PATH):
        _save_settings()
        return
    var f := FileAccess.open(SETTINGS_PATH, FileAccess.READ)
    if f == null:
        return
    var parsed = JSON.parse_string(f.get_as_text())
    f.close()
    if typeof(parsed) != TYPE_DICTIONARY:
        _log("settings.json unreadable, falling back to defaults")
        return
    # Merge: a key introduced by a later version of the mod keeps its default
    # instead of disappearing.
    for k in parsed:
        settings[k] = parsed[k]
    _save_settings()


func _save_settings() -> void:
    var f := FileAccess.open(SETTINGS_PATH, FileAccess.WRITE)
    if f == null:
        return
    f.store_string(JSON.stringify(settings, "  "))
    f.close()


# ------------------------------------------------------------------------- UI
#
# The panel sits in its own CanvasLayer, over the game. It is deliberately opaque
# with a bright border: on the round table background, a plain default panel was
# hard to pick out.

# Alpha kept fairly low: the panel sits over the round table, and the player asked
# to keep seeing the scene through it. The text stays readable because the
# background is very dark and the labels are near-white.
# The in-game panel sits over the round table and the player wants to keep seeing
# the scene through it. Readable anyway: very dark ground, near-white text.
const PANEL_BG := Color(0.07, 0.09, 0.12, 0.32)
# The options screen is a modal over a dimmed backdrop - it stays solid.
const DIALOG_BG := Color(0.07, 0.09, 0.12, 0.97)
const ACCENT := Color(0.96, 0.78, 0.35)


func _framed_box(bg: Color, border: int, border_color: Color) -> StyleBoxFlat:
    var sb := StyleBoxFlat.new()
    sb.bg_color = bg
    sb.set_border_width_all(border)
    sb.border_color = border_color
    sb.set_corner_radius_all(6)
    sb.set_content_margin_all(10)
    return sb


func _style_button(b: Button, font_size: int) -> void:
    b.add_theme_font_size_override("font_size", font_size)
    b.add_theme_color_override("font_color", Color(1, 1, 1))
    b.add_theme_color_override("font_hover_color", ACCENT)
    b.add_theme_stylebox_override("normal", _framed_box(Color(0.13, 0.16, 0.21, 0.50), 2, ACCENT))
    b.add_theme_stylebox_override("hover", _framed_box(Color(0.22, 0.27, 0.34, 0.60), 2, Color(1, 1, 1)))
    b.add_theme_stylebox_override("pressed", _framed_box(Color(0.30, 0.36, 0.44, 0.70), 2, Color(1, 1, 1)))
    b.add_theme_stylebox_override("disabled", _framed_box(Color(0.13, 0.16, 0.21, 0.30), 2, Color(0.5, 0.5, 0.5)))


# The panel can be dragged by its top bar and resized by the bottom-right grip.
# Position and size are stored in settings.json, so they survive a restart.
const MIN_PANEL := Vector2(230, 120)

var _dragging := false
var _resizing := false
var _grab_offset := Vector2.ZERO
var _edge := Vector2i.ZERO      # which border is being dragged, -1/0/1 per axis
var _box: VBoxContainer         # panel contents, measured to auto-fit the height


func _build_ui() -> void:
    _layer = CanvasLayer.new()
    _layer.layer = 100
    _layer.name = "SovereignModLayer"
    add_child(_layer)

    # A plain Panel, not a PanelContainer: the latter stretches EVERY child to its
    # own size, which would blow the resize grip up to cover the whole panel.
    _panel = Panel.new()
    # Free-floating: anchored top-left and driven by position/size, otherwise the
    # anchors would fight the drag.
    _panel.set_anchors_preset(Control.PRESET_TOP_LEFT)
    _panel.position = _stored_vector("panel_position", Vector2(-1, -1))
    _panel.size = _stored_vector("panel_size", MIN_PANEL)
    _panel.visible = false
    _panel.add_theme_stylebox_override("panel", _framed_box(PANEL_BG, 2, ACCENT))
    _panel.mouse_default_cursor_shape = Control.CURSOR_MOVE
    _panel.gui_input.connect(_on_panel_drag_input)
    _layer.add_child(_panel)

    var margin := MarginContainer.new()
    margin.set_anchors_preset(Control.PRESET_FULL_RECT)
    # Wider than EDGE, so the resize band along the border is never covered by a
    # button that would swallow the click first.
    for side in ["margin_left", "margin_right", "margin_top", "margin_bottom"]:
        margin.add_theme_constant_override(side, 14)
    margin.mouse_filter = Control.MOUSE_FILTER_IGNORE
    _panel.add_child(margin)

    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 3)
    box.mouse_filter = Control.MOUSE_FILTER_IGNORE
    margin.add_child(box)
    _box = box

    # Two rows of two. The panel is narrow and the four actions belong together:
    # stacking them pushed the score - the thing the player is actually here for -
    # off the bottom of the box.
    var row1 := HBoxContainer.new()
    row1.add_theme_constant_override("separation", 3)
    box.add_child(row1)

    _button = Button.new()
    _button.text = "Auto-assignement"
    _button.custom_minimum_size = Vector2(0, 48)
    _button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _style_button(_button, 19)
    _button.pressed.connect(_on_pressed)
    row1.add_child(_button)

    _clear_button = Button.new()
    _clear_button.text = "Clear"
    _clear_button.custom_minimum_size = Vector2(0, 48)
    _clear_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _style_button(_clear_button, 19)
    _clear_button.pressed.connect(_on_clear_pressed)
    row1.add_child(_clear_button)

    var row2 := HBoxContainer.new()
    row2.add_theme_constant_override("separation", 3)
    box.add_child(row2)

    _meal_button = Button.new()
    _meal_button.text = "Meal"
    _meal_button.custom_minimum_size = Vector2(0, 40)
    _meal_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _style_button(_meal_button, 17)
    _meal_button.pressed.connect(_on_meal_pressed)
    row2.add_child(_meal_button)

    _train_button = Button.new()
    _train_button.text = "Train"
    _train_button.custom_minimum_size = Vector2(0, 40)
    _train_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _style_button(_train_button, 17)
    _train_button.pressed.connect(_on_train_pressed)
    row2.add_child(_train_button)

    # Score first: it is the number the player is actually after. The advice below
    # is context, not the headline.
    # The score alone did not say WHICH quest it scored - the panel sits far from the
    # card. Name and figure form one block, so no rule between them.
    _quest_label = _panel_label(box, 17, ACCENT)
    _quest_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
    _score_label = _panel_label(box, 20, ACCENT)
    _score_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
    # Order asked for by the player: the score, then what could go unexpectedly, then
    # the advice that costs gold and lives in another room.
    _score_sep = _panel_rule(box)
    _outcome_label = _panel_label(box, 15, Color(0.85, 0.85, 0.85))
    # One label per topic instead of one block of text: that is what lets a thin rule
    # sit between them, and lets an empty topic disappear with its rule.
    _meal_sep = _panel_rule(box)
    _meal_label = _panel_label(box, 15, Color(0.85, 0.85, 0.85))
    _levels_sep = _panel_rule(box)
    _levels_label = _panel_label(box, 15, Color(0.85, 0.85, 0.85))
    _buy_sep = _panel_rule(box)
    _advice_label = _panel_label(box, 15, Color(0.85, 0.85, 0.85))
    _status = _panel_label(box, 15, Color(1.0, 0.55, 0.45))

    # No widget for either action: grab an edge or a corner to resize, grab anywhere
    # else to move. Buttons consume their own clicks first, and labels let them
    # through (Label defaults to MOUSE_FILTER_IGNORE).


## Feeds one knight, paying for the dish exactly as the kitchen would.
##
## Who eats follows the player's rule: the knight a meal would lift to a better
## outcome, if the plan found one, and otherwise the lowest affinity on the table -
## a meal is worth +0.5 to a score and +1.5 to affinity, so when it buys no tier it
## is spent on the relationship that needs it most.
func _on_meal_pressed() -> void:
    _buttons_busy(true)
    # Every path below leaves through here, so the panel can never stay locked.
    _meal_apply()
    _buttons_busy(false)


func _meal_apply() -> void:
    if not _room_open(Room.Room_ID.KITCHEN_ROOM):
        _set_status("the kitchen is not open yet")
        return
    if _meal_served():
        _set_status("a meal has already been served this cycle")
        return
    var knight = _meal_target()
    if knight == null:
        _set_status("no one left to feed")
        return
    var meal = _cheapest_liked_meal(knight)
    if meal == null:
        _set_status("no meal on the menu")
        return
    var cost: int = int(meal.cost)
    var fm = GameState.funds_manager
    if not fm.are_funds_sufficient(cost):
        _set_status("%s costs %d gold, you have %d" % [
            _item_label(String(meal.name)), cost, int(fm.current_funds)])
        return
    # Paid for, not conjured. give_meal() alone would hand out the score and the
    # affinity for free, which is not the same game.
    fm.update_funds(-cost)
    knight.give_meal(meal)
    # Annotated, never inferred: `in` over an untyped array has no set type, and a
    # parse error here does not degrade the mod, it deletes it - the autoload fails
    # and the panel simply never appears.
    var liked: bool = meal.meal_ID in knight.known_liked_meals
    _set_status("%s: %s, %d gold%s" % [
        String(knight.character_ink_id).to_upper(), _item_label(String(meal.name)),
        cost, ("" if liked else " (not a known favourite)")])


## Whether the kitchen has already served this cycle.
##
## One meal per cycle for the whole table, which is the game's own rule: kitchen.gd
## disables its cook button as soon as ANY knight has eaten. The counter resets on its
## own - character_manager clears `has_eaten` on every knight when the cycle turns - so
## the button comes back by itself and needs no bookkeeping here.
func _meal_served() -> bool:
    var cm = GameState.character_manager
    var everyone = cm.recruitable_knights if "recruitable_knights" in cm else cm.roundtable_knights
    for k in everyone:
        if is_instance_valid(k) and k.has_eaten:
            return true
    return false


## The knight the meal should go to, or null when there is nobody left to feed.
func _meal_target():
    var cm = GameState.character_manager
    # The plan names a knight only when the meal actually lifts an outcome - and it
    # stores a real null when it found none, which String() would turn into "<null>".
    var named = _last_plan.get("meal")
    var wanted: String = "" if named == null else String(named)
    if wanted != "":
        var k = cm.get_roundtable_knight_from_name(wanted)
        if is_instance_valid(k) and not k.has_eaten and not k.is_dead:
            return k
    var best = null
    for k in cm.roundtable_knights:
        if not is_instance_valid(k) or k.is_dead or k.has_eaten:
            continue
        if best == null or float(k.current_affinity) < float(best.current_affinity):
            best = k
    return best


## The cheapest dish this knight is KNOWN to like; the cheapest of any kind when none
## of their tastes have been discovered yet - a meal still carries its +0.5 either way.
func _cheapest_liked_meal(knight):
    var menu = GameState.inventory_manager.available_meals
    var liked = null
    var any = null
    for meal in menu:
        if not is_instance_valid(meal):
            continue
        if any == null or int(meal.cost) < int(any.cost):
            any = meal
        if not meal.meal_ID in knight.known_liked_meals:
            continue
        if liked == null or int(meal.cost) < int(liked.cost):
            liked = meal
    return liked if liked != null else any


## Sends the idlest, greenest knight to the training ground.
##
## Only knights with no quest are considered: the game clears `assigned_quest` when it
## puts someone in training, so taking the lowest level outright would quietly pull a
## knight out of a team the planner had just built.
func _on_train_pressed() -> void:
    _buttons_busy(true)
    _train_apply()
    _buttons_busy(false)


func _train_apply() -> void:
    if not _room_open(Room.Room_ID.TRAINING_GROUND):
        _set_status("the training ground is not open yet")
        return
    var cm = GameState.character_manager
    var best = null
    for k in cm.roundtable_knights:
        if not is_instance_valid(k) or k.is_dead:
            continue
        if is_instance_valid(k.assigned_quest):
            continue
        if best == null or int(k.current_level) < int(best.current_level) or (
                int(k.current_level) == int(best.current_level)
                and int(k.current_xp) < int(best.current_xp)):
            best = k
    if best == null:
        _set_status("every knight is on a quest")
        return
    # One trainee at a time, which is the rule the training ground itself applies.
    for k in cm.roundtable_knights:
        if is_instance_valid(k):
            k.is_training = (k == best)
    _set_status("%s is training (level %d)" % [
        String(best.character_ink_id).to_upper(), int(best.current_level)])


## Locks the whole panel while one action runs, and unlocks it afterwards.
##
## Pressing Clear in the middle of a plan, or Meal twice in a row, acts on a board the
## other action is still rearranging. The four buttons are one control surface and they
## are enabled and disabled as one - except Meal, which stays out while the kitchen has
## already served.
func _buttons_busy(busy: bool) -> void:
    for b in [_button, _clear_button, _train_button]:
        if is_instance_valid(b):
            b.disabled = busy
    if is_instance_valid(_meal_button):
        _meal_button.disabled = busy or _meal_served()


## Whether a room of the tower is open for business.
func _room_open(room_id: int) -> bool:
    var tm = GameState.tower_manager
    if tm == null or not tm.has_method("is_room_unlocked"):
        return true
    return bool(tm.is_room_unlocked(room_id))


## A faint rule between two sections of the panel.
func _panel_rule(parent: Node) -> HSeparator:
    var sep := HSeparator.new()
    var line := StyleBoxLine.new()
    line.color = Color(1, 1, 1, 0.16)
    line.thickness = 1
    sep.add_theme_stylebox_override("separator", line)
    # 2 px, not the default: the rule's own height stacks with the box separation
    # above AND below it, so a generous value here reads as a big empty band.
    sep.add_theme_constant_override("separation", 2)
    sep.mouse_filter = Control.MOUSE_FILTER_IGNORE
    parent.add_child(sep)
    return sep


func _panel_label(parent: Node, font_size: int, color: Color) -> Label:
    var lab := Label.new()
    lab.text = ""
    lab.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    lab.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    lab.add_theme_font_size_override("font_size", font_size)
    lab.add_theme_color_override("font_color", color)
    # Godot leaves 3 px between the lines of a Label, which at this font size opened a
    # visible hole between every entry of a list. The panel is a dense readout, not a
    # page of prose.
    lab.add_theme_constant_override("line_spacing", 0)
    parent.add_child(lab)
    return lab


## Reads a Vector2 back from settings; falls back when absent or malformed.
func _stored_vector(key: String, fallback: Vector2) -> Vector2:
    var v = settings.get(key)
    if typeof(v) == TYPE_ARRAY and v.size() == 2:
        return Vector2(float(v[0]), float(v[1]))
    return fallback


func _store_geometry() -> void:
    settings["panel_position"] = [_panel.position.x, _panel.position.y]
    settings["panel_size"] = [_panel.size.x, _panel.size.y]
    _save_settings()


## Grabbing the panel: a left or right edge resizes the width, anywhere else moves.
## The height is not draggable - it follows the content through _fit_height().
const EDGE := 12.0

func _edge_at(local: Vector2) -> Vector2i:
    var ex := 0
    if local.x <= EDGE:
        ex = -1
    elif local.x >= _panel.size.x - EDGE:
        ex = 1
    # No vertical handle on purpose: the height follows the content by itself
    # (_fit_height), so dragging it would only fight the auto-fit.
    return Vector2i(ex, 0)


func _cursor_for(edge: Vector2i) -> int:
    return Control.CURSOR_MOVE if edge.x == 0 else Control.CURSOR_HSIZE


func _on_panel_drag_input(event: InputEvent) -> void:
    if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
        if event.pressed:
            _edge = _edge_at(event.position)
            _dragging = _edge == Vector2i.ZERO
            _resizing = not _dragging
            _grab_offset = _panel.position - _panel.get_global_mouse_position()
        else:
            if _dragging or _resizing:
                _store_geometry()
            _dragging = false
            _resizing = false
    elif event is InputEventMouseMotion:
        if _dragging:
            _panel.position = _panel.get_global_mouse_position() + _grab_offset
            _keep_on_screen()
        elif _resizing:
            _resize_to(_panel.get_global_mouse_position())
        else:
            # Hovering: show which action the current spot would trigger.
            _panel.mouse_default_cursor_shape = _cursor_for(_edge_at(event.position))


## Moves the caught edge to the mouse, keeping the opposite one anchored.
func _resize_to(mouse: Vector2) -> void:
    var left := _panel.position.x
    var top := _panel.position.y
    var right := left + _panel.size.x
    var bottom := top + _panel.size.y

    if _edge.x == 1:
        right = max(mouse.x, left + MIN_PANEL.x)
    elif _edge.x == -1:
        left = min(mouse.x, right - MIN_PANEL.x)
    _panel.position = Vector2(left, top)
    _panel.size.x = right - left


## Shrinks the panel to what it actually shows.
##
## The content varies a lot: with nothing to buy it is three lines, with a full
## shopping list it is eight. A fixed height left a large empty band under the last
## line, which is what the player flagged.
func _fit_height() -> void:
    if not is_instance_valid(_box):
        return
    var wanted: float = _box.get_combined_minimum_size().y + 28.0   # 2 x 14 px margin
    if absf(_panel.size.y - wanted) > 1.0:
        _panel.size.y = wanted


## Keeps a sliver of the panel reachable: dragged fully off-screen, it could never
## be grabbed again, and the only way back would be editing settings.json by hand.
func _keep_on_screen() -> void:
    var screen := _panel.get_viewport_rect().size
    _panel.position.x = clamp(_panel.position.x, 40.0 - _panel.size.x, screen.x - 60.0)
    _panel.position.y = clamp(_panel.position.y, 0.0, screen.y - 40.0)


# ---------------------------------------------------------------- options screen
#
# Opened on demand: from the "Mod options" button on the game's home screen, or
# with F9 at any time. It used to pop up by itself on startup, which the player
# found intrusive - a screen you did not ask for is in the way.
#
# Boxes act AT ONCE: every feature re-reads its setting at run time, so nothing
# needs a restart.

var _options_layer: CanvasLayer
var _options_panel: Control
var _options_button: Button


func _build_options() -> void:
    _options_layer = CanvasLayer.new()
    _options_layer.layer = 200
    _options_layer.name = "SovereignModOptions"
    add_child(_options_layer)

    # Entry point on the game's home screen. Shown only while the main menu is up,
    # so it never sits on top of the game itself.
    # Anchored to the bottom CENTRE, next to the mail and Discord icons, so it keeps
    # its place next to them whatever the window size - a corner offset in pixels
    # would drift away on another resolution.
    _options_button = Button.new()
    _options_button.text = "Mod options"
    _options_button.custom_minimum_size = Vector2(200, 44)
    _options_button.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
    _options_button.grow_horizontal = Control.GROW_DIRECTION_BOTH
    _options_button.grow_vertical = Control.GROW_DIRECTION_BEGIN
    _style_button(_options_button, 17)
    _options_button.pressed.connect(_open_options)
    _options_button.visible = false
    _options_layer.add_child(_options_button)

    # Backdrop that swallows the mouse: without it clicks fall through to the game.
    var backdrop := ColorRect.new()
    backdrop.color = Color(0, 0, 0, 0.6)
    backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
    backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
    _options_layer.add_child(backdrop)
    _options_panel = backdrop
    _options_panel.visible = false

    var frame := PanelContainer.new()
    frame.set_anchors_preset(Control.PRESET_CENTER)
    frame.grow_horizontal = Control.GROW_DIRECTION_BOTH
    frame.grow_vertical = Control.GROW_DIRECTION_BOTH
    frame.add_theme_stylebox_override("panel", _framed_box(DIALOG_BG, 2, ACCENT))
    backdrop.add_child(frame)

    var margin := MarginContainer.new()
    for side in ["margin_left", "margin_right", "margin_top", "margin_bottom"]:
        margin.add_theme_constant_override(side, 26)
    frame.add_child(margin)

    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 12)
    box.custom_minimum_size = Vector2(620, 0)
    margin.add_child(box)

    var title := Label.new()
    title.text = "Sovereign QoL mod"
    title.add_theme_font_size_override("font_size", 26)
    title.add_theme_color_override("font_color", ACCENT)
    box.add_child(title)

    box.add_child(HSeparator.new())

    for pair in OPTIONS:
        box.add_child(_option_box(String(pair[0]), String(pair[1]), 22))

    box.add_child(HSeparator.new())

    var close := Button.new()
    close.text = "Validate"
    close.custom_minimum_size = Vector2(0, 52)
    _style_button(close, 22)
    close.pressed.connect(_close_options)
    box.add_child(close)


## A checkbox bound to a setting key. `icon_max_width` is what actually enlarges the
## tick mark: raising the font size alone leaves a tiny box next to big text.
func _option_box(key: String, label: String, font_size: int) -> CheckBox:
    var cb := CheckBox.new()
    cb.text = label
    cb.button_pressed = bool(settings.get(key, false))
    cb.add_theme_font_size_override("font_size", font_size)
    cb.add_theme_constant_override("icon_max_width", font_size * 2)
    cb.add_theme_constant_override("h_separation", 14)
    cb.add_theme_color_override("font_color", Color(0.94, 0.94, 0.94))
    cb.add_theme_color_override("font_hover_color", ACCENT)
    cb.toggled.connect(_on_option_toggled.bind(key))
    return cb


func _on_option_toggled(enabled: bool, key: String) -> void:
    settings[key] = enabled
    _save_settings()
    _log("setting %s -> %s" % [key, enabled])


## Sits to the right of the two social icons. Their row is centred and lives at a
## fixed fraction of the height, so we follow the same fractions instead of hard
## pixels: the button stays put from 1080p to ultrawide.
func _place_options_button() -> void:
    var screen := _options_button.get_viewport_rect().size
    var size := _options_button.size
    _options_button.position = Vector2(
        screen.x * 0.5 + 120.0,
        screen.y * 0.895 - size.y * 0.5)


func _open_options() -> void:
    if is_instance_valid(_options_panel):
        _options_panel.visible = true


func _close_options() -> void:
    if is_instance_valid(_options_panel):
        _options_panel.visible = false


func _unhandled_input(event: InputEvent) -> void:
    if event is InputEventKey and event.pressed and not event.echo \
            and event.keycode == KEY_F9:
        if is_instance_valid(_options_panel):
            if _options_panel.visible:
                _close_options()
            else:
                _open_options()
        get_viewport().set_input_as_handled()


## `quiet` for the loader: it speaks every 0.3 s and would drown the log.
# ------------------------------------------------------- the GDScript port, step 1
#
# scoring.gd grades a team the way the game itself does. This command prints its
# verdict for every quest on the board so it can be held against what the Python
# solver says for the same board: the two must agree to the hundredth before the
# port goes any further.

var _scoring = null                # scoring.gd, loaded once
var _sides := {}                   # side modules, by file name
var _ink = null                    # ink.gd, kept because loading the story costs 50 ms
var _native = null                 # solver.gd mid-plan, advanced a frame at a time


## The mod is loaded from disk through override.cfg, so res:// may or may not reach
## a sibling file that is not inside the .pck. Try it, then fall back to reading the
## file and compiling it by hand - which settles the question either way.
func _load_scoring():
    return _load_side("scoring.gd")


func _load_side(fname: String):
    if _sides.has(fname):
        return _sides[fname]
    var mod = load("res://sovereign_mod/" + fname)
    if mod == null:
        # Fall back to reading and compiling by hand, in case res:// only ever
        # reaches inside the .pck on some install.
        var p := OS.get_executable_path().get_base_dir().path_join("sovereign_mod/" + fname)
        var f := FileAccess.open(p, FileAccess.READ)
        if f == null:
            _log(fname + " not found at " + p)
            _sides[fname] = null
            return null
        var src := f.get_as_text()
        f.close()
        var gd := GDScript.new()
        gd.source_code = src
        if gd.reload() != OK:
            _log(fname + " failed to compile")
            _sides[fname] = null
            return null
        mod = gd
    _sides[fname] = mod
    _log(fname + " loaded")
    return mod


func _outcome_name(v: int) -> String:
    for k in Quest.QuestOutcomes.keys():
        if Quest.QuestOutcomes[k] == v:
            return String(k)
    return str(v)


func _score2() -> String:
    var S = _load_scoring()
    if S == null:
        return "scoring.gd could not be loaded"
    var out := PackedStringArray([S.ping()])
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var team := []
        for k in q.assigned_knights:
            if is_instance_valid(k):
                team.append(k)
        if team.is_empty():
            continue
        var r: Dictionary = S.score(q, team)
        var names := PackedStringArray()
        for k in team:
            names.append(String(k.character_ink_id))
        var label := "UNEXPECTED_OUTCOME"
        if not bool(r["special"]):
            label = _outcome_name(int(r["outcome"]))
        out.append("%-46s %7.2f  %-18s %s" % [
            String(q.quest_id).substr(0, 46), float(r["score"]), label,
            ",".join(names)])
    return "\n".join(out)


## Every term of the score, knight by knight, so a disagreement with the Python
## solver can be pinned on a line rather than guessed at.
func _score_detail() -> String:
    var S = _load_scoring()
    if S == null:
        return "scoring.gd could not be loaded"
    var out := PackedStringArray()
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var team := []
        for k in q.assigned_knights:
            if is_instance_valid(k):
                team.append(k)
        if team.is_empty():
            continue
        var r: Dictionary = S.score(q, team)
        out.append("### %s   total %.2f" % [String(q.quest_id), float(r["score"])])
        var req: Dictionary = S.requirements_of(q)
        var reqs := PackedStringArray()
        for st_ in req:
            reqs.append("%s=%d" % [Knight.Statistics.keys()[st_], int(req[st_])])
        out.append("    requis %s | demandes %d | equipe %d"
                   % [" ".join(reqs), int(q.nb_requested_knights), team.size()])
        var per: Dictionary = r["per_knight"]
        for k in team:
            var ks = per.get(k)
            if ks == null:
                continue
            var bits := PackedStringArray()
            bits.append("presence %.2f" % ks.presence_score)
            if ks.meal_score != 0.0:
                bits.append("repas %.2f" % ks.meal_score)
            for st_ in ks.stats_score:
                bits.append("%s %.2f" % [Knight.Statistics.keys()[st_],
                                         float(ks.stats_score[st_])])
            for d in [["bonus+", ks.known_bonuses], ["bonus?", ks.unknown_bonuses],
                      ["malus+", ks.known_maluses], ["malus?", ks.unknown_maluses]]:
                var dict: Dictionary = d[1]
                for tag in dict:
                    bits.append("%s %s %.2f" % [d[0],
                                                TagManager.CharacterTags.keys()[tag],
                                                float(dict[tag])])
            out.append("    %-11s %6.2f   %s" % [String(k.character_ink_id),
                                                 ks.get_total_score(),
                                                 "  ".join(bits)])
    return "\n".join(out)


## Holds special.gd against the game's own special-cases function, for every knight
## on the board. The port has to reproduce it exactly before it can be trusted on
## loadouts the knight is not wearing.
func _spec_verify() -> String:
    var SP = _load_side("special.gd")
    if SP == null:
        return "special.gd could not be loaded"
    var lines := PackedStringArray()
    var checked := 0
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var team := []
        for k in q.assigned_knights:
            if is_instance_valid(k):
                team.append(k)
        if team.is_empty():
            continue
        checked += team.size()
        for p in SP.verify(q, team):
            lines.append("  %s  %s" % [String(q.quest_id).substr(0, 40), String(p)])
    if lines.is_empty():
        return "special.gd matches the game on all %d knight(s)" % checked
    return "%d disagreement(s) over %d knight(s):
%s" % [
        lines.size(), checked, "
".join(lines)]


## The GDScript planner's fast path, held against scoring.gd - which asks the game
## itself. The search reads its numbers from arrays rather than from game objects,
## so it has to be proved to give the same answer before it is allowed to choose
## anything.
func _new_solver():
    var SV = _load_side("solver.gd")
    var SP = _load_side("special.gd")
    var SC = _load_side("scoring.gd")
    if SV == null or SP == null or SC == null:
        return null
    var s = SV.new()
    s.setup(SP, SC)
    s.trace_on = bool(settings.get("trace", false))
    return s


func _fast_verify() -> String:
    var s = _new_solver()
    if s == null:
        return "solver.gd could not be loaded"
    var t0 := Time.get_ticks_usec()
    var summary: String = s.snapshot()
    var snap_us := Time.get_ticks_usec() - t0
    var rows: Array = s.verify_against_scoring()
    if rows.is_empty():
        return "%s
snapshot in %d us - no quest has a team to check" % [summary, snap_us]
    var lines := PackedStringArray(["%s | snapshot %d us" % [summary, snap_us]])
    var worst := 0.0
    for r in rows:
        var d: float = abs(float(r["fast"]) - float(r["slow"]))
        if d > worst:
            worst = d
        var flag := "ok"
        if bool(r["special_fast"]) != bool(r["special_slow"]):
            flag = "SPECIAL OUTCOME DISAGREES"
        elif d > 0.005:
            # This USED to be excused: the game leaves a quest's duration at -1 until
            # the cycle resolves, so its own answer for these three tags was worth
            # nothing and the two sides could not agree. scoring.gd now works the
            # durations out itself (durations_of()), so both models grade them - and a
            # gap here is a real fault again, on either side. The tag is still named,
            # because it says where to look first. See solver.gd, DURATION_TAGS.
            flag = ("DIFFERS - duration tag" if bool(r.get("duration_tags", false))
                    else "DIFFERS")
        lines.append("  %-44s fast %8.2f  game %8.2f  %dc  %s" % [
            String(r["id"]).substr(0, 44), float(r["fast"]), float(r["slow"]),
            int(r["duration"]), flag])
        if flag != "ok" and r.has("names"):
            var names: PackedStringArray = r["names"]
            var fe: PackedFloat64Array = r["fast_each"]
            var se: PackedFloat64Array = r["slow_each"]
            for i in range(names.size()):
                var a: float = (fe[i] if i < fe.size() else 0.0)
                var b: float = (se[i] if i < se.size() else 0.0)
                lines.append("      %-12s fast %7.2f  game %7.2f  %s%s" % [
                    names[i], a, b, ("<<<" if abs(a - b) > 0.005 else ""),
                    ("  [protagonist]" if int(r["protagonist"]) == i else "")])
    lines.append("  worst gap: %.4f" % worst)
    return "
".join(lines)


## The GDScript planner, end to end. Printed so it can be held against the plan the
## Python solver produces for the same board.
func _plan2() -> String:
    # Two planners at once means two snapshots and two caches in memory for no
    # reason. The button already refuses to start a second one.
    if _native != null:
        return "a plan is already being worked out"
    var s = _new_solver()
    if s == null:
        return "solver.gd could not be loaded"
    s.snapshot()
    var r: Dictionary = s.plan()
    var lines := PackedStringArray(["plan in %d ms | value %.2f" % [
        int(r["us"]) / 1000, float(r["value"])],
        "  teams %d ms | search %d ms (%d nodes) | gear %d ms (%d climbs) | pool %d | %d distinct evals" % [
            int(r["us_teams"]) / 1000, int(r["us_search"]) / 1000, int(r["nodes"]),
            int(r["us_gear"]) / 1000, int(r["climbs"]), int(r["pool"]), int(r["evals"])]])
    for a in r["assignments"]:
        var who := PackedStringArray()
        var team: PackedStringArray = a["knights"]
        for i in range(team.size()):
            var worn: PackedStringArray = a["gear"][i]
            who.append("%s%s" % [String(team[i]).to_upper(),
                                 ("" if worn.is_empty() else " [" + ", ".join(worn) + "]")])
        lines.append("  %-44s %s%6.2f  %dc (base %d)  %s" % [
            String(a["quest_id"]).substr(0, 44),
            ("UNEXPECTED " if bool(a["special"]) else ""),
            float(a["score"]), int(a["duration"]), int(a["base_duration"]),
            " | ".join(who)])
    var info: Dictionary = r.get("meal_info", {})
    if not info.is_empty():
        lines.append("  meal: %s on %s (%s -> %s)" % [
            String(info["knight"]).to_upper(), String(info["quest_id"]).substr(0, 34),
            String(info["from"]), String(info["to"])])
    for b in r.get("buy", []):
        lines.append("  buy: %s, %d gold for %s (%s)" % [
            String(b["name"]), int(b["cost"]), String(b["for"]).to_upper(),
            String(b["to"])])
    var levels: Dictionary = r.get("levels", {})
    for kname in levels:
        lines.append("  level up %s: %s" % [String(kname).to_upper(),
                                            ", ".join(PackedStringArray(levels[kname]))])
    return "
".join(lines)


# ------------------------------------------------- the plan, computed in GDScript
#
# The planner used to be an external program. It now runs here, which removes the
# binary from the mod entirely and, measured on a full round table, answers in a
# third of the time. The old path is kept as a fallback: if solver.gd cannot be
# loaded the mod still works with st.exe next to it, and a player who has one and
# not the other is never left with nothing.

## Runs the whole plan, on the main thread. Measured at 2.6 s on ten knights, which
## is why it is worth doing here rather than blocking on a process.
func _plan_native() -> Dictionary:
    var s = _new_solver()
    if s == null:
        return {}
    s.snapshot()
    return s.plan()


## Applies a plan built here. Nothing is looked up by name: the planner hands back
## the game's own objects, so there is no way to place the wrong relic or miss a
## knight whose name the save spells differently.
func _apply_native(plan: Dictionary) -> String:
    if not plan.has("assignments"):
        return "no plan"
    var placed := 0
    var equipped := 0
    var refused := 0
    for entry in plan["assignments"]:
        var quest = entry["quest_ref"]
        if not is_instance_valid(quest):
            continue
        # assign_knight_to_quest() works on the selected quest, so it has to be
        # selected before each batch.
        _section.update_quests_panel(quest, false)
        for k in entry["knight_refs"]:
            if is_instance_valid(k):
                _section.assign_knight_to_quest(k)
                placed += 1
    # Equipment second: a knight has to be on the quest before his gear sticks.
    for entry in plan["assignments"]:
        var krefs: Array = entry["knight_refs"]
        var place: Array = entry["place"]
        for j in range(krefs.size()):
            for item in place[j]:
                if _equip(krefs[j], item):
                    equipped += 1
                else:
                    refused += 1
    var report := "%d knight(s) assigned, %d item(s) equipped" % [placed, equipped]
    if refused > 0:
        report += " (%d item(s) the game would not place)" % refused
    return report


## The live score, computed here rather than by an external process.
##
## st.py had to be handed the board state through a file and answer through another,
## because it could not see the game. scoring.gd asks the game directly, so the
## figure is both exact and immediate - and one more reason for the binary to go.
func _score_live() -> void:
    var S = _load_scoring()
    if S == null:
        return
    _scores.clear()
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        var team := []
        for k in q.assigned_knights:
            if is_instance_valid(k):
                team.append(k)
        if team.is_empty():
            continue
        var r: Dictionary = S.score(q, team)
        var qid := String(q.quest_id)
        if bool(r["special"]):
            _scores[qid] = "UNEXPECTED OUTCOME"
        else:
            _scores[qid] = "%.2f/10 - %s" % [float(r["score"]),
                                             _outcome_wording(int(r["outcome"]))]


## Quest.QuestOutcomes as the panel says it, in the wording the game itself uses.
func _outcome_wording(v: int) -> String:
    match v:
        Quest.QuestOutcomes.CRITICAL_SUCCESS:
            return "CRITICAL SUCCESS"
        Quest.QuestOutcomes.GREAT_SUCCESS:
            return "GREAT SUCCESS"
        Quest.QuestOutcomes.SUCCESS:
            return "SUCCESS"
        Quest.QuestOutcomes.FAILURE:
            return "FAILURE"
        Quest.QuestOutcomes.MAJOR_FAILURE:
            return "MAJOR FAILURE"
        Quest.QuestOutcomes.CRITICAL_FAILURE:
            return "CRITICAL FAILURE"
        Quest.QuestOutcomes.UNEXPECTED_OUTCOME:
            return "UNEXPECTED OUTCOME"
    return "-"


## Can the mod reach the story script from inside the game?
##
## Naming what an audience option offers is the last thing the external solver still
## does, and it does it by reading the compiled ink. If that text is reachable here,
## the analysis can be ported and the binary goes away entirely.
func _ink_probe() -> String:
    var out := PackedStringArray()
    var tried := ["res://content/story/master.ink.json",
                  "res://content/master.ink.json",
                  "res://master.ink.json"]
    var dir := DirAccess.open("res://.godot/imported")
    if dir != null:
        dir.list_dir_begin()
        var f := dir.get_next()
        while f != "":
            if f.begins_with("master.ink.json") and f.ends_with(".res"):
                tried.append("res://.godot/imported/" + f)
            f = dir.get_next()
        dir.list_dir_end()
    else:
        out.append("res://.godot/imported not listable")
    for p in tried:
        var r = ResourceLoader.load(p)
        if r == null:
            out.append("%-58s -> null" % p)
            continue
        var props := PackedStringArray()
        for d in r.get_property_list():
            var n := String(d.get("name", ""))
            if n != "" and not n.begins_with("script") and not n.begins_with("resource"):
                props.append(n)
        out.append("%-58s -> %s  [%s]" % [p, r.get_class(), ", ".join(props)])
    # And the ink runtime, if the game keeps one around.
    for n in ["InkPlayer", "Ink", "Story"]:
        var node = get_node_or_null("/root/" + n)
        if node != null:
            out.append("autoload /root/%s present (%s)" % [n, node.get_class()])
    return "
".join(out)


## Is the shop stock, and the pending level-ups, reachable from the round table?
##
## Both came from the Python plan. The stock lives on the game-state scene rather
## than behind a manager, so the question is which node holds it and under what
## name; the level-ups are a threshold on the knight.
func _probe_shops_and_levels() -> String:
    var out := PackedStringArray(["--- game state tree ---"])
    var names := PackedStringArray()
    var walk := [GameState]
    while not walk.is_empty():
        var w = walk.pop_back()
        if not is_instance_valid(w):
            continue
        names.append(String(w.name))
        for c in w.get_children():
            walk.append(c)
    out.append("  " + ", ".join(names))
    out.append("--- stock-looking properties ---")
    var stack := [GameState]
    while not stack.is_empty():
        var n = stack.pop_back()
        if not is_instance_valid(n):
            continue
        for c in n.get_children():
            stack.append(c)
        for d in n.get_property_list():
            var pname := String(d.get("name", ""))
            var low := pname.to_lower()
            # The stock is not called anything obvious: forge_relics, stables_mounts,
            # witch_tower_consumables, each with _act_2 and _act_3 variants.
            if not ("available" in low or "shop" in low or "stock" in low
                    or "equipment" in low or "meal" in low or "relic" in low
                    or "mount" in low or "consumable" in low or "forge" in low
                    or "stable" in low or "witch" in low):
                continue
            var v = n.get(pname)
            var size := -1
            if v is Array:
                size = v.size()
            elif v is Dictionary:
                size = v.size()
            if size >= 0:
                out.append("  %s.%s  (%s, %d)" % [n.name, pname,
                    ("Array" if v is Array else "Dictionary"), size])
    out.append("--- stock shape ---")
    var im = GameState.inventory_manager
    for pname in ["forge_relics", "stables_mounts", "witch_tower_consumables"]:
        if not (pname in im):
            out.append("  %s: absent" % pname)
            continue
        var d = im.get(pname)
        if not (d is Dictionary):
            out.append("  %s: %s" % [pname, str(typeof(d))])
            continue
        out.append("  %s: %d entree(s)" % [pname, d.size()])
        var shown := 0
        for k in d:
            if shown >= 3:
                break
            shown += 1
            var v = d[k]
            var kd := "?"
            if k is Resource:
                kd = "%s '%s' cost=%s excl=%s" % [k.get_class(), str(k.get("name")),
                                                  str(k.get("cost")), str(k.get("is_exclusive"))]
            else:
                kd = str(k)
            var vd := str(v)
            if v is Object:
                vd = "%s [%s]" % [v.get_class(), str(v.get("item") if "item" in v else "-")]
            out.append("     %s  ->  %s" % [kd, vd.substr(0, 90)])
    out.append("  act = %s | funds = %s" % [
        str(GameState.act_manager.get("current_act") if "current_act" in GameState.act_manager else "?"),
        str(GameState.funds_manager.get("current_funds") if "current_funds" in GameState.funds_manager else "?")])
    out.append("--- levels ---")
    for k in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(k):
            continue
        var lvl: int = int(k.current_level)
        var xp: int = int(k.current_xp)
        var need := -1
        var thr = LevelUpManager.level_xp_threshold
        if thr is Dictionary and thr.has(lvl):
            need = int(thr[lvl])
        out.append("  %-11s level %d  xp %d/%s  mastered %s" % [
            String(k.character_ink_id), lvl, xp,
            ("?" if need < 0 else str(need)), str(k.mastered_stats)])
    return "
".join(out)


## The audience-option reader, tested without an audience.
##
## An audience cannot be opened by script - achievement_manager listens for it and
## the achievements are real and permanent - so the analysis is exercised on labels
## written into hints_in.json instead, and held against what the Python solver says
## for the same lines.
func _hints_native() -> String:
    var INK = _load_side("ink.gd")
    if INK == null:
        return "ink.gd could not be loaded"
    var f := FileAccess.open(HINTS_IN, FileAccess.READ)
    if f == null:
        return "write the labels to hints_in.json first"
    var parsed = JSON.parse_string(f.get_as_text())
    f.close()
    if typeof(parsed) != TYPE_DICTIONARY:
        return "hints_in.json malformed"
    var labels: Array = parsed.get("choices", [])
    var ink = INK.new()
    var t0 := Time.get_ticks_msec()
    var story_len: int = ink.text().length()
    var t_load := Time.get_ticks_msec() - t0
    t0 = Time.get_ticks_msec()
    var res: Dictionary = ink.hints(labels)
    var t_scan := Time.get_ticks_msec() - t0
    var out := PackedStringArray(["story %d chars loaded in %d ms | scan %d ms" % [
        story_len, t_load, t_scan]])
    for l in labels:
        var row = res.get(l)
        if row == null:
            out.append("  %-56s NOT FOUND" % String(l).substr(0, 56))
            continue
        var eq: Array = row["equipment"]
        var qs: Array = row["quests"]
        out.append("  %-56s equipment=%s quests=%s" % [
            String(l).substr(0, 56),
            ("-" if eq.is_empty() else ", ".join(PackedStringArray(eq))),
            ("-" if qs.is_empty() else ", ".join(PackedStringArray(qs)))])
        if row.has("debug"):
            out.append("        %s" % String(row["debug"]))
    return "
".join(out)


## Names what each visible option offers, from the compiled story, in process.
##
## Returns false when the story cannot be read at all, so the external solver can
## still answer for a player who has one and not the other.
func _read_rewards_native(labels: Array) -> bool:
    var INK = _load_side("ink.gd")
    if INK == null:
        return false
    if _ink == null:
        _ink = INK.new()
        if _ink.text().is_empty():
            _ink = null
            return false
    var found: Dictionary = _ink.hints(labels)
    for lab in labels:
        var row = found.get(lab)
        var lines := PackedStringArray()
        if row != null:
            for id in row["equipment"]:
                lines.append("Reward: %s" % _equipment_name(String(id)))
            for qid in row["quests"]:
                lines.append("Unlocks quest: %s" % _quest_name(String(qid)))
        # "" is a real answer - this option grants nothing - and stops us asking again.
        _rewards[String(lab)] = "
".join(lines)
    return true


## The item's name as the player reads it. The script names items in its own casing,
## and the translation keys are the upper-case id with _NAME on the end.
func _equipment_name(id: String) -> String:
    var key := id.to_upper() + "_NAME"
    var shown := tr(key)
    if shown != key and shown != "":
        return shown
    return id.capitalize()


## An item's name as the player reads it, in the language the game is running in.
##
## `Equipment.name` holds a translation KEY - "CRAB_ARMOR", "HUNTING_BOW" - and not a
## name, so printing it raw put English identifiers in a French game. `tr()` is the
## game's own lookup, which makes the panel read exactly like the shop and the
## inventory do, whatever the language. A key the table does not know comes back
## unchanged; that is tidied into something readable rather than shown as an id.
func _item_label(key: String) -> String:
    if key == "":
        return "?"
    var shown := tr(key)
    if shown != key and shown != "":
        return shown
    return key.capitalize()


func _quest_name(qid: String) -> String:
    var q = GameState.quests_manager.get_quest_from_id(qid)
    if is_instance_valid(q):
        var shown := tr(String(q.quest_name))
        if shown != "":
            return shown
    return qid


## Applies the plan the moment the last climb is done.
func _finish_native_plan() -> void:
    var solver = _native
    _native = null
    _plan_pending = false
    if is_instance_valid(_button):
        _buttons_busy(false)
    var plan: Dictionary = solver.plan_result()
    if plan.get("assignments", []).is_empty():
        _set_status("no assignment found")
        return
    # Goes through _update_advice so the buying and meal sections are refreshed
    # rather than left showing what the previous cycle suggested.
    _update_advice(plan)
    _last_report = ("cleared %d knight(s) and %d item(s)
"
                    % [_plan_freed, _plan_stripped]) + _apply_native(plan)
    _set_status("")


## Exercises the level-up advice by pretending every knight has a point banked.
##
## The advice only fires for a knight who actually owes a level-up, and on a mature
## roster nobody does - seven of these ten are at the cap. Without this the code
## would ship having produced nothing but an empty dictionary.
func _levels_test() -> String:
    if _native != null:
        return "a plan is already being worked out"
    var s = _new_solver()
    if s == null:
        return "solver.gd could not be loaded"
    s.snapshot()
    var r: Dictionary = s.plan()
    var real: Dictionary = r.get("levels", {})
    var out := PackedStringArray(["real advice: %s" % ("none - nobody owes a level-up"
                                                       if real.is_empty() else str(real))])
    for n in [1, 2]:
        var forced: Dictionary = s.level_advice_test(n)
        out.append("with %d point(s) each:" % n)
        if forced.is_empty():
            out.append("  nothing worth raising")
        for kname in forced:
            out.append("  %-11s %s" % [String(kname).to_upper(),
                                       ", ".join(PackedStringArray(forced[kname]))])
    return "
".join(out)


func _set_status(msg: String, quiet: bool = false) -> void:
    if is_instance_valid(_status):
        _status.text = msg
    if not quiet:
        _log(msg)


func _log(msg: String) -> void:
    print("[SovereignMod] ", msg)

# --------------------------------------------------- round table: ghost portrait
#
# roundtable_container.roundtable_swip_characters_animation() slides the outgoing
# knight off screen and hides them through a CHAINED tween callback:
#
#     tween.chain().tween_callback(previous_knight.hide)
#
# The callback is the ONLY thing that hides the portrait. If that tween does not run
# to completion the knight simply stays on screen, stacked under the next one, until
# a later swipe happens to select them again - which is exactly the reported
# symptom. Nothing re-checks it afterwards.
#
# We do not try to stop the tween from being interrupted; we repair the state. Both
# portraits are legitimately visible DURING the 0.4 s slide, so a stray is only
# hidden once it has been seen unselected for several consecutive ticks - well past
# the end of any legitimate animation.

const GHOST_TICKS := 5            # ~1.5 s at one tick per 0.3 s

var _ghost_ticks := {}            # portrait instance id -> consecutive stray ticks


func _fix_ghost_portraits() -> void:
    if not is_instance_valid(_roundtable) or not _roundtable.is_visible_in_tree():
        _ghost_ticks.clear()
        return
    var box = _roundtable.characters_container
    var kn = _roundtable.current_selected_knight
    if not is_instance_valid(box) or not is_instance_valid(kn):
        _ghost_ticks.clear()
        return
    # Compared by ink id rather than through get_knight_portrait(): that helper
    # indexes a dictionary directly and would fault on a knight it does not hold.
    var want := String(kn.character_ink_id).to_lower()
    var still := {}
    for portrait in box.get_children():
        if not portrait.visible:
            continue
        var who = portrait.character
        if is_instance_valid(who) and String(who.character_ink_id).to_lower() == want:
            continue
        var id: int = portrait.get_instance_id()
        var n: int = int(_ghost_ticks.get(id, 0)) + 1
        if n >= GHOST_TICKS:
            portrait.hide()
            _log("cleared a portrait the swipe left behind")
            continue
        still[id] = n
    _ghost_ticks = still


# ------------------------------------------------- end-of-cycle results speed-up
#
# The game has a fast-forward button, but it only multiplies tweens and
# AnimationPlayers (`FAST_FORWARD_SPEED_UP = 8.0`, applied through
# `set_speed_scale`). The recap is actually paced by ~15 hard-coded
# `await get_tree().create_timer(...)` waits in cycle_transition.gd and
# gauge_controller.gd - 0.25 s between tag groups, 0.15 s after each score bubble -
# and the button does not touch a single one of them. That is why holding it down
# still feels slow.
#
# Engine.time_scale is the one lever that also covers SceneTreeTimer, so it speeds
# up the dead time too. It stacks with the game's own button: tweens end up at
# speed x 8.
#
# We never restore to a hard-coded 1.0. The game exposes its own speed setting
# (1.0 / 1.2 / 1.5 in the menu, `Engine.time_scale = user_settings.game_speed`), and
# resetting to 1.0 would silently undo the player's choice.

var _time_scale_ours := false


func _process(_delta: float) -> void:
    # A plan in progress gets a slice of each frame. Twelve milliseconds leaves the
    # game most of its budget and still finishes in about the same wall time as the
    # blocking version - only without the freeze.
    if _native != null:
        if _native.plan_step(12):
            _finish_native_plan()

    # One meal a cycle. Nothing signals the mod when the cycle turns and the game
    # clears every appetite, so the button's state is read from the world each frame
    # rather than remembered - it greys itself out and comes back on its own.
    if is_instance_valid(_meal_button) and not _plan_pending:
        _meal_button.disabled = _meal_served()

    # Two screens want the same lever, so the strongest wins rather than the last
    # one tested: they cannot be up at once today, and if that ever changes the
    # player still gets the speed they asked for.
    var speed := 1.0
    if (settings.get("fast_results", false) and is_instance_valid(_cycle_end)
            and _cycle_end.is_visible_in_tree()):
        speed = maxf(speed, float(settings.get("result_speed", 4.0)))
    if settings.get("fast_ending", false) and _ending_visible():
        speed = maxf(speed, float(settings.get("ending_speed", 3.0)))
    if speed > 1.0:
        Engine.time_scale = _player_speed() * speed
        _time_scale_ours = true
    elif _time_scale_ours:
        _restore_time_scale()


## True while any screen of the ending chain is on display.
func _ending_visible() -> bool:
    for n in _endings:
        if is_instance_valid(n) and n.is_visible_in_tree():
            return true
    return false


func _restore_time_scale() -> void:
    if not _time_scale_ours:
        return
    _time_scale_ours = false
    Engine.time_scale = _player_speed()


## Safety net: if the mod is ever unloaded while the recap is up, the game must not
## be left running at the boosted speed.
func _exit_tree() -> void:
    _restore_time_scale()


## The speed the player picked in the game's own settings, so we scale FROM it
## instead of overwriting it.
func _player_speed() -> float:
    var sc = get_node_or_null("/root/SettingsController")
    if sc == null or not ("current_user_settings" in sc):
        return 1.0
    var us = sc.current_user_settings
    if us == null or not ("game_speed" in us):
        return 1.0
    return maxf(0.1, float(us.game_speed))




# ------------------------------------------------------- round table detection
#
# We do NOT graft onto the game's interface hierarchy: the panel lives in our own
# CanvasLayer, shown only while the round table is up. Far less brittle than
# injecting into their containers.

func _script_is(n: Node, suffix: String) -> bool:
    var s = n.get_script()
    if s == null:
        return false
    return String(s.resource_path).ends_with(suffix)


# FULL paths: "home.gd" alone also matched scenes/home/debug_menu_home.gd, and the
# mod ended up driving the wrong screen.
func _on_node_added(n: Node) -> void:
    for path in ENDING_SCRIPTS:
        if _script_is(n, path):
            _endings.append(n)
            break
    if _script_is(n, "scenes/roundtable/quests_presentation_section.gd"):
        _section = n
        # Not while a plan is being computed: clearing the board rebuilds this
        # section, and "ready" would wipe the loader off the panel.
        if not _plan_pending:
            _set_status("ready")
    elif _script_is(n, "scenes/roundtable/roundtable_container.gd"):
        _roundtable = n
    elif _script_is(n, "scenes/home/home.gd"):
        _home = n
    elif _script_is(n, "scenes/tower_view/tower_view_container.gd"):
        _tower = n
    elif _script_is(n, "scenes/roundtable/difficulty_hint_wheel.gd"):
        _wheels.append(n)
    elif _script_is(n, "scenes/dialogue_interface/choice_button.gd"):
        _choices.append(n)
    elif _script_is(n, "scenes/roundtable/reward_display.gd"):
        _rewards_shown.append(n)
    elif _script_is(n, "scenes/rooms/shops/kitchen.gd"):
        _kitchen = n
    elif _script_is(n, "scenes/cycle_transition/cycle_transition.gd"):
        _cycle_end = n


func _on_node_removed(n: Node) -> void:
    if n == _section:
        _section = null
    elif n == _roundtable:
        _roundtable = null
    elif n == _home:
        _home = null
    elif n == _kitchen:
        _kitchen = null
    elif n == _cycle_end:
        _cycle_end = null
        _restore_time_scale()
    elif n in _endings:
        _endings.erase(n)
        # One screen of the chain closing does not mean the ending is over: the next
        # one is usually already up, and _process puts the speed back on its own.
        _restore_time_scale()


# ------------------------------------------------------------------ the button

func _on_pressed() -> void:
    if not is_instance_valid(_section):
        _set_status("round table not found")
        return
    if _plan_pending:
        return
    _buttons_busy(true)
    # Always start from an empty board. Applying on top of an existing assignment left
    # leftovers behind: a knight the plan does not use stayed on his quest, and gear the
    # plan wanted was still worn by someone else, so it could not be handed over.
    # Clearing first makes the button idempotent - press it twice, get the same board.
    _plan_freed = _clear_assignments()
    _plan_stripped = _unequip_all()
    var solver = _new_solver()
    if solver != null:
        # Snapshot and candidate search happen now - half a second at worst - and
        # the equipment work is then spread over the frames that follow, so the game
        # keeps drawing. Doing the whole three seconds in one call froze it, which
        # is the very thing that got fixed when the solver was external.
        solver.snapshot()
        solver.plan_start()
        _native = solver
        _plan_pending = true
        _plan_wait = 0
        _spin = 0
        _tick_loader()
        return
    # No solver.gd: fall back on the external program, which still works.
    if not _start_solver():
        _buttons_busy(false)
        return
    _plan_pending = true
    _plan_wait = 0
    _spin = 0
    _tick_loader()



## ------------------------------------------------------------------ the solver
##
## The mod is loaded from disk, next to the game executable, so the solver sits at
## <game>/sovereign_mod/solver/st.exe. Nothing to configure and nothing to install:
## it carries its own Python runtime.

var _solver_exe := ""                  # program to launch
var _solver_head: Array = []           # arguments that come before the command
var _cache_ready := false


## Locates the solver once. Returns false when it is missing, which is the one
## situation the player must be told about: the mod was unpacked incompletely.
func _resolve_solver() -> bool:
    if _solver_exe != "":
        return true
    var custom: String = String(settings.get("solver", ""))
    var py: String = String(settings.get("python", ""))
    if custom != "" and FileAccess.file_exists(custom):
        if custom.get_extension().to_lower() == "py":
            # Running from the Python sources: needs an interpreter.
            if not FileAccess.file_exists(py):
                _log("solver set to a .py file but python was not found")
                return false
            _solver_exe = py
            _solver_head = [custom]
        else:
            _solver_exe = custom
            _solver_head = []
        return true
    var exe := OS.get_executable_path().get_base_dir().path_join("sovereign_mod/solver/st.exe")
    if not FileAccess.file_exists(exe):
        _log("solver not found at " + exe)
        return false
    _solver_exe = exe
    _solver_head = []
    return true


func _solver_argv(extra: Array) -> PackedStringArray:
    var out := PackedStringArray()
    for x in _solver_head:
        out.append(String(x))
    for x in extra:
        out.append(String(x))
    return out


## The solver reads the game's own data files to answer. It extracts what it needs
## from the player's copy on first launch, and again after the game is updated -
## a few seconds, at boot, where a hitch goes unnoticed.
func _ensure_cache() -> bool:
    if _cache_ready:
        return true
    if not _resolve_solver():
        return false
    var out := []
    var state := ""
    if OS.execute(_solver_exe, _solver_argv(["cache"]), out, true) == 0:
        for line in out:
            var t := String(line).strip_edges()
            if t != "":
                state = t
    if state == "ok":
        _cache_ready = true
        return true
    if state == "nogame":
        _log("game files not found - the mod must sit in the game folder")
        return false
    var report := []
    if OS.execute(_solver_exe, _solver_argv(["setup"]), report, true) != 0:
        _log("could not read the game data:")
        for line in report:
            _log(String(line))
        return false
    _cache_ready = true
    _log("data cache built from the game files")
    return true


## Starts the solver WITHOUT blocking, and returns whether it got going.
##
## It searches every combination of knights and equipment and takes about twenty
## seconds on a full round table - `OS.execute` froze the whole game for all of it.
## Same mechanism as the live score: the answer's arrival is signalled by the file
## appearing, so the old one goes first. The solver writes it atomically, which is
## what makes "the file is there" mean "the file is complete".
func _start_solver() -> bool:
    if not _ensure_cache():
        _set_status("solver not found - reinstall the mod in the game folder")
        return false
    var args := ["cycle", str(settings.get("slot", 1)),
                 "--json=" + ProjectSettings.globalize_path(PLAN_PATH)]
    # The save is written at the end of a cycle, so a room opened DURING the cycle
    # still reads as locked - st.py then ignored the kitchen and never advised a
    # meal, and ignored a shop that had just opened. We hold the truth in memory, so
    # we hand it over: one --room= per room actually unlocked.
    for room in GameState.tower_manager.unlocked_rooms:
        if not is_instance_valid(room):
            continue
        var id := String(room.resource_path).get_file().get_basename()
        if id != "":
            args.append("--room=" + id)
    DirAccess.remove_absolute(ProjectSettings.globalize_path(PLAN_PATH))
    var pid := OS.create_process(_solver_exe, _solver_argv(args))
    if pid <= 0:
        _set_status("the solver could not start")
        return false
    return true


## Watched on every display tick. Applies the plan as soon as it lands.
func _poll_solver() -> void:
    if not _plan_pending:
        return
    if _native != null:
        # Progress is made in _process, one frame at a time; this only draws the
        # loader and gives up if something goes badly wrong.
        _plan_wait += 1
        if _plan_wait > PLAN_MAX_WAIT:
            _native = null
            _plan_pending = false
            if is_instance_valid(_button):
                _buttons_busy(false)
            _set_status("the planner timed out")
            return
        _tick_loader()
        return
    if FileAccess.file_exists(PLAN_PATH):
        _plan_pending = false
        if is_instance_valid(_button):
            _buttons_busy(false)
        var plan := _read_plan()
        if plan.is_empty():
            return
        # The report is kept for the test bench but not shown: on a normal run it only
        # restated what the player just watched happen on the board. The status line
        # stays for problems, which is when it actually carries information.
        _last_report = ("cleared %d knight(s) and %d item(s)
" % [_plan_freed, _plan_stripped]) + _apply(plan)
        _set_status("")
        return
    _plan_wait += 1
    if _plan_wait > PLAN_MAX_WAIT:
        _plan_pending = false
        if is_instance_valid(_button):
            _buttons_busy(false)
        _set_status("the solver timed out")
        return
    _tick_loader()


## The loader. There is no progress to report - the solver gives no intermediate
## signal - so it shows that something IS happening, and for how long.
func _tick_loader() -> void:
    _spin = (_spin + 1) % SPINNER.size()
    _set_status("%s  working out the best assignment...  %ds"
                % [SPINNER[_spin], int(_plan_wait * 0.3)], true)


func _read_plan() -> Dictionary:
    var f := FileAccess.open(PLAN_PATH, FileAccess.READ)
    if f == null:
        _set_status("plan.json unreadable")
        return {}
    var parsed = JSON.parse_string(f.get_as_text())
    f.close()
    if typeof(parsed) != TYPE_DICTIONARY:
        _set_status("plan.json malformed")
        return {}
    return parsed


func _on_clear_pressed() -> void:
    if not is_instance_valid(_section):
        _set_status("round table not found")
        return
    _buttons_busy(true)
    var freed := _clear_assignments()
    var stripped := _unequip_all()
    _last_report = "%d knight(s) removed, %d item(s) returned" % [freed, stripped]
    _set_status("")
    _buttons_busy(false)


# ------------------------------------------------------------ clearing the board

## Clears assignments from every quest.
##
## SPARES `requested_knights`: those are the knights the quest REQUIRES. The game
## protects them everywhere (knight_slot.gd locks them, assign_knight_to_quest
## refuses to replace them); tearing them out left the player in a state the
## interface cannot rebuild.
func _clear_assignments() -> int:
    var freed := 0
    for q in GameState.quests_manager.current_quests:
        if not is_instance_valid(q):
            continue
        for k in q.assigned_knights.duplicate():
            if not is_instance_valid(k):
                continue
            if k in q.requested_knights:
                continue
            _section._unassign_knight_from_quest(k)
            freed += 1
    # The game's own unassign only redraws the slots of the quest that happens to be
    # SELECTED - every other card keeps showing knights that are no longer on it. The
    # board was genuinely empty and the screen said otherwise, which is the same thing
    # as being broken from where the player sits.
    _refresh_board()
    return freed


## Redraws what the model already says: the quest cards and the knight portraits.
func _refresh_board() -> void:
    if not is_instance_valid(_section):
        return
    if _section.has_method("update_quest_presentations"):
        _section.update_quest_presentations()
    if _section.has_method("update_knight_vignettes"):
        _section.update_knight_vignettes()


## Returns every worn item to stock.
##
## SPARES `is_exclusive` items - Ari's griffin and friends: they belong to the
## knight, and the game's own interface already greys out their button
## (equipment_button.gd: `disabled = equipment.is_exclusive`).
func _unequip_all() -> int:
    var stripped := 0
    for knight in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(knight):
            continue
        for equipment in knight.equipments:
            if not is_instance_valid(equipment):
                continue
            if equipment.is_exclusive:
                continue
            if _unequip(knight, equipment):
                stripped += 1
    return stripped


## Goes through the game's own function (armor + return to stock) when available.
func _unequip(knight, equipment) -> bool:
    if is_instance_valid(_roundtable) and _roundtable.has_method("_on_equipment_assignation_requested"):
        _roundtable.current_selected_knight = knight
        _roundtable._on_equipment_assignation_requested(equipment, knight, null)
        return true
    if equipment is Relic:
        knight.relic = null
    elif equipment is Mount:
        knight.mount = null
    elif equipment is Consumable:
        knight.consumable = null
    else:
        return false
    knight.current_armor -= equipment.bonus_armor
    GameState.inventory_manager.add_equipments_to_inventory([equipment])
    return true


# ------------------------------------------------------------------ applying

func _find_quest(entry: Dictionary):
    var qm = GameState.quests_manager
    var wanted_id := String(entry.get("quest_id", ""))
    var wanted_path := String(entry.get("quest_path", ""))
    for q in qm.current_quests:
        if not is_instance_valid(q):
            continue
        # quest_id first, resource path as fallback: both come from the same st.py
        # cache, but either one may be missing on an injected quest.
        if wanted_id != "" and String(q.quest_id) == wanted_id:
            return q
        if wanted_path != "" and String(q.resource_path) == wanted_path:
            return q
    return null


## Applies the plan. Returns a short report, shown under the button.
func _apply(plan: Dictionary) -> String:
    var cm = GameState.character_manager
    var assignments: Array = plan.get("assignments", [])
    var equipment: Dictionary = plan.get("equipment", {})

    # 1) wipe the board: drop existing assignments, otherwise we stack on top of
    #    whatever the player had already placed.
    _clear_assignments()

    # 2) assignments, quest by quest.
    var placed := 0
    var missing: Array[String] = []
    for entry in assignments:
        var quest = _find_quest(entry)
        if quest == null:
            missing.append(String(entry.get("quest_id", "?")))
            continue
        # assign_knight_to_quest() works on `selected_quest`, so the quest has to be
        # selected before each batch of knights.
        _section.update_quests_panel(quest, false)
        for kname in entry.get("knights", []):
            var knight = cm.get_roundtable_knight_from_name(String(kname))
            if knight == null:
                missing.append(String(kname))
                continue
            _section.assign_knight_to_quest(knight)
            placed += 1

    # 3) equipment: relics and mounts only. Consumables are single-use and the
    #    player wants to arbitrate them personally (see README, "consumables").
    var equipped := 0
    var to_buy: Array[String] = []
    for kname in equipment:
        var knight = cm.get_roundtable_knight_from_name(String(kname))
        if knight == null:
            continue
        for item in equipment[kname]:
            var kind := String(item.get("kind", ""))
            if kind != "relic" and kind != "mount":
                continue
            var res = load(String(item.get("path", "")))
            if res == null:
                missing.append(String(item.get("id", "?")))
                continue
            # An item the player does not own yet still has to be BOUGHT: equipping
            # it would hand it over for free.
            #
            # The plan's `owned` flag is NOT the authority here - st.py reads the
            # save file, and the save lags behind the game. An item bought this very
            # cycle still reads `owned: false`, and the mod was refusing to equip
            # what the player had just paid for. `_item_available()` asks live
            # memory instead, which cannot be fooled either way: an item still on a
            # shop shelf is not in the player's inventory.
            if not _item_available(res):
                if not bool(item.get("owned", true)):
                    to_buy.append(_item_label(String(item.get("name",
                                                            item.get("id", "")))))
                continue
            if _equip(knight, res):
                equipped += 1

    _update_advice(plan)

    var report := "%d knight(s) assigned, %d item(s) equipped" % [placed, equipped]
    if not to_buy.is_empty():
        report += " - still to buy: " + ", ".join(to_buy)
    if not missing.is_empty():
        report += " - not found: " + ", ".join(missing)
    return report


## Advice: what the plan recommends BUYING, and who to feed.
##
## The mod applies neither: purchases cost gold and happen in shops, meals are
## served in the kitchen. It only says what to do; the player keeps their purse.
func _update_advice(plan: Dictionary) -> void:
    if not is_instance_valid(_advice_label):
        return
    _last_plan = plan
    _plan_board = _quest_set()
    if not settings.get("buying_advice", false):
        _meal_label.text = ""
        _levels_label.text = ""
        _advice_label.text = ""
        return

    # A meal is only advised when it lifts a quest to a better outcome. When it does
    # not, the line is dropped entirely rather than saying "none": an empty section
    # takes its separator with it.
    var meal = plan.get("meal")
    if meal == null:
        _meal_label.text = ""
    else:
        var who := String(meal)
        who = who.substr(0, 1).to_upper() + who.substr(1)
        var dishes: Array = plan.get("meal_plats", [])
        # The GDScript planner says WHY: a meal is only ever advised when it lifts an
        # outcome, so naming the tier it buys is the whole point of the line.
        var info: Dictionary = plan.get("meal_info", {})
        var why := ""
        if not info.is_empty():
            why = " - %s to %s" % [String(info.get("from", "")), String(info.get("to", ""))]
        elif not dishes.is_empty():
            why = " (" + ", ".join(PackedStringArray(dishes)) + ")"
        _meal_label.text = "Meal: %s%s" % [who, why]

    # Which statistic to raise on the next level: st.py works it out, the player
    # spends the point in the tower. The mod only tells.
    #
    # A statistic the player has ALREADY raised drops off the list: st.py reads the
    # save, so it keeps advising a point that was spent this very cycle. `stats_base`
    # is what the save held, equipment excluded - the same basis as
    # `get_statistic_value_from_id(id, false)`.
    var ups := PackedStringArray()
    var levels: Dictionary = plan.get("levels", {})
    var was: Dictionary = plan.get("stats_base", {})
    for kname in levels:
        var knight = GameState.character_manager.get_roundtable_knight_from_name(String(kname))
        var before: Dictionary = was.get(kname, {})
        var todo := PackedStringArray()
        for stat in levels[kname]:
            # Not `name`: this script extends Node, which already has one.
            var stat_name := String(stat)
            if knight != null and before.has(stat_name):
                # Look the enum VALUE up by key, never its position in keys().
                var id: int = int(Knight.Statistics.get(stat_name, -1))
                if id >= 0 and knight.get_statistic_value_from_id(id, false) > int(before[stat_name]):
                    continue
            todo.append(_stat_label(stat_name))
        if not todo.is_empty():
            ups.append("Level up %s: %s" % [String(kname).to_upper(), ", ".join(todo)])
    _levels_label.text = "
".join(ups)

    # Drop what the player has ALREADY bought: st.py reads the save, which lags
    # behind the game, so it keeps recommending an item paid for this very cycle.
    var purchases := []
    var spend := 0
    # "achats" from the Python plan, "buy" from the one computed here.
    var offers: Array = plan.get("achats", [])
    if offers.is_empty():
        offers = plan.get("buy", [])
    for a in offers:
        # An empty path would make load() spam the log on every tick. Older plans,
        # written before st.py carried the path, have none.
        var path := String(a.get("path", ""))
        if path != "":
            var res = load(path)
            if res != null and _item_owned(res):
                continue
        purchases.append(a)
        spend += int(a.get("cost", 0))
    var lines := PackedStringArray()
    # A deadline the plan gives up on comes FIRST, above the shopping. It is a
    # deliberate choice - no team could have succeeded, and a team that fails costs
    # the very same consequences plus its armour - but the player is the one who gets
    # to overrule it, so it cannot be a silent one.
    var kept: int = int(plan.get("gold_floor", 0))
    if kept > 0:
        lines.append("Keeping %d gold for the ultimatum" % kept)
    for m in plan.get("missed", []):
        # The GDScript planner now says WHY; a plan read from a file carries a bare id.
        var qid := ""
        var known := typeof(m) == TYPE_DICTIONARY
        if known:
            qid = String(m.get("id", ""))
        else:
            qid = String(m)
        lines.append("Deadline given up: %s" % _quest_name(qid))
        if not known or not bool(m.get("winnable", false)):
            lines.append("  no team could succeed - sending one would cost the same")
        else:
            lines.append("  a team could win it (%.2f) - the others were worth more"
                         % float(m.get("best", 0.0)))
    var edith_on := _edith_kill_quest(plan)
    if edith_on != "":
        lines.append("EDITH on %s - a killing quest" % edith_on)
        lines.append("  it changes her for good, and there is no going back")
    if purchases.is_empty():
        lines.append("Nothing to buy")
    else:
        lines.append("To buy (%d gold):" % spend)
        for a in purchases:
            # The gain is shown because it is the whole reason the line exists:
            # most purchases do not cross a tier, they just make a knight better.
            var why := ""
            if a.has("gain") and float(a["gain"]) > 0.0:
                why = " (+%.2f)" % float(a["gain"])
            if int(a.get("tiers", 0)) > 0:
                why = " -> %s" % String(a.get("to", ""))
            lines.append("  - %s: %d gold for %s%s" % [
                _item_label(String(a.get("name", ""))), int(a.get("cost", 0)),
                String(a.get("for", "?")).to_upper(), why])
    _advice_label.text = "
".join(lines)


## The quest in the plan that would change EDITH for good, "" when there is none.
##
## `_check_for_edith()` fires the moment she is assigned to a quest whose
## `involve_killing` is set - once, permanently, before any dice are rolled. It is
## not a risk to be weighed against a score, so the mod does not weigh it: it says so
## and leaves the choice where it belongs.
func _edith_kill_quest(plan: Dictionary) -> String:
    var edith = GameState.character_manager.get_knight_from_name("edith")
    if not is_instance_valid(edith):
        return ""
    # Already changed: there is nothing left to warn about.
    if "is_possessed" in edith and bool(edith.is_possessed):
        return ""
    for a in plan.get("assignments", []):
        var here := false
        for n in a.get("knights", []):
            if String(n) == "edith":
                here = true
                break
        if not here:
            continue
        # The GDScript planner carries the resource; a plan read from a file does not.
        var q = a.get("quest_ref")
        if not is_instance_valid(q):
            q = GameState.quests_manager.get_quest_from_id(String(a.get("quest_id", "")))
        if is_instance_valid(q) and bool(q.involve_killing):
            return _quest_name(String(a.get("quest_id", "")))
    return ""


## Shows a rule only where it actually separates two visible sections, and hides a
## section that has nothing to say along with the rule above it.
func _refresh_rules() -> void:
    var above := false
    for lab in [_quest_label, _score_label]:
        if is_instance_valid(lab) and lab.text != "":
            above = true
    for pair in [[_score_sep, _outcome_label], [_meal_sep, _meal_label],
                 [_levels_sep, _levels_label], [_buy_sep, _advice_label]]:
        var sep: HSeparator = pair[0]
        var lab: Label = pair[1]
        if not is_instance_valid(lab):
            continue
        # Explicit type: `lab` comes out of an untyped array, so `:=` cannot infer.
        var has: bool = lab.text != ""
        lab.visible = has
        if is_instance_valid(sep):
            sep.visible = has and above
        if has:
            above = true


## Is the item actually at the player's disposal RIGHT NOW?
##
## The game's `get_all_available_items()` = quest items + unequipped_relics /
## _mounts / _consumables. It EXCLUDES `pawnbroker_items`: an item sold to the
## pawnbroker is no longer there, and equipping it would hand it back unpaid.
##
## We also accept items worn by a comrade who STAYED at the round table: the game
## allows that transfer. An item that left on a quest with its bearer is not
## recoverable.
##
## This check reads live memory, not the save file - which lags behind and would
## miss a sale made this very cycle.
func _item_available(equipment) -> bool:
    return _available_item(equipment) != null


## Does the player OWN this item, wherever it currently is?
##
## Deliberately wider than `_item_available()`: an item worn by a knight who left on
## a quest cannot be equipped, but it is still bought and paid for. Asking the narrow
## question made the "buy this" advice come back the moment the plan equipped the
## item and sent its bearer off.
func _item_owned(equipment) -> bool:
    if equipment == null:
        return false
    if _available_item(equipment) != null:
        return true
    var wanted := String(equipment.name)
    for kn in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(kn):
            continue
        for worn in kn.equipments:
            if is_instance_valid(worn) and String(worn.name) == wanted:
                return true
    return false


## The live instance to equip, or null if the player cannot use this item now.
##
## Returns the game's OWN object rather than a boolean: an item bought this cycle is
## not the same instance as the one `load()` returns from disk, so comparing
## identity alone answered "not available" for a relic the player had just paid for.
## We fall back on the equipment name, which is its unique id, and hand back the
## instance the inventory actually holds - passing the disk copy to the game would
## leave a phantom in the stock.
func _available_item(equipment):
    if equipment == null:
        return null
    var wanted := String(equipment.name)
    for free_item in GameState.inventory_manager.get_all_available_items():
        if free_item == equipment or String(free_item.name) == wanted:
            return free_item
    for kn in GameState.character_manager.roundtable_knights:
        if not is_instance_valid(kn) or is_instance_valid(kn.assigned_quest):
            continue
        for worn in kn.equipments:
            if worn == equipment or String(worn.name) == wanted:
                return worn
    return null


## Goes through the game's own function when available: it handles armor and stock
## removal. Manual fallback otherwise.
##
## Two locks, both ways: never place an exclusive item, and never overwrite one a
## knight wears permanently (Ari's griffin).
func _equip(knight, equipment) -> bool:
    if equipment.is_exclusive:
        return false
    # Work on the instance the game holds, not the one we loaded from disk.
    equipment = _available_item(equipment)
    if equipment == null:
        return false
    for worn in knight.equipments:
        if is_instance_valid(worn) and worn.is_exclusive \
                and worn.equipment_type == equipment.equipment_type:
            return false
    if is_instance_valid(_roundtable) and _roundtable.has_method("_on_equipment_assignation_requested"):
        _roundtable.current_selected_knight = knight
        _roundtable._on_equipment_assignation_requested(equipment, null, knight)
        return true
    if equipment is Relic:
        knight.relic = equipment
    elif equipment is Mount:
        knight.mount = equipment
    else:
        return false
    knight.current_armor += equipment.bonus_armor
    GameState.inventory_manager.remove_equipment(equipment)
    return true
