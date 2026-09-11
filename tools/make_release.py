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
VERSION = "2.1.0"

# Modules importes tardivement par st.py : PyInstaller ne les voit pas tous.
HIDDEN = ["plan", "advise", "risk", "meals", "trans", "unrscc", "scnparse",
          "gdd", "gdres", "stpck"]
# Rien dans le solveur ne touche au reseau ni a la crypto : PyInstaller les
# embarquait par transitivite (random -> hashlib -> OpenSSL). Les laisser, c'est
# livrer libcrypto, libssl et des sockets dans un mod hors-ligne -- 6 Mo pour rien,
# et de quoi inquieter a juste titre quiconque ouvre l'archive.
DROP = ["tkinter", "unittest", "pydoc", "doctest", "pdb", "xml",
        "ssl", "_ssl", "socket", "_socket", "hashlib", "_hashlib",
        "email", "http", "urllib", "ftplib", "bz2", "lzma"]
# zipfile NON : le lanceur de PyInstaller s'en sert lui-meme, l'exclure produit un
# exe qui ne demarre pas du tout ("No module named 'zipfile'").

INSTALL_TXT = """Ideal Assignment - Sovereign Tower
==================================

INSTALL

  Copy  override.cfg  and the  sovereign_mod  folder  into the game folder,
  next to sovereign_tower.exe:

    Steam\\steamapps\\common\\Sovereign Tower\\sovereign_tower_windows_build\\

  In Steam: right-click the game, Manage, Browse local files, then open
  sovereign_tower_windows_build.

  Start the game from Steam. That is all - nothing to configure.

  The mod is five plain .gd text files. No program is installed and nothing is
  run outside the game; you can read every line of it.

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
    src_dir = os.path.join(stpck.GAME, "sovereign_mod")
    dst_dir = os.path.join(ROOT, "mod", "sovereign_mod")
    # Tous les .gd, pas seulement main.gd : le portage en ajoute (scoring.gd...) et
    # en oublier un livrerait un mod qui reference un fichier absent.
    names = sorted(f for f in os.listdir(src_dir)
                   if f.endswith(".gd") and not f.endswith(".bak"))
    if "main.gd" not in names:
        sys.exit("--sync: main.gd absent de %s" % src_dir)
    for f in names:
        shutil.copyfile(os.path.join(src_dir, f), os.path.join(dst_dir, f))
    print("sync :", src_dir, "->", ", ".join(names))


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


def stage():
    shutil.rmtree(STAGE, ignore_errors=True)
    mod = os.path.join(STAGE, "sovereign_mod")
    os.makedirs(mod)
    shutil.copyfile(os.path.join(ROOT, "mod", "override.cfg"),
                    os.path.join(STAGE, "override.cfg"))
    src_mod = os.path.join(ROOT, "mod", "sovereign_mod")
    for f in sorted(x for x in os.listdir(src_mod) if x.endswith(".gd")):
        shutil.copyfile(os.path.join(src_mod, f), os.path.join(mod, f))
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
    path = zip_up(stage(), a.version)
    print("archive : %s (%.1f Mo)" % (path, os.path.getsize(path) / 1e6))


if __name__ == "__main__":
    main()
