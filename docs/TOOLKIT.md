# Sovereign Tower — boîte à outils data

Extraction / décompilation du jeu (Godot 4.6) + lecture de la sauvegarde.

## Usage

```
python st.py build                      # (re)construit cache/ depuis le .pck  (~1 min, à refaire après un patch)
python st.py quest <motif>              # fiche complète d'une quête + texte FR
python st.py knight [nom]               # fiches de base des chevaliers
python st.py equip [motif|TAG]          # équipements : stats, tags, prix
python st.py tr <CLE|texte>             # clé -> texte FR, ou recherche plein texte
python st.py script <motif>             # décompile un .gd (ex: quest, special_cases, cycles_manager)
python st.py res <motif>                # dump brut d'une ressource .res/.scn
python st.py save [slot]                # état de la sauvegarde (chevaliers réels, quêtes, délais, or)
python st.py jour [quete...] [--meals]  # *** LA JOURNEE COMPLETE : audience + fin de journee + projection ***
python st.py plan [slot] [--quests=a,b] [--no-meals]   # *** LA commande : répartition optimale du cycle ***
python st.py cover <quest_id> ... [budget]             # répartition globale multi-quêtes, objets partagés
python st.py choices <knot_ink>                        # libellés de choix -> quête + modificateur
python st.py effects <knot_ink>                        # effets d'une audience (recrutements, ralliements)
python st.py ink <motif> [n]                           # recherche plein texte dans le scénario
python st.py score <quest_id> [nom ...] # simule le score de la quête (sans noms : toutes les équipes)
```

## `plan` — la commande principale

Répond à « je suis au jour X, j'ai les quêtes A/B/C, comment je fais au mieux » :
lit la sauvegarde, affiche chaque quête (requis, conditions, délai, récompenses, dégâts),
le score de **chaque chevalier** dessus, signale les **issues scénarisées** — dont les
mortelles (`damage_range` 100) —, propose l'**objet le moins cher qui fait gagner un palier**
dans la limite de l'or disponible, puis classe les répartitions possibles.
Les repas (+0,5) ne sont comptés que si la cuisine est débloquée.

## Ce qui est dans `cache/`

`res/` 2236 ressources, `scn/` 328 scènes, `gd/` 847 scripts compilés, `lang/` traductions,
plus `quests.json` (308 quêtes décodées), `knights.json`, `equipment.json`, `enums.json`,
`efficiency.json` (tags efficaces/inefficaces par catégorie et condition de quête).

## Notes de format (utile si ça casse après un patch)

