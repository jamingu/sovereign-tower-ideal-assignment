# Ideal Assignment — a quality-of-life mod for Sovereign Tower

Sovereign Tower asks you to send knights on quests without ever showing you the
numbers behind the decision. This mod puts the numbers on screen, and adds a
button that works out the best assignment for the cycle for you.

It is a helper, not a cheat: it never grants gold, items or progress. It only
arranges knights and equipment you already own, exactly as you could by hand
with a spreadsheet and a lot of patience.

**Windows only. Steam version. Tested on Sovereign Tower 1.1.**

---

## What it adds

**At the round table**

- **Ideal assignment** — one button. It clears the board, then works out which
  knights go on which quest and what each of them should carry, preferring
  quests that finish in a single cycle, then the best possible outcome.
- **Clear all** — unassigns every knight and strips their equipment.
- **Live score** — the real success score of the quest you are looking at,
  updated as you move knights and gear around.
- **Numbers on the difficulty wheel** — the exact statistic a quest requires,
  instead of Low / Mid / High / Max. Requirements the game hides stay hidden.
- **Unexpected outcomes** — the special results a quest can produce, and what
  triggers them.
- **Reward names** — the quest card says "Mount"; this says which mount.
- **Buying and meal advice** — what is worth buying with the gold you have, and
  which knight should get the meal.

**Elsewhere**

- **Kitchen** — marks the dishes each knight likes and dislikes.
- **Audiences** — names the relic, mount or consumable an option offers, and
  shows the unexpected outcomes of a quest being offered to you.
- **Faster results screen** — the end-of-cycle score bars, at your own pace.

Every one of these is a checkbox. Open the mod's options from the main menu and
turn off anything you would rather work out yourself.

---

## Install

1. Download the release archive and unzip it.
2. Copy `override.cfg` and the `sovereign_mod` folder into the game folder —
   the one holding `sovereign_tower.exe`:

   ```
   Steam\steamapps\common\Sovereign Tower\sovereign_tower_windows_build\
   ```

   In Steam: right-click the game → *Manage* → *Browse local files*, then open
   `sovereign_tower_windows_build`.
3. Start the game from Steam.

That is the whole installation. Nothing to configure, no Python to install, no
game file is modified.

On the first launch the mod spends a few seconds reading the game's own data
files to learn the quests, knights and equipment of your version. It does this
again by itself after a game update.

## Uninstall

Delete `override.cfg` and the `sovereign_mod` folder. The game goes back to
exactly what it was.

---

## Is this safe?

- **The game archive is untouched.** `sovereign_tower.pck` is never opened for
  writing. The mod is loaded from a plain file next to the executable, through
  Godot's own `override.cfg`.
- **Your saves are untouched.** The mod reads your save file; it never writes
  to it. The game saves as usual, when it usually does.
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
| `result_speed` | `2.0` | How much faster the end-of-cycle screen runs. |
| `wheel_font_size` | `32` | Size of the numbers on the difficulty wheel. |
| `slot` | `1` | Which save slot the solver reads. |
| `python`, `solver` | empty | Only to run the solver from its Python sources. |

`test_bench` lets another program on your machine drive the mod through text
files. It exists for development and is **off** by default; leave it that way.

---

## How it works

The mod itself computes nothing. It ships a small command-line solver,
`sovereign_mod/solver/st.exe`, which reads the game's data and your save file
and returns a plan as JSON; the mod applies that plan to the interface.

The solver is the Python toolkit in this repository, frozen with PyInstaller so
players need no Python. To run it from source instead:

```
python st.py setup     # read the game files (once, and after a game update)
python st.py cycle 1   # the plan for save slot 1
```

It finds the game on its own through the Steam registry keys, or through the
`ST_GAME` environment variable if it is somewhere unusual. The data it extracts
goes to `%LOCALAPPDATA%\SovereignTowerMod\cache` and stays on your machine.

To build a release: `python tools/make_release.py`.

Developer notes are in [docs/](docs/) — those are in French.

---

## Credits and licence

Mod and toolkit released under the [MIT licence](LICENSE).

Sovereign Tower belongs to its authors and publisher. This project contains no
game content: it reads the copy already installed on your machine and
redistributes nothing from it.
