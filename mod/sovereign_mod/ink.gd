extends RefCounted
##
## Reading the story script, to say what an audience option actually offers.
##
## The game builds the relic / mount / consumable icon on a choice button with EMPTY
## arguments (choice_button.gd, update_for_equipment), so the player is told "you
## gain an item" and never which one. The name exists only in the compiled ink, as
##     {"VAR?":"RELIC"},{"VAR?":"Wolf_Skin"},{"f()":"UnlockEquipment"}
## which is why this file exists at all.
##
## The Python solver did this from outside the game. Doing it here is the last thing
## standing between the mod and having no binary in it.
##
## Read only. Nothing here touches the running story - it looks at the compiled text
## the same way one would read a book.

const MAX_POSITIONS := 8       # a label can appear all over the script
const BACK := 3000             # how far before a label its knot may start
const FORWARD := 24000         # and how far its sibling choices may run on
const DEPTH := 1               # how many diverts deep a reward may be written

var _text := ""
var _loaded := false

var _re_choice: RegEx
var _re_block: RegEx
var _re_equipment: RegEx
var _re_quest: RegEx
var _re_divert: RegEx


func _init() -> void:
    _re_choice = RegEx.new()
    # label, then whatever guards it, then the marker naming its block.
    _re_choice.compile('\\^([^"]{4,300})","/str"(.{0,400}?)"/ev",\\{"\\*":"\\.\\^\\.(c-\\d+)"')
    _re_block = RegEx.new()
    _re_block.compile('"(c-\\d+)":\\[')
    _re_equipment = RegEx.new()
    # The LAST variable before the call is the item; the one before it is its type.
    _re_equipment.compile('\\{"VAR\\?":"(\\w+)"\\},\\{"f\\(\\)":"UnlockEquipment"\\}')
    _re_divert = RegEx.new()
    _re_divert.compile('\\{"->":"[.^]*([a-z][a-z0-9_]{3,60})"\\}')
    _re_quest = RegEx.new()
    _re_quest.compile('\\{"VAR\\?":"([a-z][a-z0-9_]{3,70})"\\}.{0,40}?\\{"f\\(\\)":"(UnlockQuest|AddDoleanceForNextCycle)"\\}')


## The compiled story, in the language the player is reading.
##
## There is one resource per language and they are not labelled, so the French one is
## picked the way st.py picked it: by counting accented letters. Crude, and it has
## never been wrong.
func text() -> String:
    if _loaded:
        return _text
    _loaded = true
    var best := ""
    var best_score := -1
    var dir := DirAccess.open("res://.godot/imported")
    if dir == null:
        return ""
    dir.list_dir_begin()
    var f := dir.get_next()
    while f != "":
        if f.begins_with("master.ink.json") and f.ends_with(".res"):
            var res = ResourceLoader.load("res://.godot/imported/" + f)
            if res != null and "json" in res:
                var t := String(res.json)
                var score := t.count("é") + t.count("è")
                if score > best_score:
                    best_score = score
                    best = t
        f = dir.get_next()
    dir.list_dir_end()
    _text = best
    return _text


## Lowercase, without accents or punctuation, so a line read off a button can be
## compared with the same line in the script.
##
## The ligatures are unfolded by hand: normalising would turn "l'oeuf" into "luf"
## and the option would never be found again.
static func norm(s: String) -> String:
    var t := s.to_lower()
    t = t.replace("œ", "oe").replace("æ", "ae")
    const FROM := ["à", "â", "ä", "é", "è", "ê", "ë", "î", "ï", "ô", "ö",
                   "ù", "û", "ü", "ç"]
    const TO := ["a", "a", "a", "e", "e", "e", "e", "i", "i", "o", "o",
                 "u", "u", "u", "c"]
    for i in range(FROM.size()):
        t = t.replace(FROM[i], TO[i])
    var out := ""
    for c in t:
        if (c >= "a" and c <= "z") or (c >= "0" and c <= "9"):
            out += c
    return out


## Every place a line appears in the script, not just the first.
##
## "Gardes ?" also occurs inside "Gardes ?! Faites sortir cet homme", well before the
## audience anyone cares about. Stopping at the first hit read the wrong scene.
func positions(fragment: String) -> Array:
    var t := text()
    var out := []
    if fragment.length() < 4 or t.is_empty():
        return out
    var at := t.find(fragment)
    while at >= 0 and out.size() < MAX_POSITIONS:
        out.append(at)
        at = t.find(fragment, at + 1)
    return out


