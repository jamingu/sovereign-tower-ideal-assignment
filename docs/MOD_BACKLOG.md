# Backlog du mod — améliorations demandées

Demandées par le joueur le **2026-09-06**, pendant la construction du bouton
« répartition idéale ». À traiter **après** que le bouton fonctionne.

Toutes ces fonctions transforment le mod : d'un bouton qui applique, il devient un
**overlay d'aide à la décision**. Elles partagent donc un même socle — le solveur
Python doit pouvoir être interrogé *pendant* que le joueur manipule l'interface, et
plus seulement une fois avant d'appliquer.

## 1. Issue inattendue possible, par quête  ✅ FAIT (2026-09-06)

Afficher si une quête peut produire une issue inattendue. Sert notamment à **expliquer
pourquoi le plan envoie un chevalier seul sur une quête prévue pour 2 ou 3**.

Sans cette explication, la recommandation a l'air d'une erreur. Voir
`st.py simple` / `cmd_simple`, qui sait déjà détecter ces cas, et le `follow_up` de
l'ink (une issue inattendue se juge sur son audience de suivi).

## 2. Vrai score numérique des quêtes  ✅ FAIT (2026-09-06)

Le jeu affiche un camembert coloré sans chiffre. Afficher **le nombre réel**
au-dessus, sur 10.

## 3. Conseils d'achat et de repas  ✅ FAIT (2026-09-06)

Le plan dit déjà quelle est la meilleure affectation *à inventaire constant*. Ajouter :
« achète X ou Y pour améliorer », et la même aide pour le repas.

⚠️ Achats et repas coûtent de l'or et vivent dans d'autres salles (forge, écuries,
cuisine). C'est un **conseil affiché**, pas une application automatique — ne jamais
appeler `give_meal()` directement, ce serait un repas gratuit, donc de la triche, et
le budget or de `st.py` deviendrait faux.

## 4. Score en temps réel pendant l'affectation  ✅ FAIT (2026-09-06)

Recalculer et afficher le taux de réussite sur 10 **pendant que le joueur affecte**,
et le remettre à jour **quand il change une affectation** — donc y compris sur une
répartition faite à la main, différente de celle du solveur.

C'est le point le plus exigeant : il faut brancher un recalcul sur les signaux de
l'interface. `st.py cycle` tourne en **0,1 s**, donc un appel par changement est
tenable ; à surveiller quand même, ce n'est pas le même exercice qu'un appel unique
sur clic. Un mode « service » (un processus Python qui reste ouvert) est le repli si
le coût de démarrage devient gênant.

## 5. Tout doit être activable / désactivable

**Contrainte structurante, prise en compte dès l'écriture du mod.** Chaque
fonctionnalité ci-dessus est un interrupteur indépendant, lu depuis
`user://sovereign_mod/settings.json`.

Piste évoquée par le joueur : **un écran au démarrage de la partie** pour cocher ce
qu'on veut activer pour cette partie-là.


---

# Ce qui est livré

## Point 2 — chiffres sur le camembert  ✅

Réglage `wheel_numbers` (+ `wheel_font_size`, défaut 32). La valeur exigée est
ajoutée **en plus** du libellé d'origine, jamais à la place.

Deux pièges rencontrés, tous deux corrigés :

- **Ne pas re-décider ce qui est visible.** Répliquer la règle de
  `define_difficulty()` faisait afficher « 6 » là où le jeu affiche « ? », donc
  révélait une exigence non découverte. On lit maintenant `portion.difficulty` et
  on se contente de fournir le nombre.
- **Les parts du camembert sont pivotées.** Le label héritait de la rotation : un
  6 posé en bas se lisait 9. La rotation héritée est désormais annulée
  (`lab.rotation = -portion.get_global_transform().get_rotation()`).

## Point 1 — issues inattendues  ✅

Réglage `unexpected_outcomes`. Une ligne dans le panneau dit, pour la quête
sélectionnée : aucune / possible mais conditions pas réunies / **déclenchée par
l'équipe actuelle**.

