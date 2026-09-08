# -*- coding: utf-8 -*-
"""
`python st.py plan` : repond a "je suis au jour X, j'ai les quetes A/B/C, comment je fais au mieux".

Lit la sauvegarde, croise chaque chevalier avec chaque quete, detecte les issues
scenarisees (dont les mortelles), propose la meilleure repartition et les achats
qui font gagner un palier.
"""
import os, sys, json, glob, re, itertools, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import st
from gdres import Res

_C = {}


def C(name):
    if name not in _C:
        _C[name] = json.load(open(st.j(name + '.json'), encoding='utf-8'))
    return _C[name]


# ---------------------------------------------------------------- index des issues scenarisees
def build_outcomes(force=False):
    """quest_id -> [issue], et catalogue des issues (chevalier declencheur, degats, recompenses)."""
    path = st.j('outcomes.json')
    if os.path.exists(path) and not force:
        return json.load(open(path, encoding='utf-8'))
    enums = C('enums')
    CT = enums['CharacterTags']
    S = enums['Statistics']
    outcomes, by_quest = {}, {}

    def _mk(pr, r):
        dmg = pr.get('damage_range')
        if isinstance(dmg, dict) and '@sub' in dmg:
            d2 = r.resources[dmg['@sub']][1]
            dmg = [d2.get('min', 0), d2.get('max', 10)]
        else:
            dmg = None
        return {
            'knights': [x['@ext'][1].split('/')[-1].replace('.tres', '')
                        for x in (pr.get('knights') or []) if isinstance(x, dict)],
            'tags': [CT.get(str(x)) for x in (pr.get('required_knight_characteristics') or [])],
            'stat': S.get(str(pr.get('stat', 0))), 'amount': pr.get('amount', -1),
            'requires_higher': pr.get('requires_higher', True),
            'damage': dmg, 'xp_modifier': pr.get('xp_modifier', 1.5),
            'note': pr.get('arlin_note'),
            'traitor': pr.get('for_traitor_plot', False),
            'rewards': [st._rew(r.resources[x['@sub']][1], enums)
                        for x in (pr.get('rewards') or []) if isinstance(x, dict) and '@sub' in x],
            'follow_up': (pr.get('follow_up_audience') or {}).get('@ext', [None, None])[1]
            if isinstance(pr.get('follow_up_audience'), dict) else None,
        }

    for f in glob.glob(os.path.join(st.CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        for p, (t, pr) in r.resources.items():
            if p.startswith('res://content/unexpected_outcomes/'):
                outcomes[p] = _mk(pr, r)
            elif p.startswith('res://content/quests/') and 'quest_id' in pr:
                # Une issue inattendue peut etre EXTERNE (@ext vers unexpected_outcomes/)
                # ou INTEGREE a la quete (@sub). Ne garder que les externes rendait
                # invisibles des issues entieres : celle de la competition de musique se
                # declenche sur la seule presence de Gideon et donne 4 recompenses de plus.
                # Le joueur l'a decouverte en jouant, l'outil annoncait une reussite banale.
                so = []
                for x in (pr.get('special_outcomes') or []):
                    if not isinstance(x, dict):
                        continue
                    if '@ext' in x:
                        so.append(x['@ext'][1])
                    elif '@sub' in x and x['@sub'] in r.resources:
                        key = '%s#%s' % (pr['quest_id'], x['@sub'])
                        outcomes[key] = _mk(r.resources[x['@sub']][1], r)
                        so.append(key)
                if so:
                    by_quest[pr['quest_id']] = so
    data = {'outcomes': outcomes, 'by_quest': by_quest}
    json.dump(data, open(path, 'w'), ensure_ascii=False)
    return data


def triggered_outcome(qid, knights, oc):
    """Reproduit SpecialOutcome.are_conditions_met pour une equipe donnee."""
    names = {k['name'] for k in knights}
    tags = set()
    for k in knights:
        tags |= set(k.get('tags', []))
    for path in oc['by_quest'].get(qid, []):
        o = oc['outcomes'].get(path)
        if not o or o['traitor']:
            continue
        if o['knights']:
            if not set(o['knights']) <= names:
                continue
            if not o['tags']:
                return path, o
        if o['amount'] > 0:
            for k in knights:
                v = k['stats'].get(o['stat'], 0)
                if (o['requires_higher'] and v >= o['amount']) or (not o['requires_higher'] and v <= o['amount']):
                    return path, o
            continue
        if o['tags'] and set(o['tags']) <= tags:
            return path, o
    return None, None



def save_modifier_outcomes(r, e, oc):
    """Le modificateur tire pour une quete en cours ajoute ses propres issues inattendues."""
    extra = {}
    for k, v in (e.get('current_quests') or {}).items():
        qid = st._nm(k)
        mod = v.get('selected_modifier')
        if not isinstance(mod, dict) or '@sub' not in mod:
            continue
        pr = r.resources.get(mod['@sub'], (None, {}))[1]
        paths = [x['@ext'][1] for x in (pr.get('unexpected_outcomes') or [])
                 if isinstance(x, dict) and '@ext' in x]
        if paths:
            extra[qid] = paths
    for qid, paths in extra.items():
        oc['by_quest'][qid] = list(oc['by_quest'].get(qid, [])) + [p for p in paths if p in oc['outcomes']]
    return oc


GOLD_FLOOR = [0]   # or a NE PAS depenser (condition MIN_FUNDS d'un ultimatum, etc.)
PEND = {}          # chevalier -> montees de niveau en attente
FORCED = set()     # quetes imposees a la main (--force=) : jamais laissees de cote
LAST_CHANCE = {}   # quete -> True si elle disparait du tableau apres ce cycle
MODS = {}          # qid -> quete corrigee par le modificateur tire dans la sauvegarde
_MODSIG = {}       # cache de la signature de MODS[qid] -- A PURGER a chaque ecriture
_MISSING = object()
MOD_NOTES = {}     # qid -> description du modificateur


def apply_save_modifiers(r, e):
    """Le modificateur tire pour une quete change ses exigences, degats, duree, effectifs."""
    S = C('enums')['Statistics']
    quests = C('quests')
    MODS.clear()
    _MODSIG.clear()
    MOD_NOTES.clear()
    for k, v in (e.get('current_quests') or {}).items():
        qid = st._nm(k)
        m = v.get('selected_modifier')
        if qid not in quests or not isinstance(m, dict) or '@sub' not in m:
            continue
        pr = r.resources.get(m['@sub'], (None, {}))[1]
        q = json.loads(json.dumps(quests[qid]))
        notes = []
        for i, d in (pr.get('stats_requirements_modification') or {}).items():
            if not d:
                continue
            stat = S[i]
            q['stats'][stat] = max(0, q['stats'].get(stat, 0) + d)
            notes.append('%s %+d' % (stat, d))
        dmod = pr.get('damage_modification') or 0
        if dmod:
            q['damages'] = [max(0, (q['damages'][0] or 0) + dmod), max(0, (q['damages'][1] or 0) + dmod)]
            notes.append('degats %+d' % dmod)
        nmod = pr.get('nb_requested_knights_modification') or 0
        if nmod:
            q['nb_knights'] = max(1, q['nb_knights'] + nmod)
            notes.append('effectif %+d' % nmod)
        # Le modificateur ajoute aussi des RECOMPENSES (success_rewards_modification).
        # Les oublier faisait rater un +1 erudit qui debloquait une condition d'ultimatum.
        extra_rw = []
        for x in (pr.get('success_rewards_modification') or []):
            if isinstance(x, dict) and '@sub' in x:
                extra_rw.append(st._rew(r.resources[x['@sub']][1], C('enums')))
        if extra_rw:
            q['success'] = list(q['success']) + extra_rw
            notes.append('recompenses +%s' % len(extra_rw))
        dur = pr.get('duration_modification') or 0
        if dur:
            q['duration'] = max(1, q['duration'] + dur)
            notes.append('duree %+d' % dur)
        if notes:
            MODS[qid] = q
            _MODSIG.pop(qid, None)
            MOD_NOTES[qid] = ', '.join(notes)
    return MODS

# ---------------------------------------------------------------- evaluation
SHORT = {'REUSSITE CRITIQUE': 'critique', 'GRANDE REUSSITE': 'GRANDE', 'REUSSITE': 'reussite',
         'ECHEC': 'echec', 'ECHEC MAJEUR': 'echec majeur', 'ECHEC CRITIQUE': 'ECHEC CRITIQUE'}
RANK = {'REUSSITE CRITIQUE': 4, 'GRANDE REUSSITE': 3, 'REUSSITE': 2,
        'ECHEC': -2, 'ECHEC MAJEUR': -3, 'ECHEC CRITIQUE': -5}


_EVAL_MEMO = {}


def evaluate(qid, team, oc, meals=True):
    """-> dict {score, outcome, rank, special, deadly}"""
    # La clef doit inclure le MODIFICATEUR actif : deux versions d'une meme quete
    # partagent le qid et l'equipe, et la seconde recuperait le resultat en cache de
    # la premiere. Toute comparaison entre versions renvoyait donc le meme score.
    # La signature du modificateur ne depend QUE du qid : la recalculer a chaque appel
    # coutait trois sorted() sur 322 000 appels. On la memorise par quete.
    _sig = _MODSIG.get(qid, _MISSING)
    if _sig is _MISSING:
        _qm = MODS.get(qid)
        _sig = None if _qm is None else (tuple(sorted((_qm.get('stats') or {}).items())),
                                         _qm.get('nb_knights'), _qm.get('duration'),
                                         tuple(_qm.get('damages') or ()))
        _MODSIG[qid] = _sig
    # Le tuple des noms seuls etait redondant : la signature complete ci-dessous
    # commence deja par le nom de chaque chevalier.
    key = (qid, _sig, meals,
           tuple(sorted((k.get('name', '?'), tuple(sorted(k['stats'].items())), tuple(sorted(k.get('tags', []))))
                        for k in team)))
    hit = _EVAL_MEMO.get(key)
    if hit is not None:
        return hit
    r = _evaluate_raw(qid, team, oc, meals)
    _EVAL_MEMO[key] = r
    return r


def _evaluate_raw(qid, team, oc, meals=True):
    path, o = triggered_outcome(qid, team, oc)
    if o:
        deadly = bool(o['damage'] and o['damage'][0] >= 50)
        return {'score': None, 'outcome': 'ISSUE INATTENDUE', 'special': (path, o),
                'deadly': deadly, 'rank': -9 if deadly else 3.5}
    s, out = st.score(qid, team, meals=meals, verbose=False, quest=MODS.get(qid))
    return {'score': s, 'outcome': out, 'special': None, 'deadly': False, 'rank': RANK.get(out, 0)}


def gap_to_next(score):
    for thr, label in ((0, 'reussite'), (5, 'grande reussite'), (10, 'reussite critique')):
        if score < thr or (thr < 10 and score <= thr):
            return round(thr - score + (0.01 if thr < 10 else 0), 2), label
    return None, None


# ---------------------------------------------------------------- stock reel des boutiques
THRESHOLDS = {'0': [10, 22, 40], '1': [10, 22, 42], '2': [10, 22, 42], '3': [10, 22, 42], '4': [1]}


def _respath(x):
    """Les cles de Dictionary sont stringifiees par le parseur : on recupere le res:// dedans."""
    if isinstance(x, dict) and '@ext' in x:
        return x['@ext'][1]
    m = re.search(r"res://[^'\"]+\.tres", str(x))
    return m.group(0) if m else None


def build_shops(force=False):
    """Stock des boutiques (forge / tour de la sorciere / ecuries / cuisine) par acte."""
    path = st.j('shops.json')
    if os.path.exists(path) and not force:
        return json.load(open(path, encoding='utf-8'))
    from scnparse import parse_scene
    enums = C('enums')
    eq = C('equipment')
    bypath = {v['path']: k for k, v in eq.items()}
    r, _, _, nodes = parse_scene(glob.glob(os.path.join(st.CACHE, 'scn', '*game_state*'))[0])
    out = {}
    for nd in nodes:
        for k, v in nd['props'].items():
            if k == 'available_meals':
                out.setdefault('kitchen', [])
                for x in v or []:
                    out['kitchen'].append({'item': bypath.get(_respath(x)), 'act': 1, 'req': None})
                continue
            m = None
            for shop, key in (('forge', 'forge_relics'),
                              ('witch_tower', 'witch_tower_consumables'),
                              ('stables', 'stables_mounts')):
                if k == key:
                    m = (shop, 1)
                elif k == key + '_act_2':
                    m = (shop, 2)
                elif k == key + '_act_3':
                    m = (shop, 3)
            if not m or not isinstance(v, dict):
                continue
            shop, act = m
            out.setdefault(shop, [])
            for a, b in v.items():
                req = None
                pay = 0
                if isinstance(b, dict) and '@sub' in b:
                    pr = r.resources[b['@sub']][1]
                    # COUT EN NATURE. EquipmentRequirement porte un champ `item`
                    # (Relic.RelicsID) INDEPENDANT de `type` : quand il est renseigne,
                    # shop_slot.update_cost() masque le prix en or et exige cet objet a
                    # la place. Deux entrees de la tour de la sorciere l'utilisent.
                    pay = pr.get('item') or 0
                    t = pr.get('type', 0)
                    if t == 1:
                        req = {'kind': 'county', 'id': pr.get('county_id')}
                    elif t == 2:
                        req = {'kind': 'satisfaction',
                               'cat': enums['PopulationCategory'].get(str(pr.get('population_category', 0))),
                               'amount': pr.get('amount', 0)}
                    elif t == 3:
                        req = {'kind': 'sovereign', 'tag': str(pr.get('sovereign_tag', 0)),
                               'name': enums['SovereignTags'].get(str(pr.get('sovereign_tag', 0))),
                               'amount': pr.get('amount', 0)}
                out[shop].append({'item': bypath.get(_respath(a)), 'act': act,
                                  'req': req, 'pay_item': pay})
    json.dump(out, open(path, 'w'), ensure_ascii=False)
    return out


# Objets de quete servant de MONNAIE dans une boutique (Relic.RelicsID). Il n'y en a
# que deux, tous deux a la tour de la sorciere acte 3.
PAY_ITEMS = {23: 'demon_heart', 24: 'dragon_heart'}

# Consommables/reliques impossibles a racheter : une fois depenses, ils sont perdus.
# La Potion de souffle enflamme y figure parce qu'elle se paie avec le Coeur de dragon
# -- un objet de quete unique. Un ancien commentaire accusait le cache d'etre faux : il
# ne l'etait pas, c'est le parseur qui ignorait le champ `item` du prerequis.
UNIQUE_ITEMS = {'POTION_OF_FIRE_BREATHING'}

# INTERDITS PAR LE USER. Plus fort que UNIQUE_ITEMS : ceux-la ressortent quand meme
# s'ils ouvrent une issue inattendue (etape 3a), ceux-ci ne ressortent JAMAIS. On les
# retire des le recensement, pour qu'aucune branche du planificateur ne les voie --
# ni les conseils d'achat, ni les tenues, ni le JSON envoye au mod.
# Lampe a souhait : posee au cycle 20.
BANNED_ITEMS = {'WISH_GRANTING_LAMP'}


def available_items(e, r=None):
    """Objets reellement accessibles dans l'etat de la save -> {nom: (cout, source)}."""
    eq = C('equipment')
    shops = build_shops()
    rooms = set(st._nm(x) for x in (e.get('unlocked_rooms') or []))
    act = (e.get('current_act') or 0) + 1
    sat = e.get('current_satisfaction') or {}
    counties = set(st._nm(x) for x in (e.get('rallied_counties') or []))
    prog = e.get('current_sovereign_tags_progression') or {}
    bypath = {v['path']: k for k, v in eq.items()}
    r_sub = {}
    if r is not None:
        for sub, (_t, pr) in r.resources.items():
            if isinstance(pr, dict) and pr.get('name'):
                r_sub[sub] = pr
    out = {}
    # Un objet PARTI EN QUETE avec son porteur est indisponible tout court : la boutique
    # ne revend pas ce qu'on possede deja. Sans ce verrou, l'objet retombait plus bas
    # dans la boucle des boutiques et etait propose A L'ACHAT (le filet de Goberto,
    # cycle 10) -- l'outil faisait acheter un objet que le jeu ne vend pas.
    locked = set()
    for kk, vv in (e.get('roundtable_knights') or {}).items():      # porte par un camarade -> transfert gratuit
        who = st._nm(kk)
        busy = vv.get('assigned_quest') or vv.get('is_training') or vv.get('is_dead')
        for x in vv.get('equipment') or []:
            n = bypath.get(_respath(x))
            if not n:
                continue
            if busy:
                locked.add(n)                                       # part avec lui, et reste possede
            else:
                out[n] = (0, 'porte par %s' % who)
    for key in ('unequipped_relics', 'unequipped_mounts', 'unequipped_consumables'):
        for x in e.get(key) or []:
            n = bypath.get(_respath(x))
            if n is None and isinstance(x, dict) and '@sub' in x:
                # consommable stocke en ligne dans la save (objet de quete, cost None)
                pr = r_sub.get(x['@sub']) if r_sub else None
                if pr:
                    n = bypath.get(pr.get('path')) or (pr.get('name') or '').upper() or None
            if n:
                out[n] = (0, 'possede')
    for shop, items in shops.items():
        if shop not in rooms:
            continue
        for it in items:
            n = it['item']
            if not n or n not in eq or it['act'] > act:
                continue
            # PAYE EN NATURE, pas en or. shop_slot.update_cost() masque le prix
            # quand le prerequis porte un `item` : la boutique reclame alors cet objet
            # de quete. Le planificateur ne connait que des prix en or -- il proposait
            # donc d'acheter la Decoction demoniaque « 150 or » alors qu'elle coute un
            # Coeur de demon que le joueur n'a pas (cycle 20). Ces objets sortent du
            # recensement : meme quand on possede la monnaie, la depenser est
            # irreversible et releve d'un arbitrage du joueur, pas de l'optimiseur.
            if it.get('pay_item'):
                continue
            req = it['req']
            ok = True
            if req:
                if req['kind'] == 'county':
                    ok = req['id'] in counties
                elif req['kind'] == 'satisfaction':
                    ok = sat.get((req['cat'] or '').lower(), 0) >= req['amount']
                elif req['kind'] == 'sovereign':
                    th = THRESHOLDS.get(req['tag'], [10, 22, 42])
                    lvl = req['amount']
                    ok = prog.get(req['tag'], 0) >= (th[lvl - 1] if 0 < lvl <= len(th) else 10 ** 9)
            if ok and n not in out and n not in locked:
                out[n] = (eq[n].get('cost') or 0, shop)
    for b in BANNED_ITEMS:
        out.pop(b, None)
    return out



def random_knights():
    try:
        return C('random_knights')
    except Exception:
        return {}


_ISRAND = {}


def is_random(name):
    # Appelee des millions de fois par l'optimiseur pour un test qui ne change
    # jamais au cours d'un plan.
    r = _ISRAND.get(name)
    if r is None:
        r = name in random_knights()
        _ISRAND[name] = r
    return r


def mc_evaluate(qid, team, oc, meals=True, n=3000):
    """Distribution des resultats quand un chevalier a des stats aleatoires."""
    import random as _r
    rk = random_knights()
    lo_hi = {k['name']: rk[k['name']] for k in team if k['name'] in rk}
    if not lo_hi:
        return None
    q = MODS.get(qid) or C('quests')[qid]
    stats = list(q['stats'])
    counts, total = {}, 0.0
    for _ in range(n):
        t2 = []
        for k in team:
            if k['name'] in lo_hi:
                lo, hi = lo_hi[k['name']]
                k2 = dict(k)
                k2['stats'] = dict(k['stats'])
                for s_ in stats:                       # l'equipement s'ajoute au tirage
                    k2['stats'][s_] = max(0, min(15, _r.randint(lo, hi) + k['stats'].get(s_, 0)))
                t2.append(k2)
            else:
                t2.append(k)
        r = evaluate(qid, t2, oc, meals)
        counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
        total += r['score'] if r['score'] is not None else 0.0
    return {'mean': total / n, 'probs': {k: v * 100.0 / n for k, v in sorted(counts.items(), key=lambda x: -x[1])}}



def pending_levels(e):
    """{chevalier: nombre de montees de niveau non depensees}"""
    th = C('level_thresholds')
    out = {}
    for kk, vv in (e.get('roundtable_knights') or {}).items():
        lvl = vv.get('current_level', 1)
        xp = vv.get('current_xp', 0)
        n = 0
        while str(lvl + 1) in th and th[str(lvl + 1)] <= xp and lvl < 15:
            lvl += 1
            n += 1
        if n:
            out[st._nm(kk)] = n
    return out


def levelup_gain(qid, team, oc, meals, who, n_levels):
    """Meilleur gain de score si `who` depense ses montees de niveau."""
    import itertools
    q = MODS.get(qid) or C('quests')[qid]
    stats = list(q['stats']) or ['STRENGTH']
    base = evaluate(qid, team, oc, meals)
    if base['score'] is None:
        return None
    best = None
    for combo in itertools.combinations_with_replacement(stats, n_levels):
        t2 = []
        for k in team:
            if k['name'] != who:
                t2.append(k)
                continue
            k2 = dict(k)
            k2['stats'] = dict(k['stats'])
            for s_ in combo:
                k2['stats'][s_] = k2['stats'].get(s_, 0) + 1
            t2.append(k2)
        r = evaluate(qid, t2, oc, meals)
        if r['score'] is not None and (best is None or r['score'] > best[0]):
            best = (r['score'], combo, r['outcome'])
    if best and round(best[0], 2) > round(base['score'], 2):
        return {'score': round(best[0], 2), 'stats': list(best[1]), 'outcome': best[2]}
    return None


# ---------------------------------------------------------------- suggestions d'equipement
SLOT = {'mount': 'monture', 'consumable': 'consommable'}



def _slot_of(v):
    return SLOT.get(v['kind'], 'relique')


def _slot_verrouille(k, slot):
    """Le chevalier porte-t-il un objet SOUDE sur cet emplacement ?

    L'epee d'Edith (Dainsleif) et le griffon d'Ari sont `is_exclusive` : le jeu
    refuse de les retirer et refuse d'en poser un autre du meme type par-dessus.
    Sans ce test le planificateur conseillait d'acheter une fronde pour Edith --
    un achat que le joueur ne peut tout simplement pas faire.
    """
    eq = C('equipment')
    for it in (k.get('equip') or []):
        v = eq.get(it) or {}
        if v.get('exclusive') and _slot_of(v) == slot:
            return True
    return False


def _slot_occupe(k, portes, slot):
    """L'emplacement est-il indisponible -- deja garni OU SOUDE ?

    Les etapes tardives de l'optimiseur testaient seulement `portes` (l'etat qu'elles
    manipulent). Or l'equipement soude n'y figure jamais : `_bare` le laisse sur le
    chevalier, il ne passe pas par l'etat. Elles voyaient donc un emplacement vide chez
    Edith (epee Dainsleif) et Ari (griffon), et le remplissaient. Cycle 25 : Edith
    repartait avec une Epee de duelliste qu'elle ne peut pas porter.
    """
    eq = C('equipment')
    if _slot_verrouille(k, slot):
        return True
    return any(_slot_of(eq[x]) == slot for x in (portes or []))


def _strip_slot(k, slot):
    """Retire l'objet deja porte sur ce slot (stats + tags) et renvoie (chevalier, nom_retire)."""
    eq = C('equipment')
    k2 = dict(k)
    k2['stats'] = dict(k['stats'])
    k2['tags'] = list(k['tags'])
    removed = None
    for name in k.get('equip') or []:
        v = eq.get(name)
        if not v or _slot_of(v) != slot:
            continue
        removed = name
        for s_, d in v['stats'].items():
            k2['stats'][s_] = k2['stats'].get(s_, 0) - d
        for t in v['tags']:
            if t in k2['tags']:
                k2['tags'].remove(t)
    return k2, removed


def _with_item(k, name, v):
    k2, removed = _strip_slot(k, _slot_of(v))
    for s_, d in v['stats'].items():
        k2['stats'][s_] = k2['stats'].get(s_, 0) + d
    k2['tags'] = k2['tags'] + [t for t in v['tags'] if t]
    return k2, removed


def best_team_loadout(qid, team, oc, gold, meals=0, avail=None, top=6, mc_n=6000):
    """Meilleur equipement pour TOUTE l'equipe a la fois.

    Deux erreurs reelles motivent cette fonction :
      1. comparer des objets sur des bases differentes (un objet deja porte
         laisse en place dans un cas et pas dans l'autre) -> conclusion inversee ;
         ici chaque candidat passe par _with_item, qui retire toujours l'objet
         du meme slot avant d'ajouter le nouveau.
      2. n'equiper qu'UN chevalier d'une equipe : donner une relique au second
         rapporte souvent autant que l'achat le plus cher pour le premier.

    Un objet possede ne peut servir qu'a un seul chevalier a la fois.
    """
    import itertools
    eq = C('equipment')
    if avail is None:
        avail = {}
    # Les objets UNIQUES (regle du user : « ne la repropose JAMAIS sauf issue inedite »)
    # etaient filtres dans optimise_cycle mais PAS ici : `loadout` ressortait la Potion
    # de souffle enflamme a chaque appel.
    pool = [(n, avail[n][0]) for n in avail if n in eq and n not in UNIQUE_ITEMS]
    pool.sort(key=lambda x: x[1])
    names = [k.get('name', '?') for k in team]
    rnd = any(is_random(n) for n in names)

    def val(kl):
        if rnd:
            mc = mc_evaluate(qid, kl, oc, meals, n=mc_n)
            if mc:
                return mc['mean'], ' '.join('%s %.0f%%' % (SHORT.get(a, a), b)
                                            for a, b in list(mc['probs'].items())[:3])
        r = evaluate(qid, kl, oc, meals)
        if r['special']:
            return 99.0, 'ISSUE INATTENDUE'
        return (r['score'] if r['score'] is not None else -99), r['outcome']

    # Chaque chevalier a TROIS emplacements (relique, monture, consommable) :
    # ne proposer qu'un objet par chevalier laissait passer des combinaisons
    # gratuites (PAUL en monture + une relique par-dessus).
    cost_of = dict(pool)
    by_slot = {}
    for n, _c in pool:
        by_slot.setdefault(_slot_of(eq[n]), []).append(n)
    slots = sorted(by_slot)
    per_knight = []
    for _k in team:
        opts = [()]
        for sl in slots:
            # Emplacement soude (epee d'Edith, griffon d'Ari) : la seule option est
            # de ne rien y mettre.
            choix = [None] if _slot_verrouille(_k, sl) else [None] + by_slot[sl]
            opts = [o + (x,) for o in opts for x in choix]
        per_knight.append(opts)

    # PRE-SELECTION. Sans elle, `itertools.product` explosait : ~10 000 tenues par
    # chevalier (31 reliques x 21 montures x 16 consommables), soit 10^16 combinaisons
    # a quatre. La commande tournait plus de deux minutes et n'affichait RIEN --
    # elle ne terminait tout simplement jamais. On classe donc d'abord les tenues de
    # chaque chevalier en equipant LUI SEUL (les autres nus), puis on ne croise que
    # les meilleures. 12^4 = 20 736 combinaisons, quelques secondes.
    if len(per_knight) > 1 or max(len(o) for o in per_knight) > 400:
        # Une fenetre trop etroite est PIRE que pas de fenetre : a 6 tenues par
        # chevalier, les meilleures se disputent toutes les memes objets, le filtre
        # « un objet, un porteur » eliminait TOUTES les combinaisons et la commande
        # ne renvoyait rien du tout. Il faut de la diversite, plus un repli glouton.
        keep = max(12, int(100000 ** (1.0 / max(1, len(team)))))
        pruned = []
        for i, opts in enumerate(per_knight):
            ranked = []
            for tup in opts:
                its = [x for x in tup if x]
                if sum(cost_of[x] for x in its) > gold:
                    continue
                kl = []
                for jx, k in enumerate(team):
                    k2 = k
                    if jx == i:
                        for it in its:
                            k2, _rm = _with_item(k2, it, eq[it])
                    kl.append(k2)
                ranked.append((val(kl)[0], tup))
            ranked.sort(key=lambda x: -x[0])
            pruned.append([t for _s, t in ranked[:keep]] or [()])
        per_knight = pruned

    rows = []
    for combo in itertools.product(*per_knight):
        used = [x for tup in combo for x in tup if x]
        if len(set(used)) != len(used):            # un objet, un porteur
            continue
        cost = sum(cost_of[x] for x in used)
        if cost > gold:
            continue
        kl = []
        for k, tup in zip(team, combo):
            k2 = k
            for it in tup:
                if it:
                    k2, _rm = _with_item(k2, it, eq[it])
            kl.append(k2)
        s, o = val(kl)
        rows.append((round(s, 2), o, [tuple(x for x in t if x) for t in combo], cost))
    if not rows:
        # Repli glouton : on habille les chevaliers l'un apres l'autre en retirant du
        # pool ce qui est deja porte, et on essaie chaque ordre de passage. Garantit
        # toujours une reponse, meme quand le croisement complet ne donne rien.
        best_g = None
        for order in itertools.islice(itertools.permutations(range(len(team))), 24):
            taken, choice = set(), [()] * len(team)
            for i in order:
                cur = None
                for tup in per_knight[i]:
                    its = [x for x in tup if x]
                    if any(x in taken for x in its):
                        continue
                    # `choice` contient des tuples d'emplacements ou un slot vide vaut
                    # None : sans le filtre, cost_of[None] levait un KeyError.
                    if sum(cost_of[x] for x in its) + sum(
                            cost_of[y] for t in choice for y in t if y) > gold:
                        continue
                    kl = []
                    for jx, k in enumerate(team):
                        k2 = k
                        for it in (its if jx == i else choice[jx]):
                            if it:
                                k2, _rm = _with_item(k2, it, eq[it])
                        kl.append(k2)
                    v = val(kl)[0]
                    if cur is None or v > cur[0]:
                        cur = (v, tup)
                if cur:
                    choice[i] = cur[1]
                    taken.update(x for x in cur[1] if x)
            kl = []
            for k, tup in zip(team, choice):
                k2 = k
                for it in tup:
                    if it:
                        k2, _rm = _with_item(k2, it, eq[it])
                kl.append(k2)
            sg, og = val(kl)
            cg = sum(cost_of[x] for t in choice for x in t if x)
            if best_g is None or sg > best_g[0]:
                best_g = (round(sg, 2), og, [tuple(x for x in t if x) for t in choice], cg)
        if best_g:
            # Amelioration locale : on tente de remplacer, un slot a la fois, l'objet
            # d'un chevalier par n'importe quel objet libre. Le glouton seul laissait
            # deux chevaliers sur quatre les mains vides.
            cur = [list(t) for t in best_g[2]]
            libre = [n for n, _c in pool]
            gain = True
            while gain:
                gain = False
                taken = {x for t in cur for x in t}
                for i in range(len(team)):
                    for x in libre:
                        if x in taken or cost_of[x] > gold:
                            continue
                        sl = _slot_of(eq[x])
                        old = [y for y in cur[i] if _slot_of(eq[y]) == sl]
                        cand = [y for y in cur[i] if _slot_of(eq[y]) != sl] + [x]
                        prev, cur[i] = list(cur[i]), cand
                        kl = []
                        for k, tup in zip(team, cur):
                            k2 = k
                            for it in tup:
                                k2, _rm = _with_item(k2, it, eq[it])
                            kl.append(k2)
                        v = val(kl)[0]
                        cost = sum(cost_of[y] for t in cur for y in t)
                        if v > best_g[0] + 1e-9 and cost <= gold:
                            best_g = (round(v, 2), val(kl)[1],
                                      [tuple(t) for t in cur], cost)
                            taken = {y for t in cur for y in t}
                            gain = True
                        else:
                            cur[i] = prev
                            if old:
                                pass
            rows.append(best_g)

    rows.sort(key=lambda x: (-x[0], x[3]))
    seen, out = set(), []
    for s, o, combo, cost in rows:
        key = tuple(combo)
        if key in seen:
            continue
        seen.add(key)
        out.append({'score': s, 'outcome': o, 'items': combo, 'cost': cost})
        if len(out) >= top:
            break
    return out


def cmd_loadout(argv):
    """st.py loadout <quest_id> <chevalier...> [budget]"""
    qid = argv[0]
    budget = None
    names = []
    for a in argv[1:]:
        if a.isdigit():
            budget = int(a)
        else:
            names.append(a)
    ks, r, e = st.knights_from_save()
    apply_save_modifiers(r, e)
    oc = save_modifier_outcomes(r, e, build_outcomes())
    avail = available_items(e, r)
    gold = budget if budget is not None else max(0, (e.get('current_funds') or 0) - GOLD_FLOOR[0])
    meals = 1 if 'kitchen' in set(st._nm(x) for x in (e.get('unlocked_rooms') or [])) else 0
    team = [ks[n] for n in names]
    q = MODS.get(qid) or C('quests')[qid]
    print('%s | %s | %d chevalier(s) demandes | budget %d or | repas %d'
          % (st.tr(q['name_key']) or qid, st.stats_fr(q['stats']), q['nb_knights'], gold, meals))
    print('objets disponibles : %s' % ', '.join('%s(%s)' % (n, v[0]) for n, v in sorted(avail.items(), key=lambda x: x[1][0])))
    print()
    for row in best_team_loadout(qid, team, oc, gold, meals, avail):
        detail = ' | '.join('%s: %s' % (n, '+'.join(it) if it else '-') for n, it in zip(names, row['items']))
        print('  %6.2f %-34s %4d or   %s' % (row['score'], row['outcome'], row['cost'], detail))


def gear_suggestions(qid, team, oc, gold, meals=True, max_items=2, avail=None):
    """Cherche les objets achetables qui font changer de palier."""
    eq = C('equipment')
    if avail is not None:
        eq = {k: dict(v, cost=avail[k][0]) for k, v in eq.items() if k in avail}
    base = evaluate(qid, team, oc, meals)
    if base['special'] or base['score'] is None:
        return []
    need, label = gap_to_next(base['score'])
    if need is None:
        return []
    cand = []
    for name, v in eq.items():
        c = v.get('cost')
        # `not c` jetait aussi les objets a COUT 0, c'est-a-dire tout ce que le joueur
        # possede deja : best_loadout ne proposait que des achats. Cycle 20.
        if c is None or c > gold:
            continue
        slot = _slot_of(v)
        for i, k in enumerate(team):
            if name in (k.get('equip') or []):
                continue
            if _slot_verrouille(k, slot):
                continue
            k2, removed = _with_item(k, name, v)
            team2 = list(team)
            team2[i] = k2
            r = evaluate(qid, team2, oc, meals)
            if r['special'] or r['score'] is None:
                continue
            if r['rank'] > base['rank']:
                lbl = name if not removed else '%s (remplace %s)' % (name, removed)
                cand.append((c, lbl, slot, k['name'], round(r['score'], 2), r['outcome']))
    cand.sort()
    # Pour chaque emplacement/chevalier on garde le MOINS CHER *et* le MEILLEUR.
    # Ne garder que le moins cher faisait recommander une fronde a 0,03 de marge
    # la ou une epee d'argent donnait 0,95 pour 30 or de plus.
    best_by = {}
    for c, name, slot, who, sc, o in cand:
        key = (slot, who)
        if key not in best_by or sc > best_by[key][4]:
            best_by[key] = (c, name, slot, who, sc, o)
    seen, out = set(), []
    for c, name, slot, who, sc, o in cand:
        key = (slot, who)
        if key in seen:
            continue
        seen.add(key)
        out.append({'cost': c, 'item': name, 'slot': slot, 'knight': who, 'score': sc, 'outcome': o})
        b = best_by[key]
        if b[1] != name:
            out.append({'cost': b[0], 'item': b[1], 'slot': b[2], 'knight': b[3],
                        'score': b[4], 'outcome': b[5]})
        if len(out) >= max_items:
            break
    return out


# ---------------------------------------------------------------- repartition optimale
def _meal_txt(qid, team, oc, meals, base):
    """Annotation ' [repas: ...]' -- affichee seulement si le repas fait
    basculer le palier (preference du joueur), jamais sur toutes les quetes
    a la fois : il n'y a qu'UN repas par cycle."""
    if not meals or base.get('score') is None:
        return ''
    rnd = any(is_random(k.get('name')) for k in team)
    if rnd:
        # equipe a stats aleatoires : il FAUT comparer deux distributions,
        # jamais une moyenne Monte-Carlo a un score deterministe.
        mc = mc_evaluate(qid, team, oc, 1, n=1200)
        if not mc:
            return ''
        base_mc = mc_evaluate(qid, team, oc, 0, n=1200)
        if not base_mc:
            return ''
        d = mc['mean'] - base_mc['mean']
        modal = max(mc['probs'].items(), key=lambda x: x[1])
        return '   [repas %+.2f -> %.2f, %s %.0f%%]' % (d, mc['mean'], SHORT.get(modal[0], modal[0]), modal[1])
    r = evaluate(qid, team, oc, 1)
    if r.get('score') is None or r['outcome'] == base['outcome']:
        return ''
    return '   [repas -> %.2f %s]' % (r['score'], r['outcome'])


def best_assignments(quests, knights, oc, meals=True, top=5, per_quest=None,
                     max_nodes=400000):
    """Repartition : on pre-selectionne les meilleures equipes par quete, puis on
    cherche une affectation disjointe. Borne pour rester instantane."""
    import itertools
    qids = list(quests)
    names = list(knights)
    # LARGEUR DE LA PRE-SELECTION. Elle etait figee a 15 equipes par quete, ce qui
    # convenait a une table ronde de six. A dix chevaliers, les quinze meilleures equipes
    # d'une quete contiennent toutes les MEMES fortes tetes : plus aucune combinaison
    # disjointe ne survit, et la recherche se rabat sur « quete non assignee ».
    # Cycle 31 : six quetes sur le plateau, dix chevaliers libres, et l'outil n'en
    # proposait qu'UNE seule -- alors que chacune des cinq autres sortait en reussite
    # critique avec un trio nu. On elargit donc avec l'effectif.
    if per_quest is None:
        per_quest = max(15, 8 * len(names))

    options = {}
    for qid in qids:
        _q = MODS.get(qid) or C('quests')[qid]
        nb = _q['nb_knights']
        # Chevaliers IMPOSES (`requested_knights`) : le jeu les place lui-meme, ils
        # occupent une place et NE SONT PAS a fournir. Sans ca l'outil proposait deux
        # chevaliers de plus que les slots reellement libres (Les origines de Gideon).
        nb = max(0, nb - len([x for x in (_q.get('locked_knights') or []) if x not in knights]))
        if nb == 0:
            continue
        size = min(nb, len(names))
        # Les issues speciales court-circuitent TOUT le calcul de score et se declenchent
        # sur un seul chevalier (ou un seul objet). Envoyer l'equipe complete gaspille
        # donc les autres. Comme les equipes candidates faisaient toujours exactement
        # nb_knights, ce cas etait structurellement invisible : cycle 25, l'outil mettait
        # Goberto avec Ligia sur la liche alors que Ligia seule declenche l'issue.
        # Pour une quete a issue speciale, on enumere aussi les equipes PLUS PETITES.
        _sizes = [size]
        if (MODS.get(qid) or C('quests')[qid]).get('special_outcomes'):
            _sizes = list(range(1, size + 1))
        rows = []
        for team in [t for z in _sizes for t in itertools.combinations(names, z)]:
            r = evaluate(qid, [knights[n] for n in team], oc, 0)
            if r['deadly']:
                continue
            if r['special'] is None and any(is_random(x) for x in team):
                mc = mc_evaluate(qid, [knights[n] for n in team], oc, 0, n=200)
                if mc:
                    modal = max(mc['probs'].items(), key=lambda x: x[1])
                    r = dict(r, score=mc['mean'],
                             outcome='ALEATOIRE~%s %.0f%%' % (SHORT.get(modal[0], modal[0]), modal[1]),
                             rank=sum(RANK.get(o, 0) * v / 100.0 for o, v in mc['probs'].items()))
            rows.append((r['rank'], list(team), r))
        # Trier sur le seul `rank` (le PALIER) laissait tous les ex aequo dans l'ordre
        # de itertools.combinations, c'est-a-dire l'ordre de la table ronde. La fenetre
        # rows[:per_quest] se remplissait donc des premieres combinaisons alphabetiques.
        # Cycle 21 : `angelica+goberto` (1,65) chassait `angelica+ursule` (4,69) de la
        # pre-selection -- meme palier « reussite », mais arrivee plus tot -- et Ursule
        # restait au repos pendant que Goberto plombait l'urgence de Milkford.
        # On departage donc par le SCORE.
        rows.sort(key=lambda x: (-x[0], -(x[2].get('score')
                                          if x[2].get('score') is not None else -99.0)))
        q = MODS.get(qid) or C('quests')[qid]
        # Cout de NE PAS assigner. L'ultimatum a `failure: []` dans son .tres :
        # sans ce cas special il ne « coutait » que -0,5 et le planificateur
        # preferait quatre contrats ordinaires a la bataille finale.
        if qid in FORCED:
            # Le joueur veut CETTE quete ce cycle (« de preference j'aimerais qu'on
            # fasse le Tarasque »). Un contrat sans deadline ne coute que -0,5 a
            # ignorer, donc le planificateur le sautait systematiquement.
            pen = -100.0
        elif q.get('type') == 'ULTIMATUM_QUEST':
            pen = -100.0
        elif q['deadline'] and any((x or {}).get('type') == 'LOCATION_DESTROYED'
                                   for x in (q['failure'] or [])):
            # Urgence dont l'echec DETRUIT une localite. Elle n'est intouchable que si
            # c'est le DERNIER cycle possible : sinon elle peut parfaitement attendre,
            # et la forcer a -100 gachait un cycle entier (cycle 14, Villador et
            # Treflemont a delai 2 tous les deux : 7 places pour 6 chevaliers).
            pen = -100.0 if LAST_CHANCE.get(qid) else -12.0
        elif q.get('extra_conditions'):
            pen = -30.0
        elif q['deadline'] and q.get('type') == 'MAJOR_QUEST':
            # LE bug qui m'a fait rater quatre quetes majeures d'affilee (Investigation
            # du quartier sombre aux cycles 33 ET 34, Domptons le griffon au 35, l'Ile
            # au tresor au 36). Une quete majeure a deadline a `failure: []` dans son
            # .tres -- aucune consequence d'echec scriptee -- donc la branche suivante
            # (`deadline AND failure`) etait fausse et on retombait sur pen = -0,5 : la
            # sauter coutait autant qu'un contrat trivial. Sauf que son contenu, lui,
            # disparait definitivement -- 2500 or et une relique pour l'Ile au tresor.
            pen = -100.0 if LAST_CHANCE.get(qid) else -8.0
        elif q['deadline'] and q['failure']:
            pen = -6.0
        elif LAST_CHANCE.get(qid):
            pen = -6.0
        else:
            pen = -0.5
        options[qid] = rows[:per_quest] + [(pen, [], {'outcome': 'NON ASSIGNEE', 'score': None,
                                                      'special': None, 'rank': pen, 'deadly': False})]

    # Une quete entierement pourvue par ses chevaliers IMPOSES n'a plus de slot :
    # elle sort de `options` et doit sortir de `qids`, sinon rec() plante (KeyError).
    qids = [x for x in qids if x in options]

    results = []
    nodes = [0]

    def rec(i, free, acc, total):
        if nodes[0] > max_nodes:
            return
        nodes[0] += 1
        if i == len(qids):
            results.append((total, list(acc)))
            return
        qid = qids[i]
        for rank, team, r in options[qid]:
            if team and not set(team) <= free:
                continue
            acc.append((qid, team, r))
            rec(i + 1, free - set(team), acc, total + rank)
            acc.pop()

    rec(0, set(names), [], 0.0)

    # Un seul repas par cycle : on l'attribue a la quete ou il rapporte le plus
    # (le rank encode le palier, donc c'est bien "la ou il fait basculer une issue").
    if meals:
        boosted = []
        for total, detail in results:
            best = (total, detail, None)
            for j, (qid, team, r) in enumerate(detail):
                if not team:
                    continue
                r2 = evaluate(qid, [knights[n] for n in team], oc, 1)
                if r2['deadly']:
                    continue
                if r2['special'] is None and any(is_random(x) for x in team):
                    mc = mc_evaluate(qid, [knights[n] for n in team], oc, 1, n=200)
                    if mc:
                        modal = max(mc['probs'].items(), key=lambda x: x[1])
                        r2 = dict(r2, score=mc['mean'],
                                  outcome='ALEATOIRE~%s %.0f%%' % (SHORT.get(modal[0], modal[0]), modal[1]),
                                  rank=sum(RANK.get(o, 0) * v / 100.0 for o, v in mc['probs'].items()))
                t2 = total - r['rank'] + r2['rank']
                if t2 > best[0]:
                    d2 = list(detail)
                    d2[j] = (qid, team, dict(r2, meal=team[0]))
                    best = (t2, d2, qid)
            boosted.append((best[0], best[1]))
        results = boosted

    # Le tri sur la seule somme des PALIERS laisse enormement d'affectations ex aequo
    # (tous les « reussite » valent pareil), et l'ordre d'insertion tranchait. Comme le
    # rendu ne garde qu'une candidate (top=1) et fige l'affectation AVANT l'equipement,
    # ce hasard se payait cash : cycle 21, Milkford partait avec Goberto (INT -2) puis
    # Ligia (6,61) alors qu'Ursule donnait 7,09. On departage par la somme des SCORES.
    def _ssum(detail):
        return sum((r.get('score') or 0.0) for _q, t, r in detail if t)
    results.sort(key=lambda x: (-x[0], -_ssum(x[1])))
    return results[:top]


# ---------------------------------------------------------------- rendu
_INKPATH = {}


def _ink_path(audience_name):
    """Nom de fichier d'audience -> ink_path declare dans la ressource."""
    if not _INKPATH:
        for f in glob.glob(os.path.join(st.CACHE, 'res', '*.res')):
            try:
                rr = Res(f)
            except Exception:
                continue
            for pth, (t, pr) in rr.resources.items():
                if pr.get('ink_path'):
                    _INKPATH[pth.split('/')[-1].replace('.tres', '')] = pr['ink_path']
    return _INKPATH.get(audience_name)



_FX = {}


def audience_effects(audience_name, limit=6):
    """Effets scenaristiques d'une audience de resolution, en clair."""
    if not audience_name:
        return ''
    if audience_name not in _FX:
        try:
            fx = st.ink_effects(_ink_path(audience_name) or audience_name)
        except Exception:
            fx = []
        _FX[audience_name] = fx
    fx = _FX[audience_name]
    if not fx:
        return ''
    return '; '.join('%s %s' % t for t in fx[:limit])


def dmg_for(q, outcome):
    """Fourchette de degats reelle selon le palier obtenu (cf. determine_damages)."""
    import math
    lo, hi = (q['damages'][0] or 0), (q['damages'][1] or 0)
    if outcome == 'REUSSITE CRITIQUE':
        return 0, 0
    if outcome == 'GRANDE REUSSITE':
        hi = max(lo, int(math.floor((hi - lo) / 2.0)))
    elif outcome == 'REUSSITE':
        hi = max(lo, hi - 1)
    elif outcome == 'ECHEC':
        lo = min(hi, lo + 1)
    elif outcome == 'ECHEC MAJEUR':
        lo = min(hi, int(math.ceil((hi - lo) / 2.0)))
    elif outcome == 'ECHEC CRITIQUE':
        return 100, 100
    return lo, hi


def death_flag(q, outcome, knight):
    if not q.get('lethal', True):
        return ''
    lo, hi = dmg_for(q, outcome)
    if knight['armor'] <= hi:
        rep = knight.get('max_armor', knight['armor'])
        if rep > hi:
            # tower_manager.gd : cout = 15*(1+n) + 4*n^2, n = reparations deja faites CE cycle
            return ('  !! armure %d <= degats %d -> REPARER a la forge (max %d ; cout 15 or'
                    ' la 1re reparation du cycle, puis 34, 61, 96...)' % (knight['armor'], hi, rep))
        pct = 100.0 if lo >= knight['armor'] else (hi - knight['armor'] + 1) * 100.0 / (hi - lo + 1)
        return '  !! MORT possible (%.0f%%, armure %d vs degats %d-%d)' % (pct, knight['armor'], lo, hi)
    return ''


def ultimatum_extra_conditions(r, e):
    """Conditions supplementaires vivantes de l'ultimatum (elles ne sont PAS
    dans le .tres de la quete : le manager les y injecte au declenchement).
    quest.gd ajoute +2 par condition remplie, et transforme tout score <= 0
    en ECHEC CRITIQUE -> degats 100 -> morts."""
    ref = e.get('current_ultimatum')
    if not e.get('has_current_ultimatum') or not isinstance(ref, dict) or '@sub' not in ref:
        return None
    ult = r.resources[ref['@sub']][1]
    POP = C('enums')['PopulationCategory']
    sat = e.get('current_satisfaction') or {}
    funds = e.get('current_funds')
    if funds is None:
        funds = e.get('funds') or 0
    rallied = len(e.get('rallied_counties') or [])
    out, met = [], {}
    for sub in (ult.get('selected_conditions_set') or []):
        if not isinstance(sub, dict) or '@sub' not in sub:
            continue
        c = r.resources[sub['@sub']][1]
        t = c.get('type', 0)
        if t == 0:
            need = c.get('min_rallied_counties', 1)
            ok, key = rallied > need, 'Allied_County'
            txt = 'comtes rallies > %d (actuel %d)' % (need, rallied)
        elif t == 1:
            pop = POP[str(c.get('targeted_population', 0))]
            amount = c.get('amount', 0)
            cur = sat.get(pop.lower(), 0)
            ok, key = cur >= amount, pop
            txt = 'satisfaction %s >= %d (actuel %d)' % (pop, amount, cur)
        else:
            amount = c.get('amount', 0)
            ok, key = funds >= amount, 'Funds'
            txt = 'or >= %d (actuel %d)' % (amount, funds)
        out.append((txt, ok))
        if ok:
            met[key] = 2
    return {'list': out, 'met': met, 'quests': [x['@ext'][1].split('/')[-1].replace('.tres', '')
                                                for x in (ult.get('ultimatum_follow_up_quests') or [])
                                                if isinstance(x, dict) and '@ext' in x]}


def _deadline_txt(q, lv, e):
    if not q['deadline']:
        return ''
    rem = lv.get('remaining_cycles_before_faillure')
    if rem is None:
        return ' | DELAI'
    cur = (e.get('cycle_index') or 0) + 1
    last = cur + rem - 1
    marge = 'PEUT ATTENDRE (dernier cycle utile : %d)' % last if rem > 1 else 'DERNIERE CHANCE CE CYCLE'
    return ' | DELAI %d -> %s' % (rem, marge)


def fmt_rewards(rw):
    out = []
    for r in rw:
        t = r.get('type')
        if t == 'FUNDS':
            out.append('%s or' % r.get('amount'))
        elif t == 'SATISFACTION':
            out.append('%+d satisfaction %s' % (r.get('amount', 0), r.get('affected_category')))
        elif t == 'LOCATION_DESTROYED':
            out.append('%s DETRUIT' % r.get('location'))
        elif t == 'MOUNT':
            out.append('monture')
        elif t == 'RELIC':
            out.append('relique')
        else:
            out.append(str(t))
    return ', '.join(out) or '-'


def main(argv):
    slot = 1
    only = None
    meals = None
    for x in argv:
        if x.isdigit():
            slot = int(x)
        elif x.startswith('--quests='):
            only = x.split('=', 1)[1].split(',')
        elif x == '--no-meals':
            meals = False
        elif x == '--meals':
            meals = True
        elif x.startswith('--floor='):
            GOLD_FLOOR[0] = int(x.split('=', 1)[1])
    ks, r, e = st.knights_from_save(slot)
    set_welded_reductions(ks)
    busy = {n: k for n, k in ks.items() if k.get('busy') or k.get('dead')}
    ks = {n: k for n, k in ks.items() if n not in busy}
    if busy:
        print('-- indisponibles ce cycle : %s (deja engages) -- leur equipement est bloque avec eux'
              % ', '.join(busy))
    oc = save_modifier_outcomes(r, e, build_outcomes())
    apply_save_modifiers(r, e)
    PEND.clear()
    PEND.update(pending_levels(e))
    if PEND:
        print('++ montees de niveau EN ATTENTE : %s  (+1 stat chacune, +1 armure aux niv. 5/10/15)'
              % ', '.join('%s x%d' % (a, b) for a, b in PEND.items()))
    creux = [n for n, k in ks.items() if not any(k['stats'].values())]
    if creux:
        print('!! fiche de base introuvable pour : %s -- leurs scores sont faux, verifie en jeu\n' % ', '.join(creux))
    quests = C('quests')
    rooms = [st._nm(x) for x in (e.get('unlocked_rooms') or [])]
    gold = max(0, e.get('current_funds', 0) - GOLD_FLOOR[0])   # plancher a preserver
    kitchen = 'kitchen' in rooms
    forge = 'forge' in rooms
    if meals is None:
        meals = kitchen

    current = [st._nm(k) for k in (e.get('current_quests') or {})]
    live = {st._nm(k): v for k, v in (e.get('current_quests') or {}).items()}
    if only:
        current = [q for q in current if any(o.lower() in q.lower() for o in only)]
    # La sauvegarde reference les quetes par CHEMIN de fichier, alors que
    # quests.json est indexe par quest_id : les deux different pour les
    # ultimatums (quest_ultimatum_dragonknight_default.tres -> quest_ultimatum_dragonknight).
    # Sans ce repli, une quete active disparaissait sans un mot du rapport.
    by_file = {}
    for _qid, _q in quests.items():
        by_file.setdefault(_q['path'].split('/')[-1].replace('.tres', ''), _qid)
    resolved, unknown = [], []
    for q in current:
        if q in quests:
            resolved.append(q)
        elif q in by_file:
            live[by_file[q]] = live.get(q, {})
            resolved.append(by_file[q])
        else:
            unknown.append(q)
    if unknown:
        print('!! QUETES DE LA SAUVEGARDE NON RECONNUES (a verifier en jeu) : %s' % ', '.join(unknown))
    current = resolved
    ULT = ultimatum_extra_conditions(r, e)
    if ULT:
        print('!! ULTIMATUM EN COURS -- conditions supplementaires (+2 chacune si remplie) :')
        for txt, ok in ULT['list']:
            print('     [%s] %s' % ('OK' if ok else '  ', txt))
        print('     -> bonus acquis : +%d  |  ATTENTION : sur cette quete tout score <= 0'
              ' est un ECHEC CRITIQUE (degats 100 = MORTS)' % sum(ULT['met'].values()))
        for _qid in list(current):
            _q = MODS.get(_qid) or quests.get(_qid) or {}
            if _q.get('type') == 'ULTIMATUM_QUEST':
                _q2 = json.loads(json.dumps(quests[_qid]))
                _q2['extra_met'] = ULT['met']
                _q2['extra_conditions'] = len(ULT['list'])
                MODS[_qid] = _q2
                _MODSIG.pop(_qid, None)
    avail = available_items(e, r)

    print('=== %s | or %s | cuisine %s | forge %s' % (
        e.get('title'), gold, 'OK' if kitchen else 'VERROUILLEE', 'OK' if forge else 'VERROUILLEE'))
    print('chevaliers : %s' % ', '.join('%s (%s)' % (n, ' '.join('%s%s' % (st.sfr(a), b) for a, b in k['stats'].items() if b))
                                        for n, k in ks.items()))
    print('repas : %s (UN SEUL par cycle)'
          % ('disponible' if meals
             else "cuisine listee VERROUILLEE dans la sauvegarde -- le fichier peut etre"
                  " en retard sur le jeu, relancer avec --meals si elle est ouverte"))
    print('objets accessibles (%d) : %s\n' % (len(avail), ', '.join(sorted(avail)) or 'aucun'))

    for qid in current:
        q = MODS.get(qid) or quests[qid]
        lv = live.get(qid, {})
        print('### %s  [%s]' % (st.tr(q['name_key']) or qid, qid))
        print('    %s / %s / %s | %s | %s chevalier(s), %s cycle(s)%s' % (
            q['type'], q['category'], q['location'], ' + '.join(q['conditions']) or '-',
            q['nb_knights'], q['duration'],
            _deadline_txt(q, lv, e)))
        if q['duration'] > 1:
            cur = (e.get('cycle_index') or 0) + 1
            print('    /!\\ duree %d : les chevaliers envoyes sont IMMOBILISES jusqu\'au cycle %d'
                  % (q['duration'], cur + q['duration']))
        print('    requis : %s | degats %s | succes: %s | echec: %s' % (
            st.stats_fr(q['stats']), q['damages'], fmt_rewards(q['success']), fmt_rewards(q['failure'])))
        if qid in MOD_NOTES:
            print('    modificateur actif : %s' % MOD_NOTES[qid])
        # Comparaison des branches : si une issue debloque une quete que les
        # autres ne debloquent pas, la choisir n'est pas un bonus mais une
        # OBLIGATION sous peine de perdre une mission pour de bon.
        _branches = {}
        for _lbl, _key in (('reussite', 'success_follow_up'), ('echec', 'failure_follow_up')):
            if q.get(_key):
                _branches[_lbl] = st.branch_unlocks(q[_key]) or []
        for _o in oc['by_quest'].get(qid, []):
            _fu = (oc['outcomes'].get(_o) or {}).get('follow_up')
            if _fu:
                # follow_up est un chemin de ressource, branch_unlocks veut le nom du knot
                _branches['INATTENDUE'] = st.branch_unlocks(_fu.split('/')[-1].replace('.tres', '')) or []
        _all = set().union(*_branches.values()) if _branches else set()
        if _all and len(set(map(tuple, (sorted(v) for v in _branches.values())))) > 1:
            print('    /!\\ les branches ne debloquent PAS la meme chose :')
            for _lbl, _v in _branches.items():
                print('        %-11s -> %s' % (_lbl, ', '.join(_v) or 'rien'))
            _only = [x for x in _all if sum(1 for v in _branches.values() if x in v) == 1]
            for _x in _only:
                _who = [l for l, v in _branches.items() if _x in v][0]
                print('        >>> %s n\'existe QUE par la branche %s' % (_x, _who))
        for lbl, key in (('succes', 'success_follow_up'), ('echec', 'failure_follow_up')):
            fx = audience_effects(q.get(key))
            if fx:
                print('    suite en cas de %s : %s [%s]' % (lbl, q[key], fx))
        for path in oc['by_quest'].get(qid, []):
            o = oc['outcomes'].get(path)
            if not o or o['traitor']:
                continue
            if o['knights']:
                cond = 'envoyer ' + '+'.join(o['knights'])
            elif o['tags']:
                cond = 'un chevalier avec le tag ' + '+'.join(o['tags'])
            elif o['amount'] > 0:
                cond = '%s %s %s' % (o['stat'], '>=' if o['requires_higher'] else '<=', o['amount'])
            else:
                cond = '?'
            mortel = ' *** MORTEL ***' if (o['damage'] and o['damage'][0] >= 50) else ''
            suite = ' -> %s' % o['follow_up'].split('/')[-1].replace('.tres', '') if o['follow_up'] else ''
            print('    ISSUE INATTENDUE possible : %s | degats %s | %s%s%s' % (
                cond, o['damage'], fmt_rewards(o['rewards']), suite, mortel))
        rows = []
        if q['nb_knights'] > 1:
            import itertools as _it
            teams = []
            for size in range(1, min(q['nb_knights'], len(ks)) + 1):
                for team in _it.combinations(ks, size):
                    t = [ks[n] for n in team]
                    r = evaluate(qid, t, oc, 0)
                    if r['special'] is None and any(is_random(n) for n in team):
                        mc = mc_evaluate(qid, t, oc, 0, n=1200)   # stats aleatoires -> probabilites
                        if mc:
                            modal = max(mc['probs'].items(), key=lambda x: x[1])
                            r = dict(r, score=mc['mean'],
                                     outcome='ALEA ' + ' '.join('%s %.0f%%' % (SHORT.get(o, o), v)
                                                                for o, v in list(mc['probs'].items())[:3]),
                                     rank=sum(RANK.get(o, 0) * v / 100.0 for o, v in mc['probs'].items()))
                    sc = r['score']
                    if sc is None and r['special']:
                        sc = 99
                    teams.append((r['rank'], sc, team, r))
            teams.sort(key=lambda x: (-x[0], -(x[1] or -99)))
            for rank, sc, team, r in teams[:4]:
                lbl = r['outcome'] if r['score'] is None else '%6.2f  %s' % (r['score'], r['outcome'])
                oc_ref = r['outcome'] if not str(r['outcome']).startswith('ALEA') else _outcome(r['score'] or 0)
                dmgw = ''.join(death_flag(q, oc_ref, ks[n]) for n in team)
                print('    %-28s %s%s%s' % ('+'.join(team), lbl, _meal_txt(qid, [ks[n] for n in team], oc, meals, r), dmgw))
            continue
        for n, k in ks.items():
            res = evaluate(qid, [k], oc, 0)
            rows.append((res['rank'], n, res))
        for rank, n, res in sorted(rows, reverse=True):
            if res['special']:
                path, o = res['special']
                tag = 'MORTEL' if res['deadly'] else 'issue inattendue'
                print('    %-11s >>> %s : degats %s, %s%s' % (
                    n, tag, o['damage'], fmt_rewards(o['rewards']),
                    ', XP x%s' % o['xp_modifier'] if not res['deadly'] else ''))
            elif is_random(n):
                mc = mc_evaluate(qid, [ks[n]], oc, 0)
                pr = ' '.join('%s %.0f%%' % (SHORT.get(o, o), v) for o, v in mc['probs'].items())
                print('    %-11s ALEATOIRE  moyenne %5.2f | %s' % (n, mc['mean'], pr))
            else:
                sug = gear_suggestions(qid, [ks[n]], oc, gold, 0, avail=avail)
                extra = _meal_txt(qid, [ks[n]], oc, meals, res)
                if n in PEND:
                    lu = levelup_gain(qid, [ks[n]], oc, 0, n, PEND[n])
                    if lu:
                        extra += '   [niveau: +%s -> %.2f %s]' % ('/'.join(st.sfr(s) for s in lu['stats']),
                                                                 lu['score'], lu['outcome'])
                if sug:
                    s0 = sug[0]
                    # Le moins cher n'est pas le plus sur : sur une quete dont l'echec
                    # est lourd, une marge de 0,03 ne vaut rien. On signale aussi
                    # l'objet qui donne la meilleure marge (preference du joueur :
                    # equipement de qualite reutilisable).
                    best = max(sug, key=lambda x: x['score'])
                    if best is not s0 and best['score'] - s0['score'] >= 0.3:
                        extra += '   [+sur : %s (%s or) = %.2f %s]' % (
                            best['item'], best['cost'], best['score'], best['outcome'])
                    extra += '   -> %s (%s or, %s) = %.2f %s' % (s0['item'], s0['cost'], s0['slot'], s0['score'], s0['outcome'])
                print('    %-11s %6.2f  %-18s%s%s' % (n, res['score'], res['outcome'], extra,
                                                       death_flag(q, res['outcome'], ks[n])))
        print()

    print('=== meilleures repartitions ===')
    for tot, detail in best_assignments(current, ks, oc, meals):
        parts = []
        for qid, names, r in detail:
            who = '+'.join(names) if names else '(personne)'
            lab = r['outcome'] if r['score'] is None else '%s %.2f' % (r['outcome'], r['score'])
            if r.get('meal'):
                lab += ' +REPAS(%s)' % r['meal']
            parts.append('%s: %s [%s]' % (st.tr(quests[qid]['name_key']) or qid, who, lab))
        print(' %5.1f  %s' % (tot, ' | '.join(parts)))


# ---------------------------------------------------------------- meilleur equipement complet
def best_loadout(qid, knight, gold, oc, meals=True, topk=12, avail=None):
    """Meilleure combinaison relique + monture + consommable dans le budget."""
    eq = C('equipment')
    if avail is not None:
        eq = {k: dict(v, cost=avail[k][0]) for k, v in eq.items() if k in avail}
    slots = {'relique': [], 'monture': [], 'consommable': []}
    for name, v in eq.items():
        c = v.get('cost')
        if not c or c > gold:
            continue
        sl = SLOT.get(v['kind'], 'relique')
        if _slot_verrouille(knight, sl):
            continue                # emplacement soude : rien a proposer dessus
        slots[sl].append((name, c, v))
    def apply(k, items):
        k2 = k
        cost = 0
        for name, c, v in items:
            cost += c
            k2, _ = _with_item(k2, name, v)
        return k2, cost
    # pre-selection: meilleurs objets par slot pris isolement
    short = {}
    for sl, items in slots.items():
        rated = []
        for it in items:
            k2, _ = apply(knight, [it])
            r = evaluate(qid, [k2], oc, meals)
            if r['score'] is not None:
                rated.append((r['score'], it))
        rated.sort(key=lambda x: -x[0])
        short[sl] = [it for _, it in rated[:topk]]
    best = None
    for a in [None] + short['relique']:
        for b in [None] + short['monture']:
            for c in [None] + short['consommable']:
                items = [x for x in (a, b, c) if x]
                k2, cost = apply(knight, items)
                if cost > gold:
                    continue
                r = evaluate(qid, [k2], oc, meals)
                if r['score'] is None:
                    continue
                key = (r['score'], -cost)
                if best is None or key > best[0]:
                    best = (key, r, cost, [x[0] for x in items])
    if not best:
        return None
    _, r, cost, names = best
    return {'score': round(r['score'], 2), 'outcome': r['outcome'], 'cost': cost, 'items': names}


def cmd_gear(argv):
    qid = argv[0]
    who = argv[1]
    ks, r, e = st.knights_from_save()
    gold = int(argv[2]) if len(argv) > 2 else e.get('current_funds', 0)
    meals = 'kitchen' in [st._nm(x) for x in (e.get('unlocked_rooms') or [])]
    oc = save_modifier_outcomes(r, e, build_outcomes())
    avail = available_items(e, r)
    base = evaluate(qid, [ks[who]], oc, meals)
    print('%s / %s | budget %s or | repas %s' % (qid, who, gold, 'oui' if meals else 'non'))
    print('  sans rien      : %s %s' % (round(base['score'], 2) if base['score'] is not None else '-', base['outcome']))
    b = best_loadout(qid, ks[who], gold, oc, meals, avail=avail)
    if b:
        print('  meilleur achat : %.2f %s  (%s or) -> %s' % (b['score'], b['outcome'], b['cost'], ', '.join(b['items']) or 'rien'))


# ---------------------------------------------------------------- couverture multi-quetes
def _tagsets(q):
    enums = C('enums')
    eff = C('efficiency')
    CT = enums['CharacterTags']
    E, I = set(), set()
    cid = [k for k, v in enums['QuestTags'].items() if v == q['category']]
    if cid and cid[0] in eff['quest']:
        E |= {CT[str(x)] for x in eff['quest'][cid[0]]['eff']}
        I |= {CT[str(x)] for x in eff['quest'][cid[0]]['ineff']}
    for c in q['conditions']:
        ci = [k for k, v in enums['ConditionTags'].items() if v == c]
        if ci and ci[0] in eff['condition']:
            E |= {CT[str(x)] for x in eff['condition'][ci[0]]['eff']}
            I |= {CT[str(x)] for x in eff['condition'][ci[0]]['ineff']}
    return E, I


def _contrib(k, q, E, I, size, meal=False):
    """Apport individuel d'un chevalier (le score total en est la somme + la base -10)."""
    nb = q['nb_knights']
    missing = nb - size
    t = st.snap(10.0 / nb) + (0.5 if meal else 0)
    for stat, req in q['stats'].items():
        val = max(0, min(15, k['stats'].get(stat, 0)))
        s = (val - (req + missing)) * 0.66
        if abs(s) < 1e-9:
            s = 0.22
        s = s / size
        s *= (1 + req * 0.0275) if s > 0 else (1 + req * 0.01)
        t += st.snap(s)
    # Meme calcul que st.score() : passe par les helpers pour que les deux ne
    # divergent plus. L'equipe n'est pas connue ici (recherche combinatoire),
    # donc LOYAL retombe a 0 et la reduction de duree est celle de ce seul
    # chevalier -- l'arbitrage final passe de toute facon par st.score().
    st.prep_quest(q, q.get('_id') or q.get('id') or '', [k])
    kk = st.prep_knight(k, size, [k])
    for _tag, _v in st.tag_score(q, kk, E, I):
        t += _v
    return t


def _tier(s):
    return 4 if s >= 10 else 3 if s > 5 else 2 if s > 0 else -2 if s > -5 else -3 if s > -10 else -5


def _outcome(s):
    return ('REUSSITE CRITIQUE' if s >= 10 else 'GRANDE REUSSITE' if s > 5 else 'REUSSITE' if s > 0
            else 'ECHEC CRITIQUE' if s <= -10 else 'ECHEC MAJEUR' if s <= -5 else 'ECHEC')


def cmd_cover(argv):
    import itertools
    qids = [a for a in argv if not a.isdigit()]
    ks, r, e = st.knights_from_save()
    busy = {n: k for n, k in ks.items() if k.get('busy') or k.get('dead')}
    ks = {n: k for n, k in ks.items() if n not in busy}
    if busy:
        print('-- indisponibles : %s' % ', '.join(busy))
    oc = save_modifier_outcomes(r, e, build_outcomes())
    apply_save_modifiers(r, e)
    PEND.clear()
    PEND.update(pending_levels(e))
    avail = available_items(e, r)
    eq = C('equipment')
    gold = e.get('current_funds', 0)
    budget = int([a for a in argv if a.isdigit()][0]) if any(a.isdigit() for a in argv) else gold
    meals_on = 'kitchen' in set(st._nm(x) for x in (e.get('unlocked_rooms') or []))

    Q = {}
    for qid in qids:
        q = dict(MODS.get(qid) or C('quests')[qid])
        q['coastal'] = q['location'] in st.COASTAL_LOCATIONS
        Q[qid] = (q, _tagsets(q))
    names = list(ks)
    items = [None] + list(avail)

    def variants(n, qid, size):
        """(apport, objet, montee_de_niveau) les meilleurs pour ce chevalier sur cette quete."""
        q, (E, I) = Q[qid]
        stats = list(q['stats']) or ['STRENGTH']
        lvl_opts = [None]
        if n in PEND:
            lvl_opts += list(itertools.combinations_with_replacement(stats, PEND[n]))
        out = []
        for it in items:
            base = _with_item(ks[n], it, eq[it])[0] if it else ks[n]
            for lv in lvl_opts:
                k2 = base
                if lv:
                    k2 = dict(base)
                    k2['stats'] = dict(base['stats'])
                    for s_ in lv:
                        k2['stats'][s_] = k2['stats'].get(s_, 0) + 1
                out.append((_contrib(k2, q, E, I, size), it, lv))
        out.sort(key=lambda x: -x[0])
        top = out[:4]
        nones = [o for o in out if o[1] is None]          # l'option "sans objet" doit toujours rester
        if nones and not any(o[1] is None for o in top):
            top.append(nones[0])
        frees = [o for o in out if o[1] is not None and avail[o[1]][0] == 0]
        if frees and not any(o[1] is not None and avail[o[1]][0] == 0 for o in top):
            top.append(frees[0])
        return top

    size_opts = {qid: list(range(1, min(Q[qid][0]['nb_knights'], len(names)) + 1))[::-1] for qid in qids}
    best = None
    for split in _splits_var(names, [size_opts[q] for q in qids]):
        cand = {}
        ok = True
        for qid, team in zip(qids, split):
            for n in team:
                cand[(n, qid)] = variants(n, qid, len(team))
        flat = [(n, qid) for qid, team in zip(qids, split) for n in team]
        for combo in itertools.product(*[cand[k] for k in flat]):
            used = [c[1] for c in combo if c[1]]
            if len(set(used)) != len(used):
                continue
            cost = sum(avail[c][0] for c in used)
            if cost > budget:
                continue
            scores, i = {}, 0
            for qid, team in zip(qids, split):
                s = -10.0 + sum(combo[i + j][0] for j in range(len(team)))
                i += len(team)
                scores[qid] = s
            # le repas (un seul par cycle) va la ou il fait basculer un palier
            best_meal, best_sc = None, None
            for target in ([None] + qids if meals_on else [None]):
                sc = dict(scores)
                if target:
                    sc[target] += 0.5
                tiers = sum(_tier(v) for v in sc.values())
                cand_key = (tiers, round(min(sc.values()), 2))
                if best_sc is None or cand_key > best_sc[0]:
                    best_sc, best_meal = (cand_key, sc), target
            scores = best_sc[1]
            risk = 0
            for qid2, team2 in zip(qids, split):
                _, hi2 = dmg_for(Q[qid2][0], _outcome(scores[qid2]))
                if Q[qid2][0].get('lethal', True):
                    risk += sum(1 for n2 in team2
                                if ks[n2]['armor'] <= hi2 and ks[n2].get('max_armor', ks[n2]['armor']) <= hi2)
            key = (-risk, sum(_tier(v) for v in scores.values()),
                   round(min(scores.values()), 2), round(sum(scores.values()), 2), -cost)
            if best is None or key > best[0]:
                best = (key, split, [(c[1], c[2]) for c in combo], cost, scores, flat, best_meal)
    if not best:
        print('aucune repartition possible')
        return
    _, split, choices, cost, scores, flat, meal_target = best
    print('budget %s or | repas %s | montees en attente %s\n' % (budget, 'oui' if meals_on else 'non', PEND or '-'))
    for qid, team in zip(qids, split):
        q = Q[qid][0]
        s = scores[qid]
        oc_lbl = ('CRITIQUE' if s >= 10 else 'GRANDE' if s > 5 else 'reussite' if s > 0 else 'ECHEC')
        lo, hi = dmg_for(q, {'CRITIQUE': 'REUSSITE CRITIQUE', 'GRANDE': 'GRANDE REUSSITE',
                             'reussite': 'REUSSITE'}.get(oc_lbl, 'ECHEC'))
        manque = q['nb_knights'] - len(team)
        print('%-42s %5.2f %-9s degats %d-%d%s' % (st.tr(q['name_key']) or qid, s, oc_lbl, lo, hi,
              '  (%d chevalier(s) en moins : exigences +%d)' % (manque, manque) if manque > 0 else ''))
        for n in team:
            it, lv = choices[flat.index((n, qid))]
            bits = []
            if it:
                bits.append('%s (%s or, %s)' % (it, avail[it][0], avail[it][1]))
            if lv:
                bits.append('niveau +%s' % '/'.join(lv))
            armure = ks[n]['armor']
            mx = ks[n].get('max_armor', armure)
            warn = ''
            if armure <= hi:
                warn = ('  !! REPARER l armure a la forge (%d -> %d, ~15 or)' % (armure, mx)) if mx > hi                        else ('  !! MORT possible (armure %d vs degats %d)' % (armure, hi))
            print('    %-10s %s%s' % (n, ', '.join(bits) or '-', warn))
    if meal_target:
        team_m = split[qids.index(meal_target)]
        who = min(team_m, key=lambda n: ks[n]['affinity'])
        print('REPAS -> %s (sur %s) : le +0,5 fait basculer le palier'
              % (who, st.tr(Q[meal_target][0]['name_key']) or meal_target))
    elif meals_on:
        print('REPAS : aucun palier ne bascule -> au chevalier a l affinite la plus basse')
    print('\ncout total : %s or' % cost)
    print('non assignes : %s' % ', '.join(n for n in names if not any(n in t for t in split)))


def _splits_var(names, size_options):
    """Repartitions possibles, en autorisant des equipes plus petites que demande
    (le jeu l'accepte : chaque manquant ajoute +1 aux exigences)."""
    import itertools
    if not size_options:
        yield []
        return
    for size in size_options[0]:
        if size > len(names):
            continue
        for first in itertools.combinations(names, size):
            rest = [n for n in names if n not in first]
            for tail in _splits_var(rest, size_options[1:]):
                yield [list(first)] + tail

# ---------------------------------------------------------------- LA commande du cycle
def _bare(k, eq):
    """Chevalier depouille de son equipement AMOVIBLE (base commune de comparaison).

    Un objet `exclusive` reste sur lui : l'epee d'Edith et le griffon d'Ari ne se
    retirent pas. Les depouiller aussi libérait leur emplacement, et le
    planificateur conseillait d'acheter une fronde pour Edith -- un achat qu'elle
    ne pourra jamais porter.
    """
    import copy as _c
    k2 = _c.deepcopy(k)
    k2['stats'] = dict(k['stats'])
    k2['tags'] = list(k.get('tags') or [])
    garde = [it for it in (k.get('equip') or []) if (eq.get(it) or {}).get('exclusive')]
    for it in (k.get('equip') or []):
        if it in eq and it not in garde:
            for s, v in (eq[it]['stats'] or {}).items():
                k2['stats'][s] = k2['stats'].get(s, 0) - v
            for t in (eq[it].get('tags') or []):
                if t in k2['tags']:
                    k2['tags'].remove(t)
    k2['equip'] = list(garde)
    return k2


def _dress(bare, items, eq):
    # Un deepcopy trainait ici. Il coutait 60 % du temps de `st.py cycle` (12,6 s sur
    # 20,9) pour rien : les SEULS champs mutables d'un chevalier sont stats, tags et
    # equip, et les trois lignes qui suivent les reconstruisent deja. Le reste
    # (name, level, armor, affinity...) est scalaire, une copie plate suffit.
    k = dict(bare)
    k['stats'] = dict(bare['stats'])
    k['tags'] = list(bare['tags'])
    for it in items:
        for s, v in (eq[it]['stats'] or {}).items():
            k['stats'][s] = k['stats'].get(s, 0) + v
        k['tags'] += [t for t in (eq[it].get('tags') or []) if t]
    # `bare` porte deja l'equipement soude, stats comprises : on ajoute, on n'ecrase pas.
    k['equip'] = list(bare.get('equip') or []) + list(items)
    return k



# Reduction de duree deja SOUDEE sur un chevalier (le griffon d'Ari, -4). `_bare`
# garde ces objets sur leur porteur, donc ils n'apparaissent JAMAIS dans l'etat
# manipule par l'optimiseur -- et team_duration les ignorait. Cycle 18 :
# l'exploratrice restait a 2 cycles parce que le planificateur croyait Ari a pied et
# lui achetait une monture par-dessus un emplacement soude.
_WELDED_RED = {}


def set_welded_reductions(ks):
    """A appeler des que `ks` est connu : renseigne la reduction soudee par chevalier."""
    eq = C('equipment')
    _WELDED_RED.clear()
    for n, k in (ks or {}).items():
        tot = 0
        for it in (k.get('equip') or []):
            v = eq.get(it) or {}
            if v.get('exclusive'):
                tot += v.get('duration_reduction', 0) or 0
        if tot:
            _WELDED_RED[n] = tot
    return _WELDED_RED


def team_duration(q, team, items):
    """Duree REELLE d'une quete. quests_manager.calculate_updated_duration : la
    reduction retenue est celle du chevalier le PLUS LENT de l'equipe, pas la somme.
    Une monture sur trois des quatre equipiers ne sert donc a rien."""
    eq = C('equipment')
    base = max(1, q.get('duration', 1))
    if not team:
        return base
    red = None
    for n in team:
        porte = list(items.get(n) or [])
        k = 0
        seen = set()
        for x in porte:
            if x in seen:
                continue
            seen.add(x)
            k += (eq.get(x) or {}).get('duration_reduction', 0) or 0
        # L'equipement soude n'est pas dans `items` : il faut l'ajouter a la main,
        # sans le compter deux fois s'il y figure quand meme.
        w = _WELDED_RED.get(n, 0)
        if w:
            deja = sum((eq.get(x) or {}).get('duration_reduction', 0) or 0
                       for x in seen if (eq.get(x) or {}).get('exclusive'))
            k += w - deja
        red = k if red is None else min(red, k)
    return max(1, base - (red or 0))


# Bonus PAR CHEVALIER quand une quete longue tombe a un seul cycle. Calibre pour
# dominer un ecart de palier (une reussite critique vaut ~5 points de plus qu'une
# grande reussite) sans jamais rattraper un echec, que le garde-fou de palier exclut
# de toute facon.
ONE_CYCLE_BONUS = 8.0

# DERNIER CRITERE, tres loin derriere tout le reste (regle du user, cycle 20) : a
# palier et duree egaux, preferer l'affectation qui fait GAGNER de l'affinite.
#
# Le gain ne depend PAS d'une preference du chevalier pour la quete -- il n'en existe
# pas. cycle_transition/portrait_container.gd le donne au seul vu du palier obtenu :
#   REUSSITE CRITIQUE  -> +1,5      GRANDE REUSSITE -> +0,75
#   ECHEC MAJEUR       -> -0,5      tout le reste   ->  0
# Il est verse A CHAQUE equipier survivant, et bute sur le plafond de 10 : un
# chevalier deja au maximum ne rapporte rien. Le critere departage donc surtout la
# TAILLE des equipes sur les quetes deja critiques, et favorise les chevaliers encore
# sous le plafond.
AFFINITY_CAP = 10.0
# Calibre SOUS le pas de quantification des scores (st.snap = 0,01) pour ne jamais
# departager autre chose qu'une egalite parfaite.
AFFINITY_TIEBREAK = 0.0002


def _affinity_per_knight(outcome):
    """Affinite versee a chaque equipier pour ce palier.

    On n'utilise PAS _RKf ici : il classe par sous-chaine et « ECHEC MAJEUR » contient
    « ECHEC », donc il rend le rang de l'echec simple. Sans consequence pour ses autres
    usages (des comparaisons >= 3), fatal pour un bareme qui distingue les deux.
    """
    base = (outcome or '').split('(')[0].upper()
    if 'INATTENDUE' in base:
        return 0.0                      # palier reel inconnu a ce stade
    if 'ECHEC' in base:
        return -0.5 if 'MAJEUR' in base else 0.0
    if 'CRITIQUE' in base:
        return 1.5
    if 'GRANDE' in base:
        return 0.75
    return 0.0


def affinity_gain(team, outcome, ks):
    """Affinite REELLEMENT gagnee par l'equipe, plafond de 10 par chevalier compris."""
    per = _affinity_per_knight(outcome)
    if not per:
        return 0.0
    tot = 0.0
    for n in team:
        cur = float((ks.get(n) or {}).get('affinity', AFFINITY_CAP))
        tot += min(AFFINITY_CAP, max(-AFFINITY_CAP, cur + per)) - cur
    return tot

_RK = {'ECHEC CRITIQUE': 0, 'ECHEC MAJEUR': 1, 'ECHEC': 2,
       'REUSSITE': 3, 'GRANDE REUSSITE': 4, 'REUSSITE CRITIQUE': 5,
       'ISSUE INATTENDUE': 6}.get


def _RKf(o):
    base = (o or '').split('(')[0].upper()
    return max([v for k, v in {'ECHEC CRITIQUE': 0, 'ECHEC MAJEUR': 1, 'ECHEC': 2,
                               'REUSSITE': 3, 'GRANDE REUSSITE': 4,
                               'REUSSITE CRITIQUE': 5, 'ISSUE INATTENDUE': 6}.items()
                if k in base] or [3])


def optimise_cycle(assign, ks, oc, avail, gold, meals, pend, mc_n=1500, _no_conso=False):
    """Repartit objets, montees de niveau et repas sur une affectation DEJA fixee.

    Corrige quatre erreurs commises a la main :
      - un objet ne peut servir qu'a UN chevalier (les equipes se les disputaient)
      - NE RIEN PORTER est une option : un objet peut etre un malus
        (epee d'argent FOR-1 sur une quete sans magie)
      - les trois emplacements comptent (relique + monture + consommable)
      - les montees de niveau se calculent sur la quete REELLEMENT assignee,
        pas sur celle ou le chevalier etait candidat
    """
    import itertools
    eq = C('equipment')
    STATS = ['STRENGTH', 'AGILITY', 'CHARISMA', 'MAGIC', 'WITS', 'LUCK']
    used_kn = [n for _q, team in assign for n in team]
    set_welded_reductions(ks)
    bare = {n: _bare(ks[n], eq) for n in ks}
    # OBJETS UNIQUES : impossibles a racheter une fois depenses. Regle du user, posee au
    # cycle 30 : ne JAMAIS les proposer, sauf s'ils declenchent une issue inattendue.
    # La Potion de souffle enflamme figure pourtant dans le stock acte 3 de la tour de
    # la sorciere avec `req: null` -- le cache se trompe, l'observation en jeu fait foi.
    # Un objet `exclusive` (le griffon d'Ari, Dainsleif sur Edith) ne se retire pas et
    # ne se pose pas sur quelqu'un d'autre. Il figurait pourtant dans `avail` a cout 0
    # (« porte par ari ») et l'etape montures le distribuait au premier venu : le
    # candidat etait rejete au scoring, et aucune autre combinaison n'etait tentee.
    # On le sort du vivier -- son porteur le garde de toute facon via `_bare`.
    pool = [n for n in avail if n in eq and avail[n][0] <= gold and n not in UNIQUE_ITEMS
            and not (eq[n].get('exclusive'))]
    slot = {}
    for n in pool:
        slot.setdefault(_slot_of(eq[n]), []).append(n)

    # Emplacement soude : ce chevalier ne peut RIEN recevoir dessus.
    slot_kn = {n: {sl: [x for x in lst if not _slot_verrouille(ks[n], sl)]
                   for sl, lst in slot.items()} for n in used_kn}
    state = {n: [] for n in used_kn}           # chevalier -> objets
    levels = {n: [] for n in pend if n in used_kn}
    meal = [None]

    # La contribution d'une quete ne depend QUE de son equipe, de l'equipement de
    # cette equipe, de ses montees de niveau et du repas. Or l'optimiseur ne deplace
    # qu'un objet a la fois : sans ce cache, les trois autres quetes du plan etaient
    # integralement recalculees a chaque essai. C'est ce qui faisait 154 000 appels
    # a score_all et 1,5 million d'habillages de chevalier.
    _sa_memo = {}
    # Tout ce qui ne depend que de (quete, equipe) est fige ici : la fiche de quete,
    # sa duree de base, l'effectif, et le fait qu'une recrue aleatoire y figure.
    # Recalcule dans la boucle, ca representait 1,4 million de recherches de quete
    # et 3,6 millions d'appels a is_random pour des reponses toujours identiques.
    _sa_prep = []
    for _qid, _team in assign:
        _qq = MODS.get(_qid) or C('quests')[_qid]
        _sa_prep.append((_qid, _team, tuple(_team),
                         any(is_random(n) for n in _team),
                         _qq, _qq.get('duration', 1), len(_team)))

    def score_all(state, levels, meal_on):
        tot = 0.0
        det = []
        for qid, team, team_t, alea, _q, _base_d, _nb in _sa_prep:
            # Une equipe a recrue aleatoire passe par mc_evaluate, qui tire au sort :
            # la mettre en cache figerait le tirage. On ne la memorise pas.
            memo_key = None
            if not alea:
                # Comprehensions de liste et non generateurs : la clef est
                # construite des millions de fois, et un generateur paie un cadre
                # d'execution par element.
                memo_key = (qid, team_t, meal_on in team,
                            tuple([tuple(sorted(state.get(n) or ())) for n in team]),
                            tuple([tuple(levels.get(n) or ()) for n in team]))
                hit = _sa_memo.get(memo_key)
                if hit is not None:
                    # Termes reappliques UN A UN, dans l'ordre d'origine : une somme
                    # pre-agregee differerait d'un ULP et pourrait retourner une
                    # egalite entre deux plans.
                    tot += hit[0]
                    tot += hit[1]
                    tot += hit[2]
                    tot += hit[3]
                    det.append(hit[4])
                    continue
            kl = []
            for n in team:
                k = _dress(bare[n], state.get(n, []), eq)
                for s in levels.get(n, []):
                    k['stats'][s] = k['stats'].get(s, 0) + 1
                kl.append(k)
            m = 1 if meal_on in team else 0
            if alea:
                mc = mc_evaluate(qid, kl, oc, m, n=mc_n)
                s = mc['mean'] if mc else -99
                o = ' '.join('%s %.0f%%' % (SHORT.get(a, a), b0) for a, b0 in list(mc['probs'].items())[:3]) if mc else '?'
            else:
                r = evaluate(qid, kl, oc, m)
                if r['special']:
                    s, o = 99.0, 'ISSUE INATTENDUE'
                else:
                    s, o = (r['score'] if r['score'] is not None else -99), r['outcome']
            tot += s
            # Un CYCLE GAGNE libere toute l'equipe un tour plus tot : ca pese autant
            # qu'un demi-palier. Sans ca l'optimiseur laissait un equipier sans monture
            # et annulait la reduction des trois autres (cycle 14, Goberto).
            # _q, _base_d et _nb viennent de _sa_prep.
            # Un cycle gagne libere TOUTE l'equipe un tour plus tot : le gain est
            # proportionnel a l'effectif, pas forfaitaire. Le poids fixe de 1,5 sous-evaluait
            # une quete de 3 cycles a 2 chevaliers face a quelques reliques (cycle 11, le
            # remede des sirenes bloquait Ursule et Arron jusqu'au cycle 14).
            _real_d = team_duration(_q, team, state)
            _t_dur = 1.5 * _nb * (_base_d - _real_d)
            tot += _t_dur
            # RAMENER UNE QUETE A UN SEUL CYCLE PASSE AVANT LE SCORE. Regle posee par le
            # user au cycle 18 : l'exploratrice (duree 3, 3 slots) partait avec trois
            # montures a -1 pour 2 cycles, alors que trois montures a -2 -- toutes
            # gratuites -- la bouclaient en 1. Le poids lineaire seul (1,5 par cycle et
            # par chevalier) ne pesait pas assez face a quelques points de score.
            # Le bonus est conditionne au palier : gagner un cycle en ratant la quete
            # n'est pas un gain, donc rien en dessous de REUSSITE.
            _t_bonus = 0.0
            if _base_d > 1 and _real_d <= 1 and _RKf(o) >= 3:
                _t_bonus = ONE_CYCLE_BONUS * _nb
                tot += _t_bonus
            _t_aff = AFFINITY_TIEBREAK * affinity_gain(team, o, ks)
            tot += _t_aff
            _entry = (qid, team, round(s, 2), o)
            det.append(_entry)
            if memo_key is not None:
                _sa_memo[memo_key] = (s, _t_dur, _t_bonus, _t_aff, _entry)
        return tot, det

    # 1) montees de niveau sur l'affectation reelle
    for n in list(levels):
        best = None
        for combo in itertools.combinations_with_replacement(STATS, pend[n]):
            levels[n] = list(combo)
            t, _d = score_all(state, levels, None)
            if best is None or t > best[0]:
                best = (t, list(combo))
        levels[n] = best[1]

    # 2) objets. Les CONSOMMABLES sont a usage unique : ne les proposer que s'ils
    #    font basculer un palier, jamais pour un gain marginal (regle du repas).
    eqk = C('equipment')
    consumables = {n for n in pool if eqk[n]['kind'] == 'consumable'}

    def tier_of(state_, levels_, meal_):
        _t, d = score_all(state_, levels_, meal_)
        return tuple(x[3] for x in d)

    # 2b) montee gloutonne puis echanges, "rien" toujours candidat
    improved = True
    while improved:
        improved = False
        base, _d = score_all(state, levels, meal[0])
        cands = []
        for n in used_kn:
            for sl, names in slot_kn[n].items():
                cur = [x for x in state[n] if _slot_of(eq[x]) == sl]
                for cand in [None] + names:
                    if cand and any(cand in v for k2, v in state.items() if k2 != n):
                        continue
                    new = [x for x in state[n] if _slot_of(eq[x]) != sl] + ([cand] if cand else [])
                    if sorted(new) == sorted(state[n]):
                        continue
                    cost = sum(avail[x][0] for kk, v in state.items() for x in (new if kk == n else v))
                    if cost > gold:
                        continue
                    old = state[n]
                    state[n] = new
                    t, _d = score_all(state, levels, meal[0])
                    state[n] = old
                    if t > base + 1e-9:
                        cands.append((t, n, new))
        # Un consommable qui ne fait basculer aucun palier est refuse (regle du user).
        # L'ancien code faisait alors `break` et ABANDONNAIT LA BOUCLE ENTIERE : toutes
        # les reliques gratuites encore ameliorables restaient au vestiaire. Comme un
        # consommable donne souvent le plus gros gain brut, il arrivait en tete et
        # coupait l'optimisation des la premiere iteration. Cycle 20 : Angelica, Arron,
        # Rufus et Gideon partaient les mains vides alors que deux reliques a 0 or
        # faisaient passer Milkford de 8,27 a 9,72 (50% de reussite critique).
        # On passe donc au CANDIDAT SUIVANT au lieu de tout arreter.
        cands.sort(key=lambda x: -x[0])
        for _t, n_, new_ in cands:
            added = [x for x in new_ if x in consumables and x not in state[n_]]
            if added:
                before = tier_of(state, levels, meal[0])
                old_ = state[n_]
                state[n_] = new_
                if tier_of(state, levels, meal[0]) == before:
                    state[n_] = old_
                    continue                 # consommable inutile -> candidat suivant
            else:
                state[n_] = new_
            improved = True
            break

    # 2c) elagage final des CONSOMMABLES. La montee gloutonne peut avoir ajoute un
    #     consommable qui faisait basculer un palier A CE MOMENT-LA, puis avoir equipe
    #     une relique qui le rend inutile -- rien ne revenait dessus. On les retire donc
    #     un a un a la fin et on ne garde que ceux dont le retrait fait PERDRE un palier.
    #     (usage unique : `nb_utilisation` vaut 1 par defaut sur Consumable.)
    for n in list(state):
        for x in [y for y in state[n] if y in consumables]:
            before = tier_of(state, levels, meal[0])
            old_ = state[n]
            state[n] = [y for y in old_ if y != x]
            if tier_of(state, levels, meal[0]) == before:
                continue                      # inutile -> on le garde en stock
            state[n] = old_                   # il tenait le palier -> on le laisse

    # 2d) MONTURES : passe a deux coups. La montee gloutonne ne deplace qu'un objet a
    #     la fois et exige une amelioration immediate ; elle ne peut donc jamais prendre
    #     une monture a un chevalier pour la donner a un autre, meme quand l'echange
    #     ramene une quete de 2 cycles a 1. Cycle 10 : Ennba sur Arron ne servait a rien
    #     tant qu'Angelica etait a pied (la reduction retenue est celle du plus lent), et
    #     Paul dormait sur une quete d'un seul cycle. Le user l'a vu avant l'outil.
    mounts = [x for x in pool if _slot_of(eq[x]) == 'monture']
    if mounts:
        moved = True
        while moved:
            moved = False
            base, _d = score_all(state, levels, meal[0])
            best = None
            for qid, team in assign:
                _q = MODS.get(qid) or C('quests')[qid]
                if team_duration(_q, team, state) <= 1:
                    continue          # deja au minimum, rien a gagner
                for n in team:
                    if _slot_occupe(ks[n], state.get(n, []), 'monture'):
                        continue      # deja monte
                    for m in mounts:
                        holder = next((k2 for k2, v in state.items()
                                       if m in v and k2 != n), None)
                        old_n = list(state[n])
                        old_h = list(state[holder]) if holder else None
                        if holder:
                            state[holder] = [y for y in state[holder] if y != m]
                        state[n] = [y for y in state[n]
                                    if _slot_of(eq[y]) != 'monture'] + [m]
                        cost = sum(avail[x][0] for v in state.values() for x in v)
                        t, _d2 = score_all(state, levels, meal[0])
                        state[n] = old_n
                        if holder:
                            state[holder] = old_h
                        if cost <= gold and t > base + 1e-9 and (best is None or t > best[0]):
                            best = (t, n, m, holder)
            if best:
                _t, n_, m_, h_ = best
                if h_:
                    state[h_] = [y for y in state[h_] if y != m_]
                state[n_] = [y for y in state[n_] if _slot_of(eq[y]) != 'monture'] + [m_]
                moved = True

    # 2e) REEQUILIBRAGE DU BUDGET MONTURES. Deux montures reduisent TOUTES la duree
    #     de 1 : payer 175 or pour l'Hirondelle plutot que 45 pour Assil n'achete que
    #     des stats, et vide le budget qui aurait monte une deuxieme equipe. La montee
    #     gloutonne ne le voit pas (degrader une monture fait baisser le score sur le
    #     coup). On teste donc explicitement : degrader une monture chere vers la moins
    #     chere ET monter un equipier a pied d'une quete longue. Cycle 11, le remede
    #     des sirenes restait a 3 cycles faute de 105 or.
    chers = sorted([x for x in pool if _slot_of(eq[x]) == 'monture'],
                   key=lambda x: avail[x][0])[:8]
    if len(chers) > 1:
        changed = True
        while changed:
            changed = False
            base, _d = score_all(state, levels, meal[0])
            best = None
            for qid, team in assign:
                _q = MODS.get(qid) or C('quests')[qid]
                if team_duration(_q, team, state) <= 1:
                    continue
                for n in team:
                    if _slot_occupe(ks[n], state.get(n, []), 'monture'):
                        continue
                    for m_new in chers:
                        if any(m_new in v for v in state.values()):
                            continue
                        for holder in list(state):
                            worn = [x for x in state[holder]
                                    if _slot_of(eq[x]) == 'monture']
                            if not worn or avail[worn[0]][0] <= avail[m_new][0]:
                                continue
                            for m_cheap in chers:
                                if m_cheap == m_new:
                                    continue
                                if any(m_cheap in v for k2, v in state.items()
                                       if k2 != holder):
                                    continue
                                old_h, old_n = list(state[holder]), list(state[n])
                                state[holder] = [y for y in old_h
                                                 if _slot_of(eq[y]) != 'monture'] + [m_cheap]
                                state[n] = [y for y in old_n
                                            if _slot_of(eq[y]) != 'monture'] + [m_new]
                                cost = sum(avail[x][0] for v in state.values() for x in v)
                                t, _d2 = score_all(state, levels, meal[0])
                                state[holder], state[n] = old_h, old_n
                                if cost <= gold and t > base + 1e-9 and (best is None or t > best[0]):
                                    best = (t, holder, m_cheap, n, m_new)
            if best:
                _t, h_, mc_, n_, mn_ = best
                state[h_] = [y for y in state[h_] if _slot_of(eq[y]) != 'monture'] + [mc_]
                state[n_] = [y for y in state[n_] if _slot_of(eq[y]) != 'monture'] + [mn_]
                changed = True
    # 2f) MONTURES : ACHETER PAR LE NOMBRE, PAS PAR LA QUALITE.
    #     Toutes les montures reduisent la duree de 1 : seule compte la QUANTITE, le
    #     prix n'achete que des stats. Et il faut monter TOUTE une equipe d'un coup,
    #     donc un echange chevalier par chevalier est toujours rejete. On enumere donc
    #     les SOUS-ENSEMBLES de quetes longues couvrables integralement, et on garde le
    #     meilleur score reel. Un ordre fixe ne marche pas : monter les 3 de la musique
    #     (45 or) empechait de monter l'expedition ET le remede (105 or, 4 chevaliers-
    #     cycles). Cycle 11.
    # Trier par PRIX seul revenait a toujours prendre les montures gratuites a
    # reduction 1 et a ignorer celles qui reduisent de 2 ou 3. Cycle 22 : le golem
    # (duree 3) restait a 2 cycles avec Hirondelle + Kelpy alors que Gringalet
    # (gratuit, -2) et Sivko-Burko (155 or, -2) le ramenaient a 1. On classe donc
    # par REDUCTION DECROISSANTE, le prix ne departageant qu'a reduction egale.
    all_m = sorted([x for x in pool if _slot_of(eq[x]) == 'monture'],
                   key=lambda x: (-(eq[x].get('duration_reduction') or 0), avail[x][0]))
    longues = [(qid, list(team)) for qid, team in assign
               if (MODS.get(qid) or C('quests')[qid]).get('duration', 1) > 1]
    if all_m and longues:
        base, _d = score_all(state, levels, meal[0])
        old = {n: list(v) for n, v in state.items()}
        hors_m = sum(avail[x][0] for v in old.values() for x in v
                     if _slot_of(eq[x]) != 'monture')
        best = None
        for r in range(1, len(longues) + 1):
            for sub in itertools.combinations(longues, r):
                # Un chevalier dont l'emplacement monture est SOUDE ne peut rien
                # recevoir, et porte deja sa reduction : le compter dans le besoin
                # consommait une monture pour rien et faisait echouer la couverture.
                besoin = [n for _q, t in sub for n in t
                          if not _slot_verrouille(ks[n], 'monture')]
                if not besoin:
                    continue
                if len(besoin) > len(all_m):
                    continue
                pick = all_m[:len(besoin)]
                # NE PAS dépouiller tout le monde : l'ancienne version retirait sa
                # monture a CHAQUE chevalier, y compris a ceux des quetes d'un cycle qui
                # portaient une monture a stats (Agro +2 FOR sur Rufus, Ponzi sur
                # Gwendan). Leurs quetes perdaient des points, le total baissait, et le
                # gain d'un cycle entier sur la quete longue etait rejete. Cycle 22.
                # On ne libere une monture que si elle fait partie de `pick`.
                for n in state:
                    state[n] = [y for y in old[n]
                                if _slot_of(eq[y]) != 'monture'
                                or (n not in besoin and y not in pick)]
                for n, m in zip(besoin, pick):
                    state[n] = state[n] + [m]
                if sum(avail[x][0] for v in state.values() for x in v) > gold:
                    continue
                t, _d2 = score_all(state, levels, meal[0])
                # Comparer les SCORES BRUTS masquait le gain : retirer une monture a stats
                # d'une quete d'un cycle lui fait perdre plusieurs points alors qu'elle
                # reste au meme palier, et ca ecrasait le cycle gagne sur la quete longue.
                # Cycle 31 : Pegase (-3) dormait sur le dragon avec deux equipiers a -1,
                # pendant que quatre montures a -2 trainaient sur des quetes d'un cycle.
                # On accepte donc des qu'AUCUN PALIER ne recule et que la duree totale baisse.
                cand_t = tuple(x[3] for x in _d2)
                cand_d = sum(team_duration(MODS.get(q) or C('quests')[q], tm, state)
                             for q, tm in assign)
                if best is None or (cand_d, -t) < (best[2], -best[0]):
                    best = (t, {n: list(v) for n, v in state.items()}, cand_d, cand_t)
        for n in old:
            state[n] = old[n]
        base_t = tuple(x[3] for x in _d)
        base_d = sum(team_duration(MODS.get(q) or C('quests')[q], tm, state)
                     for q, tm in assign)
        _rk = {'ECHEC CRITIQUE': 0, 'ECHEC MAJEUR': 1, 'ECHEC': 2,
               'REUSSITE': 3, 'GRANDE REUSSITE': 4, 'REUSSITE CRITIQUE': 5}
        def _r(o):
            b = (o or '').split('(')[0].upper()
            return 6 if 'INATTENDUE' in b else max([v for k, v in _rk.items() if k in b] or [3])
        if best and (best[0] > base + 1e-9
                     or (best[2] < base_d
                         and all(_r(a) >= _r(b) for a, b in zip(best[3], base_t)))):
            for n, v in best[1].items():
                state[n] = v
    # 2g) NE PAS PAYER CE QUI NE CHANGE RIEN. L'optimiseur maximise le score BRUT,
    #     mais les recompenses d'une quete sont les memes a tous les paliers de reussite
    #     et les degats ne dependent que du palier : au-dela de « critique », un point de
    #     score de plus ne vaut rien. Cycle 24 : le plan achetait 470 or pour passer de
    #     23,27 a 24,45, deux fois la meme reussite critique. On retire donc tout objet
    #     PAYANT dont le retrait ne coute ni un palier ni un cycle -- meme regle que pour
    #     les consommables, etendue a l'or.
    def _durs(state_):
        return tuple(team_duration(MODS.get(q) or C('quests')[q], t, state_)
                     for q, t in assign)
    #     Retirer ne suffit pas : Trojan a 185 or etait garde parce qu'Arron avait besoin
    #     d'UNE monture pour que la reduction de duree s'applique -- n'importe laquelle,
    #     y compris gratuite. On teste donc aussi le REMPLACEMENT par un objet a 0 or du
    #     meme emplacement, et on ne paie que si rien de gratuit ne tient le palier.
    for n in list(state):
        for x in [y for y in state[n] if avail[y][0] > 0]:
            before_t = tier_of(state, levels, meal[0])
            before_d = _durs(state)
            old_ = state[n]
            sl = _slot_of(eq[x])
            porte = {y for v in state.values() for y in v}
            # On ne testait que les remplacements GRATUITS : Pegase a 350 or restait en
            # place alors que Le Duc a 325 tenait le meme palier, la meme duree, et
            # marquait meme mieux. On accepte donc tout objet STRICTEMENT MOINS CHER
            # du meme emplacement, du moins cher au plus cher.
            cands = [None] + sorted([z for z in pool
                                     if avail[z][0] < avail[x][0] and z not in porte
                                     and _slot_of(eq[z]) == sl],
                                    key=lambda z: (avail[z][0],
                                                   -sum((eq[z].get('stats') or {}).values())))
            for repl in cands:
                state[n] = [y for y in old_ if y != x] + ([repl] if repl else [])
                if (tier_of(state, levels, meal[0]) == before_t
                        and _durs(state) == before_d):
                    break                     # gratuit et equivalent -> on garde l'or
            else:
                state[n] = old_               # rien de gratuit ne tient : on paie

    # 3) repas : la ou il fait basculer un palier
    if meals:
        base, det0 = score_all(state, levels, None)
        # On compare des PALIERS, pas des libelles : `d[3]` porte aussi les
        # pourcentages ('critique 66% grande 33%'), qui bougent au moindre demi-point.
        # Le repas partait donc sur le premier chevalier venu -- Gwendan, deja a 10,0
        # d'affinite -- alors qu'il ne faisait basculer aucun palier (cycle 14).
        _rank = {'ECHEC CRITIQUE': 0, 'ECHEC MAJEUR': 1, 'ECHEC': 2,
                 'REUSSITE': 3, 'GRANDE REUSSITE': 4, 'REUSSITE CRITIQUE': 5}
        def _tier(o):
            o = (o or '').lower()
            for k in ('critique', 'grande', 'echec majeur', 'echec critique', 'echec', 'reussite'):
                if k in o:
                    return k
            return o
        for n in used_kn:
            t, det = score_all(state, levels, n)
            if any(_tier(d[3]) != _tier(d0[3]) for d, d0 in zip(det, det0)):
                meal[0] = n
                break
        # Aucun palier gagne : PAS de repas conseille. Le repli « sers le chevalier
        # dont l'affinite est la plus basse » existait ici, mais le user l'a retire
        # le 2026-09-07 : il veut un conseil qui sert la QUETE, pas une suggestion
        # d'affinite melee aux autres. Servir un plat pour l'affinite reste evidemment
        # une bonne idee -- c'est juste sa decision, pas celle du planificateur.

    # 3b) ELAGAGE FINAL, REPAS CONNU. Les passes 2c et 2g tournent AVANT le choix du
    #     repas : un consommable que le repas rend superflu leur echappait, et l'etape 4
    #     ne compare que « tous les consommables » contre « aucun », donc un seul objet
    #     utile suffisait a garder les inutiles avec lui. Cycle 28 : le Reblochon de
    #     Childeric ne changeait rien (le renegat restait critique a 10,28) mais etait
    #     conserve parce que la Potion de mana bleue, elle, tenait un palier ailleurs.
    #     On repasse donc objet par objet, une fois le repas fixe.
    for n in list(state):
        for x in list(state[n]):
            before_t = tier_of(state, levels, meal[0])
            before_d = _durs(state)
            old_ = state[n]
            state[n] = [y for y in old_ if y != x]
            if (tier_of(state, levels, meal[0]) == before_t
                    and _durs(state) == before_d):
                continue                      # inutile : on le garde en stock
            state[n] = old_

    # 3b) ELAGAGE FINAL, LE REPAS ETANT CONNU. Les passes 2c et 2g tournent AVANT le
    #     choix du repas : un objet que le repas rend superflu y survivait. Et l'etape 4
     #    ne compare que « tous les consommables » contre « aucun », donc un consommable
    #     inutile reste des qu'un AUTRE est indispensable. Cycle 28 : le Reblochon de
    #     Childeric etait garde alors que le renegat tenait la critique sans lui, juste
    #     parce que la potion de mana bleue etait necessaire aux inondations.
    for n in list(state):
        for x in [y for y in state[n]
                  if eq[y]['kind'] == 'consumable' or avail[y][0] > 0]:
            before_t = tier_of(state, levels, meal[0])
            before_d = _durs(state)
            old_ = state[n]
            state[n] = [y for y in old_ if y != x]
            if tier_of(state, levels, meal[0]) == before_t and _durs(state) == before_d:
                continue                      # ni palier ni cycle perdu -> on le garde en stock
            state[n] = old_

    # 3a) OBJETS UNIQUES : on ne les ressort que s'ils ouvrent une ISSUE INATTENDUE,
    #     jamais pour un gain de score ou meme un palier ordinaire.
    for u in sorted(UNIQUE_ITEMS):
        if u in BANNED_ITEMS:
            continue
        if u not in avail or u not in eq or avail[u][0] > gold:
            continue
        if any(u in v for v in state.values()):
            continue
        for n in used_kn:
            if _slot_verrouille(ks[n], _slot_of(eq[u])):
                continue
            old_ = state[n]
            state[n] = [y for y in old_ if _slot_of(eq[y]) != _slot_of(eq[u])] + [u]
            gagne = False
            for qid, team in assign:
                if n not in team:
                    continue
                kl = []
                for m in team:
                    k = _dress(bare[m], state.get(m, []), eq)
                    for st_ in levels.get(m, []):
                        k['stats'][st_] = k['stats'].get(st_, 0) + 1
                    kl.append(k)
                if evaluate(qid, kl, oc, 1 if meal[0] in team else 0)['special']:
                    gagne = True
            if not gagne:
                state[n] = old_

    # 3b) ELAGAGE FINAL, une fois le REPAS connu. Les passes 2c et 2g tournent avant le
    #     choix du repas : un consommable que le repas rend superflu leur echappait, et
    #     l'etape 4 ne compare que « tous les consommables » contre « aucun », donc elle
    #     gardait l'inutile des qu'un seul etait necessaire. Cycle 30 : le Chocolat au
    #     gingembre faisait passer Enberg de 10,27 a 10,57 -- deux fois reussite critique.
    for n in list(state):
        for x in list(state[n]):
            before_t = tier_of(state, levels, meal[0])
            before_d = _durs(state)
            old_ = state[n]
            state[n] = [y for y in old_ if y != x]
            if (tier_of(state, levels, meal[0]) == before_t
                    and _durs(state) == before_d):
                continue                      # ne tenait ni palier ni cycle -> on le garde en stock
            state[n] = old_

    # 3c) REMPLIR LES EMPLACEMENTS VIDES AVEC CE QU ON POSSEDE DEJA.
    #     Le user l'a releve quatre fois. Toutes les passes precedentes raisonnent en
    #     PALIER ou en CYCLE : un objet gratuit qui ne fait basculer ni l'un ni l'autre
    #     etait systematiquement laisse a l'ecurie, et le tableau affichait « rien ».
    #     Cycle 36, Debroussaillage : Goberto partait nu alors qu'Hirondelle + Dague
    #     d'assassin le faisaient passer de 5,65 a 7,42 pour 0 or. Meme palier, certes,
    #     mais un point de degat en moins et aucune raison de s'en priver.
    #     On ne prend que du GRATUIT (rien a payer qui ne change pas de palier, regle du
    #     user), on n'allonge jamais une duree et on ne baisse jamais un palier.
    _libres = [x for x in avail
               if avail[x][0] == 0 and (eq.get(x) or {}).get('kind') in ('relic', 'mount')]
    _pris = {x for v in state.values() for x in v}
    for n in list(state):
        for slot in ('relique', 'monture'):
            if _slot_occupe(ks[n], state[n], slot):
                continue
            b_t, b_d = tier_of(state, levels, meal[0]), _durs(state)
            b_tot = score_all(state, levels, meal[0])[0]
            keep = None
            for x in _libres:
                if x in _pris or _slot_of(eq[x]) != slot:
                    continue
                state[n] = state[n] + [x]
                t2, d2 = tier_of(state, levels, meal[0]), _durs(state)
                tot2 = score_all(state, levels, meal[0])[0]
                state[n] = [y for y in state[n] if y != x]
                if (all(c <= d for c, d in zip(d2, b_d))
                        and all(_RKf(c) >= _RKf(d) for c, d in zip(t2, b_t))
                        and tot2 > b_tot + 1e-9
                        and (keep is None or tot2 > keep[0])):
                    keep = (tot2, x)
            if keep:
                state[n] = state[n] + [keep[1]]
                _pris.add(keep[1])

    # 2h) REPARATION FINALE DES DUREES : L'EQUIPE ENTIERE D'UN COUP.
    #     La reduction retenue est celle du chevalier le PLUS LENT : monter un seul
    #     equipier ne gagne donc RIEN, et toutes les passes gloutonnes -- qui n'acceptent
    #     que ce qui ameliore immediatement -- sont structurellement aveugles a ce cas.
    #     2f essayait bien par sous-ensembles, mais en prenant les montures aux equipiers
    #     des quetes courtes il les laissait A PIED : leur propre quete rallongeait et le
    #     candidat etait rejete. Cycle 28 : le port de Pince (base 2) portait Bayard ET
    #     Sivko-Burko -- deux fois la reduction utile -- pendant que Naoned (base 3)
    #     restait a 2 cycles avec deux montures -1 et qu'un Cheval Noir dormait en stock.
    #     Ici on habille TOUTE l'equipe d'une quete longue en un seul mouvement, et on
    #     RECHANGE : celui qui cede sa monture recoit celle du chevalier servi.
    def _red_de(n, st_):
        r_ = _WELDED_RED.get(n, 0)
        for y in st_.get(n) or []:
            v = eq.get(y) or {}
            if not v.get('exclusive'):
                r_ += v.get('duration_reduction', 0) or 0
        return r_

    _montures = [x for x in pool if _slot_of(eq[x]) == 'monture']
    for _qid, _team in assign:
        _q = MODS.get(_qid) or C('quests')[_qid]
        _base = _q.get('duration', 1)
        if _base <= 1 or team_duration(_q, _team, state) <= 1:
            continue
        _need = _base - 1
        _manque = [n for n in _team
                   if not _slot_verrouille(ks[n], 'monture') and _red_de(n, state) < _need]
        if not _manque or len(_manque) < len([n for n in _team if _red_de(n, state) < _need]):
            continue          # un equipier soude bloque la reduction : rien a faire
        _cands = [x for x in _montures
                  if (eq[x].get('duration_reduction') or 0) >= _need]
        if len(_cands) < len(_manque):
            continue
        _b_sum = sum(_durs(state))
        _base_state = {k2: list(v) for k2, v in state.items()}
        # LA DUREE PRIME SUR LE PALIER (regle du user, posee deux fois). Exiger qu'aucun
        # palier ne recule -- la regle des autres passes -- rejetait justement l'echange
        # utile : rendre Bayard au port de Pince contre une monture -1 lui faisait perdre
        # la critique pour quelques dixiemes, et Naoned restait bloque a 2 cycles.
        # On n'interdit donc plus que de DESCENDRE SOUS LA REUSSITE, et parmi les
        # echanges valides on garde celui qui abime le moins le score.
        _best = None
        for _combo in itertools.permutations(_cands, len(_manque)):
            for k2, v in _base_state.items():
                state[k2] = list(v)
            _ok = True
            for _n, _m in zip(_manque, _combo):
                _holder = next((k2 for k2, v in state.items() if _m in v and k2 != _n), None)
                if _holder is not None and _slot_verrouille(ks[_holder], 'monture'):
                    _ok = False
                    break
                _porte = [y for y in state[_n] if _slot_of(eq[y]) == 'monture']
                state[_n] = [y for y in state[_n] if _slot_of(eq[y]) != 'monture'] + [_m]
                if _holder is not None:
                    state[_holder] = [y for y in state[_holder] if y != _m] + _porte
            if not _ok:
                continue
            if sum(avail[x][0] for v in state.values() for x in v) > gold:
                continue
            _d2 = sum(_durs(state))
            if _d2 >= _b_sum:
                continue
            _t2 = tier_of(state, levels, meal[0])
            if any(_RKf(x) < 3 for x in _t2):
                continue                      # jamais au prix d'un echec
            _tot2 = score_all(state, levels, meal[0])[0]
            if _best is None or (_d2, -_tot2) < (_best[0], -_best[1]):
                _best = (_d2, _tot2, {k2: list(v) for k2, v in state.items()})
        for k2, v in (_best[2] if _best else _base_state).items():
            state[k2] = v

    tot, det = score_all(state, levels, meal[0])
    spend = sum(avail[x][0] for v in state.values() for x in v)
    # Garde-fou : un objet ne peut pas etre porte par deux chevaliers. Le tableau du
    # cycle 31 donnait Gringalet ET Ponzi a deux personnes chacun, parce que deux quetes
    # avaient ete optimisees separement. Mieux vaut hurler que livrer un plan injouable.
    _tous = [x for v in state.values() for x in v]
    _dbl = sorted({x for x in _tous if _tous.count(x) > 1})
    if _dbl:
        print('!! OBJET ATTRIBUE DEUX FOIS -- PLAN INJOUABLE : %s' % ', '.join(_dbl))
    out = {'items': state, 'levels': levels, 'meal': meal[0],
           'detail': det, 'total': round(tot, 2), 'spend': spend}

    # 4) Les consommables sont a USAGE UNIQUE. L'elagage objet-par-objet ne suffit pas :
    #    retirer un consommable peut faire perdre un palier alors qu'une AUTRE
    #    repartition des reliques et des montees l'atteint sans lui. On refait donc
    #    tout le calcul sans aucun consommable, et on ne les depense que si les paliers
    #    obtenus sont reellement inferieurs.
    if not _no_conso and any(eq[x]['kind'] == 'consumable' for v in state.values() for x in v):
        alt = optimise_cycle(assign, ks, oc,
                             {k: v for k, v in avail.items()
                              if k not in eq or eq[k]['kind'] != 'consumable'},
                             gold, meals, pend, mc_n, _no_conso=True)
        # Comparaison de PLANS ENTIERS. Greffer la version sans consommable quete par
        # quete est faux : le repas est unique et global, la greffe le laissait sur
        # l'autre quete et faisait perdre le palier qu'elle etait censee preserver.
        # On garde les consommables en stock des que la version sans eux tient des
        # paliers EQUIVALENTS OU MEILLEURS -- pas seulement identiques : une egalite
        # stricte rejetait des plans pourtant superieurs (cycle 11).
        rank = {'ECHEC CRITIQUE': 0, 'ECHEC MAJEUR': 1, 'ECHEC': 2,
                'REUSSITE': 3, 'GRANDE REUSSITE': 4, 'REUSSITE CRITIQUE': 5}

        def rk(o):
            # On classe sur le PALIER SEUL. `o` traine des annotations -- pourcentages,
            # « (fortune : 50% -> reussite critique) » -- et le `k in o` d'avant y trouvait
            # 'REUSSITE CRITIQUE', gonflait le rang de la version avec consommable et la
            # faisait gagner. Resultat : des consommables brules pour rien (cycle 9 : trois
            # grandes reussites des deux cotes). Le user l'a signale deux fois.
            base = (o or '').split('(')[0].upper()
            return max([v for k, v in rank.items() if k in base] or [3])
        if all(rk(a[3]) >= rk(b[3]) for a, b in zip(alt['detail'], det)):
            alt['saved_consumables'] = sorted({x for v in state.values() for x in v
                                               if eq[x]['kind'] == 'consumable'})
            return alt
        out['alt_no_consumable'] = alt      # paliers differents : a arbitrer, pas a imposer
    return out


def _plats_de(e, nom):
    """Plats aimes du chevalier a nourrir, dans son etat courant.

    L'etat compte : Arron passe en Violent apres son rituel et `give_meal` lit alors
    `violent_liked_meals` -- lui servir une crepe ne rapporte plus rien.
    """
    if not nom:
        return []
    try:
        mm = C('meals')
    except Exception:
        return []
    etat = ((e.get('roundtable_knights') or {}).get(nom) or {}).get('current_state')
    if etat is None:
        for kk, vv in (e.get('roundtable_knights') or {}).items():
            if st._nm(kk) == nom:
                etat = vv.get('current_state')
                break
    return list(mm.get('%s@%s' % (nom, etat)) or mm.get(nom) or [])


def _default_json_path():
    """user://sovereign_mod/plan.json vu du jeu."""
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'Godot', 'app_userdata', 'Sovereign Tower (VS)',
                        'sovereign_mod', 'plan.json')


def _stats_hors_equipement(ks, eq):
    """knights_from_save() a fondu les stats de l'equipement dans k['stats'].
    On les retire, pour obtenir une base comparable a
    `get_statistic_value_from_id(id, false)` cote jeu."""
    out = {}
    for n, k in (ks or {}).items():
        base = dict(k.get('stats') or {})
        for it in (k.get('equip') or []):
            for nom, v in ((eq.get(it) or {}).get('stats') or {}).items():
                base[nom] = base.get(nom, 0) - v
        out[n] = base
    return out


def _export_json(path, e, res, quests, eq, idle, warn, slot, avail=None, ks=None):
    """Ecrit le plan sous forme machine, pour le mod en jeu.

    Le mod n'applique que `assignments` et `equipment` (relique + monture) : ce sont
    les deux seules choses gratuites et localisees a la table ronde. Le repas et les
    achats coutent de l'or et se font dans d'autres salles -- ils restent informatifs.

    Les objets sont identifies par leur CHEMIN de ressource : le champ `name` du cache
    n'est pas toujours la cle d'enum que le jeu compare dans get_string_from_id().
    """
    assignments = []
    for qid, team, sc, out in res['detail']:
        q = MODS.get(qid) or quests[qid]
        assignments.append({
            'quest_id': qid,
            'quest_path': q.get('path'),
            'quest_name': st.tr(q['name_key']) or qid,
            'knights': list(team),
            'score': round(sc, 2),
            'outcome': out,
            'duration': team_duration(q, team, res['items']),
        })

    avail = avail or {}
    equipment, achats = {}, []
    for n, items in (res['items'] or {}).items():
        rows = []
        for it in (items or []):
            info = eq.get(it) or {}
            # avail donne le cout REEL dans l'etat de la partie : 0 si le joueur
            # possede deja l'objet, son prix s'il reste a acheter en boutique.
            cout, source = (avail.get(it) or (0, None))[:2]
            rows.append({'id': it, 'kind': info.get('kind'),
                         'path': info.get('path'),
                         'name': st.tr(it) or it,
                         'cost': cout, 'owned': not cout})
            if cout:
                # `path` sert au mod : la sauvegarde retarde sur le jeu, il doit
                # pouvoir verifier en memoire vive si l'objet est deja achete.
                achats.append({'id': it, 'name': st.tr(it) or it, 'kind': info.get('kind'),
                               'path': info.get('path'),
                               'cost': cout, 'source': source, 'for': n})
        if rows:
            equipment[n] = rows
    achats.sort(key=lambda x: -x['cost'])

    doc = {
        'schema': 1,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'slot': slot,
        'cycle': (e.get('cycle_index') or 0) + 1,
        'assignments': assignments,
        'equipment': equipment,
        'levels': {n: list(v) for n, v in (res['levels'] or {}).items() if v},
        # Stats de chaque chevalier HORS equipement, telles que la sauvegarde les
        # donne. Le mod compare avec les stats vivantes : si le joueur a deja depense
        # son point, la ligne « Level up X » disparait au lieu de rester affichee.
        'stats_base': _stats_hors_equipement(ks or {}, eq),
        'achats': achats,
        'meal': res['meal'],
        'meal_plats': _plats_de(e, res['meal']),
        'spend': res['spend'],
        'idle': list(idle),
        'warnings': list(warn),
    }
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    # Ecriture ATOMIQUE : le mod scrute l'apparition du fichier pour savoir que
    # le calcul est fini. Ecrit en place, il pourrait le lire a moitie ecrit.
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print('json    : %s (%d quete(s), %d chevalier(s) equipe(s))'
          % (path, len(assignments), len(equipment)))


def cmd_cycle(argv):
    eq = C('equipment')
    """st.py cycle [slot] [--meals] : le tableau recapitulatif complet du cycle."""
    slot, force_meals, extra, pin, force_rooms = 1, None, [], [], []
    force_gold = [None]
    no_conso = [False]
    fast_mounts = [False]
    skip = []
    json_out = [None]
    for a in argv:
        if a.startswith('--force='):
            FORCED.update(x for x in a.split('=', 1)[1].split(',') if x)
        elif a == '--montures-rapides':
            # Le user l'a redit deux fois (cycles 26 et 34) : sur une quete longue, ce
            # qui compte est la reduction MINIMALE de l'equipe -- une seule monture -1
            # dans le lot annule les autres. La passe montures raisonne sur la duree
            # TOTALE du cycle et accepte volontiers un Trojan (-1) qui bloque une quete
            # a 2 cycles. Ce drapeau retire purement les montures < -2 du pool : cycle 34,
            # « Les Haut-Chevaliers » passait de 2 cycles a 1 pour le meme palier.
            fast_mounts[0] = True
        elif a.startswith('--skip='):
            # Le joueur ecarte une quete a la main (cycle 32 : « Les Haut-Chevaliers »,
            # 3 cycles de base et pas assez de montures -2 pour la ramener a 1 -- elle
            # immobilisait la moitie de la table sans deadline pour le justifier).
            # Sans ce filtre il fallait epingler toutes les AUTRES quetes une par une.
            skip += [x for x in a.split('=', 1)[1].split(',') if x]
        elif a == '--meals':
            force_meals = True
        elif a == '--no-meals':
            force_meals = False
        elif a.startswith('--room='):
            # la sauvegarde retarde sur le jeu : une salle ouverte PENDANT le cycle
            # n'apparait pas encore dans `unlocked_rooms`. --room=stables la force.
            force_rooms.append(a.split('=', 1)[1])
        elif a.startswith('='):
            # =qid:chevalier,chevalier -> fige une affectation (arbitrage manuel)
            pin.append(a[1:])
        elif a.startswith('+'):
            # +qid ou +qid@N : injecte une quete tout juste prise a l'audience, que la
            # sauvegarde ne connait pas encore (elle est ecrite en fin de cycle).
            # @N applique le Nieme modificateur (la « version » choisie a l'audience).
            extra.append(a[1:])
        elif a == '--no-conso':
            # Regle du user, redite plusieurs fois : un consommable ne se depense que
            # s'il fait basculer un palier QUI COMPTE. Les recompenses d'une quete sont
            # les memes a tous les paliers de reussite, seuls les degats changent : bruler
            # 5 consommables pour passer reussite -> grande sur l'invasion de loups ne
            # valait rien. --no-conso les interdit d'office.
            no_conso[0] = True
        elif a.startswith('--or='):
            # La sauvegarde retarde sur le jeu : elle est ecrite avant les achats et les
            # depenses d'audience. Cycle 11 : elle annoncait 438 or, le joueur en avait 88,
            # et le plan achetait 385 or d'equipement qui n'existait pas. --or=N impose le
            # VRAI montant. A demander au joueur des que le plan depense beaucoup.
            force_gold[0] = int(a.split('=', 1)[1])
        elif a == '--json' or a.startswith('--json='):
            # Sortie machine pour le mod en jeu (bouton « repartition ideale »).
            # Purement additif : la sortie texte ci-dessous est inchangee.
            json_out[0] = a.split('=', 1)[1] if '=' in a else _default_json_path()
        elif a.isdigit():
            slot = int(a)
    ks, r, e = st.knights_from_save(slot)
    set_welded_reductions(ks)
    apply_save_modifiers(r, e)
    oc = save_modifier_outcomes(r, e, build_outcomes())
    quests = C('quests')
    busy = st.busy_knights(e)
    free = {n: k for n, k in ks.items() if n not in busy}
    PEND.clear(); PEND.update({n: v for n, v in pending_levels(e).items() if n in free})
    if force_rooms:
        e = dict(e)
        e['unlocked_rooms'] = list(e.get('unlocked_rooms') or []) + force_rooms
    avail = available_items(e, r)
    if fast_mounts[0]:
        _eq = C('equipment')
        avail = {k: v for k, v in avail.items()
                 if (_eq.get(k) or {}).get('kind') != 'mount'
                 or (_eq[k].get('duration_reduction') or 0) >= 2}
    # plancher d'or : la condition MIN_FUNDS d'un ultimatum en cours doit rester tenable
    u = ultimatum_extra_conditions(r, e)
    if u:
        for txt, _ok in u['list']:
            if txt.startswith('or >='):
                try:
                    GOLD_FLOOR[0] = max(GOLD_FLOOR[0], int(txt.split('>=')[1].split('(')[0].strip()))
                except Exception:
                    pass
    gold = max(0, (force_gold[0] if force_gold[0] is not None
                   else (e.get('current_funds') or 0)) - GOLD_FLOOR[0])
    meals = 'kitchen' in set(st._nm(x) for x in (e.get('unlocked_rooms') or []))
    if force_meals is not None:
        meals = force_meals
    by_file = {}
    for qid, q in quests.items():
        by_file.setdefault(q['path'].split('/')[-1].replace('.tres', ''), qid)
    current = []
    for k in (e.get('current_quests') or {}):
        n = st._nm(k)
        qid = n if n in quests else by_file.get(n)
        if not qid:
            print('!! QUETE INTROUVABLE dans le cache : %s -- relancer `st.py build`. '
                  'Elle NE FIGURE PAS dans le plan ci-dessous.' % n)
        if qid in skip or n in skip:
            continue
        if qid:
            current.append(qid)
            # `remaining_cycles_before_faillure` vaut 1 par DEFAUT sur toutes les
            # quetes (`cycles_before_automatic_faillure: int = 1`), et le decompte
            # n'a lieu que `if quest.has_deadline` (quests_manager). Un contrat sans
            # echeance NE DISPARAIT PAS : ne jamais le presenter comme perdu.
            rc = (e['current_quests'][k] or {}).get('remaining_cycles_before_faillure')
            if quests[qid]['deadline'] and rc is not None and rc <= 1:
                LAST_CHANCE[qid] = True
    for spec in extra:
        qid, _, mi = spec.partition('@')
        qid = qid if qid in quests else by_file.get(qid, qid)
        if qid not in quests:
            print('!! quete inconnue : %s' % spec); continue
        if mi != '':
            import advise
            vs = advise.quest_variants(qid)
            k = int(mi) + 1
            if k < len(vs):
                MODS[qid] = vs[k]['q']
                _MODSIG.pop(qid, None)
                print('++ %s : version « %s » (%s)' % (qid, vs[k]['name'], vs[k]['note'] or '-'))
            else:
                print('!! pas de modificateur %s sur %s' % (mi, qid))
        if qid not in current:
            current.append(qid)
    # L'ULTIMATUM doit recevoir ses conditions supplementaires (+2 chacune) AVANT
    # toute evaluation : sans elles il score <= 0, donc « mortel », donc il etait
    # purement et simplement retire du tableau. C'est la quete la plus importante
    # du jeu, elle ne doit jamais disparaitre en silence.
    if u:
        for _qid in current:
            _q = MODS.get(_qid) or quests.get(_qid) or {}
            if _q.get('type') == 'ULTIMATUM_QUEST':
                _q2 = json.loads(json.dumps(quests[_qid]))
                _q2['extra_met'] = u['met']
                _q2['extra_conditions'] = len(u['list'])
                MODS[_qid] = _q2
                _MODSIG.pop(_qid, None)
                print('!! ULTIMATUM ce cycle : %s -- bonus +%d, %d chevalier(s) requis'
                      % (st.tr(_q2['name_key']) or _qid, sum(u['met'].values()), _q2['nb_knights']))
    fixed, taken = [], set()
    for spec in pin:
        qid, _, who = spec.partition(':')
        qid = qid if qid in quests else by_file.get(qid, qid)
        names = [x for x in who.split(',') if x]
        if qid not in current:
            # Erreur reelle (cycle 14) : `quest_almor_fight_dragon` epinglee alors que
            # la table portait `contract_dragon_hunt`. Deux quetes, deux ids, des noms
            # affiches voisins. On refuse au lieu de fabriquer un plan imaginaire.
            print('!! %s N EST PAS sur la table ce cycle -- epinglage REFUSE.' % qid)
            print('   quetes en cours : %s' % ', '.join(current))
            continue
        fixed.append((qid, names)); taken.update(names)
    # QUETE ENTIEREMENT POURVUE PAR SES CHEVALIERS IMPOSES. `quests_manager` fait
    # `assigned_knights = requested_knights.duplicate()` : le chevalier est deja dessus,
    # donc `busy_knights` le sort du vivier et la quete n'a aucune place a remplir. Elle
    # sortait donc du plan de bout en bout -- et personne ne lui donnait d'equipement.
    # Cycle 28 : le diplome d'Oliver (duree 4) le clouait 2 cycles avec sa propre
    # monture, alors que Pegase le ramenait a 1, et l'outil ne le voyait meme pas.
    # On les epingle d'office, avec leur equipe imposee.
    verrouilles = set()
    for qid in current:
        if qid in [x for x, _n in fixed]:
            continue
        _q = MODS.get(qid) or quests[qid]
        lk = [x for x in (_q.get('locked_knights') or []) if x in ks]
        if lk and len(lk) >= _q.get('nb_knights', 1):
            fixed.append((qid, lk))
            taken.update(lk)          # hors de best_assignments : leur place est prise
            verrouilles.update(lk)
    # Ils restent « occupes » pour le reste du monde, mais l'optimiseur doit pouvoir les
    # habiller et depenser leurs montees de niveau.
    pool_ks = dict(free)
    pool_ks.update({n: ks[n] for n in verrouilles})
    PEND.update({n: v for n, v in pending_levels(e).items() if n in verrouilles})

    rest = [q for q in current if q not in [x for x, _n in fixed]]
    # best_assignments choisit sur les scores NUS : sans equipement, sans montees de
    # niveau, sans repas. Avec top=1 ce choix etait definitif, alors que ces trois
    # leviers changent completement le classement -- Ligia a deux montees en attente,
    # Ursule une seule, et le repas fait basculer un palier chez l'une pas chez l'autre.
    # Cycle 21 : l'outil sortait Milkford a 6,61 avec Ligia quand Ursule donnait 7,09.
    # On optimise donc PLUSIEURS candidates jusqu'au bout et on garde la vraie meilleure.
    top = best_assignments(rest, {n: k for n, k in free.items() if n not in taken}, oc, 0, top=24)
    if no_conso[0]:
        avail = {k: v for k, v in avail.items()
                 if (C('equipment').get(k) or {}).get('kind') != 'consumable'}
    res, assign = None, None
    seen, t_start = set(), time.time()
    for _tot, _detail in (top or []):
        cand = fixed + [(qid, names) for qid, names, _r in _detail if names]
        if not cand:
            continue
        # Deux candidates de best_assignments ne different souvent que par une quete
        # sans enjeu : sans dedoublonnage on optimisait dix fois la meme chose et la
        # vraie alternative ne remontait jamais. La signature est l'affectation exacte.
        sig = tuple(sorted((q, tuple(sorted(t))) for q, t in cand))
        if sig in seen:
            continue
        seen.add(sig)
        r2 = optimise_cycle(cand, pool_ks, oc, avail, gold, meals, dict(PEND))
        s2 = sum(x[2] for x in r2['detail'])
        if res is None or s2 > sum(x[2] for x in res['detail']):
            res, assign = r2, cand
        # garde-fou : l'optimisation complete est bon marche, mais pas infinie.
        if time.time() - t_start > 20.0:
            break
    if not assign:
        if not current:
            print('La sauvegarde ne liste AUCUNE quete sur le plateau.')
            print('Elle est ecrite en fin de cycle : sauvegarde dans le jeu puis relance.')
        else:
            print('aucune repartition trouvee')
        return

    print('=== CYCLE %d | %d or (dont %d preserves) | %d chevaliers libres ==='
          % ((e.get('cycle_index') or 0) + 1,
             (force_gold[0] if force_gold[0] is not None else (e.get('current_funds') or 0)),
             GOLD_FLOOR[0], len(free)))
    print()
    # une colonne par chevalier : nom + relique(s) + montee de niveau
    rows = []
    maxk = 1
    for qid, team, sc, out in res['detail']:
        q = MODS.get(qid) or quests[qid]
        cells = []
        for n in team:
            its = res['items'].get(n) or []
            itxt = ' + '.join(st.tr(x) or x for x in its) or 'rien'
            lvs = res['levels'].get(n) or []
            ltxt = ' | ' + ', '.join('%s+1' % st.sfr(x) for x in lvs) if lvs else ''
            cells.append('%s : %s%s' % (n.upper(), itxt, ltxt))
        maxk = max(maxk, len(cells))
        # marge au palier : le seuil est `>= 10` / `> 5` / `> 0`. Un plan a 10,00
        # pile n'est PAS une reussite critique acquise -- il suffit qu'une montee
        # de niveau ne soit pas depensee ou qu'un consommable ne soit pas equipe.
        marg = ''
        for th in (10.0, 5.0, 0.0):
            if sc >= th:
                d = sc - th
                if d < 0.75:
                    marg = '  (!) marge %+.2f -- FRAGILE' % d
                break
        rows.append(['%s  [%s]' % (st.tr(q['name_key']) or qid, qid), '%dc' % team_duration(q, team, res['items']),
                     '%.2f %s%s' % (sc, out.lower(), marg)] + cells)

    head = ['QUETE', 'DUREE', 'RESULTAT'] + ['CHEVALIER %d' % (i + 1) for i in range(maxk)]
    for r in rows:
        r += [''] * (len(head) - len(r))
    w = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    line = lambda r: '  '.join(r[i].ljust(w[i]) for i in range(len(head))).rstrip()
    print(line(head))
    print('-' * len(line(head)))
    for r in rows:
        print(line(r))
    print()
    idle = [n for n in free if n not in [x for _q, t, _s, _o in res['detail'] for x in t]]
    # checklist : le plan suppose des actions MANUELLES dans l'interface du jeu.
    todo = []
    for qid, team, sc, out in res['detail']:
        for n in team:
            for stat in (res['levels'].get(n) or []):
                todo.append('%-9s depenser 1 montee de niveau dans %s' % (n.upper(), st.sfr(stat)))
            for it in (res['items'].get(n) or []):
                if eq[it]['kind'] == 'consumable':
                    todo.append('%-9s equiper le consommable %s' % (n.upper(), st.tr(it) or it))
    if todo:
        print('AVANT DE LANCER LES QUETES :')
        for t in todo:
            print('  [ ] %s' % t)
        print()
    alt = res.get('alt_no_consumable')
    if alt:
        print('VARIANTE SANS CONSOMMABLE (usage unique -- a arbitrer) : repas %s' % alt['meal'])
        for qid, team, sc, out_ in alt['detail']:
            q = MODS.get(qid) or quests[qid]
            print('   %-30s %6.2f %s' % ((st.tr(q['name_key']) or qid)[:30], sc, out_.lower()))
        print()
    if res.get('saved_consumables'):
        print('CONSOMMABLES GARDES EN STOCK (usage unique, ne changeaient aucun palier) : %s'
              % ', '.join(st.tr(x) or x for x in res['saved_consumables']))
    # Donner le nom du chevalier sans les plats obligeait a relancer `st.py meal`.
    # L'etat compte : Arron passe en Violent apres son rituel et `give_meal` lit alors
    # `violent_liked_meals` -- lui servir une crepe ne rapporte plus rien.
    _MEALS_FR = {'GALETTE_SAUCISSE': 'Galette saucisse', 'CROQUE_MONSIEUR': 'Croque-monsieur',
                 'PREFOU': 'Prefou', 'CREPE': 'Crepe',
                 'BRIZHIAN_BUTTER_SHORTBREAD': 'Palet Brizhien', 'LIONS_TACO': 'Tacos du lion'}
    def _plats(n):
        try:
            mm = C('meals')
        except Exception:
            return ''
        etat = ((e.get('roundtable_knights') or {}).get(n)
                or {}).get('current_state')
        if etat is None:
            for kk, vv in (e.get('roundtable_knights') or {}).items():
                if st._nm(kk) == n:
                    etat = vv.get('current_state')
                    break
        lst = mm.get('%s@%s' % (n, etat)) or mm.get(n) or []
        return ('  (%s)' % ', '.join(_MEALS_FR.get(x, x) for x in lst)) if lst else ''
    print('repas   : %s%s' % ((res['meal'] or 'aucun'),
                              _plats(res['meal']) if res['meal'] else ''))
    print('achats  : %d or' % res['spend'])
    if idle:
        print('AU REPOS : %s' % ', '.join(idle))

    # ------------------------------------------------------------- AUDIT
    # Chaque regle vient d'une erreur REELLE signalee par le user. Elle est
    # verifiee sur le plan FINAL : si l'optimiseur regresse, c'est l'outil qui
    # le dit, plus le joueur.
    warn = []
    dur = lambda qid, team: team_duration(MODS.get(qid) or quests[qid], team, res['items'])
    mounted = lambda n: any(eq[x]['kind'] == 'mount' for x in (res['items'].get(n) or []))

    # (0) MORT. La regle la plus chere de toutes : Ligia est morte au cycle 20 sur
    #     contract_dragon_hunt parce que j'avais annonce que 2,62 de reussite
    #     « laissait de la marge ». quest.gd, determine_damages(), lignes 308-325 :
    #         match outcome:
    #             CRITICAL_SUCCESS: min = max = 0          <- AUCUN degat
    #             GREAT_SUCCESS:    max = max(min, floor((max-min)/2))
    #             SUCCESS:          max = max(min, max-1)
    #             CRITICAL_FAILURE: min = max = 100        <- mort assuree
    #         var base_damages: int = randi_range(min_value, max_value)
    #         if quest_can_be_lethal: continue             <- aucun ecretage
    #     Le PALIER pilote donc les degats : sur cette quete a 3-6, une reussite
    #     donne 3-5 (Ligia, armure 5 -> 1 chance sur 3) et une critique donne 0.
    #     Monter le score EST la protection. Sur une quete letale, en revanche,
    #     le garde-fou `damages = armure - 1` est saute : rien ne rattrape un
    #     palier trop bas. death_flag() calculait deja tout ca a partir du palier
    #     REELLEMENT atteint, et n'etait appelee NULLE PART.
    for qid, team, _sc, _o in res['detail']:
        q0 = MODS.get(qid) or quests[qid]
        for n in team:
            f = death_flag(q0, _o, ks[n])
            if f:
                warn.insert(0, '%s sur %s :%s' % (n.upper(), qid, f))

    # (1) une quete de plusieurs cycles avec un equipier a pied : la reduction
    #     retenue est celle du plus lent, celles des autres sont ANNULEES.
    for qid, team, _sc, _o in res['detail']:
        if dur(qid, team) <= 1:
            continue
        apied = [n for n in team if not mounted(n)]
        # Ne crier que si l'echange est POSSIBLE : montures dormant sur une quete
        # deja a 1 cycle, plus celles encore achetables avec le reste de l'or.
        dispo = len([1 for q2, t2, _s2, _o2 in res['detail']
                     if (MODS.get(q2) or quests[q2]).get('duration', 1) <= 1
                     for n2 in t2 if mounted(n2)])
        dispo += len([x for x in avail
                      if x in eq and eq[x]['kind'] == 'mount'
                      and avail[x][0] <= (gold - res['spend'])
                      and not any(x in v for v in res['items'].values())])
        if apied and any(mounted(n) for n in team) and dispo >= len(apied):
            warn.append("%s dure %d cycles, %s sans monture : la reduction des autres est annulee"
                        % (qid, dur(qid, team), ', '.join(x.upper() for x in apied)))

    # (2) monture posee sur une quete d'un seul cycle alors qu'une quete longue
    #     du meme plan en manque une.
    manque = any(dur(qid, team) > 1 and not all(mounted(n) for n in team)
                 for qid, team, _s, _o in res['detail'])
    if manque:
        for qid, team, _sc, _o in res['detail']:
            if (MODS.get(qid) or quests[qid]).get('duration', 1) > 1:
                continue
            for n in team:
                if mounted(n):
                    warn.append("%s porte une monture sur %s qui ne dure qu un cycle,"
                                " alors qu une quete longue en manque" % (n.upper(), qid))

    # (3) repas sur un chevalier deja au plafond d'affinite.
    if res['meal'] and (ks.get(res['meal']) or {}).get('affinity', 0) >= 10.0:
        autres = [n for n in [x for _q, t, _s, _o in res['detail'] for x in t]
                  if (ks.get(n) or {}).get('affinity', 0) < 10.0]
        if autres:
            warn.append("repas sur %s deja a 10,0 d amitie ; %s en profiterait"
                        % (res['meal'].upper(),
                           min(autres, key=lambda n: ks[n]['affinity']).upper()))

    # (4) une quete en DERNIERE CHANCE ne doit jamais rester sur le plateau ;
    #     une urgence reportee dont l'echec detruit une localite doit etre signalee.
    for qid in current:
        if qid in [x for x, _t, _s, _o in res['detail']]:
            continue
        q = MODS.get(qid) or quests[qid]
        if LAST_CHANCE.get(qid):
            warn.append("%s est en DERNIERE CHANCE et n est PAS assignee" % qid)
        elif q['deadline'] and any((x or {}).get('type') == 'LOCATION_DESTROYED'
                                   for x in (q['failure'] or [])):
            warn.append("%s reportee : son echec DETRUIT une localite -- verifier"
                        " l effectif du cycle suivant" % qid)

    # (5) chevalier au repos alors qu'une quete reste ouverte sur le plateau.
    if idle:
        # une quete entierement pourvue par ses chevaliers IMPOSES n'est pas "ouverte"
        def _libre(qid):
            q = MODS.get(qid) or quests[qid]
            return q['nb_knights'] - len(q.get('locked_knights') or []) > 0
        # Ne crier que si les chevaliers au repos peuvent REELLEMENT la tenter. Cycle 27 :
        # l'audit reclamait Oliver sur quatre quetes ou il sortait entre -13 et -18, soit
        # ECHEC CRITIQUE -- et l'echec critique met les degats a 100, donc mort certaine.
        # La regle ne testait que la disponibilite, jamais la faisabilite.
        def _tentable(qid):
            q = MODS.get(qid) or quests[qid]
            n = max(1, q['nb_knights'] - len(q.get('locked_knights') or []))
            if len(idle) < n:
                return False
            for combo in itertools.combinations(idle, n):
                rr = evaluate(qid, [ks[x] for x in combo], oc, 0)
                if rr['special'] or (rr['score'] or -99) > 0:
                    return True
            return False
        ouvertes = [qid for qid in current
                    if qid not in [x for x, _t, _s, _o in res['detail']]
                    and _libre(qid) and _tentable(qid)]
        if ouvertes:
            warn.append("%s au repos alors que %s reste(nt) sur le plateau"
                        % (', '.join(x.upper() for x in idle), ', '.join(ouvertes)))

    # (N) IMMOBILISATION. Le user a decouvert au cycle 6 que son plan envoyait des
    #     chevaliers pour trois cycles sans que rien ne le dise. Le tableau porte la
    #     duree, mais elle se lit mal quand on valide vite : on la reformule en clair.
    for qid, team, _sc, _out in res['detail']:
        d = dur(qid, team)
        if d > 1 and team:
            warn.append("%s : %s immobilise(s) %d cycles"
                        % (st.tr((MODS.get(qid) or quests[qid])['name_key']) or qid,
                           ', '.join(x.upper() for x in team), d))

    if warn:
        print()
        print('!! AUDIT -- a verifier avant de valider :')
        for x in warn:
            print('   - %s' % x)

    if json_out[0]:
        _export_json(json_out[0], e, res, quests, eq, idle, warn, slot, avail, ks)

# ---------------------------------------------------------------- journal de partie
LOG = os.path.join(HERE, 'runlog.md')


def cmd_snapshot(*args):
    """Ajoute l'etat du cycle courant a runlog.md. A lancer a CHAQUE cycle :
    le journal doit venir de la sauvegarde, jamais de la memoire."""
    note = ' '.join(args)
    ks, r, e = st.knights_from_save()
    apply_save_modifiers(r, e)
    quests = C('quests')
    busy = st.busy_knights(e)
    cyc = (e.get('cycle_index') or 0) + 1
    by_file = {}
    for _qid, _q in quests.items():
        by_file.setdefault(_q['path'].split('/')[-1].replace('.tres', ''), _qid)

    L = []
    L.append('')
    L.append('## Cycle %d%s' % (cyc, (' - ' + note) if note else ''))
    L.append('')
    L.append('- or %s | satisfaction %s | corruption %s'
             % (e.get('current_funds'), e.get('current_satisfaction'), e.get('corruption_score')))
    L.append('- comtes rallies : %s'
             % ', '.join(x['@ext'][1].split('/')[-1].replace('.tres', '')
                         for x in (e.get('rallied_counties') or []) if isinstance(x, dict)) or '-')
    L.append('- **effectif : %d chevaliers** (%s)' % (len(ks), ', '.join(sorted(ks))))

    occupied = []
    for k, v in (e.get('ongoing_quests') or {}).items():
        kn = [x['@ext'][1].split('/')[-1].replace('.tres', '')
              for x in (v.get('assigned_knights') or []) if isinstance(x, dict)]
        occupied.append('%s (%s, encore %s cycle(s))' % (st._nm(k), '+'.join(kn) or '-', v.get('duration')))
    L.append('- en mission : %s' % ('; '.join(occupied) or 'personne'))

    if e.get('has_current_ultimatum'):
        u = ultimatum_extra_conditions(r, e)
        if u:
            L.append('- ULTIMATUM actif, conditions : %s'
                     % '; '.join('%s [%s]' % (t, 'OK' if ok else 'NON') for t, ok in u['list']))

    L.append('')
    L.append('| quete | type | chevaliers | duree | delai |')
    L.append('|---|---|---|---|---|')
    for k, v in (e.get('current_quests') or {}).items():
        n = st._nm(k)
        qid = n if n in quests else by_file.get(n)
        q = MODS.get(qid) or quests.get(qid)
        if not q:
            L.append('| %s | ? NON RECONNUE | ? | ? | ? |' % n)
            continue
        L.append('| %s | %s | %d | %d | %s |' % (st.tr(q['name_key']) or qid, q['type'],
                                                 q['nb_knights'], q['duration'],
                                                 v.get('remaining_cycles_before_faillure') if q['deadline'] else '-'))
    L.append('')
    L.append('- **decision :** _(a completer)_')

    head = ''
    if not os.path.exists(LOG):
        head = ('# Journal de partie - Sovereign Tower\n\n'
                'Genere par `python st.py snapshot`. Un bloc par cycle, tire de la SAUVEGARDE.\n'
                'Les regles structurelles de la partie sont dans `strategie.md`.\n')
    with open(LOG, 'a', encoding='utf-8') as f:
        if head:
            f.write(head)
        f.write('\n'.join(L) + '\n')
    print('runlog.md : cycle %d ajoute (%d chevaliers, %d quetes)'
          % (cyc, len(ks), len(e.get('current_quests') or {})))


# ---------------------------------------------------------------- st.py simple
# Le joueur veut jouer seul et ne demander QUE ce qu'il ne peut pas deviner :
# les issues inattendues du plateau, leur declencheur et leur recompense.
# Volontairement sans score, sans equipement, sans repartition.

def _fr_reward(rw):
    """Une recompense en francais lisible. `st._rew` rend un dict brut."""
    t = (rw or {}).get('type')
    a = rw.get('amount')
    CAT = {'PEOPLE': 'Peuple', 'NOBLES': 'Nobles',
           'MERCHANTS': 'Marchands', 'SCHOLARS': 'Erudits'}
    if t == 'FUNDS':
        return '%+d or' % (a or 0)
    if t == 'SATISFACTION':
        return '%s %+d' % (CAT.get(rw.get('affected_category'), rw.get('affected_category')), a or 0)
    if t == 'AFFINITY':
        return 'affinite %+d' % (a or 0)
    if t == 'RELIC':
        return 'relique' + (' (%s)' % rw['relic'] if rw.get('relic') else '')
    if t == 'MOUNT':
        return 'monture' + (' (%s)' % rw['mount'] if rw.get('mount') else '')
    if t == 'CONSUMABLE':
        return 'consommable' + (' (%s)' % rw['consumable'] if rw.get('consumable') else '')
    if t == 'QUEST_ITEM':
        return 'objet de quete'
    if t == 'SOVEREIGN_TAG':
        return 'tag souverain %+d' % (a or 0)
    if t == 'BOOL_STORY_VAR_MODIF':
        return 'variable %s' % rw.get('variable_name')
    if t == 'LOCATION_TAX':
        return 'taxe %+d (%s)' % (a or 0, rw.get('location'))
    if t == 'AUDIENCE_REQUEST':
        return 'nouvelle audience'
    return t or '?'


def _fr_trigger(o):
    """Le declencheur de l'issue, en clair."""
    bits = []
    if o.get('knights'):
        bits.append('chevalier ' + ' + '.join(x.upper() for x in o['knights']))
    if o.get('amount', -1) > 0 and o.get('stat'):
        sens = '>=' if o.get('requires_higher', True) else '<='
        bits.append('%s %s %d' % (st.sfr(o['stat']), sens, o['amount']))
    if o.get('tags'):
        bits.append('tag ' + ' + '.join(str(t) for t in o['tags'] if t))
    return ' et '.join(bits) or 'inconditionnelle'


def cmd_simple(argv):
    """st.py simple [slot] : les issues inattendues du plateau, rien d'autre."""
    slot = int(argv[0]) if argv and argv[0].isdigit() else 1
    ks, r, e = st.knights_from_save(slot)
    set_welded_reductions(ks)
    apply_save_modifiers(r, e)
    oc = save_modifier_outcomes(r, e, build_outcomes())
    quests = C('quests')
    eq = C('equipment')
    busy = st.busy_knights(e)
    libres = {n: k for n, k in ks.items() if n not in busy}
    # Les chevaliers TELS QU'ILS SONT EQUIPES, pas depouilles. Un declencheur
    # d'issue inattendue peut etre un tag porte par l'equipement (NON_EQUINE sur
    # une monture, par exemple) : avec _bare() je repondais « aucun chevalier ne
    # la declenche » alors que SILGUR avait deja le Lion de Gavault sur le dos.
    # knights_from_save() fusionne les stats de l'equipement mais PAS ses tags.
    bare = {}
    for n, k in libres.items():
        k2 = dict(k)
        k2['tags'] = list(k.get('tags') or [])
        for it in (k.get('equip') or []):
            for t in ((eq.get(it) or {}).get('tags') or []):
                if t not in k2['tags']:
                    k2['tags'].append(t)
        bare[n] = k2

    by_file = {}
    for qid, q in quests.items():
        by_file.setdefault(q['path'].split('/')[-1].replace('.tres', ''), qid)
    board = []
    for k in (e.get('current_quests') or {}):
        n = st._nm(k)
        qid = n if n in quests else by_file.get(n)
        if qid:
            board.append(qid)

    print('=== CYCLE %d -- issues inattendues du plateau ==='
          % ((e.get('cycle_index') or 0) + 1))

    # EDITH se fait devorer par le demon de son epee des qu'elle participe a une
    # quete qui tue un humain. Le declencheur est le champ `involve_killing` de
    # la quete, pas un tag ni un score : edith.gd::update_for_kill() met
    # is_possessed a vrai et programme l'audience de metamorphose. C'est
    # irreversible (aucune branche de retour dans l'ink), donc ca se signale
    # AVANT la repartition, pas apres.
    _mortelles = [q for q in board if (quests.get(q) or {}).get('involve_killing')]
    _edith = ks.get('edith')
    if _mortelles and _edith and not _edith['dead']:
        print()
        for qid in _mortelles:
            print(r'/!\ %s [%s] -- INVOLVE_KILLING : ne PAS y mettre EDITH '
                  '(possession definitive)'
                  % (st.tr((quests[qid])['name_key']) or qid, qid))

    trouve = False
    for qid in board:
        q = MODS.get(qid) or quests[qid]
        paths = oc['by_quest'].get(qid) or []
        if not paths:
            continue
        nom = st.tr(q['name_key']) or qid
        for p in paths:
            o = oc['outcomes'].get(p)
            if not o or o.get('traitor'):
                continue
            trouve = True
            # Qui, parmi les chevaliers DISPONIBLES, declenche l'issue en solo.
            qui = [n for n in sorted(bare)
                   if triggered_outcome(qid, [bare[n]], oc)[0] == p]
            rw = [_fr_reward(x) for x in (o.get('rewards') or [])]
            normal = [_fr_reward(x) for x in (q.get('success') or [])]
            malus = any(str(x).strip().startswith('-') or ' -' in str(x) for x in rw)
            print()
            print('%s  [%s]' % (nom, qid))
            print('   declencheur : %s' % _fr_trigger(o))
            print('   dispo       : %s' % (', '.join(x.upper() for x in qui)
                                           if qui else 'AUCUN chevalier libre ne la declenche seul'))
            print('   issue       : %s' % (', '.join(rw) if rw else 'aucune recompense'))
            print('   au lieu de  : %s' % (', '.join(normal) if normal else 'aucune recompense'))
            if o.get('damage') is not None:
                print('   degats      : %s' % (o['damage'],))
            if o.get('follow_up'):
                print('   suite       : %s' % o['follow_up'])
            if malus:
                print(r'   /!\ ATTENTION : cette issue contient un MALUS et REMPLACE '
                      'les recompenses normales.')
    if not trouve:
        print('\nAucune quete du plateau ne porte d issue inattendue.')


# --------------------------------------------------------------- st.py choix
# Repondre a « voici la photo des choix d'une audience, lequel je prends ? »
# me prenait six a huit appels d'outil : trouver la phrase dans l'ink, remonter
# au knot, lire les choix, ouvrir chaque quete debloquee, puis noter le roster.
# Tout ca est mecanique -- c'est donc du ressort du script, pas du mien.

_TAG_INK_FR = {'Tyrannic': 'TYRANNIQUE', 'Wise': 'SAGE', 'Kind': 'BIENVEILLANT',
               'Audacious': 'AUDACIEUX', 'Omniscient': 'OMNISCIENT'}
_TAG_IDX_FR = {'0': 'TYRANNIQUE', '1': 'SAGE', '2': 'BIENVEILLANT',
               '3': 'AUDACIEUX', '4': 'OMNISCIENT'}


def _sovereign_levels(e):
    """{NOM_FR: niveau} des tags de souverain, lus dans la sauvegarde.
    Les tags ne sont PAS dans les variables ink -- cette erreur m'avait fait
    annoncer a tort qu'une option etait fermee (cf. le deguisement de crabe)."""
    prog = e.get('current_sovereign_tags_progression') or {}
    try:
        th = json.load(open(st.j('level_thresholds.json'), encoding='utf-8'))
    except Exception:
        th = {}
    out = {}
    for i, p in prog.items():
        lv = max((int(k) for k, v in th.items() if p >= v), default=0)
        out[_TAG_IDX_FR.get(str(i), str(i))] = lv
    return out


_CAT_FR = {'people': 'Peuple', 'nobles': 'Nobles',
           'merchants': 'Marchands', 'scholars': 'Erudits'}


def _ink_vars(e):
    """Variables du scenario, lues dans `story_state` de la sauvegarde.
    (Les tags de souverain, eux, n'y sont PAS -- voir _sovereign_levels.)"""
    raw = e.get('story_state') or ''
    try:
        st_ = json.loads(raw)
        v = (st_.get('variablesState') or st_)
        return v if isinstance(v, dict) else {}
    except Exception:
        # Repli textuel : la forme "nom":valeur suffit pour un booleen.
        return {m.group(1): (m.group(2) == 'true') for m in
                re.finditer(r'"([a-z][a-z0-9_]{3,60})":(true|false)', raw)}


def _knot_at(txt, pos):
    """Nom du dernier knot ouvert avant `pos` (meme methode que scan_effect)."""
    best, bestpos = '?', -1
    for m in re.finditer(r'"([a-z][a-z0-9_]{3,60})":\s*[\[{]', txt):
        if m.start() >= pos:
            break
        if m.start() > bestpos:
            best, bestpos = m.group(1), m.start()
    return best


def _slice_near(name, pos):
    """Tranche du knot `name` tel que l'atteint un divert depuis `pos`.

    Les diverts ink sont RELATIFS (`.^.^.^.^.accept_quest`) et les noms sont
    massivement reutilises : `accept_quest` existe 34 fois, `omniscient` 8 fois.
    - viser globalement rend la premiere occurrence du fichier (faux) ;
    - viser la plus proche en octets rend un knot voisin appartenant a une
      AUTRE audience (teste : ca m'a sorti la Bete de Groveshire sous une
      audience de Gavault).
    La seule cible juste est celle qui partage un conteneur avec le knot de
    depart : on remonte donc les conteneurs englobants jusqu'a en trouver un
    qui contienne le nom vise.
    """
    txt = st._ink_text()
    if ('"%s":' % name) not in txt:
        return None
    for start in _ancestors(pos):       # du plus proche au plus lointain
        seg = st._bracket_slice(txt, start)
        k = seg.find('"%s":' % name)
        if k < 0:
            continue
        j2 = min(x for x in (seg.find('[', k), seg.find('{', k)) if x > 0)
        return st._bracket_slice(seg, j2)
    return None


_ANC = {}


def _ancestors(pos):
    """Positions des conteneurs ouverts qui englobent `pos`, du plus proche au
    plus lointain. Un simple balayage a pile : c'est la seule facon fiable de
    resoudre un chemin ink relatif, les noms de knots n'etant pas uniques."""
    key = pos // 512
    if key in _ANC:
        return _ANC[key]
    txt = st._ink_text()
    stack = []
    instr = esc = False
    for i in range(pos):
        c = txt[i]
        if instr:
            if esc:
                esc = False
            elif c == chr(92):
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c in '[{':
            stack.append(i)
        elif c in ']}':
            if stack:
                stack.pop()
    out = list(reversed(stack))
    _ANC[key] = out
    return out


def _near_effects(name, pos, depth=1, _seen=None):
    """[(etiquette, argument)] atteignables depuis le knot `name` proche de `pos`."""
    if _seen is None:
        _seen = set()
    if name in _seen or depth < 0:
        return []
    _seen.add(name)
    seg = _slice_near(name, pos)
    if seg is None:
        return []
    # Un knot de renvoi contient souvent d'AUTRES choix (`"c-0":[...]`). Ce qui
    # s'y trouve n'est pas acquis : ca depend d'une reponse ultérieure. On ne
    # garde donc que les effets du tronc commun, sinon j'annonce un tag gagne
    # alors qu'il faut encore le choisir.
    autres = False
    while True:
        m2 = re.search(r'"c-\d+":\[', seg)
        if not m2:
            break
        autres = True
        blk2 = st._bracket_slice(seg, m2.end() - 1)
        seg = seg[:m2.start()] + seg[m2.end() - 1 + len(blk2):]
    out = []
    if autres:
        out.append(('suite', 'd autres choix suivent dans ce renvoi'))
    for u in re.finditer(r'\{"VAR\?":"([a-z][a-z0-9_]{3,70})"\}'
                         r'.{0,60}?\{"f\(\)":"(UnlockQuest|AddDoleanceForNextCycle)"\}', seg):
        out.append(('quete', u.group(1)))
    for u in re.finditer(r'\{"VAR\?":"(\w+)"\},(-?\d+),\{"f\(\)":"UpdateSovereignValue"\}', seg):
        out.append(('tag', '%s %+d' % (_TAG_INK_FR.get(u.group(1), u.group(1)), int(u.group(2)))))
    for u in re.finditer(r'\{"VAR=":"([a-z0-9_]+)","re":true\}', seg):
        out.append(('var', u.group(1)))
    for u in re.finditer(r'\{"VAR\?":"(\w+)"\},\{"f\(\)":"(KnightRecruitment|KnightDemission'
                         r'|UnlockEquipment|UnlockAudienceRequest|SpecialInstruction'
                         r'|CountyRallied)"\}', seg):
        out.append((u.group(2), u.group(1)))
    for u in re.finditer(r'\{"->":"[.^]*([a-z][a-z0-9_]{3,60})"\}', seg):
        out += _near_effects(u.group(1), pos, depth - 1, _seen)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


# Une garde d'option est compilee par ink en NOTATION POSTFIXEE :
#   {"VAR?":"a"},"!",{"VAR?":"b"},"!","&&"   se lit   « ni a ni b ».
# L'ancienne lecture listait les variables a plat et annoncait « NON acquise,
# option CACHEE » : l'inverse de la verite sur les 393 negations du scenario.
_CMP = {'>=': lambda a, b: a >= b, '<=': lambda a, b: a <= b,
        '>': lambda a, b: a > b, '<': lambda a, b: a < b,
        '==': lambda a, b: a == b, '!=': lambda a, b: a != b}

_COND_TOK = re.compile(r'\{"VAR\?":"([a-z][a-z0-9_]{3,60})"\}'
                       r'|\{"CNT\?":"([^"]+)"\}'
                       r'|"(!|&&|\|\||>=|<=|==|!=|>|<)"'
                       r'|(?<![\w."])(-?\d+)(?![\w."])')


def _cond_parse(seg):
    """(jetons, tout_reconnu). Ce qui reste hors jetons trahit une garde qu'on
    ne sait pas lire : mieux vaut le dire que de deviner."""
    toks, reste, prev = [], [], 0
    for m in _COND_TOK.finditer(seg or ''):
        reste.append(seg[prev:m.start()])
        prev = m.end()
        if m.group(1):
            toks.append(('var', m.group(1)))
        elif m.group(2):
            toks.append(('cnt', m.group(2).split('.')[-1]))
        elif m.group(3):
            toks.append(('op', m.group(3)))
        else:
            toks.append(('num', int(m.group(4))))
    reste.append((seg or '')[prev:])
    return toks, not ''.join(reste).strip(', \t')


def _cond_non(t):
    return t[4:] if t.startswith('NON ') else 'NON ' + t


def _cond_eval(toks, inkvars):
    """(expression lisible, visible). `visible` vaut None quand l'issue depend
    d'un element hors sauvegarde (nombre de passages dans un noeud)."""
    pile = []
    for kind, val in toks:
        if kind == 'var':
            pile.append((bool(inkvars.get(val)), val))
        elif kind == 'cnt':
            pile.append((None, 'passages par %s' % val))
        elif kind == 'num':
            pile.append((val, str(val)))
        elif val == '!':
            if not pile:
                return '', None
            v, t = pile.pop()
            pile.append((None if v is None else not v, _cond_non(t)))
        elif val in ('&&', '||'):
            if len(pile) < 2:
                return '', None
            b, tb = pile.pop()
            a, ta = pile.pop()
            if val == '&&':
                r = False if (a is False or b is False) else (
                    None if (a is None or b is None) else True)
            else:
                r = True if (a is True or b is True) else (
                    None if (a is None or b is None) else False)
            pile.append((r, '(%s %s %s)' % (ta, 'ET' if val == '&&' else 'OU', tb)))
        else:
            if len(pile) < 2:
                return '', None
            b, tb = pile.pop()
            a, ta = pile.pop()
            f = _CMP.get(val)
            r = f(a, b) if (f and isinstance(a, int) and isinstance(b, int)) else None
            pile.append((r, '%s %s %s' % (ta, val, tb)))
    if len(pile) != 1:
        return '', None
    v, t = pile[0]
    return (t[1:-1] if t.startswith('(') and t.endswith(')') else t), v


def _choices_detailed(knot, pos=0):
    """[(libelle, [requis], [effets])] pour un knot d'audience.
    st.ink_choices() ne rend pas les RequiresTag ; or c'est exactement ce qui
    grise une option a l'ecran, donc c'est la premiere chose a verifier."""
    # Meme piege que pour les diverts : `arrest`, `choices`, `conclusion`... ne
    # sont pas uniques. _knot_slice() rendait la premiere occurrence du fichier
    # et l'audience sortait « aucun choix trouve ».
    seg = _slice_near(knot, pos) if pos else st._knot_slice(st._ink_text(), knot)
    if seg is None:
        return []
    labels = []
    prev = 0
    # Le suffixe est souple : une option conditionnee par une variable s'ecrit
    # `"/str",{"VAR?":"xxx"},"/ev"` et non `"/str","/ev"`. C'est exactement la
    # forme des options « je sais deja... » debloquees par la connaissance --
    # celles que je dois surtout ne pas rater.
    # La fenetre du suffixe doit etre LARGE : une option gardee par une
    # expression booleenne (`egg_obtained ! && egg_info... || ...`) depasse
    # facilement 120 caracteres, et l'option disparaissait purement et
    # simplement de la liste -- j'ai rate « Volons-leur l'oeuf » comme ca.
    for m in re.finditer(r'\^([^"]{4,300})","/str"(.{0,400}?)"/ev",\{"\*":"\.\^\.(c-\d+)"', seg):
        head = seg[prev:m.start()]
        req = [('tag', _TAG_INK_FR.get(a, a), int(b)) for a, b in
               re.findall(r'\{"VAR\?":"(\w+)"\},(\d+),\{"f\(\)":"RequiresTag"\}', head)]
        req += [('satisfaction', a, int(b)) for a, b in
                re.findall(r'\{"VAR\?":"(\w+)"\},(\d+),\{"f\(\)":"RequiresMinSatisfaction"\}', head)]
        toks, entier = _cond_parse(m.group(2))
        if toks:
            req.append(('cond', toks, entier))
        labels.append((m.group(3), m.group(1), req))
        prev = m.end()
    blocks = {}
    for m in re.finditer(r'"(c-\d+)":\[', seg):
        blk = st._bracket_slice(seg, m.end() - 1)
        eff = []
        # Le motif ne doit PAS se limiter a `quest_*` : les contrats s'appellent
        # `contract_*` et les doleances de comte `county_quest_*`. Avec l'ancien
        # motif, une audience qui debloquait un contrat affichait « renvoi »
        # tout seul, sans jamais dire quelle quete elle ouvrait.
        for u in re.finditer(r'\{"VAR\?":"([a-z][a-z0-9_]{3,70})"\}'
                             r'(.{0,40}?)\{"f\(\)":"(UnlockQuest|AddDoleanceForNextCycle)"\}', blk):
            mod = re.search(r',(\d+),', u.group(2))
            eff.append(('quete', u.group(1),
                        ' [modificateur %s]' % mod.group(1) if mod else ''))
        for u in re.finditer(r'\{"VAR\?":"(\w+)"\},(-?\d+),\{"f\(\)":"UpdateSovereignValue"\}', blk):
            eff.append(('tag', _TAG_INK_FR.get(u.group(1), u.group(1)), int(u.group(2))))
        for u in re.finditer(r'\{"VAR=":"([a-z0-9_]+)","re":true\}', blk):
            eff.append(('var', u.group(1), ''))
        for u in re.finditer(r'\{"VAR\?":"(\w+)"\},\{"f\(\)":"(KnightRecruitment|KnightDemission'
                             r'|UnlockEquipment|UnlockAudienceRequest|SpecialInstruction)"\}', blk):
            eff.append((u.group(2), u.group(1), ''))
        # Une branche peut n'etre qu'un renvoi (`-> omniscient`) : tout l'effet
        # est alors dans le knot vise. Sans ca l'option la plus interessante --
        # celle debloquee par la connaissance -- s'affiche « aucun effet ».
        # ATTENTION : ces noms ne sont PAS uniques (`accept_quest` apparait 34
        # fois). Un _knot_slice() global rendait la premiere occurrence venue et
        # j'ai affiche une doleance de Groveshire sous une audience de Gavault.
        # Le divert est relatif : la bonne cible est la plus PROCHE du knot.
        for u in re.finditer(r'\{"->":"[.^]*([a-z][a-z0-9_]{3,60})"\}', blk):
            cible = u.group(1)
            eff.append(('renvoi', cible, ''))
            for kind2, val2 in _near_effects(cible, pos):
                eff.append((kind2, val2, ' (via %s)' % cible))
        blocks.setdefault(m.group(1), []).append(eff)
    out, seen = [], {}
    for c, lab, req in labels:
        i = seen.get(c, 0)
        seen[c] = i + 1
        ee = blocks.get(c, [])
        out.append((lab, req, ee[i] if i < len(ee) else (ee[0] if ee else [])))
    return out


def _quest_card(qid, ks, oc, indent='   '):
    """Fiche complete d'une quete + note de chaque chevalier disponible."""
    quests = C('quests')
    q = MODS.get(qid) or quests.get(qid)
    if q is None:
        print(indent + '%s : quete introuvable dans le cache' % qid)
        return
    dmg = q.get('damages') or [0, 0]
    dl = ''
    if q.get('deadline'):
        dl = ' | delai %s' % (q.get('deadline_cycles') or '1 (defaut)')
    print(indent + '%s  [%s]' % (st.tr(q['name_key']) or qid, qid))
    print(indent + '  %s | %s chevalier(s) | %s cycle(s)%s | degats %s-%s%s'
          % (q.get('type'), q.get('nb_knights'), q.get('duration'), dl,
             dmg[0], dmg[1], ' LETAL' if q.get('lethal') else ''))
    print(indent + '  requis  : %s' % st.stats_fr(q.get('stats') or {}))
    succ = [_fr_reward(x) for x in (q.get('success') or [])]
    fail = [_fr_reward(x) for x in (q.get('failure') or [])]
    print(indent + '  succes  : %s' % (', '.join(succ) or 'rien'))
    if fail:
        print(indent + '  echec   : %s' % ', '.join(fail))
    if q.get('success_follow_up'):
        print(indent + '  suite   : %s' % q['success_follow_up'])
    # Les chevaliers imposes par la quete. En proposer d'autres n'a aucun sens :
    # c'est ce qui m'a fait conseiller ARI sur la quete ou OLIVER est verrouille.
    imposes = [x for x in (q.get('locked_knights') or []) if x in ks]
    complet = imposes and len(imposes) >= (q.get('nb_knights') or 1)
    if imposes:
        print(indent + '  impose  : %s%s'
              % (', '.join(x.upper() for x in imposes),
                 '  (aucune place libre)' if complet
                 else '  + %d place(s) libre(s)' % ((q.get('nb_knights') or 1)
                                                    - len(imposes))))
    # Notes. Le seuil est par chevalier contre l'exigence COMPLETE : les stats
    # ne s'additionnent jamais entre membres de l'equipe.
    notes = []
    for n, k in ks.items():
        if k['dead']:
            continue
        # Un chevalier verrouille SUR CETTE QUETE est compte « occupe » : il doit
        # quand meme figurer, c'est lui qui part.
        if k['busy'] and n not in imposes:
            continue
        if complet and n not in imposes:
            continue
        try:
            s, out = st.score(qid, [k], meals=False, verbose=False, quest=MODS.get(qid))
        except Exception:
            continue
        notes.append((s, n, out))
    if notes:
        print(indent + '  solo    : ' + ' | '.join(
            '%s %.2f %s' % (n.upper(), s, out.lower())
            for s, n, out in sorted(notes, reverse=True)[:6]))
    for p in (oc['by_quest'].get(qid) or []):
        o = oc['outcomes'].get(p)
        if not o or o.get('traitor'):
            continue
        rw = ', '.join(_fr_reward(x) for x in (o.get('rewards') or [])) or 'aucune recompense'
        print(indent + '  INATTENDU (%s) -> %s | degats %s'
              % (_fr_trigger(o), rw, o.get('damage')))


def _quete_par_ref(quests, ref):
    """La quete designee par `ref`, que ref soit son quest_id ou son NOM DE FICHIER.

    Les deux divergent parfois : le scenario appelle
    `quest_groveshire_defeat_the_hord` (le fichier .tres), alors que la ressource
    porte l'identifiant `quest_groveshire_defeat_the_herd` -- « hord » contre
    « herd ». Chercher seulement par identifiant faisait afficher le nom technique
    au lieu de « Affronter la meute ».
    """
    q = quests.get(ref)
    if q:
        return q
    for v in quests.values():
        chemin = v.get('path') or ''
        if chemin and os.path.basename(chemin).rsplit('.', 1)[0] == ref:
            return v
    return {}


def cmd_hints(argv):
    """st.py hints --in=<entree.json> --out=<sortie.json>

    Ce que chaque option d'audience debloque, pour l'infobulle du mod en jeu.

    Pourquoi passer par ici : le jeu construit l'icone relique/monture/consommable
    avec des arguments VIDES (choice_button.gd, `update_for_equipment`). Le nom de
    l'objet n'existe que dans le scenario, sous la forme
    `{"VAR?":"RELIC"},{"VAR?":"Wolf_Skin"},{"f()":"UnlockEquipment"}`. Le joueur voit
    donc « tu gagnes un objet » sans savoir lequel. On relit l'ink pour lui dire.

    Lecture seule : rien n'est ecrit dans la partie.

    Entree : {"choices": ["libelle du bouton", ...]}
    Sortie : {"hints": [{"label": ..., "equipment": [...], "quests": [...]}]}
    """
    src = dst = None
    for a in argv:
        if a.startswith('--in='):
            src = a.split('=', 1)[1]
        elif a.startswith('--out='):
            dst = a.split('=', 1)[1]
    if not src:
        print(cmd_hints.__doc__)
        return
    with open(src, encoding='utf-8') as f:
        req = json.load(f)
    wanted = [str(x) for x in (req.get('choices') or []) if str(x).strip()]

    txt = st._ink_text()
    eq = C('equipment')
    quests = C('quests')
    rows, knot = [], None
    by_norm = {}

    # Les options affichees ensemble appartiennent au MEME knot. On evalue donc tous
    # les knots candidats et on retient celui qui couvre le plus de libelles, au lieu
    # de faire confiance a la premiere occurrence du premier libelle : « Gardes ? »
    # existe a cinq endroits du scenario, et un seul est la bonne audience.
    vises = [_ink_norm(l) for l in wanted]
    cands = []                       # (position, knot, {libelle normalise: effets})
    vus = set()
    for lab in wanted:
        for i in _ink_positions(txt, lab, 8):
            if i in vus:
                continue
            vus.add(i)
            k = _knot_at(txt, i)
            d = {}
            for lab2, _r2, eff2 in _choices_detailed(k, i):
                d.setdefault(_ink_norm(lab2), eff2)
            if d:
                cands.append((i, k, d))
    cands.sort(key=lambda c: -sum(1 for v in vises if v in c[2]))
    for _i, k, d in cands:
        if not any(v in d for v in vises):
            continue
        if knot is None:
            knot = k
        for n, eff in d.items():
            by_norm.setdefault(n, eff)

    for lab in wanted:
        eff = by_norm.get(_ink_norm(lab))
        row = {'label': lab, 'equipment': [], 'quests': []}
        for kind, val, _extra in (eff or []):
            if kind == 'UnlockEquipment':
                key = str(val).upper()
                info = eq.get(key) or {}
                row['equipment'].append({'id': key, 'name': st.tr(key) or key,
                                         'kind': info.get('kind'),
                                         'known': key in eq})
            elif kind == 'quete':
                q = _quete_par_ref(quests, val)
                row['quests'].append({'id': val,
                                      'name': st.tr(q.get('name_key') or '') or val})
        if eff is None:
            row['note'] = 'option introuvable dans le scenario'
        rows.append(row)

    doc = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'knot': knot, 'hints': rows}
    if dst:
        d = os.path.dirname(dst)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        # Ecriture ATOMIQUE : le mod ne bloque plus sur ce processus, il attend
        # l'apparition du fichier. Sans le renommage, il lirait un JSON a moitie
        # ecrit et le parserait en vain.
        tmp = dst + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))


