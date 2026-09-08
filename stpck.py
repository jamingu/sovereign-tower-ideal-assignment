"""Lecture du .pck de Sovereign Tower (Godot 4.6, pack format 3)."""
import struct, os, json, sys

PCK_NAME = "sovereign_tower.pck"
FILE_BASE = 0x70


def _here():
    """Dossier de l'outil (celui du .exe une fois gele par PyInstaller)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HERE = _here()


def _steam_root():
    try:
        import winreg
    except ImportError:
        return None
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for val in ("SteamPath", "InstallPath"):
                    try:
                        p = winreg.QueryValueEx(k, val)[0]
                        if p and os.path.isdir(p):
                            return p
                    except OSError:
                        pass
        except OSError:
            pass
    return None


def _steam_libraries():
    """Toutes les bibliotheques Steam declarees (le jeu n'est pas toujours sur C:)."""
    root = _steam_root()
    if not root:
        return []
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        import re as _re
        txt = open(vdf, encoding="utf-8", errors="replace").read()
        for m in _re.finditer(r'"path"\s*"([^"]+)"', txt):
            libs.append(m.group(1).replace("\\\\", "\\"))
    return libs


def find_game():
    """Localise le dossier contenant sovereign_tower.pck.

    1. la variable ST_GAME (le mod la renseigne : il connait son propre chemin) ;
    2. un dossier parent de l'outil (cas d'une installation dans le jeu) ;
    3. les bibliotheques Steam.
    """
    env = os.environ.get('ST_GAME')
    if env and os.path.exists(os.path.join(env, PCK_NAME)):
        return env
    d = HERE
    for _ in range(4):
        if os.path.exists(os.path.join(d, PCK_NAME)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for lib in _steam_libraries():
        for sub in (("steamapps", "common", "Sovereign Tower", "sovereign_tower_windows_build"),
                    ("steamapps", "common", "Sovereign Tower")):
            cand = os.path.join(lib, *sub)
            if os.path.exists(os.path.join(cand, PCK_NAME)):
                return cand
    return ""


def _default_cache():
    env = os.environ.get('ST_CACHE')
    if env:
        return env
    if getattr(sys, 'frozen', False):
        # Le dossier du jeu peut etre en lecture seule (Program Files) : on ecrit
        # dans les donnees utilisateur.
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, "SovereignTowerMod", "cache")
    return os.path.join(HERE, "cache")


GAME = find_game()
PCK = os.path.join(GAME, PCK_NAME) if GAME else ""
CACHE = _default_cache()

def read_dir(pck=PCK):
    f = open(pck, 'rb')
    f.seek(0x20); dir_off, = struct.unpack('<Q', f.read(8))
    f.seek(dir_off)
    cnt, = struct.unpack('<I', f.read(4))
    ents = []
    for _ in range(cnt):
        pl, = struct.unpack('<I', f.read(4))
        p = f.read(pl).rstrip(b'\x00').decode('utf-8', 'replace')
        off, sz = struct.unpack('<QQ', f.read(16))
        f.read(16)                       # md5
        fl, = struct.unpack('<I', f.read(4))
        ents.append((p, off, sz, fl))
    f.close()
    return ents

def extract(pred, outdir, flat=False, pck=PCK):
    ents = read_dir(pck)
    f = open(pck, 'rb')
    n = 0
    for p, off, sz, fl in ents:
        if not pred(p):
            continue
        f.seek(FILE_BASE + off)
        d = f.read(sz)
        dest = os.path.join(outdir, p.split('/')[-1] if flat else p.replace('/', os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, 'wb').write(d)
        n += 1
    f.close()
    return n
