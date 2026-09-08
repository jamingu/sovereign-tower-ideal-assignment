# Brief — mod « répartition idéale » pour Sovereign Tower

**Pour une session Claude Code démarrée à froid.** Tout ce qui suit a été vérifié
empiriquement le 2026-09-06 sur la machine de l'utilisateur. Les affirmations non
vérifiées sont explicitement marquées **[À TESTER]**.

Ouvre ce fichier, lis-le en entier, puis attaque à l'étape 1. Ne redécouvre pas
ce qui est déjà établi ici.

---

## 1. L'objectif

L'utilisateur joue à Sovereign Tower. À chaque cycle il doit répartir à la main
ses chevaliers sur les quêtes, choisir leurs reliques, leurs montures et le repas.
Un outil Python (`st.py`, dans ce dossier) calcule déjà la répartition optimale,
mais il faut la recopier manuellement dans le jeu.

**But du mod : un bouton dans le jeu qui applique la répartition optimale.**

Le solveur reste en Python. Le mod n'a pas à recalculer quoi que ce soit — il lit
un fichier JSON produit par `st.py` et applique. C'est le point de conception le
plus important : ne réécris pas `plan.py` (155 ko) en GDScript.

---

## 2. Environnement

| | |
|---|---|
| Jeu | `D:\Steam\steamapps\common\Sovereign Tower\sovereign_tower_windows_build\` |
| Exécutable | `sovereign_tower.exe` (template **release**, Godot 4.6.2) |
| Archive | `sovereign_tower.pck` — 1 461 007 628 o, 12 810 entrées, **non chiffrée** |
| Sauvegardes | `%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\saved_games\` |
| Boîte à outils | racine de ce dépôt |
| Plateforme | Windows 11, PowerShell + Bash dispo |

Le `.pck` est **séparé de l'exe** (pas embarqué). C'est ce qui rend le mod
raisonnable.

### Outils déjà écrits dans ce dossier

- `stpck.py` — lecture du `.pck`. `read_dir()` rend `[(chemin, offset, taille, flags)]`,
  `extract(pred, outdir, flat=False)` extrait.
- `gdd.py` — décompilateur de bytecode `.gdc` → source GDScript lisible.
  `gdd.decompile(chemin_gdc)` rend une chaîne.
- `st.py` — le solveur. `st.py cycle`, `st.py plan`, `st.py simple`.
- `README.md` — modèle de score, pièges, historique des erreurs corrigées.

**Pour relire n'importe quel script du jeu**, décompile tout d'un coup :

```python
import glob, os, re, gdd
out = r'<scratchpad>/gdsrc'; os.makedirs(out, exist_ok=True)
for f in glob.glob('cache/gd/**/*.gdc', recursive=True):
    src = gdd.decompile(f)
    rel = re.sub(r'[^A-Za-z0-9_.-]', '__', os.path.relpath(f, 'cache/gd')) + '.txt'
    open(os.path.join(out, rel), 'w', encoding='utf-8', errors='replace').write(src)
```

847 fichiers, quelques secondes. Ensuite `grep -rn` dedans.

---

## 3. La découverte qui rend tout ça possible

**Le jeu embarque la console de développement Panku, avec un module maison
`sovereign_cheater`, dans la build de vente.**

`addons/panku_console/` est présent dans le `.pck` : 109 scripts, dont
`modules/sovereign_cheater/` (ajouter/retirer des chevaliers de la table ronde,
forcer des quêtes, éditer l'inventaire, l'or, les comtés). La console fournit
aussi un **shell GDScript interactif** qui exécute des expressions arbitraires.

**Ce qui la désactive :** dans `project.binary`, la liste des autoloads contient
`PankuManager` mais **pas `Panku`**. Or `systems/autoloads/panku_manager.gd` fait :

```gdscript
panku = get_node_or_null(load(custom_class.path).new().SingletonPath)  # "/root/Panku"
if panku: Log.info('Panku manager : console available and initialized')
else:     Log.info('Panku manager : console not available')
```

Le nœud `/root/Panku` n'existe jamais. La console est livrée mais jamais instanciée.

`addons/panku_console/console.gd` (décompilé) **ne contient aucune garde
`OS.is_debug_build()`**. Son `_ready()` enregistre l'action `toggle_console`
liée à `KEY_QUOTELEFT` (la touche `²`/backtick). Il suffit donc de l'instancier.

Cible de l'autoload :
```
Panku = *res://addons/panku_console/console.tscn
```
(`console.tscn.remap` pointe vers
`res://.godot/exported/133200997/export-937c61fb21cb697a9366316706b91b90-console.scn` ;
Godot résout le remap tout seul, n'utilise **pas** le chemin `.scn` en dur.)

