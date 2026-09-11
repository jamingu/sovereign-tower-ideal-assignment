# Ideal Assignment, a quality-of-life mod for Sovereign Tower

Sovereign Tower asks you to send knights on quests without ever showing you the
numbers behind the decision. This mod puts the numbers on screen, and adds a
button that works out the best assignment for the cycle for you.

It is a helper, not a cheat: it never grants gold, items or progress. It only
arranges knights and equipment you already own, exactly as you could by hand
with a spreadsheet and a lot of patience.

**Five plain text files. No installer, no runtime, nothing to configure.**
Windows, Steam, tested on Sovereign Tower 1.1.

---

## What it adds

**At the round table**

- **Auto-assignment**: one button. It clears the board, then works out which
  knights go on which quest and what each of them should carry, preferring
  quests that finish in a single cycle, then the best possible outcome. A
  deadline expiring this cycle that can still be won comes first of all.
- **Clear**: unassigns every knight and strips their equipment. Items bound to a
  knight stay where they are, as the game itself forbids removing them.
- **Meal**: serves a meal to the knight a meal would lift to a better outcome,
  or to the lowest affinity when it buys no tier. It pays for the cheapest dish
  that knight is known to like, and only once per cycle, as the kitchen allows.
- **Train**: sends the lowest-level knight with no quest assigned to the
  training ground.
- **Live score**: the real success score of the quest you are looking at,
  updated as you move knights and gear around.
- **Numbers on the difficulty wheel**: the exact statistic a quest requires,
  instead of Low / Mid / High / Max. Requirements the game hides stay hidden.
- **Unexpected outcomes**: the special results a quest can produce, and what
  triggers them.
- **Reward names**: the quest card says "Mount"; this says which mount.
- **Buying and meal advice**: what is worth buying with the gold you have, and
  which knight should get the meal. Both only ever suggested when they change an
  outcome: a meal is single use and fifty gold for a tenth of a point is not a
  bargain.

**Elsewhere**

- **Kitchen**: marks the dishes each knight likes and dislikes.
- **Audiences**: names the relic, mount or consumable an option offers, which
  the game itself leaves blank, and shows the unexpected outcomes of a quest
  being offered to you.
- **Faster results screen**: the end-of-cycle score bars, at your own pace.
- **Faster ending**: the closing sequence, at your own pace.

Every one of these is a checkbox, twelve in all. Open the mod's options from the
main menu and turn off anything you would rather work out yourself. Meal and
Train share a single checkbox, since they are one row of the panel.

---

## Install

1. Download the release archive and unzip it.
2. Copy `override.cfg` and the `sovereign_mod` folder into the game folder,
   the one holding `sovereign_tower.exe`:

   ```
   Steam\steamapps\common\Sovereign Tower\sovereign_tower_windows_build\
   ```

   In Steam: right-click the game → *Manage* → *Browse local files*, then open
   `sovereign_tower_windows_build`.
3. Start the game from Steam.

That is the whole installation. The mod is five GDScript files that the game
loads itself; nothing is installed, nothing runs outside the game, and you can
read every line of it before you trust it.

## Uninstall

Delete `override.cfg` and the `sovereign_mod` folder. The game goes back to
exactly what it was.

---

## Is this safe?

- **No executable, anywhere.** The archive holds seven text files and nothing
  else. There is no program to install and nothing runs outside the game.
- **The game archive is untouched.** `sovereign_tower.pck` is never opened for
  writing. The mod is loaded from plain files next to the executable, through
  Godot's own `override.cfg`.
- **Your saves are untouched.** The mod reads the game's state; it never writes
  to a save. The game saves as usual, when it usually does.
- **No achievement is triggered.** The mod never starts an audience, a dialogue
  or a recruitment.
- **Nothing leaves your machine.** No network access of any kind.

Back up your saves anyway, as with any mod. They live in
`%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\`.

---

## Options

The gear icon on the main menu opens the mod's options. Settings are stored in
`%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\sovereign_mod\settings.json`.

A few settings live only in that file:

| Key | Default | What it does |
| --- | --- | --- |
| `result_speed` | `4.0` | How much faster the end-of-cycle screen runs. |
| `wheel_font_size` | `32` | Size of the numbers on the difficulty wheel. |
| `slot` | `1` | Which save slot the external solver reads, if one is used. |

`ending_speed` (`3.0`) does the same for the closing sequence.

`test_bench` lets another program on your machine drive the mod through text
files. It exists for development, it is deliberately absent from the options
screen, and it is **off** by default. When it is off no timer is even created.

---

## Known limitation

On a very full board, ten knights and eight or more quests, the planner can stop
searching before it has tried everything and hand back a slightly less good
arrangement. It watches free memory and steps back rather than risking the
session. The assignment is still valid, just not always the best one available.

---

## How it works

The mod scores a team the way the game does, because it asks the game: the
efficiency tags, the special cases, the protagonist rule and the special-outcome
conditions all come from the game's own `TagLibrary` rather than being
reimplemented. `Quest.determine_outcome()` itself is never called, since it
freezes the outcome and hands out damage and rewards, so only its scoring half
is reproduced and nothing writes to a game object.

What is genuinely the mod's own is the search: which knights on which quest, and
who carries what. It runs in about three seconds on a full round table of ten
knights.

| File | What it does |
| --- | --- |
| `main.gd` | The interface: panel, buttons, tooltips, options. |
| `solver.gd` | The planner: snapshot, search, equipment, advice. |
| `scoring.gd` | Quest scoring, from the game's own rules. |
| `special.gd` | The special cases, ported so hypothetical loadouts can be graded. |
| `ink.gd` | Reads the compiled story, to name what an audience option offers. |

A Python version of the solver lives in this repository as well. It was the
original implementation, it is no longer needed, and it stays as a reference to
check the GDScript one against:

```
python st.py setup     # read the game files (once, and after a game update)
python st.py cycle 1   # the plan for save slot 1
```

To build a release: `python tools/make_release.py --sync`.

Developer notes are in [docs/](docs/), in French.

---

## Credits and licence

Mod and toolkit released under the [MIT licence](LICENSE).

Sovereign Tower belongs to its authors and publisher. This project contains no
game content: it reads the copy already installed on your machine and
redistributes nothing from it.