def _ink_norm(s):
    """Minuscules sans accents ni ponctuation, pour comparer un texte affiche a
    l'ecran avec celui du scenario.

    DECOMPOSER les accents, sinon « enquête » devient « enqute » d'un cote et
    « enquete » de l'autre : la recherche echoue toujours. NFKD ne casse PAS les
    ligatures oe/ae : « l'œuf » se reduisait a « luf » alors que l'utilisateur tape
    « oeuf ». On les deplie a la main.
    """
    import unicodedata
    s = (s.replace('œ', 'oe').replace('Œ', 'OE')
          .replace('æ', 'ae').replace('Æ', 'AE'))
    s = unicodedata.normalize('NFKD', s)
    return re.sub(r'[^a-z0-9]+', '', s.lower())


# Espaces Unicode que la typographie francaise glisse devant « ? » et « ! ».
# Les remplacer par une espace ordinaire ne change PAS la longueur du texte, donc
# les positions restent valables - c'est tout l'interet, une normalisation complete
# decalerait les index et couterait une seconde sur 3,9 Mo.
_ESPACES = {0xA0: ' ', 0x202F: ' ', 0x2007: ' ', 0x2009: ' ', 0x2060: ' '}
_PLAT = {}


def _ink_flat(txt):
    """Le scenario avec ses espaces insecables ramenees a des espaces normales."""
    c = _PLAT.get('c')
    if c is not None and c[0] is txt:
        return c[1]
    flat = txt.translate(_ESPACES)
    _PLAT['c'] = (txt, flat)
    return flat