**Ordre des autoloads :** `Panku` doit être déclaré **avant** `PankuManager`,
sinon le manager ne le trouvera pas dans son `_ready()`. La console fonctionnerait
quand même en direct, mais autant faire les choses proprement.

---

## 4. Les trois voies, dans l'ordre où il faut les essayer

### Voie A — `override.cfg` (à tester en premier, 5 minutes, zéro modification)

La chaîne `res://override.cfg` **est présente dans `sovereign_tower.exe`** (vérifié),
donc le moteur gère ce mécanisme.

**[À TESTER]** Godot documente `override.cfg` comme devant être à côté de
`project.godot` — dans un export, ça veut dire *à l'intérieur du `.pck`*. Il est
possible que le moteur regarde aussi le dossier de l'exécutable. **C'est le premier
test à faire**, parce que si ça marche il n'y a strictement rien à modifier.

Poser à côté de `sovereign_tower.exe` :

```ini
[autoload]
Panku="*res://addons/panku_console/console.tscn"
```

Lancer, appuyer sur `²`. Si la console s'ouvre : voie A, terminé. Sinon voie B.

Le journal du jeu dira `Panku manager : console available and initialized` ou
`... not available` — cherche le fichier de log dans `user://` pour trancher sans
ambiguïté.

### Voie B — patch du `.pck` en place (recommandée si A échoue)

Pas besoin de réécrire 1,46 Go. La structure permet un patch chirurgical.

**En-tête du `.pck`** (vérifié octet par octet sur ce fichier) :

| offset | champ |
|---|---|
| `0x00` | magic `GDPC` |
| `0x04` | pack format = 3 (uint32) |
| `0x08` | version Godot 4, 6, 2 (3 × uint32) |
| `0x14` | flags = **2** → `REL_FILEBASE`, **pas de chiffrement** |
| `0x18` | `files_base` = **112** (uint64) |
| `0x20` | **offset du répertoire** = 1 459 615 536 (uint64) |

À `dir_off` : uint32 `count` (= 12810), puis pour chaque entrée :

```
uint32 path_len
bytes  path            (complété par des \x00)
uint64 offset          (RELATIF à files_base : lecture réelle = 112 + offset)
uint64 size
bytes  md5[16]
uint32 flags
```

**Procédure :**

1. Copier `sovereign_tower.pck` en `.pck.orig` (sauvegarde de repli).
2. Extraire `project.binary` (79 023 o), y ajouter l'entrée d'autoload (§5).
3. **Ajouter le nouveau contenu à la fin du fichier `.pck`** (après le répertoire —
   le répertoire est bien la queue du fichier, mais l'accès se fait par offset,
   donc écrire après lui est sans danger).
4. Réécrire, **dans l'entrée de répertoire de `project.binary`**, les champs
   `offset` (= position d'ajout − 112) et `size`.
5. **[À TESTER]** Le md5 : Godot 4 ne vérifie normalement pas le md5 des entrées
   à l'ouverture. Laisse-le périmé d'abord ; si le jeu refuse de démarrer,
   recalcule `hashlib.md5(nouveau_contenu).digest()` et écris-le dans l'entrée.

Cette approche évite de reconstruire l'archive et se défait en restaurant `.pck.orig`.

### Voie C — le bouton lui-même

Une fois la console active, il faut du code à nous. Deux sous-options :

- **C1 (simple)** : tout passe par le shell Panku. L'utilisateur ouvre la console
  et lance une expression. Fonctionnel, mais mauvaise ergonomie au quotidien.
- **C2 (recommandée)** : injecter **un seul petit script** dans le `.pck`, déclaré
  comme autoload, dont le seul rôle est de charger `user://sovereign_mod/main.gd`
  au démarrage s'il existe. Le vrai code du mod vit alors en **texte clair,
  hors du `.pck`**, modifiable sans jamais retoucher l'archive.

  Injecter un fichier neuf demande d'**ajouter une entrée au répertoire** (donc
  réécrire la queue du `.pck` et incrémenter `count`) — plus lourd que la voie B
  mais du même ordre.

  **[À TESTER]** un `.gd` en clair ajouté à l'archive sera-t-il compilé à
  l'exécution ? L'export utilise des `.gdc` (tokens binaires), mais le compilateur
  GDScript est forcément présent dans le binaire puisque Panku évalue des
  expressions à la volée. Très probable, à confirmer.