- Source : `quest.special_outcomes` (+ `selected_modifier.unexpected_outcomes`) et
  `SpecialOutcome.are_conditions_met()`, qui est une fonction **pure**.
- ⚠️ **Ne jamais appeler `quest.determine_outcome()` pour un aperçu** : elle FIGE
  l'issue (`if not outcome == UNDEFINED: return outcome`) et déclenche dégâts et
  récompenses. Le résultat de la quête serait gelé avant validation.
- On annonce le fait, jamais la recette : les conditions portent parfois sur des
  caractéristiques que le joueur n'a pas encore découvertes.
- Éprouvé dans les deux sens sur `contract_almora_new_ramparts_building` (2 places,
  1 issue) : `false` avec ANGELICA seule, `true` avec CHILDERIC, le chevalier réclamé.

## Point 4 — score en temps réel  ✅

Réglage `live_score`. Nouvelle commande **`st.py live --in= --out=`** : le mod
envoie l'état RÉEL du plateau (équipe, équipement porté, repas), st.py renvoie les
scores. Le score du panneau se met à jour dès que le plateau change, y compris sur
une répartition faite à la main.

- `knights_from_save()` fond DÉJÀ les stats de l'équipement de la save dans
  `k['stats']`. Se contenter de remplacer `k['equip']` ne changeait donc rien au
  score. `_rehabille()` retire l'équipement de la save avant d'appliquer celui du
  jeu — sans ça, VICTORIA_SWORD laissait le score à 3,94 au lieu de 8,12.
- L'appel est bloquant (~0,2 s) : on ne relance que si la signature du plateau a
  changé, jamais à chaque image.

## Point 3 — conseils d'achat et de repas  ✅

Réglage `buying_advice`. `plan.json` porte maintenant `achats` (objet, prix,
boutique, bénéficiaire), `meal` et `meal_plats`. Le mod affiche, il n'applique rien.

**Bug trouvé au passage :** `_apply()` équipait les objets du plan sans vérifier que
le joueur les possède — il offrait donc gratuitement ce qui restait à acheter. Chaque
objet porte désormais `cost` / `owned`, et seul un objet possédé est équipé.

⚠️ Non éprouvé sur un cas réel : la partie de mise au point n'a ni or ni boutique
ouverte. Validé seulement côté Python via `--or=2000 --room=forge --room=stables`,
qui produit bien « Assil 45 or (stables) » et « Épée Rouillée 35 or (forge) ».


---

# Langue

Le code du mod (`sovereign_mod/main.gd`) est en **anglais** : commentaires, noms de
symboles, textes affichés en jeu, et noms des commandes du banc d'essai. Demandé par
le joueur le 2026-09-06.

Les clés de `settings.json` suivent : `assignment_button`, `clear_button`, `wheel_numbers`,
`wheel_font_size`, `unexpected_outcomes`, `live_score`, `buying_advice`,
`test_bench`.

`st.py` / `plan.py` restent en français — c'est un projet existant déjà entièrement
commenté ainsi, et mélanger les deux langues dans un même fichier serait pire. Seule
conséquence visible : les paliers renvoyés par le solveur (`REUSSITE`, `GRANDE
REUSSITE`…) s'affichent en français dans un panneau anglais.

# Écran d'options  ✅ FAIT (2026-09-06)

Point 5 du backlog. S'affiche au menu principal, une case par fonctionnalité,
rouvrable par **F9**, avec une case pour ne plus l'afficher au démarrage.

Chaque case agit **immédiatement** : tout est construit et démarré sans condition au
`_ready()`, et chaque fonction relit son réglage à l'exécution. C'est ce qui évite un
redémarrage après chaque changement.


# État au 2026-09-06 (fin de session)

Les 5 points du backlog sont livrés. Le mod s'appelle **Sovereign QoL mod** dans
l'interface. L'écran d'options s'ouvre par le bouton **« Mod options »** de l'écran
d'accueil ou par **F9** — plus d'ouverture automatique, le joueur l'a trouvée
intrusive.

