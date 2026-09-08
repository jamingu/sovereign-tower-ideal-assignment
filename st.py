#!/usr/bin/env python
"""
Sovereign Tower data toolkit.
  python st.py build                 (re)construit le cache depuis le .pck  (~1 min)
  python st.py quest <motif>         fiche complete d'une quete (+ texte FR)
  python st.py knight [nom]          stats/tags des chevaliers (fiches de base)
  python st.py equip [motif]         equipements (stats, tags, prix)
  python st.py tr <CLE|motif>        cherche une cle ou un texte FR
  python st.py script <motif>        decompile un script .gd
  python st.py res <motif>           dump d'une ressource .tres/.res/.scn
  python st.py choices <knot_ink>                       libelles de choix -> quete / modificateur
  python st.py effects <knot_ink>                       effets d'une audience (recrutements, ralliements...)
  python st.py ink <motif> [n]                          cherche dans le scenario (audiences de suivi !)
  python st.py simple                *** issues inattendues du plateau : declencheur + recompense ***
  python st.py cycle [--meals]            # *** TABLEAU DU CYCLE : quetes, chevaliers, reliques, montees, repas ***
  python st.py loadout <quest_id> <chevalier...> [budget]  # equipement de TOUTE l'equipe
  python st.py gear <quest_id> <chevalier> [budget]      meilleur equipement achetable
  python st.py cover <quest_id> [quest_id ...] [budget]  repartition globale, objets partages
  python st.py plan [slot] [--quests=a,b] [--no-meals]   *** repartition optimale du cycle ***
  python st.py passifs [chevalier]   table des passifs (score/degats/recompenses)
  python st.py meal [chevalier]      plats aimes (repas du banquet)
  python st.py save [slot]           etat de la sauvegarde
  python st.py score <quest_id> [chevalier ...]   simule le score (sinon: toutes les equipes)
"""
import sys, os, json, glob, re, itertools, time
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stpck
from stpck import CACHE
from gdres import Res
import gdd

SAVEDIR = os.path.expandvars(r"%APPDATA%\Godot\app_userdata\Sovereign Tower (VS)\saved_games")


def j(n):
    return os.path.join(CACHE, n)