---

## 5. Format de `project.binary`

Format `ECFG`, vérifié en le parsant :

```
bytes  "ECFG"
uint32 count                       (= 126 ici)
répété count fois :
    uint32 key_len
    bytes  key
    uint32 val_len
    bytes  val                     (Variant encodé)
```

Un Variant String s'encode :
```
uint32 type = 4
uint32 str_len
bytes  str                         (complété à un multiple de 4 par des \x00)
```

Exemple réel relevé dans le fichier — clé `autoload/PankuManager`, `val_len` 52 :
```
04 00 00 00  29 00 00 00  "*res://systems/autoloads/panku_manager.gd"  00 00 00
```

Pour ajouter l'autoload : insérer une entrée `autoload/Panku` →
`*res://addons/panku_console/console.tscn` **avant** celle de `PankuManager`, et
incrémenter `count` à 127. L'astérisque de tête signifie « nœud singleton ».

Les 23 autoloads existants, dans l'ordre : `PankuManager`, `ControllerIcons`,
`TagManager`, `SignalsEventBus`, `StoryController`, `TagLibrary`, `LevelUpManager`,
`GameState`, `ControllerSupport`, `TransitionController`, `AudioManager`,
`PauseMenu`, `CutsceneController`, `SettingsController`, `NotificationManager`,
`Debug`, `FocusController`, `LogManager`, `TutorialController`,
`AchievementManager`, `ResponsivityManager`, `AccessibilityManager`,
`CurrentPlatformManager`.

---

## 6. Le contrat d'échange Python ↔ mod

C'est à définir avec l'utilisateur, mais voici la proposition de départ. Le solveur
Python écrit, le mod lit et applique. **Aucune logique de score côté GDScript.**

Fichier : `%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\sovereign_mod\plan.json`
(soit `user://sovereign_mod/plan.json` vu du jeu).

```json
{
  "cycle": 12,
  "generated_at": "2026-09-06T15:04:00",
  "assignments": [
    {"quest_id": "quest_blacksmith",
     "knights": ["angelica"],
     "items": {"angelica": ["CLOVER_SWORD", "ASSILE"]}}
  ],
  "meal": {"knight": "silgur", "meal": "GALETTE_SAUCISSE_NAME"}
}
```

Les identifiants de quête, de chevalier et d'équipement sont exactement ceux du
cache de `st.py` (`cache/quests.json`, `cache/knights.json`, `cache/equipment.json`),
qui sont eux-mêmes ceux du jeu. Pas de traduction à faire.

Côté `st.py`, ajouter une sortie `--json <chemin>` sur la commande `cycle`.
**Demande à l'utilisateur avant de modifier `st.py`** — il tient à ce que l'outil
reste sous contrôle et n'apprécie pas les changements non sollicités (cf. §9).

### API du jeu à piloter

Le point d'entrée de l'affectation est
`scenes/roundtable/quests_presentation_section.gd` (décompile-le) :

- `assign_knight_to_quest(knight: Knight)`
- `_unassign_knight_from_quest(knight: Knight)`
- `udpate_assigned_knigts_to_quest(new_knight, previous_knight, updated_knight_slot)`
  (oui, la faute de frappe est dans le jeu)
- `has_reached_max_assigned_knights(slots, max_knights)`
- la liste vit dans `quest.assigned_knights`

État global accessible depuis n'importe où :
`GameState.character_manager`, `GameState.quests_manager`,
`GameState.satisfaction_manager`, `GameState.world_manager`.

Voir aussi `addons/panku_console/modules/sovereign_cheater/*.gd` : ils montrent
comment manipuler proprement la table ronde (`_recruit_knight`,
`_remove_knight_from_roundtable`, `get_roundtable_knight_from_name`).