Le panneau de jeu se déplace en l'attrapant n'importe où, se redimensionne en
largeur par ses bords gauche/droit, et sa hauteur suit le contenu (`_fit_height`).
Position et largeur sont mémorisées dans `settings.json`.

## Jamais éprouvé en conditions réelles

La partie de mise au point est au cycle 1 : un seul chevalier, aucun objet, aucune
boutique ouverte, une seule quête. Trois chemins n'ont donc jamais tourné pour de
vrai, et sont à vérifier quand la partie aura avancé :

1. **Équiper un objet** — seul le REFUS (objet verrouillé, objet non possédé) a été
   testé, jamais la pose effective d'une relique ou d'une monture.
2. **Conseils d'achat** — validés uniquement côté Python en forçant
   `--or=2000 --room=forge --room=stables`, jamais affichés dans le panneau.
3. **Plusieurs quêtes et plusieurs chevaliers en même temps** — la boucle
   d'affectation n'a jamais traité plus d'une quête à un seul chevalier.

---

# Écran d'audience (demandé le 2026-09-06)

Trois ajouts, chacun avec son interrupteur.

## Valeurs sur le camembert  — normalement déjà couvert

La détection des camemberts se fait par chemin de script sur `node_added`, sans
condition de scène : si l'audience affiche le même `DifficultyHintWheel`, les
chiffres y sont déjà. **À confirmer en jeu**, aucune audience n'a pu être testée.

## `audience_rewards` — nommer l'objet offert  ✅

Le jeu construit l'icône relique/monture/consommable avec des arguments **vides**
(`choice_button.gd`, `update_for_equipment`) : le nom de l'objet n'arrive jamais
jusqu'au bouton, le joueur voit « tu gagnes un objet » sans savoir lequel.

Le nom n'existe que dans l'ink, sous la forme
`{"VAR?":"RELIC"},{"VAR?":"Wolf_Skin"},{"f()":"UnlockEquipment"}`.

Nouvelle commande **`st.py hints --in= --out=`** : le mod envoie les libellés des
options affichées, `plan.py` les localise dans le scénario et renvoie ce que chacune
débloque (équipement + quêtes). `Wolf_Skin` → `WOLF_SKIN` → « Peau de Loup ».

- La recherche de texte de `cmd_choix` a été extraite en `_ink_index()` / `_ink_norm()`
  et est maintenant partagée par les deux commandes (non-régression vérifiée).
- Un knot est cherché **par libellé**, pas une seule fois : une option amenée par un
  renvoi vit dans un autre knot et sortait « introuvable ».
- Éprouvé côté Python : « Peau de Loup » (relic) et « Provisions » (consumable)
  correctement extraites, option inexistante correctement signalée.
- **Pas encore vu en jeu** : il faut une vraie audience.

## `audience_outcomes` — issues inattendues et qui les déclenche  ✅

Un choix qui octroie une quête porte son `related_quest_id`, donc la ressource se
charge et s'inspecte avant même que le choix soit fait
(`quests_manager.get_quest_from_id`).

Le tooltip du bouton reçoit, sous un séparateur (celui du jeu est préservé) : places
et durée, puis chaque issue inattendue avec **le chevalier nommé** — ou le trait, ou
la statistique requise à défaut.

Contrairement au panneau de la table ronde, celui-ci **nomme** les chevaliers : le
joueur l'a demandé explicitement, pour décider si une quête vaut d'être prise.

Éprouvé : `contract_almora_new_ramparts_building` → « Unexpected outcome: 1 / with
Childéric ».

⚠️ **Ne jamais déclencher une audience par script pour tester** :
`achievement_manager` écoute `audience_started` et `dialogue_started`. Voir la note
sur les succès Steam irréversibles.

---

# Le mod ne doit JAMAIS bloquer une image (2026-09-07)

Le joueur a vu « une seconde de latence avant l'affichage des conversations », le jeu
figé, et un plantage au moment d'un choix. Deux causes, cumulees.