# ---------------------------------------------------------------- build
def build():
    os.makedirs(CACHE, exist_ok=True)
    print("res", stpck.extract(lambda p: p.endswith('.res') and p.startswith('.godot/exported'),
                               os.path.join(CACHE, 'res'), flat=True))
    print("scn", stpck.extract(lambda p: p.endswith('.scn'), os.path.join(CACHE, 'scn'), flat=True))
    print("gdc", stpck.extract(lambda p: p.endswith('.gdc'), os.path.join(CACHE, 'gd')))
    print("lang", stpck.extract(lambda p: p.startswith('lang/'), CACHE))
    enums = build_enums()
    json.dump(enums, open(j('enums.json'), 'w'), ensure_ascii=False)
    json.dump(build_efficiency(), open(j('efficiency.json'), 'w'), ensure_ascii=False)
    q = build_quests(enums)
    json.dump(q, open(j('quests.json'), 'w'), ensure_ascii=False, indent=1)
    k = build_knights(enums)
    json.dump(k, open(j('knights.json'), 'w'), ensure_ascii=False, indent=1)
    e = build_equipment(enums)
    json.dump(e, open(j('equipment.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(build_random_knights(), open(j('random_knights.json'), 'w'), ensure_ascii=False)
    json.dump(build_level_thresholds(), open(j('level_thresholds.json'), 'w'), ensure_ascii=False)
    print("cache pret:", CACHE, "| quetes", len(q), "| chevaliers", len(k), "| equipements", len(e))


BUILD_FORMAT = 1

# Fichiers indispensables pour que le solveur reponde.
CACHE_FILES = ['enums.json', 'efficiency.json', 'quests.json', 'knights.json',
               'equipment.json', 'random_knights.json', 'level_thresholds.json',
               'meals.json', 'outcomes.json', 'shops.json']

# Sous-dossiers entierement produits par l'extraction : on peut les vider sans
# risque avant une reconstruction (les .res de sauvegarde restent a la racine).
CACHE_DIRS = ['res', 'scn', 'gd', 'lang', 'ink']


def _pck_stamp():
    s = os.stat(stpck.PCK)
    return {'format': BUILD_FORMAT, 'size': s.st_size, 'mtime': int(s.st_mtime)}


def cache_state():
    """nogame | missing | stale | ok. Le mod interroge cet etat au demarrage."""
    if not stpck.PCK or not os.path.exists(stpck.PCK):
        return 'nogame'
    if any(not os.path.exists(j(n)) for n in CACHE_FILES):
        return 'missing'
    try:
        info = json.load(open(j('build_info.json'), encoding='utf-8'))
    except Exception:
        return 'missing'
    return 'ok' if info == _pck_stamp() else 'stale'


def setup(force=False):
    """Reconstruit tout le cache depuis le .pck du joueur.

    build() ne suffit pas : les issues, les boutiques, les repas et le scenario ink
    ont chacun leur propre cache et ne se regenerent pas apres une mise a jour du
    jeu. C'est la seule commande que le joueur ait a connaitre.
    """
    if not stpck.PCK or not os.path.exists(stpck.PCK):
        print('ERREUR: sovereign_tower.pck introuvable.')
        print('Definir ST_GAME, ou placer l outil dans le dossier du jeu.')
        return 2
    if cache_state() == 'ok' and not force:
        print('cache a jour :', CACHE)
        return 0
    import shutil
    print('jeu   :', stpck.GAME)
    print('cache :', CACHE)
    os.makedirs(CACHE, exist_ok=True)
    for d in CACHE_DIRS:
        shutil.rmtree(os.path.join(CACHE, d), ignore_errors=True)
    print('[1/5] extraction du .pck')
    build()
    print('[2/5] scenario')
    _ink_file()
    print('[3/5] issues de quete')
    import plan as _plan
    _plan.build_outcomes(force=True)
    print('[4/5] boutiques')
    _plan.build_shops(force=True)
    print('[5/5] repas')
    import meals as _meals
    _meals.build()
    json.dump(_pck_stamp(), open(j('build_info.json'), 'w', encoding='utf-8'))
    print('cache pret.')
    return 0


def gdsrc(pat):
    for f in glob.glob(os.path.join(CACHE, 'gd', '**', '*.gdc'), recursive=True):
        if pat in f.replace('\\', '/'):
            return gdd.decompile(f), f
    return None, None


def build_enums():
    out = {}
    files = ['autoloads/tag_manager', 'resources/characters/knight', 'resources/other/location',
             'resources/quests/quest', 'resources/quests/quest_reward', 'autoloads/satisfaction_manager',
             'resources/equipment/meal', 'resources/quests/quest_extra_condition']
    for fp in files:
        src, _ = gdsrc(fp + '.gdc')
        if not src:
            continue
        cur = None
        auto = 0
        for line in src.splitlines():
            parts = line.split(None, 1)
            t = parts[1].strip() if len(parts) > 1 else ''
            m = re.match(r'enum (\w+) \{', t)
            if m:
                cur = m.group(1)
                out[cur] = {}
                auto = 0
                continue
            if cur is None:
                continue
            if t.startswith('}'):
                cur = None
                continue
            m = re.match(r'([A-Z_0-9]+)(?: = (-?\d+))?,?$', t)
            if m:
                v = int(m.group(2)) if m.group(2) is not None else auto
                out[cur][str(v)] = m.group(1)
                auto = v + 1
    return out


def build_efficiency():
    from scnparse import parse_scene
    f = glob.glob(os.path.join(CACHE, 'scn', '*tag_library.scn'))[0]
    _, _, _, nodes = parse_scene(f)
    quest_tags, cond_tags = {}, {}
    parent_names = {n['i']: n['name'] for n in nodes}
    for n in nodes:
        p = n['props']
        if 'efficient_character_tags' not in p and 'inefficient_character_tags' not in p:
            continue
        d = {'eff': p.get('efficient_character_tags') or [],
             'ineff': p.get('inefficient_character_tags') or []}
        if parent_names.get(n['parent']) == 'QuestTags':
            quest_tags[str(p.get('quest_tag', 0))] = d
        else:
            cond_tags[str(p.get('condition_tag', 0))] = d
    return {'quest': quest_tags, 'condition': cond_tags}


def _script_of(pr):
    sc = pr.get('script')
    return sc['@ext'][1] if isinstance(sc, dict) and '@ext' in sc else ''


def build_quests(enums):
    out = {}
    for f in glob.glob(os.path.join(CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        for p, (t, pr) in r.resources.items():
            if not p.startswith('res://content/quests/'):
                continue
            # Les quetes scriptees derivent quest.gd sans porter son chemin :
            # `quest_search_for_the_traitor` utilise quest_traitor_plot.gd et etait
            # donc absente du cache -- puis ecartee EN SILENCE du planificateur.
            if not _script_of(pr).endswith(('quests/quest.gd', 'quests/quest_traitor_plot.gd',
                                            'quests/quest_ultimatum.gd')):
                continue

            def sub(ref):
                return r.resources[ref['@sub']][1] if isinstance(ref, dict) and '@sub' in ref else ref

            dmg = sub(pr.get('quest_damages')) or {}
            # BUG DE DONNEES DU JEU : `quest_victoria_trial_1_discreet_assassination.tres`
            # porte `quest_id = quest_victoria_trial_1_simulate_assassination` -- un
            # copier-coller des developpeurs. Deux fichiers, un seul id : le second
            # ecrasait le premier et la quete DISPARAISSAIT du cache. La sauvegarde,
            # elle, reference les quetes par NOM DE FICHIER, donc `st.py cycle` la
            # declarait introuvable et la sortait du plan. On retombe sur le nom de
            # fichier des que l'id est deja pris par un autre fichier.
            _key = pr.get('quest_id') or p
            _file = p.split('/')[-1].replace('.tres', '')
            # Un id revendique par un AUTRE fichier revient de droit a celui dont c'est
            # le nom : l'usurpateur est reindexe sous son propre nom de fichier, qui est
            # de toute facon la clef que porte la sauvegarde.
            _data = {
                'path': p,
                'name_key': pr.get('quest_name'), 'desc_key': pr.get('quest_description'),
                'type': enums['QuestTypes'].get(str(pr.get('quest_type', 0))),
                'category': enums['QuestTags'].get(str(pr.get('quest_category', 0))),
                'location': enums['LocationsID'].get(str(pr.get('quest_location', 0))),
                'conditions': [enums['ConditionTags'].get(str(c)) for c in (pr.get('quest_conditions') or [])],
                'stats': {enums['Statistics'][k]: v for k, v in (pr.get('stats_requirements') or {}).items()},
                'nb_knights': pr.get('nb_requested_knights', 1),
                # chevaliers IMPOSES sur la quete : quests_manager fait
                # `assigned_knights = requested_knights.duplicate()`. Ils occupent
                # deja leur place -- il ne reste que nb_knights - len(locked) slots.
                'locked_knights': [x['@ext'][1].split('/')[-1].replace('.tres', '')
                                   for x in (pr.get('requested_knights') or [])
                                   if isinstance(x, dict) and '@ext' in x],
                'duration': pr.get('duration', 1),
                'damages': [dmg.get('min', 0), dmg.get('max', 10)],
                'lethal': pr.get('quest_can_be_lethal', True),
                'involve_killing': pr.get('involve_killing', False),
                'deadline': pr.get('has_deadline', False),
                'deadline_cycles': pr.get('cycles_before_automatic_faillure'),
                'success': [_rew(sub(x), enums) for x in (pr.get('success_rewards') or [])],
                'failure': [_rew(sub(x), enums) for x in (pr.get('faillure_consequences') or [])],
                'success_follow_up': _aud(pr.get('success_follow_up_audience')),
                'failure_follow_up': _aud(pr.get('failure_follow_up_audience')),
                'special_outcomes': len(pr.get('special_outcomes') or []),
                'extra_conditions': len(pr.get('extra_conditions') or []),
            }
            # Le NOM DE FICHIER est toujours indexe : il est unique, et c'est la clef
            # que porte la sauvegarde. L'id ne l'est qu'en plus -- s'il est usurpe, son
            # proprietaire legitime l'ecrasera plus loin dans la boucle, ce qui est
            # exactement le comportement voulu.
            out[_file] = _data
            if _key != _file:
                out[_key] = _data
    return out


def _aud(x):
    if isinstance(x, dict) and '@ext' in x:
        return x['@ext'][1].split('/')[-1].replace('.tres', '')
    return None


def _rew(pr, enums):
    if not isinstance(pr, dict):
        return str(pr)
    d = {'type': enums['RewardType'].get(str(pr.get('reward_type', 0)))}
    for k in ('amount', 'affected_category', 'location', 'character_tag', 'variable_name'):
        if k in pr:
            d[k] = pr[k]
    if 'affected_category' in d:
        d['affected_category'] = enums['PopulationCategory'].get(str(d['affected_category']))
    if 'location' in d:
        d['location'] = enums['LocationsID'].get(str(d['location']))
    if 'character_tag' in d:
        d['character_tag'] = enums['CharacterTags'].get(str(d['character_tag']))
    return d


def build_knights(enums):
    CT = enums['CharacterTags']
    S = enums['Statistics']
    out = {}
    for f in glob.glob(os.path.join(CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        for p, (t, pr) in r.resources.items():
            path = _script_of(pr)
            if 'statistics_value' not in pr or '/characters/' not in path:
                continue
            nm = p.split('/')[-1].replace('.tres', '')

            def feats(key):
                o = []
                for it in pr.get(key) or []:
                    if isinstance(it, dict) and '@sub' in it:
                        p2 = r.resources[it['@sub']][1]
                        if p2.get('character_tag') is not None and p2.get('type') in (None, 0):
                            o.append(CT.get(str(p2['character_tag'])))
                return o

            out[nm] = {'stats': {S[k]: v for k, v in pr['statistics_value'].items()},
                       'armor': pr.get('max_armor') or 5,
                       'mastered': [S[str(x)] for x in (pr.get('mastered_stats') or [])],
                       'known': feats('known_features'), 'unknown': feats('unknown_features'),
                       'script': path.split('/')[-1]}
    return out


def build_level_thresholds():
    from scnparse import parse_scene
    f = glob.glob(os.path.join(CACHE, 'scn', '*level_up_manager*'))
    if not f:
        f = glob.glob(os.path.join(CACHE, 'scn', '*game_state*'))
    _, _, _, nodes = parse_scene(f[0])
    for nd in nodes:
        if 'level_xp_threshold' in nd['props']:
            return {str(k): v for k, v in nd['props']['level_xp_threshold'].items()}
    return {}


def build_random_knights():
    """Chevaliers dont get_statistic_value_from_id tire un nombre au hasard."""
    out = {}
    for f in glob.glob(os.path.join(CACHE, 'gd', 'systems', 'resources', 'characters', '*.gdc')):
        try:
            src = gdd.decompile(f)
        except Exception:
            continue
        if 'func get_statistic_value_from_id' not in src or 'randi_range' not in src:
            continue
        import re
        m = re.search(r'randi_range \((\d+), (\d+)\)', src)
        if m:
            out[os.path.basename(f).replace('.gdc', '')] = [int(m.group(1)), int(m.group(2))]
    return out


def build_equipment(enums):
    CT = enums['CharacterTags']
    S = enums['Statistics']
    out = {}
    for f in glob.glob(os.path.join(CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        for p, (t, pr) in r.resources.items():
            path = _script_of(pr)
            if '/equipment' not in path and not path.endswith(('relic.gd', 'mount.gd', 'consumable.gd', 'meal.gd')):
                continue
            # Certains objets n'ont AUCUN bonus de stat et n'ont donc pas de
            # 'statistics_value' : Guignole et Sivko-Burko, deux montures vendues
            # aux ecuries en acte 2. L'ancien 'continue' les jetait, build_shops
            # les ecrivait en item:null, et le planificateur ne les a jamais vues.
            if 'cost' not in pr and 'statistics_value' not in pr:
                continue
            # Le 'kind' vient du CHEMIN DE LA RESSOURCE, pas du script : Guignole a
            # son propre guignole.gd et se retrouvait avec kind='guignole'.
            kind = path.split('/')[-1].replace('.gd', '')
            for seg, k in (('/mounts/', 'mount'), ('/relics/', 'relic'),
                           ('/consumables/', 'consumable'), ('/meals/', 'meal')):
                if seg in p:
                    kind = k
                    break
            nm = pr.get('name') or p.split('/')[-1].replace('.tres', '')
            out[nm] = {'stats': {S[k]: v for k, v in (pr.get('statistics_value') or {}).items() if v},
                       'tags': [CT.get(str(x)) for x in (pr.get('tags') or [])],
                       'armor': pr.get('bonus_armor') or 0,
                       'duration_reduction': pr.get('duration_reduction') or 0,
                       'cost': pr.get('cost'), 'kind': kind,
                       # Objet SOUDE a son porteur : l'epee d'Edith, le griffon
                       # d'Ari. On ne peut ni le retirer, ni poser autre chose du
                       # meme type par-dessus. Sans ce drapeau le planificateur
                       # conseillait d'acheter une fronde pour Edith.
                       'exclusive': bool(pr.get('is_exclusive')),
                       'path': p}
    return out


# ---------------------------------------------------------------- traductions
_TR = {}


def _load_tr(lang):
    if lang in _TR:
        return _TR[lang]
    import trans
    d = {}
    for f in glob.glob(os.path.join(CACHE, 'lang', 'en_fr', '*.%s.translation' % lang)):
        try:
            d[os.path.basename(f)] = trans.T(f)
        except Exception:
            pass
    _TR[lang] = d
    return d


def tr(key, lang='fr'):
    if not key:
        return None
    for t in _load_tr(lang).values():
        v = t.get(key)
        if v:
            return v.rstrip('\x00')
    return None


def tr_search(pat, lang='fr'):
    hits = []
    for name, t in _load_tr(lang).items():
        for h, v in t.all():
            if pat.lower() in v.lower():
                hits.append((name, v.rstrip('\x00')))
    return hits


# ---------------------------------------------------------------- sauvegarde
def load_save(slot=1):
    import unrscc
    src = os.path.join(SAVEDIR, 'save_slot_%d.res' % slot)
    d = open(src, 'rb').read()
    if d[:4] == b'RSCC':
        tmp = j('_save%d' % slot)
        unrscc.decompress(src, tmp + '.raw')
        open(tmp + '.res', 'wb').write(b'RSRC' + open(tmp + '.raw', 'rb').read())
        r = Res(tmp + '.res', off_base=4)
    else:
        r = Res(src)
    root = [v for k, v in r.resources.items() if 'save_entries' in v[1]][0][1]
    entry = r.resources[list(root['save_entries'].values())[0]['@sub']][1]
    return r, entry


def _nm(k):
    m = re.search(r'/([a-z_0-9]+)\.tres', str(k))
    return m.group(1) if m else str(k)[:40]


def st_busy(vv):
    """Chevalier deja engage sur une quete en cours (ou a l'entrainement)."""
    return bool(vv.get('assigned_quest')) or bool(vv.get('is_training'))


def busy_knights(e):
    """{nom du chevalier -> raison} pour ceux qui ne sont PAS disponibles ce cycle.
    (st_busy() ne juge qu'un chevalier a la fois ; cette fonction balaye la save.)"""
    out = {}
    for kk, vv in (e.get('roundtable_knights') or {}).items():
        if not isinstance(vv, dict):
            continue
        if vv.get('is_dead'):
            out[_nm(kk)] = 'mort'
        elif vv.get('assigned_quest'):
            q = vv['assigned_quest']
            nm = _nm(q['@ext'][1]) if isinstance(q, dict) and '@ext' in q else 'en quete'
            out[_nm(kk)] = nm
        elif vv.get('is_training'):
            out[_nm(kk)] = 'entrainement'
    return out


def knights_from_save(slot=1):
    enums = json.load(open(j('enums.json'), encoding='utf-8'))
    base = json.load(open(j('knights.json'), encoding='utf-8'))
    equip = json.load(open(j('equipment.json'), encoding='utf-8'))
    eqbypath = {v['path']: (k, v) for k, v in equip.items()}
    r, e = load_save(slot)
    CT = enums['CharacterTags']
    S = enums['Statistics']
    out = {}
    for kk, vv in e['roundtable_knights'].items():
        nm = _nm(kk)
        stats = dict(base.get(nm, {}).get('stats', {}))
        for i, v in (vv.get('bonus_stats') or {}).items():
            stats[S[i]] = stats.get(S[i], 0) + v
        for i, v in (vv.get('trained_stats') or {}).items():
            stats[S[i]] = stats.get(S[i], 0) + v
        eqs = []
        for x in vv.get('equipment') or []:
            if isinstance(x, dict) and '@ext' in x:
                n2, d2 = eqbypath.get(x['@ext'][1], (x['@ext'][1].split('/')[-1], {'stats': {}}))
                eqs.append(n2)
                for st, v in (d2.get('stats') or {}).items():
                    stats[st] = stats.get(st, 0) + v
        tags = []
        for key in ('known_features', 'unknown_features'):
            for it in vv.get(key) or []:
                if isinstance(it, dict) and '@sub' in it:
                    p2 = r.resources[it['@sub']][1]
                    if p2.get('character_tag') is not None and p2.get('type') in (None, 0):
                        tags.append(CT.get(str(p2['character_tag'])))
        out[nm] = {'name': nm, 'stats': stats, 'tags': tags, 'equip': eqs,
                   'armor': vv.get('current_armor', 5),
                   'max_armor': (base.get(nm, {}).get('armor') or 5) + (vv.get('bonus_armor') or 0),
                   'level': vv.get('current_level'),
                   'busy': st_busy(vv), 'dead': bool(vv.get('is_dead')),
                   'affinity': vv.get('current_affinity'), 'has_eaten': vv.get('has_eaten'),
                   'kills': float(vv.get('bonus_for_kills') or 0)}
    set_context(e)
    return out, r, e


def save_report(slot=1):
    quests = json.load(open(j('quests.json'), encoding='utf-8'))
    ks, r, e = knights_from_save(slot)
    print('=== %s | cycle %s | moment %s | or %s | corruption %s' % (
        e.get('title'), e.get('cycle_index'), e.get('current_daytime'),
        e.get('current_funds'), e.get('corruption_level')))
    print('satisfaction', e.get('current_satisfaction'))
    # Tags de souverain. Les audiences les testent via RequiresTag(<Tag>, <niveau>).
    # Je les avais cherches dans les variables ink -- ils n'y sont pas -- et j'en avais
    # conclu a tort que l'option « deguisement de crabe » (Audacieux 3) etait fermee.
    # Elle etait ouverte : Audacieux 112 de progression. Ils vivent ici, on les affiche.
    _tg = e.get('current_sovereign_tags_progression') or {}
    if _tg:
        # NE PAS nommer ce dict `_nm` : ca masque la fonction _nm() du module et
        # save_report plantait juste apres (TypeError: 'dict' object is not callable).
        _tagfr = {'0': 'TYRANNIQUE', '1': 'SAGE', '2': 'BIENVEILLANT',
                  '3': 'AUDACIEUX', '4': 'OMNISCIENT'}
        try:
            _th = json.load(open(j('level_thresholds.json'), encoding='utf-8'))
        except Exception:
            _th = {}
        def _lv(p):
            return max((int(k) for k, v in _th.items() if p >= v), default=0)
        print('tags souverain ' + ' | '.join(
            '%s %d (niv %d)' % (_tagfr.get(k, k), v, _lv(v)) for k, v in sorted(_tg.items())))
    print('\n--- table ronde ---')
    for nm, k in ks.items():
        print('%-11s niv%-3s armure %-3s %s' % (nm, k['level'], k['armor'],
              ' '.join('%s=%s' % (sfr(a), b) for a, b in k['stats'].items())))
        print('%-11s equip=%s tags=%s affinite=%s repas=%s' % ('', k['equip'] or '-', k['tags'],
              k['affinity'], k['has_eaten']))
    print('\n--- quetes en cours ---')
    for kk, vv in (e.get('current_quests') or {}).items():
        qid = _nm(kk)
        q = quests.get(qid, {})
        print('%-38s %-30s delai=%s chevaliers=%s' % (
            qid, tr(q.get('name_key')) or '', vv.get('remaining_cycles_before_faillure'),
            [_nm(x) for x in (vv.get('assigned_knights') or [])]))
    print('\nrecrutables :', [_nm(k) for k in (e.get('recruitable_knights') or {})])
    print('relics libres :', [_nm(x) for x in (e.get('unequipped_relics') or [])],
          '| montures :', [_nm(x) for x in (e.get('unequipped_mounts') or [])])


# ---------------------------------------------------------------- score
def _people(q):
    for rw in q.get('success', []):
        if rw.get('type') == 'SATISFACTION' and rw.get('affected_category') == 'PEOPLE':
            return rw.get('amount', 0)
    return 0


COMBAT = ('HUNT', 'CONFRONTATION', 'DUEL', 'ASSASSINATION')

# Contexte de partie lu dans la sauvegarde. special_cases.gd interroge
# GameState (satisfaction courante, quetes deja terminees) ; sans ca une
# dizaine de passifs etaient silencieusement comptes zero.
CTX = {'sat': {}, 'all_time': set()}


def set_context(e):
    """Alimente CTX depuis l'entree de sauvegarde. Appele par
    knights_from_save() : rien a faire cote appelant."""
    CTX['sat'] = dict(e.get('current_satisfaction') or {})
    CTX['all_time'] = set(e.get('all_time_completed_quests') or [])


def _sat(cat):
    return float(CTX['sat'].get(cat, 0))


def _rw_amount(q, cat):
    """special_cases.gd::_get_reward_amount_for -- SOMME (et non premiere
    occurrence) des recompenses de satisfaction de cette categorie."""
    n = 0
    for rw in q.get('success') or []:
        if rw.get('type') == 'SATISFACTION' and rw.get('affected_category') == cat:
            n += rw.get('amount', 0) or 0
    return n


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _replayed(q):
    """Quete deja terminee dans une run precedente. all_time_completed_quests
    SURVIT au NG+ et aux retours du demon : en seconde partie, l'Oeil de demon
    et la Decoction demoniaque se declenchent quasiment partout."""
    return q.get('_id') in CTX['all_time']


# Passifs agissant sur le SCORE (check_for_special_cases_for_score).
# Chaque entree rend le score du cas, ou None si le cas ne se declenche pas
# (nuance : dans le jeu un cas absent du dictionnaire n'ecrase pas la valeur
# d'efficacite, alors qu'un cas present a 0 ou negatif la laisse vivre a cote).
SPECIAL = {
    # --- passifs innes de chevalier ---------------------------------------
    'KIND_HEARTED':       lambda q, k: _clamp(_rw_amount(q, 'PEOPLE'), -1, 1) or None,
    'NOBLE_SOUL':         lambda q, k: _clamp(_rw_amount(q, 'NOBLES'), -1, 1) or None,
    'TRUE_NOBLE_SOUL':    lambda q, k: _clamp(sum(_rw_amount(q, c) for c in
                                              ('PEOPLE', 'NOBLES', 'MERCHANTS', 'SCHOLARS')),
                                              -1, 1) or None,
    'PATIENT':            lambda q, k: 1 if q['_dur'] > 1 else None,
    'OVERWORKED':         lambda q, k: -(q['_dur'] - 1) * 0.5 if q['_dur'] > 1 else None,
    'SPEEDSTER':          lambda q, k: q['_red'] * 0.5 if q['_red'] > 0 else None,
    'TIMID':              lambda q, k: -1 if k['party'] == 1 else None,
    'LONER':              lambda q, k: 1 if k['party'] == 1 else None,
    'TRUE_DRAGON_KNIGHT': lambda q, k: 1.0 if q['category'] in COMBAT else 0.5,
    'BRUTAL':             lambda q, k: 1.5 if q['category'] in COMBAT else -1.5,
    'SADDISTIC':          lambda q, k: 1 if q.get('involve_killing') else None,
    'SERRATED_BLADE':     lambda q, k: 1 if q.get('involve_killing') else None,
    'MUTE':               lambda q, k: -3 if q['category'] == 'DIPLOMACY' else None,
    'TANK':               lambda q, k: k['armor'] * 0.08,
    'RESOURCEFULL':       lambda q, k: k['stats'].get('WITS', 0) * 0.08,
    'GAMBLER':            lambda q, k: k['stats'].get('LUCK', 0) * 0.1,
    'PROBLEM_SOLVER':     lambda q, k: k['stats'].get('STRENGTH', 0) * 0.1,
    'COASTAL':            lambda q, k: 1.0 if q.get('coastal') else None,
    'REVOLUTIONAR':       lambda q, k: _clamp((_sat('people') - _sat('nobles')) * 0.2, -2.0, 2.0),
    'NOBILITY_PRIMES':    lambda q, k: _clamp((_sat('nobles') - _sat('people')) * 0.2, -2.0, 2.0),
    'BELIEVER':           lambda q, k: (_sat('scholars') * 0.025
                                        if _sat('scholars') >= 10 else None),
    'LOYAL':              lambda q, k: (_clamp(k['_team_aff'] * 0.25, -2.0, 2.0)
                                        if k['party'] > 1 else None),
    'CHEESE_LOVER':       lambda q, k: 1 if 'CHEESY' in k['_all_tags'] else None,
    'TIME_PERCEPTION':    lambda q, k: 1 if _replayed(q) else None,
    'SYPHON':             lambda q, k: k['_kills'] if k['_kills'] > 0 else None,
    # --- passifs portes par l'equipement ----------------------------------
    'DEADLY_WEAPON':      lambda q, k: 100 if q['category'] == 'ASSASSINATION' else None,
    'WISH_GRANTING_LAMP': lambda q, k: 100,
    'DEMON_DECOCTION':    lambda q, k: 100 if _replayed(q) else None,
    'AMBER_EYE':          lambda q, k: 8 if _replayed(q) else None,
    'GRANNYS_HERBAL_TEA': lambda q, k: _clamp(_rw_amount(q, 'PEOPLE'), -1, 1) or None,
    'FINE_WINE':          lambda q, k: _clamp(_rw_amount(q, 'NOBLES'), -1, 1) or None,
}
# Volontairement absents, la donnee n'etant ni dans la sauvegarde ni au cache :
#   LASTING_IMPRESSION (Gideon : +1 sur un lieu ou il a deja reussi)
#   BRIZH_CONNOISSEUR  (Alwena : +1 en Brizh -- pas de table lieu -> comte)
# PROTAGONIST (Gideon) depend des equipiers : traite a part dans score().

# Modificateurs de DEGATS -- check_for_special_cases_for_damage.
DAMAGE_MODS = {
    'PERFECT_ARMOR':    "annule les degats des equipiers mais encaisse +75 % par equipier",
    'BODYGUARD':        "-1 degat pour chaque equipier",
    'SHORT_TARGET':     "-1 degat pour lui-meme",
    'INTANGIBLE':       "-la moitie des degats de base (arrondi sup.) pour lui-meme",
    'EXTREMELY_CLUMSY': "+2 degats si la quete echoue",
    'FIRE_LADY':        "+1 degat si la quete a la condition EAU",
    'CONDUCTOR':        "+1 degat si la quete a la condition EAU",
    'POTION_OF_FIRE_BREATHING': "+2 degats (objet)",
}

# Modificateurs de RECOMPENSES -- check_for_special_cases_for_rewards.
REWARD_MODS = {
    'IN_DEBT':          "50 % de chance de -15 % sur l'or",
    'OFFICE_WORKER':    "or x1.1 (1 cycle), x1.3 (2), x1.6 (3), x2.0 (4)",
    'PACK_OF_SERPENT_OIL_VIALS': "meme multiplicateur d'or qu'OFFICE_WORKER (objet, 35 or)",
    'POACHER':          "25 % de chance de +1 satisfaction MARCHANDS (si succes)",
    'NOBLES_DEFENDER':  "25 % de chance de +1 satisfaction NOBLES (si succes)",
    'SCHOLAR':          "25 % de chance de +1 satisfaction ERUDITS (si succes)",
    'IDOL':             "25 % de chance de +1 satisfaction PEUPLE (si succes)",
}

COASTAL_LOCATIONS = {'CLOVERMONT', 'GREST', 'PINCE_HARBOR', 'SHELLINGTON', 'SOUTHBAY_GATE',
                     'MERMAID_ISLAND', 'DJINN_ISLAND', 'ISLE_OF_BASALT', 'TORTOSA_ISLES',
                     'POPOTA_ISLES', 'TREASURE_ISLAND', 'ROZENN', 'NAONED', 'AVALON'}



def prep_quest(q, qid, knights=()):
    """Complete la quete avec son identifiant, la reduction de duree
    REELLEMENT tenue par l'equipe (le minimum : une monture sur un seul
    equipier ne sert a rien) et la duree qui en resulte."""
    equip = _load('equipment')
    q['_id'] = qid
    base_dur = max(1, q.get('duration', 1) or 1)
    red = None
    for k0 in knights:
        rk = 0
        for it in (k0.get('equip') or []):
            rk += (equip.get(it) or {}).get('duration_reduction', 0) or 0
        red = rk if red is None else min(red, rk)
    red = max(0, red or 0)
    q['_red'] = min(red, base_dur - 1)
    q['_dur'] = max(1, base_dur - red)
    return q


def prep_knight(k0, size, knights=()):
    """Ajoute au chevalier ce dont les passifs ont besoin : effectif, tags
    d'equipement fusionnes (get_all_characteristics en fait autant), affinite
    moyenne des equipiers, compteur de meurtres d'Edith."""
    equip = _load('equipment')
    k = dict(k0)
    k['party'] = size
    k['_kills'] = float(k0.get('kills') or 0)
    tags = list(k.get('tags') or [])
    for it in (k.get('equip') or []):
        for tg in ((equip.get(it) or {}).get('tags') or []):
            if tg not in tags:
                tags.append(tg)
    k['_all_tags'] = tags
    # special_cases.gd divise par l'effectif TOTAL, pas par le nombre d'autres.
    k['_team_aff'] = (sum(float(o.get('affinity') or 0) for o in knights
                          if o is not k0) / size) if (knights and size) else 0.0
    return k


def tag_score(q, k, E, I):
    """[(tag, valeur)] -- efficacite et cas speciaux combines.
    quest.gd ecrit les deux dans le MEME dictionnaire indexe par tag : un cas
    special positif ECRASE donc le +1/-1 d'efficacite, tandis qu'un cas
    negatif ou nul part dans un autre dictionnaire et s'y ADDITIONNE."""
    out = []
    for tag in k['_all_tags']:
        base = (1 if tag in E else 0) + (-1 if tag in I else 0)
        fn = SPECIAL.get(tag)
        sp = fn(q, k) if fn else None
        if sp is None:
            v = base
        elif sp > 0:
            v = sp
        else:
            v = base + sp
        if v:
            out.append((tag, round(v, 2)))
    return out


def snap(x):
    return round(x / 0.01) * 0.01


_JC = {}


def _load(name):
    if name not in _JC:
        _JC[name] = json.load(open(j(name + '.json'), encoding='utf-8'))
    return _JC[name]


def outcome_for_score(total, has_extra=False):
    """quest.gd get_outcome_for_score. ATTENTION : sur une quete a conditions
    supplementaires, TOUT score <= 0 est un ECHEC CRITIQUE (degats 100 =
    morts), il n'y a pas d'echec doux."""
    if total >= 10:
        return 'REUSSITE CRITIQUE'
    if total > 5:
        return 'GRANDE REUSSITE'
    if total <= -10:
        return 'ECHEC CRITIQUE'
    if total > 0:
        return 'REUSSITE'
    if has_extra:
        return 'ECHEC CRITIQUE'
    return 'ECHEC MAJEUR' if total <= -5 else 'ECHEC'


def score(qid, knights, meals=0, verbose=True, quest=None):
    """meals = NOMBRE de chevaliers nourris dans cette equipe (le jeu n'en autorise qu'un par cycle, tous quetes confondues)."""
    quests = _load('quests')
    enums = _load('enums')
    eff = _load('efficiency')
    CT = enums['CharacterTags']
    q = dict(quest or quests[qid])
    q['coastal'] = q['location'] in COASTAL_LOCATIONS
    E, I = set(), set()
    for table, val in (('quest', q['category']), ):
        cid = [k for k, v in enums['QuestTags'].items() if v == val]
        if cid and cid[0] in eff['quest']:
            E |= {CT[str(x)] for x in eff['quest'][cid[0]]['eff']}
            I |= {CT[str(x)] for x in eff['quest'][cid[0]]['ineff']}
    for c in q['conditions']:
        cid = [k for k, v in enums['ConditionTags'].items() if v == c]
        if cid and cid[0] in eff['condition']:
            E |= {CT[str(x)] for x in eff['condition'][cid[0]]['eff']}
            I |= {CT[str(x)] for x in eff['condition'][cid[0]]['ineff']}
    nb = q['nb_knights']
    size = len(knights)
    missing = nb - size
    total = -10.0
    lines = []
    nmeal = int(meals)          # nombre de repas REELLEMENT distribuables (1 par cycle)
    equip = _load('equipment')
    prep_quest(q, qid, knights)
    for i_k, k0 in enumerate(knights):
        k = prep_knight(k0, size, knights)
        t = snap(10.0 / nb)
        det = [('presence', t)]
        if i_k < nmeal:
            t += 0.5
            det.append(('repas', 0.5))
        for stat, req in q['stats'].items():
            val = max(0, min(15, k['stats'].get(stat, 0)))
            s = (val - (req + missing)) * 0.66
            if abs(s) < 1e-9:
                s = 0.22
            s = s / size
            s *= (1 + req * 0.0275) if s > 0 else (1 + req * 0.01)
            s = snap(s)
            t += s
            det.append((stat, round(s, 2)))
        for tag, v in tag_score(q, k, E, I):
            t += v
            det.append((tag, v))
        total += t
        lines.append((k.get('name', '?'), round(t, 2), det))
    # PROTAGONIST (Gideon) : +1 s'il n'est pas le meilleur score de l'equipe.
    if len(lines) > 1:
        best = max(t for _, t, _ in lines)
        for idx, k0 in enumerate(knights):
            tags = list(k0.get('tags') or [])
            for it in (k0.get('equip') or []):
                tags += ((equip.get(it) or {}).get('tags') or [])
            if 'PROTAGONIST' in tags and lines[idx][1] < best:
                n, t, det = lines[idx]
                det.append(('PROTAGONIST', 1.0))
                lines[idx] = (n, round(t + 1.0, 2), det)
                total += 1.0
    # quest.gd : chaque condition supplementaire REMPLIE vaut +2 (extra_conditions_score).
    for key, bonus in (q.get('extra_met') or {}).items():
        total += bonus
        lines.append(('[%s]' % key, bonus, [('condition remplie', bonus)]))
    total = round(total, 2)
    has_extra = bool(q.get('extra_conditions'))
    oc = outcome_for_score(total, has_extra)
    # quest.gd:268-272 -- un chevalier FORTUNATE donne 50 % de chance de passer
    # au palier superieur si score+1 suffirait a l'atteindre.
    if any('FORTUNATE' in (k.get('tags') or []) for k in knights):
        up = outcome_for_score(round(total + 1.0, 2), has_extra)
        if up != oc:
            oc = '%s (FORTUNE : 50%% -> %s)' % (oc, up)
    if verbose:
        print('%s -> %.2f = %s   (seuils: >0 reussite, >5 grande, >=10 critique)' % (qid, total, oc))
        for n, t, det in lines:
            print('   %-12s %6.2f  %s' % (n, t, det))
    return total, oc



# ---------------------------------------------------------------- scenario ink
def _ink_file():
    import glob
    import stpck, unrscc
    d = os.path.join(CACHE, 'ink')
    os.makedirs(d, exist_ok=True)
    bins = glob.glob(os.path.join(d, '*.bin'))
    if not bins:
        stpck.extract(lambda p: 'master.ink.json-' in p, d, flat=True)
        for f in glob.glob(os.path.join(d, '*.res')):
            try:
                unrscc.decompress(f, f + '.bin')
            except Exception:
                pass
        bins = glob.glob(os.path.join(d, '*.bin'))
    for f in bins:                       # on garde la version FR
        if 'Tr\u00e8s bien'.encode('utf-8') in open(f, 'rb').read()[:0] or True:
            pass
    best, score = None, -1
    for f in bins:
        raw = open(f, 'rb').read()
        n = raw.count('é'.encode('utf-8')) + raw.count('è'.encode('utf-8'))
        if n > score:
            best, score = f, n
    return best


def ink_search(pattern, before=300, after=300, limit=8):
    import re
    s = _ink_text()
    hits = []
    for m in re.finditer(re.escape(pattern), s):
        hits.append(s[max(0, m.start() - before):m.start() + after])
        if len(hits) >= limit:
            break
    return hits, s.count(pattern)


# Abreviations telles qu'elles apparaissent sur la fiche de chevalier EN JEU.
# (Ne pas inventer de traduction : WITS s'affiche INT, LUCK s'affiche FRT.)
STAT_FR = {'STRENGTH': 'FOR', 'AGILITY': 'AGI', 'CHARISMA': 'CHA',
           'MAGIC': 'MAG', 'WITS': 'INT', 'LUCK': 'FRT'}


def sfr(stat):
    """Nom interne d'une stat -> abreviation affichee dans le jeu."""
    return STAT_FR.get(stat, stat[:3])


def stats_fr(d):
    """Dictionnaire de stats -> texte lisible dans le vocabulaire du jeu."""
    order = ['STRENGTH', 'AGILITY', 'CHARISMA', 'MAGIC', 'WITS', 'LUCK']
    keys = [k for k in order if k in d] + [k for k in d if k not in order]
    return ' '.join('%s %s' % (sfr(k), d[k]) for k in keys)


INTERESTING = {'KnightRecruitment': 'RECRUTE', 'KnightDemission': 'DEMISSION',
               'CountyRallied': 'COMTE RALLIE', 'UnlockQuest': 'debloque quete',
               'UnlockAudienceRequest': 'debloque requete', 'AddDoleanceForNextCycle': 'ajoute doleance',
               'UpdateSovereignValue': 'tag souverain', 'SpecialInstruction': 'instruction',
               'UnlockEquipment': 'debloque equipement', 'UnlockFillerAudiencesPack': 'debloque doleances',
               'UpdateSatisfaction': 'satisfaction', 'RevealLocation': 'revele lieu'}


def _knot_slice(s, name):
    key = '"%s":' % name
    i = s.find(key)
    if i < 0:
        return None
    j = i + len(key)
    while j < len(s) and s[j] not in '[{':
        j += 1
    if j >= len(s):
        return None
    op = s[j]
    cl = ']' if op == '[' else '}'
    depth = 0
    k = j
    instr = esc = False
    while k < len(s):
        c = s[k]
        if instr:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == op:
                depth += 1
            elif c == cl:
                depth -= 1
                if depth == 0:
                    return s[j:k + 1]
        k += 1
    return None


_INK_TEXT = []


def _ink_text():
    if not _INK_TEXT:
        _INK_TEXT.append(open(_ink_file(), 'rb').read().decode('utf-8', 'replace'))
    return _INK_TEXT[0]


def ink_effects(knot, depth=2, _seen=None):
    """Effets scenaristiques atteignables depuis un knot (diverts suivis)."""
    import re
    if _seen is None:
        _seen = set()
    if knot in _seen or depth < 0:
        return []
    _seen.add(knot)
    seg = _knot_slice(_ink_text(), knot)
    if seg is None:
        return []
    out = []
    for m in re.finditer(r'\{"f\(\)":"(\w+)"\}', seg):
        fn = m.group(1)
        if fn not in INTERESTING:
            continue
        args = re.findall(r'\{"VAR\?":"([\w]+)"\}', seg[max(0, m.start() - 220):m.start()])
        out.append((INTERESTING[fn], ' '.join(args[-2:])))
    for m in re.finditer(r'\{"->(?:t->)?":"([a-z][a-z0-9_]{4,60})"\}', seg):
        out += ink_effects(m.group(1), depth - 1, _seen)
    seen, uniq = set(), []
    for e in out:
        if e in seen:
            continue
        seen.add(e)
        uniq.append(e)
    return uniq


def branch_unlocks(knot):
    """Quetes debloquees par UN knot de resolution precis.
    Indispensable : une issue INATTENDUE peut etre la SEULE branche qui
    debloque la suite d'une quete. Choisir la reussite ordinaire fait alors
    disparaitre definitivement une mission (cas verifie : l'oie de Bourg-en-Lait,
    dont la partie 2 n'existe que via l'issue inattendue d'Angelique)."""
    import re
    seg = _knot_slice(_ink_text(), knot)
    if seg is None:
        return None
    out = []
    for m in re.finditer(r'\{"VAR\?":"(\w+)"\},\s*\d+,\{"f\(\)":"UnlockQuest"\}', seg):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def scan_effect(fn_label='RECRUTE'):
    """Balaye TOUT le scenario et rend chaque endroit qui declenche un effet donne
    (recrutement, ralliement de comte...). Sert a planifier une partie : savoir
    OU et QUAND on peut recruter est la contrainte n1 (il faut 9 chevaliers au
    cycle 8 pour enchainer les urgences et un ultimatum)."""
    import re
    txt = _ink_text()
    fns = [f for f, lbl in INTERESTING.items() if lbl == fn_label] or [fn_label]
    # index des knots : nom -> position d'ouverture
    knots = [(m.start(), m.group(1)) for m in re.finditer(r'"([a-z][a-z0-9_]{3,60})":\s*[\[{]', txt)]
    knots.sort()
    out = []
    for fn in fns:
        for m in re.finditer(r'\{"f\(\)":"%s"\}' % re.escape(fn), txt):
            args = re.findall(r'\{"VAR\?":"(\w+)"\}', txt[max(0, m.start() - 260):m.start()])
            lo, hi = 0, len(knots)
            while lo < hi:                                  # dernier knot ouvert avant l'appel
                mid = (lo + hi) // 2
                if knots[mid][0] < m.start():
                    lo = mid + 1
                else:
                    hi = mid
            knot = knots[lo - 1][1] if lo else '?'
            out.append((args[-1] if args else '?', knot))
    seen, uniq = set(), []
    for a, k in out:
        if (a, k) in seen:
            continue
        seen.add((a, k))
        uniq.append((a, k))
    return uniq


def _bracket_slice(s, start):
    """Tranche equilibree a partir du crochet ouvrant en `start`."""
    op = s[start]
    cl = ']' if op == '[' else '}'
    depth = 0
    instr = esc = False
    for k in range(start, len(s)):
        c = s[k]
        if instr:
            if esc:
                esc = False
            elif c == chr(92):
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == op:
                depth += 1
            elif c == cl:
                depth -= 1
                if depth == 0:
                    return s[start:k + 1]
    return s[start:]


def ink_choices(knot):
    """Libelles de choix d'une audience -> quete debloquee et modificateur applique."""
    import re
    seg = _knot_slice(_ink_text(), knot)
    if seg is None:
        return []
    labels = []
    for m in re.finditer(r'\^([^"]{8,200})","/str","/ev",\{"\*":"\.\^\.(c-\d+)"', seg):
        labels.append((m.group(2), m.group(1)))
    blocks = {}
    for m in re.finditer(r'"(c-\d+)":\[', seg):
        blk = _bracket_slice(seg, m.end() - 1)      # bornage exact du bloc, pas une fenetre fixe
        eff = []
        for u in re.finditer(r'\{"VAR\?":"(quest_[a-z0-9_]+|[a-z0-9_]*candidacy)"\}(.{0,40}?)\{"f\(\)":"(UnlockQuest|AddDoleanceForNextCycle)"\}', blk):
            arg = u.group(2)
            mod = re.search(r',(\d+),', arg)
            eff.append('%s%s' % (u.group(1), ' [modificateur %s]' % mod.group(1) if mod else ' [sans modificateur]'))
        for u in re.finditer(r'\{"VAR=":"([a-z0-9_]+)","re":true\}', blk):
            eff.append('var %s' % u.group(1))
        for u in re.finditer(r'\{"VAR\?":"(\w+)"\},(-?\d+),\{"f\(\)":"UpdateSovereignValue"\}', blk):
            eff.append('tag %s %+d' % (u.group(1), int(u.group(2))))
        blocks.setdefault(m.group(1), []).append(eff)
    out = []
    seen = {}
    for c, lab in labels:
        idx = seen.get(c, 0)
        seen[c] = idx + 1
        eff = blocks.get(c, [])
        out.append((lab, eff[idx] if idx < len(eff) else (eff[0] if eff else [])))
    return out

# ---------------------------------------------------------------- CLI

def cmd_passifs(argv):
    """Table des passifs : effet sur le score, les degats, les recompenses,
    et qui les porte (chevaliers de la table ronde + objets)."""
    equip = _load('equipment')
    base = json.load(open(j('knights.json'), encoding='utf-8'))
    # tag -> porteurs
    par_tag = {}
    for n, v in base.items():
        for t in (v.get('known') or []) + (v.get('unknown') or []):
            par_tag.setdefault(t, []).append(n.upper())
    obj_tag = {}
    for n, v in equip.items():
        for t in (v.get('tags') or []):
            obj_tag.setdefault(t, []).append(n)

    filt = (argv[0].lower() if argv else None)
    if filt:
        ks, _r, _e = knights_from_save()
        k = ks.get(filt)
        if k is None:
            print('chevalier inconnu dans la sauvegarde : %s' % filt)
            print('table ronde : %s' % ', '.join(sorted(ks)))
            return
        tags = list(k.get('tags') or [])
        for it in (k.get('equip') or []):
            tags += ((equip.get(it) or {}).get('tags') or [])
        print('=== %s ===' % filt.upper())
        print('equipement : %s' % (', '.join(k['equip']) or 'rien'))
        vus = [t for t in tags if t in SPECIAL or t in DAMAGE_MODS or t in REWARD_MODS]
        if not vus:
            print('aucun passif a effet chiffre.')
        for t in vus:
            if t in SPECIAL:
                print('  %-22s score      -- %s' % (t, _DOC.get(t, '')))
            if t in DAMAGE_MODS:
                print('  %-22s degats     -- %s' % (t, DAMAGE_MODS[t]))
            if t in REWARD_MODS:
                print('  %-22s recompense -- %s' % (t, REWARD_MODS[t]))
        return

    def porteurs(t):
        a = par_tag.get(t) or []
        b = obj_tag.get(t) or []
        s = ', '.join(a)
        if b:
            s += (' | objets: ' if s else 'objets: ') + ', '.join(b)
        return s or '-'

    print('=== PASSIFS A EFFET SUR LE SCORE ===')
    for t in sorted(SPECIAL):
        print('  %-22s %s' % (t, _DOC.get(t, '')))
        print('  %-22s   porte par : %s' % ('', porteurs(t)))
    print()
    print('=== PASSIFS A EFFET SUR LES DEGATS ===')
    for t, d in sorted(DAMAGE_MODS.items()):
        print('  %-22s %s' % (t, d))
        print('  %-22s   porte par : %s' % ('', porteurs(t)))
    print()
    print('=== PASSIFS A EFFET SUR LES RECOMPENSES ===')
    for t, d in sorted(REWARD_MODS.items()):
        print('  %-22s %s' % (t, d))
        print('  %-22s   porte par : %s' % ('', porteurs(t)))
    print()
    print('non modelises : LASTING_IMPRESSION (lieux ou Gideon a deja reussi),')
    print('                BRIZH_CONNOISSEUR (pas de table lieu -> comte au cache)')


_DOC = {
    'KIND_HEARTED': '+1/-1 selon la satisfaction PEUPLE donnee par la quete',
    'NOBLE_SOUL': '+1/-1 selon la satisfaction NOBLES donnee par la quete',
    'TRUE_NOBLE_SOUL': '+1/-1 selon la SOMME des quatre satisfactions',
    'PATIENT': '+1 si la quete dure plus d un cycle (duree tenue, pas de base)',
    'OVERWORKED': '-0.5 par cycle au-dela du premier',
    'SPEEDSTER': '+0.5 par cycle de duree economise par les montures',
    'TIMID': '-1 en solo',
    'LONER': '+1 en solo',
    'TRUE_DRAGON_KNIGHT': '+1 en combat, +0.5 sinon',
    'BRUTAL': '+1.5 en combat, -1.5 sinon',
    'SADDISTIC': '+1 si la quete tue (involve_killing)',
    'SERRATED_BLADE': '+1 si la quete tue (involve_killing)',
    'MUTE': '-3 en DIPLOMATIE',
    'TANK': '+0.08 par point d armure',
    'RESOURCEFULL': '+0.08 par point d INTELLIGENCE',
    'GAMBLER': '+0.1 par point de FORTUNE',
    'PROBLEM_SOLVER': '+0.1 par point de FORCE',
    'COASTAL': '+1 sur un lieu cotier',
    'REVOLUTIONAR': '(peuple - nobles) x0.2, borne a +/-2',
    'NOBILITY_PRIMES': '(nobles - peuple) x0.2, borne a +/-2',
    'BELIEVER': 'erudits x0.025 si erudits >= 10',
    'LOYAL': 'affinite moyenne des equipiers x0.25, borne a +/-2',
    'CHEESE_LOVER': '+1 s il porte un objet FROMAGE (15 or suffit)',
    'TIME_PERCEPTION': '+1 si la quete a deja ete faite dans une run precedente',
    'SYPHON': '+1 par meurtre accumule par Edith',
    'DEADLY_WEAPON': '+100 sur une quete d ASSASSINAT',
    'WISH_GRANTING_LAMP': '+100, sans condition',
    'DEMON_DECOCTION': '+100 si la quete a deja ete faite dans une run precedente',
    'AMBER_EYE': '+8 si la quete a deja ete faite dans une run precedente',
    'GRANNYS_HERBAL_TEA': '+1/-1 selon la satisfaction PEUPLE donnee par la quete',
    'FINE_WINE': '+1/-1 selon la satisfaction NOBLES donnee par la quete',
}


def _rehabille(k0, items):
    """Rend le chevalier `k0` avec l'equipement `items` a la place du sien.

    knights_from_save() a DEJA fondu les stats de l'equipement de la sauvegarde dans
    k['stats']. Se contenter de remplacer k['equip'] ne changeait donc rien au score :
    les bonus de la save restaient, ceux du jeu n'entraient jamais. On defait d'abord,
    on refait ensuite.
    """
    eq = _load('equipment')
    stats = dict(k0.get('stats') or {})
    for it in (k0.get('equip') or []):
        for nom, v in ((eq.get(it) or {}).get('stats') or {}).items():
            stats[nom] = stats.get(nom, 0) - v
    for it in items:
        for nom, v in ((eq.get(it) or {}).get('stats') or {}).items():
            stats[nom] = stats.get(nom, 0) + v
    k = dict(k0)
    k['stats'] = stats
    k['equip'] = list(items)
    return k


def cmd_live(argv):
    """st.py live --in=<entree.json> --out=<sortie.json>

    Score des affectations TELLES QU'ELLES SONT dans le jeu, pour l'affichage en
    temps reel du mod.

    Pourquoi une commande a part : `st.py score` reconstruit les chevaliers depuis
    la SAUVEGARDE, donc avec l'equipement qu'ils portaient au dernier cycle. Pendant
    que le joueur manipule la table ronde, la save retarde -- une relique qu'il vient
    de poser n'y est pas. Le mod envoie donc l'etat reel et on ne garde de la save
    que ce qui ne bouge pas (stats, niveau, affinite, tags).

    Lecture seule : rien n'est ecrit dans la partie.

    Entree :
      {"slot": 1,
       "stats": {"angelica": {"STRENGTH": 3, "WITS": 5, ...}},   # hors equipement
       "quests": [{"quest_id": "...", "knights": ["angelica"],
                   "equip": {"angelica": ["CLOVER_SWORD"]},
                   "has_eaten": ["angelica"]}]}
    Sortie :
      {"scores": [{"quest_id": "...", "score": 3.94, "outcome": "REUSSITE"}]}
    """
    src = dst = None
    for a in argv:
        if a.startswith('--in='):
            src = a.split('=', 1)[1]
        elif a.startswith('--out='):
            dst = a.split('=', 1)[1]
    if not src:
        print(cmd_live.__doc__)
        return
    with open(src, encoding='utf-8') as f:
        req = json.load(f)

    ks, _r, _e = knights_from_save(int(req.get('slot') or 1))
    # Stats VIVANTES envoyees par le mod, hors equipement. La sauvegarde ignore une
    # montee de niveau faite dans le cycle en cours : le score restait fige apres un
    # « +1 INT ». Elles remplacent donc celles de la save quand elles sont la.
    live_stats = req.get('stats') or {}
    quests = _load('quests')
    out = []
    for entry in (req.get('quests') or []):
        qid = entry.get('quest_id')
        if qid not in quests:
            out.append({'quest_id': qid, 'error': 'quest not in cache'})
            continue
        equip = entry.get('equip') or {}
        manges = set(entry.get('has_eaten') or [])
        team, absents = [], []
        for n in (entry.get('knights') or []):
            k0 = ks.get(n)
            if k0 is None:
                absents.append(n)
                continue
            base = live_stats.get(n)
            if base:
                # Deja hors equipement : on repart d'elles, sans rien retirer.
                k0 = dict(k0)
                k0['stats'] = {str(a): int(b) for a, b in base.items()}
                k0['equip'] = []
            k = _rehabille(k0, equip.get(n) or [])
            k['has_eaten'] = n in manges
            team.append(k)
        if not team:
            out.append({'quest_id': qid, 'score': None,
                        'outcome': 'no knight assigned',
                        'unknown_knights': absents})
            continue
        total, oc = score(qid, team, meals=sum(1 for k in team if k['has_eaten']),
                          verbose=False)
        row = {'quest_id': qid, 'score': total, 'outcome': oc,
               'knights': [k['name'] for k in team]}
        if absents:
            row['unknown_knights'] = absents
        out.append(row)

    doc = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'scores': out}
    if dst:
        d = os.path.dirname(dst)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        # Ecriture ATOMIQUE : le mod n'attend plus la fin du processus, il attend
        # l'apparition du fichier. Sans le renommage il lirait un JSON tronque.
        tmp = dst + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    a = sys.argv[2:]
    if cmd == 'build':
        build()
    elif cmd == 'setup':
        sys.exit(setup('--force' in a or '-f' in a))
    elif cmd == 'cache':
        print(cache_state())
    elif cmd == 'quest':
        qs = json.load(open(j('quests.json'), encoding='utf-8'))
        for qid, q in qs.items():
            if a[0].lower() in qid.lower() or a[0].lower() in (q['name_key'] or '').lower():
                print('###', qid)
                print('   FR   :', tr(q['name_key']))
                print('   texte:', tr(q['desc_key']))
                for k, v in q.items():
                    if k not in ('name_key', 'desc_key'):
                        print('   %-18s %s' % (k, v))
    elif cmd == 'knight':
        ks = json.load(open(j('knights.json'), encoding='utf-8'))
        for n, v in sorted(ks.items()):
            if not a or a[0].lower() in n:
                print('%-12s %s' % (n, json.dumps(v, ensure_ascii=False)))
    elif cmd == 'equip':
        es = json.load(open(j('equipment.json'), encoding='utf-8'))
        for n, v in sorted(es.items()):
            if not a or a[0].lower() in n.lower() or a[0].upper() in str(v['tags']):
                print('%-26s %s' % (n, json.dumps(v, ensure_ascii=False)))
    elif cmd == 'tr':
        v = tr(a[0])
        if v:
            print(a[0], '=>', v)
        else:
            for f, t in tr_search(a[0])[:25]:
                print('%-28s %s' % (f, t[:200]))
    elif cmd == 'script':
        src, f = gdsrc(a[0])
        print('###', f)
        print(src)
    elif cmd == 'res':
        for f in glob.glob(os.path.join(CACHE, 'res', '*.res')) + glob.glob(os.path.join(CACHE, 'scn', '*.scn')):
            if a[0].lower() not in os.path.basename(f).lower():
                continue
            try:
                r = Res(f)
            except Exception as ex:
                print(f, 'ERR', ex)
                continue
            print('###', os.path.basename(f))
            for p, (t, pr) in r.resources.items():
                print(' --', p, t)
                print('   ', json.dumps(pr, ensure_ascii=False, default=str)[:2000])
    elif cmd == 'choices':
        for lab, eff in ink_choices(a[0]):
            print('  %-72s -> %s' % (lab[:72], ', '.join(eff) or '-'))
    elif cmd == 'effects':
        for f, arg in ink_effects(a[0]):
            print('  %-20s %s' % (f, arg))
    elif cmd in ('jour', 'day', 'best'):
        import advise as _adv
        _adv.cmd_day(*a)
    elif cmd == 'risk':
        import risk as _risk
        _risk.cmd_risk(a)
    elif cmd == 'snapshot':
        import plan as _plan
        _plan.cmd_snapshot(*a)
    elif cmd == 'scan':
        lbl = a[0] if a else 'RECRUTE'
        rows = scan_effect(lbl)
        print('%d endroit(s) declenchant %s' % (len(rows), lbl))
        for arg, knot in sorted(rows):
            print('  %-26s <- %s' % (arg, knot))
    elif cmd == 'ink':
        hits, n = ink_search(a[0], limit=int(a[1]) if len(a) > 1 else 6)
        print('%d occurrence(s) de %r' % (n, a[0]))
        for h in hits:
            print('-' * 70)
            print(h)
    elif cmd in ('simple', 'inattendu'):
        import plan as _plan
        _plan.cmd_simple(a)
    elif cmd in ('choix', 'audience'):
        import plan as _plan
        _plan.cmd_choix(a)
    elif cmd == 'hints':
        import plan as _plan
        _plan.cmd_hints(a)
    elif cmd == 'live':
        cmd_live(a)
    elif cmd == 'cycle':
        import plan as _plan
        _plan.cmd_cycle(a)
    elif cmd == 'loadout':
        import plan as _plan
        _plan.cmd_loadout(a)
    elif cmd == 'gear':
        import plan as _plan
        _plan.cmd_gear(a)
    elif cmd == 'cover':
        import plan as _plan
        _plan.cmd_cover(a)
    elif cmd == 'plan':
        import plan as _plan
        _plan.main(a)
    elif cmd == 'meal':
        import meals as _meals
        _meals.cmd(a)
    elif cmd in ('passifs', 'passif'):
        cmd_passifs(a)
    elif cmd == 'save':
        save_report(int(a[0]) if a else 1)
    elif cmd == 'score':
        qid = a[0]
        ks, _, _ = knights_from_save()
        if len(a) > 1:
            team = [ks[n] for n in a[1:]]
            score(qid, team)
            score(qid, team, meals=True)
        else:
            qs = json.load(open(j('quests.json'), encoding='utf-8'))
            nb = min(qs[qid]['nb_knights'], len(ks))
            res = [(score(qid, list(c), meals=True, verbose=False)[0], [x['name'] for x in c])
                   for c in itertools.combinations(ks.values(), nb)]
            for s, t in sorted(res, reverse=True):
                print('%6.2f  %s  (avec repas)' % (s, ' + '.join(t)))
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