def _ink_positions(txt, frag, maxn=24):
    """Toutes les positions de `frag`, pas seulement la premiere.

    « Gardes ? » apparait aussi dans « Gardes ?! Faites sortir cet homme », bien
    avant l'audience qui nous interesse : s'arreter a la premiere occurrence menait
    au mauvais knot et l'option ressortait « introuvable ».
    """
    if not frag:
        return []
    flat, ff = _ink_flat(txt), frag.translate(_ESPACES)
    out, pos = [], 0
    while len(out) < maxn:
        i = flat.find(ff, pos)
        if i < 0:
            break
        out.append(i)
        pos = i + 1
    if out:
        return out
    i = _ink_index(txt, frag)
    return [i] if i >= 0 else []


def _ink_index(txt, frag):
    """Position de `frag` dans le scenario, -1 s'il est introuvable.
    Essaie d'abord tel quel, puis en comparaison normalisee."""
    if not frag:
        return -1
    i = _ink_flat(txt).find(frag.translate(_ESPACES))
    if i >= 0:
        return i
    nt, nf = _ink_norm(txt), _ink_norm(frag)
    k = nt.find(nf)
    if k < 0:
        return -1
    cnt = 0                          # index normalise -> index reel
    for i, ch in enumerate(txt):
        if _ink_norm(ch):
            if cnt == k:
                return i
            cnt += 1
    return -1