## Le mauvais libelle

`ChoiceButton` **etend `Button`** : le texte de l'option est sa propre propriete
`text` (`dialogue_container.gd` : `choice_buttons[i].text = ... choices[i].text`).
Le mod parcourait ses enfants et prenait le premier label non vide — c'est-a-dire les
pastilles decoratives. Il interrogeait donc le solveur sur `"+"`, `"->"` et `"-"`, a
chaque ligne de dialogue, pour rien. `hints_in.json` en portait la preuve :
`{"choices":["+","→","-"]}`.

## L'appel bloquant

`OS.execute(..., true)` gele la frame pendant tout le demarrage de Python. Remplace
par `OS.create_process` : le mod lance, et relit la reponse un tick plus tard.

- Cote Python, `st.py hints` et `st.py live` ecrivent maintenant dans `<dst>.tmp`
  puis `os.replace()`. **L'apparition du fichier est le signal**, donc elle doit etre
  atomique — sans le renommage, le mod lirait un JSON tronque.
- Un compteur (`MAX_WAIT`, 40 ticks = 12 s) libere l'attente si l'interpreteur ne
  repond jamais ; sans lui, plus aucune infobulle ne serait annotee.
- `pythonw.exe` est prefere a `python.exe` quand il est a cote : pas de console qui
  clignote.
- Les scores restent affiches pendant le recalcul (`_scores.clear()` deplace dans
  `_collect_scores`), sinon le panneau clignotait a vide a chaque changement.
- Le cache des recompenses garde `""` pour une option qui ne donne rien : elle n'est
  jamais redemandee.

Seul `_run_solver()` (bouton « repartition ideale ») reste bloquant — le joueur vient
de cliquer et attend le resultat.

## « Gardes ? » existe cinq fois

`_ink_index` rendait la PREMIERE occurrence. « Gardes ? » apparait aussi dans
« Gardes ?! Faites sortir cet homme », bien plus haut : on tombait sur le knot
`suspicious` au lieu de `guards`, et l'option ressortait « introuvable ».

- Nouveau `_ink_positions()` : toutes les positions, pas la premiere.
- La normalisation complete decalerait les index (3,9 Mo, 0,18 s) ; on se contente
  donc de rabattre les **espaces insecables** (`_ESPACES`), ce qui garde la longueur
  du texte donc les positions. C'est le seul ecart reel : la typographie francaise
  met une espace fine devant « ? ».
- `cmd_hints` retient le knot qui couvre le **plus** de libelles affiches : les
  options d'un meme ecran appartiennent au meme knot.

## Format de l'infobulle

A la demande du joueur : plus d'en-tete (nom et duree de la quete sont deja sur la
carte), plus de compteur, **une ligne par issue** — « Unexpected outcome with
Childéric ». Verifie en jeu par la commande `test_choice`.

## Nommer la recompense promise par une quete (2026-09-07)

La carte de quete affiche une icone « Monture » generique, et le jeu ne laisse PAS
la survoler : le survol ferme la popup d'audience. Le joueur ne peut donc pas savoir
ce qu'il gagne avant d'accepter.

L'objet est pourtant dans la ressource : `QuestReward` porte `relic` / `mount` /
`consumable` / `quest_item` selon son `reward_type`. `_quest_reward_lines()` lit
`quest.success_rewards` — de la donnee exportee, pure — et rend
« Reward: Paul (mount) ». Verifie sur `contract_magpie` :
`res://content/equipment/mounts/paul.tres`, soit PAUL (FOR +1, CHA -2).

⚠️ Ne JAMAIS appeler `quest.determine_rewards()` : elle declenche les recompenses.

À noter : l'extracteur Python ne garde que `{"type": "MOUNT"}` dans `cache/quests`,
sans l'identifiant de l'objet. Le mod lit la ressource vivante, donc il n'en souffre
pas, mais `st.py` ne sait pas quelle monture une quete promet.

## La sauvegarde retarde : trois symptomes, une seule cause (2026-09-07)