**Piste à privilégier :** passer par les fonctions de l'UI plutôt que d'écrire
directement dans `quest.assigned_knights`, pour que l'affichage et les signaux
suivent. Sinon l'écran restera désynchronisé.

---

## 7. Sécurité, rollback, Steam

- **Sauvegarde obligatoire de `sovereign_tower.pck` avant toute écriture.**
  1,46 Go, prévois la place.
- **Steam** : une vérification de l'intégrité des fichiers restaurera le `.pck`
  d'origine et effacera le mod. Ce n'est pas une perte de données, juste à savoir.
  **[À TESTER]** copier tout le dossier `sovereign_tower_windows_build` ailleurs et
  modder la copie : le jeu utilise `steam_api64.dll`, il démarrera probablement si
  Steam tourne, mais ça n'est pas garanti.
- **Le mod ne touche jamais aux sauvegardes.** Elles sont dans `%APPDATA%`, pas
  dans le `.pck`.
- **Règle absolue héritée des sessions précédentes : le jeu doit être FERMÉ avant
  de restaurer une sauvegarde.** L'autosave se déclenche sur `tower_view_ready` et
  `audience_finished` et écrasera silencieusement le fichier restauré. Ça a déjà
  piégé l'utilisateur une fois.
- Avant de lancer une version moddée, faire une copie du dossier `saved_games`.

---

## 8. Ordre de marche suggéré

1. Sauvegarder `sovereign_tower.pck` et `saved_games`.
2. **Voie A** : poser `override.cfg`, lancer, appuyer sur `²`. Vérifier le log.
3. Si échec → **voie B** : patcher `project.binary`, réinjecter par ajout en fin de
   fichier + réécriture de l'entrée de répertoire. Relancer, retester `²`.
4. Une fois la console ouverte : ouvrir la fenêtre **Sovereign Cheater** et le shell.
   Valider qu'on peut lire `GameState.quests_manager` depuis le shell.
   **C'est le jalon qui prouve la faisabilité — s'arrêter là et faire le point avec
   l'utilisateur avant d'aller plus loin.**
5. Ensuite seulement : voie C2, contrat JSON, bouton.

---

## 9. Comment travailler avec cet utilisateur

Ce sont des consignes permanentes, apprises à ses dépens :

- **Il demande souvent « est-ce que c'est possible ? » et ne veut PAS qu'on le
  fasse dans la foulée.** Répondre à la question posée. Attendre le feu vert avant
  d'implémenter. Il l'a rappelé explicitement pendant la session qui a produit ce
  brief.
- **Répondre vite.** Il attend une réponse en 5-10 s sur les questions simples, pas
  une enquête de trois minutes.
- Il écrit vite et avec des fautes de frappe ; interpréter avec bienveillance.
- Il parle français, répondre en français.
- Ne jamais annoncer qu'une salle du jeu est verrouillée d'après la sauvegarde :
  **le fichier retarde sur le jeu.**
- Noms de chevaliers en MAJUSCULES, noms d'objets en français, dans les tableaux.

---

## 10. Ce qui reste inconnu

À lever par le test, pas par la spéculation :

1. `override.cfg` posé à côté de l'exe est-il lu ? (décide voie A vs B)
2. Godot vérifie-t-il le md5 des entrées du `.pck` ? (décide s'il faut le recalculer)
3. Un `.gd` en clair ajouté au `.pck` est-il compilé à l'exécution ?
4. Le jeu démarre-t-il depuis un dossier copié hors de `steamapps` ?
5. La console Panku a-t-elle été amputée d'une partie de ses modules à l'export ?
   (les 109 scripts sont là, mais leurs `.tscn` — à vérifier au premier lancement)

---

## 11. Contexte utile mais non bloquant

Le simulateur `st.py` a été corrigé le 2026-09-06 : les passifs des chevaliers et
des objets sont désormais modélisés d'après `systems/autoloads/special_cases.gd`.
Voir la section « La moitié des passifs n'était pas simulée » du `README.md`.
Ça n'affecte pas le mod, mais c'est le contexte dans lequel ce brief a été écrit.

Les sauvegardes des versions d'avant correction sont dans le scratchpad de la
session d'origine (`st_py_avant_passifs.py`, `plan_py_avant_passifs.py`).
