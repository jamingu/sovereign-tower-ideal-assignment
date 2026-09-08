# -*- coding: utf-8 -*-
"""Fabrique l'archive a publier (GitHub / Nexus).

    python tools/make_release.py [--sync] [--version 1.0.0]

--sync recopie d'abord le main.gd installe dans le jeu vers mod/ : c'est la
copie du jeu qu'on edite et qu'on teste, elle fait foi.

Le zip contient les deux elements que le joueur depose dans le dossier du jeu :
override.cfg et sovereign_mod/ (avec le solveur gele dedans). Aucun fichier
extrait du jeu n'y figure.
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")
STAGE = os.path.join(BUILD, "stage")
VERSION = "1.0.0"

# Modules importes tardivement par st.py : PyInstaller ne les voit pas tous.
HIDDEN = ["plan", "advise", "risk", "meals", "trans", "unrscc", "scnparse",
          "gdd", "gdres", "stpck"]
DROP = ["tkinter", "unittest", "pydoc", "doctest", "pdb", "xml"]

INSTALL_TXT = """Ideal Assignment - Sovereign Tower
==================================

INSTALL

  Copy  override.cfg  and the  sovereign_mod  folder  into the game folder,
  next to sovereign_tower.exe:

    Steam\\steamapps\\common\\Sovereign Tower\\sovereign_tower_windows_build\\

  In Steam: right-click the game, Manage, Browse local files, then open
  sovereign_tower_windows_build.

  Start the game from Steam. That is all - nothing to configure.

  The first launch spends a few seconds reading the game's own data files.

UNINSTALL

  Delete override.cfg and the sovereign_mod folder.

The game archive and your saves are never modified.
Full description: https://github.com/jamingu/sovereign-tower-ideal-assignment
"""


def sync_mod():
    """Recupere le main.gd reellement teste, dans le dossier du jeu."""
    sys.path.insert(0, ROOT)
    import stpck
    if not stpck.GAME:
        sys.exit("--sync: jeu introuvable (definir ST_GAME)")
    src = os.path.join(stpck.GAME, "sovereign_mod", "main.gd")
    if not os.path.exists(src):
        sys.exit("--sync: %s absent" % src)
    dst = os.path.join(ROOT, "mod", "sovereign_mod", "main.gd")
    shutil.copyfile(src, dst)
    print("sync :", src)


def freeze():
    """Gele st.py en un .exe autonome : le joueur n'installe pas Python."""
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", os.path.join(BUILD, "dist"),
           "--workpath", os.path.join(BUILD, "work"),
           "--specpath", BUILD,
           "--name", "st", "--console"]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    for m in DROP:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join(ROOT, "st.py"))
    print("gel du solveur...")
    subprocess.run(cmd, check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=None)
    out = os.path.join(BUILD, "dist", "st")
    if not os.path.exists(os.path.join(out, "st.exe")):
        sys.exit("PyInstaller n'a pas produit st.exe")
    return out


def stage(solver_dir):
    shutil.rmtree(STAGE, ignore_errors=True)
    mod = os.path.join(STAGE, "sovereign_mod")
    os.makedirs(mod)
    shutil.copyfile(os.path.join(ROOT, "mod", "override.cfg"),
                    os.path.join(STAGE, "override.cfg"))
    shutil.copyfile(os.path.join(ROOT, "mod", "sovereign_mod", "main.gd"),
                    os.path.join(mod, "main.gd"))
    shutil.copytree(solver_dir, os.path.join(mod, "solver"))
    with open(os.path.join(STAGE, "INSTALL.txt"), "w",
              encoding="utf-8", newline="\r\n") as f:
        f.write(INSTALL_TXT)
    return STAGE


def zip_up(src, version):
    name = "SovereignTower-IdealAssignment-v%s.zip" % version
    path = os.path.join(BUILD, name)
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(src):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, src))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true",
                    help="recopier main.gd depuis le dossier du jeu")
    ap.add_argument("--version", default=VERSION)
    a = ap.parse_args()
    if a.sync:
        sync_mod()
    path = zip_up(stage(freeze()), a.version)
    print("archive : %s (%.1f Mo)" % (path, os.path.getsize(path) / 1e6))


if __name__ == "__main__":
    main()