## The choices written around `pos`, as normalised label -> effects.
func choices_near(pos: int) -> Dictionary:
    var t := text()
    var from: int = maxi(0, pos - BACK)
    var to: int = mini(t.length(), pos + FORWARD)
    var seg := t.substr(from, to - from)
    var out := {}
    for m in _re_choice.search_all(seg):
        var label := m.get_string(1)
        var block_id := m.get_string(3)
        # The block is looked up FROM THE CHOICE ONWARDS, not from the start of the
        # window. Block names are generic - every passage has its own "c-0" - so
        # taking the first one in the window read some other scene's branch, and the
        # rewards came back empty.
        var at := seg.find('"%s":[' % block_id, m.get_end())
        var body := ("" if at < 0 else _bracket_slice(seg, at + block_id.length() + 3))
        var eff := _effects_in(body, seg, maxi(at, m.get_end()))
        out[norm(label)] = {"label": label, "block": block_id, "effects": eff}
    return out


## What a choice grants.
##
## Usually nothing is written in the choice's own block: it only diverts somewhere,
## and the reward is handed out there. Following that one hop is the difference
## between naming the Dragon Sword and reporting nothing at all.
func _effects_in(body: String, window: String, from: int) -> Dictionary:
    var eq := []
    var quests := []
    if body == "":
        return {"equipment": eq, "quests": quests}
    _collect(body, eq, quests)
    var seen := {}
    for m in _re_divert.search_all(body):
        _follow(m.get_string(1), window, from, eq, quests, seen, DEPTH)
    return {"equipment": eq, "quests": quests}


## Walks one divert, and the diverts it leads to, collecting what is granted.
##
## Nested choices are CUT OUT first. What sits inside them is not gained by taking
## this option - it depends on an answer the player has not given yet - and
## announcing it would promise a reward that may never come.
func _follow(target: String, window: String, from: int, eq: Array, quests: Array,
             seen: Dictionary, depth: int) -> void:
    if depth < 0 or seen.has(target):
        return
    seen[target] = true
    var slice := _knot_slice(window, target, from)
    if slice == "":
        return
    slice = _strip_choices(slice)
    _collect(slice, eq, quests)
    for m in _re_divert.search_all(slice):
        _follow(m.get_string(1), window, from, eq, quests, seen, depth - 1)


## Removes every "c-N":[...] run, leaving only what a passage grants outright.
func _strip_choices(s: String) -> String:
    var out := s
    while true:
        var m := _re_block.search(out)
        if m == null:
            break
        var body := _bracket_slice(out, m.get_end() - 1)
        if body.is_empty():
            break
        out = out.substr(0, m.get_start()) + out.substr(m.get_end() - 1 + body.length())
    return out


func _collect(body: String, eq: Array, quests: Array) -> void:
    for m in _re_equipment.search_all(body):
        var id := m.get_string(1).to_upper()
        if not id in eq:
            eq.append(id)
    for m in _re_quest.search_all(body):
        var id := m.get_string(1)
        if not id in quests:
            quests.append(id)


## The passage named `knot`, taken from the window rather than from the whole story:
## these names are NOT unique - "accept_quest" occurs thirty-four times - and the one
## that matters is the one written next to the choice that diverts to it.
func _knot_slice(window: String, knot: String, from: int) -> String:
    var at := window.find('"%s":[' % knot, from)
    if at < 0:
        at = window.find('"%s":[' % knot)
    if at < 0:
        at = window.find('"%s":{' % knot, from)
    if at < 0:
        at = window.find('"%s":{' % knot)
    if at < 0:
        return ""
    return _bracket_slice(window, at + knot.length() + 3)


## The bracketed run starting at `start`, which must be the opening bracket.
## Quotes are respected: the script is full of dialogue containing brackets.
func _bracket_slice(s: String, start: int) -> String:
    var depth := 0
    var i := start
    var in_string := false
    var escaped := false
    while i < s.length():
        var c := s[i]
        if in_string:
            if escaped:
                escaped = false
            elif c == "\\":
                escaped = true
            elif c == '"':
                in_string = false
        elif c == '"':
            in_string = true
        elif c == "[" or c == "{":
            depth += 1
        elif c == "]" or c == "}":
            depth -= 1
            if depth == 0:
                return s.substr(start, i - start + 1)
        i += 1
    return s.substr(start, mini(4000, s.length() - start))


## What each of the given options unlocks.
##
## Options shown together belong to the SAME passage, so every candidate position is
## scored by how many of them it covers and the best one wins. Trusting the first
## occurrence of the first label picked the wrong scene often enough to matter.
func hints(labels: Array) -> Dictionary:
    var wanted := []
    for l in labels:
        var n := norm(String(l))
        if n != "":
            wanted.append(n)
    var out := {}
    if wanted.is_empty() or text().is_empty():
        return out
    var best := {}
    var best_cover := 0
    var seen := {}
    for l in labels:
        for pos in positions(String(l)):
            var bucket: int = pos / 4000
            if seen.has(bucket):
                continue
            seen[bucket] = true
            var found := choices_near(pos)
            var cover := 0
            for w in wanted:
                if found.has(w):
                    cover += 1
            if cover > best_cover:
                best_cover = cover
                best = found
    for l in labels:
        var n := norm(String(l))
        if best.has(n):
            out[String(l)] = best[n]["effects"]
    return out