- `.pck` Godot 4.6, pack format **3** : le répertoire est à `dir_offset` (u64 à l'offset 0x20),
  les données à partir de `file_base` = 0x70.
- `.res/.scn` : format binaire v6. Le champ `script_class` n'existe **que** si `flags & 8`.
  NodePath = 2×u16 (name_count, subname_count|0x8000). PackedByteArray padde sur la **longueur**,
  pas sur la position absolue. Les chaînes ne sont **pas** paddées.
- `.gdc` : header `GDSC` + version + taille décompressée + zstd. Corps :
  4 compteurs u32, identifiants (UTF-32 XOR 0xB6), constantes (marshalling Variant),
  2 tables ligne/colonne, puis **8 octets par token** (u32 token | u32 ligne).
  Token : `type = w & 0x7F`, valeur = `w >> 8` si bit 0x80.
- `.translation` = OptimizedTranslation : table de hachage FNV + smaz. `trans.py` fait le décodage.
- Sauvegarde : `%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\saved_games\save_slot_N.res`,
  conteneur `RSCC` (blocs zstd de 4096). Une fois décompressé le flux commence **après** le magic
  `RSRC` : d'où `Res(..., off_base=4)` après avoir re-préfixé `RSRC`.

## Modèle de score des quêtes (quest.gd `determine_outcome`)

```
score = -10 + Σ(10/nb_demandés par chevalier présent) + Σ stats + Σ tags + 0,5 par repas
stat  = (valeur - (requis + manquants)) × 0,66 / nb_envoyés
        × (1 + requis×0,0275) si > 0, sinon × (1 + requis×0,01)     (0,22 si valeur == requis)
```
Seuils : `>0` réussite, `>5` grande réussite, `>=10` critique ; `<=0` échec (conséquences),
`<=-5` échec majeur, `<=-10` échec critique (dégâts 100 → morts si `lethal`).
Les tags **inconnus** comptent aussi. `special_cases.gd` ajoute les bonus conditionnels
(TANK, GAMBLER, PROBLEM_SOLVER, COASTAL, KIND_HEARTED…), modélisés dans `SPECIAL` de `st.py`.


## Pièges vérifiés par l'outil (une entrée = une erreur commise)

- chevaliers déjà engagés (`assigned_quest`) exclus, et leur équipement avec eux
- modificateur tiré par la sauvegarde appliqué aux exigences / dégâts / effectifs
- délais : dernier cycle utile calculé, `PEUT ATTENDRE` vs `DERNIERE CHANCE`
- risque de mort : armure comparée aux dégâts du palier, avec le coût de réparation à la forge
- équipement déjà porté = transfert gratuit, jamais un achat
- montées de niveau en attente : chiffrées par quête, stat par stat
- stats aléatoires (Chester) : évaluation Monte-Carlo, jamais un score déterministe
- audiences de suivi lues dans l'ink (recrutements cachés)
- choix scénaristiques mappés depuis les blocs ink, jamais devinés
- un seul repas par cycle, placé là où il fait basculer un palier
- équipes plus petites que demandé autorisées (exigences +1 par manquant)
- un repas = **un seul chevalier**, jamais toute l'equipe ni toutes les quetes a la fois
- comparaison repas / equipement sur une equipe a stats aleatoires : deux distributions Monte-Carlo, jamais une moyenne face a un score deterministe
- **surnombre impossible** : `quest_presentation_short.gd` n'affiche que `max_knights` emplacements, on ne peut pas envoyer plus de chevaliers que demande
- duree > 1 cycle signalee : les chevaliers envoyes sont immobilises, verifier qui reste pour les urgences du cycle suivant
- contrats sans `deadline` : les laisser en attente ne coute rien, verifie avant de sacrifier une urgence
- quetes de la sauvegarde introuvables dans `quests.json` : repli sur le nom de fichier (les ultimatums en dependent) puis **avertissement**, jamais un silence
- conditions supplementaires (`QuestExtraCondition`) : **+2 par condition remplie**, lues dans l'ultimatum vivant de la sauvegarde (comtes rallies / satisfaction / or), pas dans le .tres de la quete
- sur une quete a conditions supplementaires, tout score <= 0 est un **ECHEC CRITIQUE** (degats 100, morts) : pas d'echec doux, la marge de securite n'est pas la meme
- garder l'or au-dessus du seuil `MIN_FUNDS` : depenser fait perdre le +2 correspondant
- cout de reparation croissant dans le cycle : `15*(1+n) + 4*n^2` -> 15, 34, 61, 96 or (ce n'est pas 15 a chaque fois)
- branches de resolution comparees (`branch_unlocks`) : une issue inattendue peut etre la **seule** a debloquer une quete de suite ; prendre la reussite ordinaire supprime alors une mission definitivement
- vocabulaire des stats : afficher les abreviations du JEU (`FOR AGI CHA MAG INT FRT`) et jamais une traduction inventee -- `WITS` s'affiche **INT**, `LUCK` s'affiche **FRT**
- **portes de satisfaction** (`RequiresMinSatisfaction`) : certaines quetes ne sont PROPOSEES qu'au-dessus d'un seuil de satisfaction, invisible dans le .tres de la quete -- il est dans l'ink de l'audience. Verifier le seuil AVANT de planifier le cycle qui doit la debloquer (Recherches mystiques : Erudits >= 7)
- l'issue inattendue d'une quete est **retiree au sort quand on recharge une sauvegarde anterieure** (`selected_modifier`) : le declencheur peut changer d'un chargement a l'autre (chevalier nomme -> tag requis). Toujours relire la save, ne jamais reutiliser le declencheur d'une lecture precedente.
- suggestion d'equipement : afficher le moins cher **et** celui qui donne la meilleure marge ; une marge de 0,03 sur une quete a echec lourd n'est pas une reussite, c'en est l'illusion
- **la sauvegarde est en retard sur le jeu** pour les salles debloquees en cours de cycle (cuisine, etables...). Ne jamais AFFIRMER qu'une salle est verrouillee : le dire au conditionnel et proposer `--meals` pour forcer. Erreur commise deux fois sur la cuisine.
- `success_rewards_modification` du modificateur tire : fusionner dans les recompenses de la quete, sinon on rate des gains (un +1 erudit manquant a failli couter une condition d'ultimatum)
- **plafond de la table ronde** (`character_manager.gd get_roundtable_size_limit`) : 6 chevaliers a l'acte 1, 8 a l'acte 2, 10 a l'acte 3. Un conflit d'effectif ne se resout JAMAIS par du recrutement a l'acte 1 : presenter l'arbitrage tout de suite.

## `jour` -- la commande de la journee

`python st.py jour [motifs de quetes proposees] [--meals]` repond aux deux questions
du cycle en une fois :

1. **AUDIENCE** : pour chaque quete citee, ses variantes avec le **libelle exact du jeu**
   (extrait de l'ink, jamais devine), les exigences/degats/effectif/duree de chacune,
   le meilleur resultat atteignable avec les chevaliers libres, les recompenses,
   les portes de satisfaction (`RequiresMinSatisfaction`), les quetes qu'une seule
   branche debloque, et surtout **l'impact sur le calendrier**.
2. **FIN DE JOURNEE** : le rapport `plan` (qui part sur quoi, achats, repas, niveaux).
3. **PROJECTION** : effectif libre cycle par cycle contre les echeances connues, avec
   un vrai **test de faisabilite d'ordonnancement**. S'il n'existe aucun calendrier ou
   toutes les echeances partent a temps, l'outil le dit et designe **la moins grave a
   sacrifier**, ponderee par le cout de la perte (`loss_cost` : lieu detruit, mort de
   personnage, ultimatum...) et surtout PAS par le nombre de corps mobilises.

C'est ce qui manquait a chaque fois : on arrivait au cycle de l'echeance sans les
chevaliers, faute d'avoir simule les durees a l'avance.
- **la cuisine se debloque au cycle 5** (constate en partie). La sauvegarde la liste encore dans `locked_rooms` : lancer `st.py jour --meals` a partir du cycle 5 sans attendre que le fichier le reflete.
- stock de la forge : 11 objets a l'acte 1 (tous <= 180 or). Les bonnes reliques magiques (GLASS_BLADE, SPELL_BOOK, VERMEIL...) sont **acte 2+**, l'or ne sert a rien pour les obtenir plus tot.
- **equiper toute l'equipe, pas un seul chevalier** : `st.py loadout <quete> <chevaliers...>` balaye les objets pour tous les membres a la fois. Donner une relique gratuite au second chevalier rapporte souvent autant que l'achat le plus cher pour le premier (verifie sur Abattre le malefice : +0,30 dans les deux cas).
- **comparer les equipements sur une base identique** : toujours passer par `_with_item`, qui retire l'objet du meme slot avant d'ajouter le candidat. Comparer en laissant un objet deja porte dans un cas et pas dans l'autre a produit une recommandation inversee (arc de chasse au lieu de la claymore).

## `cycle` -- le tableau recapitulatif

`python st.py cycle [--meals]` sort en une fois : quete, chevaliers, **reliques a affecter**,
**montees de niveau**, repas, achats. Il corrige quatre erreurs faites a la main :

- **un objet, un porteur** : les equipes ne peuvent plus se disputer la meme relique
- **« rien » est un choix** : un objet peut etre un malus (epee d'argent FOR-1 sur une quete
  sans magie -> 4,66 contre 4,93 sans rien). L'optimiseur essaie systematiquement de desequiper.
- **trois emplacements** par chevalier (relique + monture + consommable), consommables possedes inclus
- **montees de niveau calculees sur la quete REELLEMENT assignee**, jamais sur celle ou le
  chevalier etait seulement candidat (erreur : +2 FORTUNE conseilles pour une quete qui n'en demandait pas)
- colonne DUREE dans `st.py cycle` : « 2c>8 » = quete de 2 cycles, chevaliers de nouveau libres au cycle 8. Indispensable pour ne pas immobiliser du monde avant une echeance.
- **consommables possedes invisibles** : `available_items` eliminait tout objet dont `cost` est `None` (objets de quete comme les PROVISIONS). Ils sont desormais resolus depuis la sauvegarde et proposes comme les autres. Un consommable occupe son propre emplacement : il se cumule avec relique + monture.
- **l'ultimatum disparaissait du tableau** : sans ses conditions supplementaires injectees dans MODS, il score <= 0, donc `deadly`, donc `best_assignments` l'ecartait en silence. Les conditions sont maintenant appliquees avant toute evaluation dans `cmd_cycle`, avec un avertissement en tete de tableau.
- penalite de non-assignation ponderee par le TYPE : un `ULTIMATUM_QUEST` a `failure: []` dans son .tres, donc il ne coutait que -0,5 et le planificateur preferait quatre contrats ordinaires a la bataille finale. Desormais -100 pour un ultimatum, -30 pour toute quete a conditions supplementaires (echec = morts).
- **tag FORTUNATE modelise** (`quest.gd:268-272`) : un chevalier qui le porte donne **50 % de chance de monter d'un palier** si score+1 suffirait. Signale dans l'issue : `REUSSITE (FORTUNE : 50% -> GRANDE REUSSITE)`. Angelica le porte.
- **affinite et preferences de quete n'entrent PAS dans le score** : `determine_affinity_scores` (±1,33 / -0,75 par categorie ou condition aimee) n'est appelee que par l'interface. Elle ne joue que sur l'affinite gagnee, jamais sur la reussite. Ne pas en tenir compte dans les calculs de score.
- **consommables a usage unique** : ne les affecter QUE s'ils font basculer un palier, comme le repas. L'optimiseur les brulait pour +0,7 sur une quete deja en reussite critique.

- **Un chevalier accepté à l'audience n'arrive qu'AU CYCLE SUIVANT.** L'audience ne
  permet que de l'inviter pour le lendemain ; il ne compte pas dans l'effectif du
  cycle en cours. Ne jamais le placer sur une quête du jour même.

- **Un score de 10,00 n'est PAS une reussite critique acquise.** Le seuil est
  `score >= 10` : a marge nulle, une montee de niveau depensee dans la mauvaise stat
  ou un consommable non equipe fait retomber d'un palier. Le tableau marque desormais
  `/!\ marge +X -- FRAGILE` sous 0,75 de marge, et imprime une checklist
  « AVANT DE LANCER LES QUETES » des actions manuelles que le plan suppose
  (montees de niveau, consommables a equiper). Erreur reelle : cycle 9,
  « Les plumes d'argent » annoncee critique a 10,00, sortie en grande reussite a 9,255
  parce que la montee etait partie en CHA et la soupe de gargouilles n'etait pas equipee.

- **`remaining_cycles_before_faillure` ne veut RIEN dire sans `deadline: True`.**
  `cycles_before_automatic_faillure` vaut 1 par defaut sur toutes les quetes, et
  `quests_manager` fait `if not quest.has_deadline: continue` avant de decompter.
  Un contrat sans echeance reste sur la table indefiniment : ne jamais annoncer au
  user qu'il va « perdre » une quete. Erreur reelle : cycle 10, quatre contrats
  presentes comme expirant alors qu'aucun n'avait d'echeance.

## Une urgence a deadline dont l'echec detruit une localite est NON ARBITRABLE
Cycle 14 : le planificateur a sorti `quest_villador_emergency` (deadline 2, `failure:
LOCATION_DESTROYED VILLADOR`) du plan pour prendre `contract_search_merchants_guild_traitor`,
mieux note. La penalite de non-assignation valait -6 pour toute quete a deadline avec
recompenses d'echec ; c'est desormais **-100** (comme l'ultimatum) des qu'une recompense
d'echec est de type `LOCATION_DESTROYED`. Voir `best_assignments`.

## Le compte a rebours d'une deadline ne tourne que tant que la quete n'est pas lancee
`quests_manager.update_quests_duration` : la boucle qui fait
`remaining_cycles_before_faillure -= 1` parcourt `current_quests`. Une quete lancee est
passee dans `newly_locked_quests` puis `ongoing_quests` : son delai ne descend plus, seule
`duration` compte. Donc une quete duree 3 / deadline 3 (`quest_almor_jousting_tournament`)
peut attendre deux cycles avant d'etre lancee.

### Correctif du correctif (meme cycle)
Forcer -100 des qu'une deadline existe est FAUX : une urgence a delai 2 peut attendre un
cycle. Villador (4 places) + Treflemont (2) + le tournoi (1) = 7 places pour 6 chevaliers,
et l'outil se retrouvait sans solution. La penalite vaut donc **-100 seulement si
`LAST_CHANCE[qid]`** (dernier cycle avant echec automatique), **-12 sinon** : lourde, mais
arbitrable. Reporter une urgence n'est legitime que si l'effectif du cycle suivant est
verifie.

## Le repas partait sur un chevalier deja au plafond d'affinite
`optimise_cycle` decidait « le repas fait-il basculer un palier ? » en comparant les
libelles `d[3]`, qui contiennent les POURCENTAGES ('critique 66% grande 33%'). Un
demi-point deplace les probabilites sans changer de palier : la comparaison etait donc
toujours vraie et le repas allait au premier chevalier de la liste. Cycle 14 : Gwendan,
deja a 10,0 d'affinite, alors que le tournoi restait critique sans repas (10,98 contre
11,48). La comparaison porte desormais sur le PALIER seul.

## Boucle temporelle : les options debloquees par la CONNAISSANCE
Le demon ne remet a zero QUE les variables ink listees dans
`demon_manager.ink_variables_to_reset` (`DemonSave.apply`) ; toutes les autres gardent
leur valeur courante. C'est le coeur du design : certains choix d'audience sont caches
derriere une variable de connaissance acquise dans la boucle precedente.

**Cas de reference -- la Lampe a souhaits (Port-Azur).**
`county_quest_southbay_first_audience > ask_contract > quest_accepts`, choix c-3 :
« Je sais ou est la lampe. Nous mettrons la main dessus les premiers. »
- conditions : `RequiresTag(Omniscient, 1)` ET `wish_granting_lamp_stolen_known`
- `wish_granting_lamp_stolen_known` est pose par la REUSSITE de
  `quest_southbay_expedition_on_djinn_isle`, et n'est PAS dans la liste de reset
- effet : `UnlockQuest(quest_southbay_expedition_on_djinn_isle, 0)` -> modificateur 0,
  dont les `success_rewards_modification` contiennent la Lampe a souhaits
  (le modificateur 1, celui des autres branches, pose `lamp_found_on_djinn_island=false`)
- suite : Ligia repond par `isle_already_visited` (« vous auriez du commencer par la »)

**Erreur commise :** j'ai d'abord situe ce choix dans `county_quest_southbay_2` (l'audience
du cycle 13). Faux -- c'est la PREMIERE audience du comte. Le user a depense un retour en
arriere au cycle 12-13 pour rien. Cause : j'ai cherche les frontieres de noeuds sur la
chaine DECODEE alors que les offsets de recherche etaient en OCTETS. Les accents rendent
les deux echelles differentes. **Toujours travailler en octets d'un bout a l'autre quand
on localise un noeud ink.**

## Les versions d'une quete peuvent changer les OBJETS, pas que les stats
`apply_save_modifiers` ne notait que stats/degats/duree/effectif. Or
`success_rewards_modification` peut ajouter un objet decisif (la Lampe) ou poser un flag.
Avant de conseiller une version a une audience, comparer les recompenses de CHAQUE
modificateur, pas seulement les chiffres.

## Consommables brules pour rien : le rang etait calcule sur le libelle annote
`optimise_cycle` gardait les consommables des que la variante sans consommable paraissait
d'un rang inferieur. Le rang venait d'un `k in o` sur le libelle COMPLET, qui contient les
pourcentages et les annotations du type « (fortune : 50% -> reussite critique) » : la
sous-chaine 'REUSSITE CRITIQUE' y etait trouvee et gonflait artificiellement la version
avec consommable. Cycle 9 : trois grandes reussites des deux cotes, et l'outil brulait
quand meme Fromage murmurant et Soupe de gargouilles. Le rang se calcule desormais sur la
partie AVANT la premiere parenthese. Regle du user, redite deux fois : **un consommable ne
se depense que s'il fait basculer un palier.**

## Montures : la montee gloutonne ne pouvait PAS trouver l'echange
`optimise_cycle` ne deplace qu'un objet a la fois et exige un gain immediat. Prendre une
monture a un chevalier pour la donner a un autre est un mouvement en DEUX temps dont la
premiere moitie est une perte : l'optimiseur ne pouvait structurellement jamais le
trouver. Cycle 10 : Ennba sur Arron ne servait a rien tant qu'Angelica etait a pied (la
reduction retenue est celle du plus lent), et Paul dormait sur une quete d'un seul cycle.
Ajout de la passe **2d) MONTURES**, qui teste explicitement ces echanges sur toute quete de
plus d'un cycle. Beaconsbury est passee de 2c a 1c.

## AUDIT automatique en fin de plan
`st.py cycle` imprime desormais un bloc `!! AUDIT` qui rejoue, sur le plan FINAL, chaque
erreur deja signalee par le user :
1. equipier a pied sur une quete de plusieurs cycles (annule la reduction des autres)
2. monture posee sur une quete d'un cycle alors qu'une quete longue en manque
3. repas sur un chevalier deja a 10,0 d'affinite
4. quete en DERNIERE CHANCE non assignee ; urgence reportee dont l'echec detruit une localite
5. chevalier au repos alors qu'une quete reste jouable sur le plateau

Le but est que ce soit l'outil qui detecte une regression, plus le joueur. **Ne jamais
presenter un plan sans avoir lu son bloc AUDIT.**

## La moitié des passifs n'était pas simulée (`special_cases.gd`)

Tous les passifs vivent dans un seul fichier décompilé, `systems/autoloads/special_cases.gd`,
en trois fonctions : `check_for_special_cases_for_score`, `..._for_damage`, `..._for_rewards`.
La table `SPECIAL` de `st.py` n'en couvrait que quinze sur trente et une, avec deux erreurs.

Ce qui manquait ou était faux :

- `KIND_HEARTED` pouvait valoir **−1** (quête qui coûte de la satisfaction au Peuple) ;
  l'outil rendait 0. Idem `TRUE_NOBLE_SOUL`, qui somme les **quatre** catégories et non le
  seul Peuple.
- Absents : `NOBLE_SOUL`, `REVOLUTIONAR`, `NOBILITY_PRIMES`, `LOYAL`, `SPEEDSTER`,
  `BELIEVER`, `CHEESE_LOVER`, `TIME_PERCEPTION`, `SYPHON`, `PROTAGONIST`.
- `PATIENT` / `OVERWORKED` lisaient la durée **de base** au lieu de la durée réellement
  tenue par l'équipe (`updated_duration`).

### Un objet donne un vrai passif, pas qu'un bonus de stat

`knight.gd::get_all_characteristics()` **fusionne les tags de l'équipement** avec ceux du
chevalier. Un objet déclenche donc tout ce qu'un tag inné déclenche — cas spéciaux,
efficacité, et conditions d'issue inattendue. `score()` reconstruit maintenant cette liste
fusionnée ; avant, les tags d'objets n'atteignaient jamais `SPECIAL`.

Les gros morceaux, tous portés par de l'équipement :

| Objet | Effet | Condition |
|---|---|---|
| Lampe à souhaits | +100 | aucune |
| Dague empoisonnée / Venin de manticore | +100 | quête d'ASSASSINAT |
| Décoction démoniaque (150 or, tour de la sorcière acte 3) | +100 | quête déjà faite dans une run précédente |
| Œil de démon (400 or, réutilisable) | +8 | idem |

`all_time_completed_quests` **survit au NG+ et aux retours du démon** (176 entrées dans la
sauvegarde actuelle). En seconde partie, les deux derniers se déclenchent quasiment partout.

### Un cas spécial ÉCRASE l'efficacité, il ne s'y ajoute pas

`quest.gd` écrit l'efficacité (`±1`) et le cas spécial dans le **même dictionnaire indexé
par tag**. Donc :

- cas spécial **positif** → il remplace le `+1`/`−1` d'efficacité ;
- cas spécial **négatif ou nul** → il part dans un autre dictionnaire et s'**ajoute** ;
- cas **absent** du dictionnaire → seule l'efficacité compte.

18 tags sont concernés dans les tables d'efficacité, dont `DEADLY_WEAPON` : sur un
assassinat il vaut 100, pas 101. L'outil additionnait bêtement les deux.

### Où c'est implémenté

Trois helpers dans `st.py`, utilisés à la fois par `score()` et par `plan.py::_contrib`
(qui dupliquait le calcul et divergeait) :

- `prep_quest(q, qid, knights)` — pose `_id`, `_red` (réduction de durée retenue = minimum
  de l'équipe) et `_dur` ;
- `prep_knight(k, size, knights)` — tags fusionnés, affinité moyenne des équipiers, meurtres
  d'Édith ;
- `tag_score(q, k, E, I)` — applique la règle d'écrasement ci-dessus.

`CTX` (satisfaction courante, quêtes déjà terminées) est rempli automatiquement par
`knights_from_save()`.

`st.py passifs [chevalier]` affiche la table complète et qui porte quoi. Les modificateurs
de **dégâts** (`DAMAGE_MODS`) et de **récompenses** (`REWARD_MODS`) y figurent aussi, mais
ne sont pas encore injectés dans le calcul — ils sont documentaires.

Non modélisés faute de donnée : `LASTING_IMPRESSION` (lieux où Gideon a déjà réussi) et
`BRIZH_CONNOISSEUR` (pas de table lieu → comté dans le cache).