`st.py` reconstruit la partie depuis `save_slot_1.res`. Or la sauvegarde est ecrite
en fin de cycle : tout ce que le joueur fait PENDANT le cycle lui est invisible.
Trois bugs signales le meme jour, tous de la meme famille.

**1. L'objet achete restait « a acheter ».** Le plan portait `"owned": false` pour
une epee deja payee, et `_apply()` refusait de l'equiper. Le drapeau `owned` n'est
plus l'autorite : `_item_available()` interroge la memoire vive. Le garde-fou
anti-triche tient toujours — un objet encore en boutique n'est pas dans l'inventaire.

**2. L'objet achete n'etait pas reconnu.** `load()` rend l'instance du disque ;
l'exemplaire achete dans le cycle en est une AUTRE. La comparaison par identite
repondait « indisponible ». `_available_item()` compare desormais aussi le `name`
(l'identifiant unique) et **rend l'instance du jeu**, pas celle du disque — passer la
copie disque a `_on_equipment_assignation_requested` laisserait un fantome en stock.

**3. La montee de niveau ne changeait pas le score.** Les stats venaient de la save.
Le mod envoie maintenant `stats` dans `live_in.json` :
`get_statistic_value_from_id(id, false)`, donc **hors equipement**, que `cmd_live`
utilise a la place de celles de la save avant de rajouter l'equipement porte.
Il a fallu aussi ajouter le total des stats a `_board_signature()`, sinon rien ne
declenchait le recalcul.

## Conseil de montee de niveau  ✅

`plan.json` portait deja `levels` sans que rien ne l'affiche. Le panneau dit
« Level up ANGELICA: AGILITY ». Le mod n'applique pas : le point se depense dans la
tour, et c'est un choix definitif.

## Conseils perimes : `stats_base` (2026-09-07)

Meme famille que le reste : `st.py` lit la sauvegarde, donc il conseille encore un
point de niveau deja depense. `plan.json` porte maintenant `stats_base` — les stats
de chaque chevalier **hors equipement**, telles que la save les donne. C'est la meme
base que `get_statistic_value_from_id(id, false)` cote jeu, donc les deux sont
comparables : si la valeur vivante a depasse celle du plan, la ligne disparait.

Retirer l'equipement est indispensable : `knights_from_save()` a fondu ses stats dans
`k['stats']`, et comparer une valeur avec epee a une valeur sans epee ferait
disparaitre le conseil a tort.

Le panneau est decoupe en sections (issue / repas / niveau / achats), separees par un
filet fin. Une section vide se cache **avec son filet** : `_refresh_rules()` ne pose
un trait que la ou il separe vraiment deux blocs visibles.

## Nommer la recompense sur la carte de quete  ✅ (2026-09-07)

Reglage `reward_names`. Chaque pastille de « Recompenses promises » est un
`RewardDisplay` (`scenes/roundtable/reward_display.gd`) qui porte son propre
`QuestReward` : l'objet est a un saut de la, il suffit de le mettre en infobulle.
Le mod y ajoute aussi les stats : « Paul (mount, STRENGTH +1, CHARISMA -2) ».

⚠️ Un `PanelContainer` ignore la souris par defaut : sans
`mouse_filter = MOUSE_FILTER_STOP`, aucune infobulle ne s'afficherait jamais.

### Piste : afficher la VRAIE carte de l'objet

Demande du joueur, **pas encore faite**. `res://scenes/roundtable/equipment_display.tscn`
existe dans le .pck (`EquipmentDisplay`, extends AspectRatioContainer) : nom,
illustration, description, stats, passif, armure, duree. Il en existe une version
courte, `equipment_display_short.tscn`.

Il suffirait de l'instancier et de lui poser `equipment`. Deux pieges :
`_set_equipment()` **sort immediatement si le noeud n'est pas pret** — il faut
l'ajouter a l'arbre et `await ready` AVANT de poser l'objet ; et un
`AspectRatioContainer` n'a pas de taille propre, il lui faut un parent dimensionne.