def cmd_choix(argv):
    """st.py choix "<bout de phrase du jeu>" : les options d'une audience,
    ce que chacune debloque, et la note de chaque chevalier dessus."""
    if not argv:
        print('usage: st.py choix "un bout de phrase affichee a l ecran"')
        return
    frag = ' '.join(a for a in argv if not a.isdigit())
    digits = [a for a in argv if a.isdigit()]
    slot = int(digits[0]) if digits else 1
    txt = st._ink_text()
    i = _ink_index(txt, frag)
    if i < 0:
        print('phrase introuvable dans le scenario : %r' % frag)
        print('donne un fragment plus long, ou sans les mots accentues.')
        return
    knot = _knot_at(txt, i)
    ks, r, e = st.knights_from_save(slot)
    set_welded_reductions(ks)
    apply_save_modifiers(r, e)
    oc = save_modifier_outcomes(r, e, build_outcomes())
    lv = _sovereign_levels(e)
    sat = {k.lower(): v for k, v in (e.get('current_satisfaction') or {}).items()}
    inkvars = _ink_vars(e)
    busy = st.busy_knights(e)
    for n in ks:
        ks[n]['busy'] = n in busy

    print('=== CHOIX  [knot %s]' % knot)
    print('tags souverain : %s' % ' | '.join('%s niv %d' % (k, v)
                                             for k, v in sorted(lv.items())))
    if busy:
        print('indisponibles  : %s' % ', '.join('%s (%s)' % (n.upper(), why)
                                                for n, why in sorted(busy.items())))
    ch = _choices_detailed(knot, i)
    if not ch:
        print('aucun choix trouve dans ce knot -- la phrase est peut-etre une '
              'replique, pas une option. Essaie le texte du bouton.')
        return
    vues = []
    for idx, (lab, req, eff) in enumerate(ch, 1):
        print()
        print('[%d] %s' % (idx, lab))
        for kind, who, need in req:
            if kind == 'tag':
                have, unite = lv.get(who, 0), 'niv'
            elif kind == 'satisfaction':
                have, unite = sat.get(who.lower(), 0), 'satisfaction'
                who = _CAT_FR.get(who.lower(), who)
            elif kind == 'cond':
                expr, visible = _cond_eval(who, inkvars)
                verdict = {True: 'option VISIBLE', False: '>>> option CACHEE <<<'}.get(
                    visible, 'visibilite indeterminee')
                if not expr or not need:
                    verdict += ' (garde partiellement lue)'
                print('    condition: %s  -- %s' % (expr or '?', verdict))
                vus = []
                for k2, v2 in who:
                    if k2 == 'var' and v2 not in vus:
                        vus.append(v2)
                if vus:
                    print('               %s' % '  |  '.join(
                        '%s = %s' % (v2, 'oui' if inkvars.get(v2) else 'non')
                        for v2 in vus))
                continue
            print('    requiert : %s %s %d  -- tu as %d  %s'
                  % (who, unite, need, have,
                     'OK' if have >= need else '>>> VERROUILLE <<<'))
        if not eff:
            print('    effets   : aucun effet mecanique (replique seule)')
        for kind, val, extra in eff:
            if kind == 'quete':
                print('    debloque : %s%s' % (val, extra))
                if val not in vues:
                    vues.append(val)
            elif kind == 'tag':
                # `extra` vaut un entier quand l'effet est lu dans le bloc lui-meme,
                # et une chaine « (via ...) » quand il vient d'un renvoi.
                print('    tag      : %s%s' % (val, ' %+d' % extra
                                               if isinstance(extra, int) else extra))
            elif kind == 'var':
                print('    variable : %s = vrai%s' % (val, extra or ''))
            else:
                print('    %-9s: %s' % (kind, val))
    if vues:
        print()
        print('--- quetes concernees ---')
        for qid in vues:
            if qid.endswith('candidacy'):
                print('   %s : candidature (recrutement), pas une quete' % qid)
                continue
            print()
            _quest_card(qid, ks, oc)
