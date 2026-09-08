# -*- coding: utf-8 -*-
"""
`python st.py jour` : LA journee complete, en une commande.

Trois sections :
  1. AUDIENCE      -- pour chaque quete proposable, la variante a prendre et ce
                      qu'elle donne (exigences, recompenses, portes de satisfaction,
                      quetes que la branche debloque et que les autres ne debloquent pas)
  2. FIN DE JOURNEE -- qui part sur quoi, quoi acheter, quel repas, quelle montee de niveau
  3. PROJECTION    -- cycle par cycle : combien de chevaliers seront libres, quelles
                      echeances tombent, et lesquelles vont echouer faute de corps

Regles encodees (chacune vient d'une erreur reelle) :
  - table ronde plafonnee : 6 chevaliers a l'acte 1, 8 a l'acte 2, 10 a l'acte 3.
    Un conflit d'effectif ne se resout JAMAIS par du recrutement.
  - ne pas boucler une quete en reussite ordinaire si une issue scriptee meilleure
    y est encore atteignable et qu'elle n'a pas de delai
  - une issue scriptee peut etre la SEULE branche qui debloque une quete de suite
  - duree > 1 : le chevalier est pris jusqu'a cycle+duree-1 inclus
  - sur une quete a conditions supplementaires, tout score <= 0 est un ECHEC CRITIQUE
  - la sauvegarde retarde sur le jeu (salles, or) : ne rien affirmer, proposer --meals
"""
import os, sys, json, itertools, copy
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import st
import plan as P

ACT_CAP = {0: 6, 1: 8, 2: 10}


# ---------------------------------------------------------------- variantes d'audience
def quest_variants(qid):
    """Toutes les versions d'une quete (base + modificateurs), telles que l'audience
    les propose. Le .tres porte `modifiers`; chacun modifie exigences / degats /
    effectif / duree et AJOUTE des recompenses."""
    import glob
    from gdres import Res
    quests = P.C('quests')
    if qid not in quests:
        return []
    fname = quests[qid]['path'].split('/')[-1].replace('.tres', '')
    enums = P.C('enums')
    S = enums['Statistics']
    out = [{'name': 'BASE (aucun modificateur)', 'q': json.loads(json.dumps(quests[qid])), 'note': ''}]
    for f in glob.glob(os.path.join(st.CACHE, 'res', '*%s.res' % fname)):
        try:
            r = Res(f)
        except Exception:
            continue
        main = None
        for p, (t, pr) in r.resources.items():
            if pr.get('quest_id') == qid:
                main = pr
        if not main:
            continue
        for i, x in enumerate(main.get('modifiers') or []):
            if not isinstance(x, dict) or '@sub' not in x:
                continue
            pr = r.resources[x['@sub']][1]
            q = json.loads(json.dumps(quests[qid]))
            notes = []
            for k, d in (pr.get('stats_requirements_modification') or {}).items():
                if not d:
                    continue
                stat = S[k]
                q['stats'][stat] = max(0, q['stats'].get(stat, 0) + d)
                notes.append('%s %+d' % (st.sfr(stat), d))
            dm = pr.get('damage_modification') or 0
            if dm:
                q['damages'] = [max(0, q['damages'][0] + dm), max(0, q['damages'][1] + dm)]
                notes.append('degats %+d' % dm)
            nm = pr.get('nb_requested_knights_modification') or 0
            if nm:
                q['nb_knights'] = max(1, q['nb_knights'] + nm)
                notes.append('effectif %+d' % nm)
            du = pr.get('duration_modification') or 0
            if du:
                q['duration'] = max(1, q['duration'] + du)
                notes.append('duree %+d' % du)
            extra = []
            for y in (pr.get('success_rewards_modification') or []):
                if isinstance(y, dict) and '@sub' in y:
                    extra.append(st._rew(r.resources[y['@sub']][1], enums))
            if extra:
                q['success'] = list(q['success']) + extra
            out.append({'name': 'MODIFICATEUR %d' % i, 'q': q, 'note': ', '.join(notes) or 'exigences inchangees'})
        break
    return out


def satisfaction_gates(qid):
    """Seuils de satisfaction qui conditionnent la PROPOSITION d'une quete.
    Ils ne sont pas dans le .tres : ils sont dans l'ink de l'audience.
    Appariement STRICT (la condition colle immediatement a sa quete) : une fenetre
    large attrapait la condition de la quete VOISINE et sous-estimait le seuil."""
    import re
    txt = st._ink_text()
    out = set()
    pat = (r'\{"VAR\?":"(\w+)"\},(\d+),\{"f\(\)":"RequiresMinSatisfaction"\},"out","/ev",'
           r'"ev",\{"VAR\?":"QUEST"\},\{"VAR\?":"%s"\}' % re.escape(qid))
    for m in re.finditer(pat, txt):
        out.add((m.group(1), int(m.group(2))))
    return sorted(out)


def choice_labels(qid):
    """Libelles EXACTS des choix d'audience -> index de modificateur.
    Motif ink : {"VAR?":"QUEST"},{"VAR?":"<qid>"},<idx|str>,{"f()":"HintModification"},"out","/ev","^<LABEL>"
    (c'est en devinant ce mapping que je m'etais trompe sur Villador.)"""
    import re
    txt = st._ink_text()
    out = {}
    pat = (r'\{"VAR\?":"QUEST"\},\{"VAR\?":"%s"\},(?:(\d+)|"str","\^","/str"),'
           r'\{"f\(\)":"HintModification"\},"out","/ev",(?:.{0,120}?)"\^([^"]{4,120})"' % re.escape(qid))
    for m in re.finditer(pat, txt, re.S):
        idx = int(m.group(1)) if m.group(1) is not None else None
        out.setdefault(idx, m.group(2))
    return out


def branch_diff(qid, oc):
    """Ce que chaque branche de resolution debloque, et ce qui n'existe QUE par une seule."""
    q = P.C('quests').get(qid) or {}
    br = {}
    for lbl, key in (('reussite', 'success_follow_up'), ('echec', 'failure_follow_up')):
        if q.get(key):
            br[lbl] = st.branch_unlocks(q[key]) or []
    for o in oc['by_quest'].get(qid, []):
        fu = (oc['outcomes'].get(o) or {}).get('follow_up')
        if fu:
            br['INATTENDUE'] = st.branch_unlocks(fu.split('/')[-1].replace('.tres', '')) or []
    allq = set().union(*br.values()) if br else set()
    only = {}
    for x in allq:
        owners = [l for l, v in br.items() if x in v]
        if len(owners) == 1:
            only[x] = owners[0]
    return br, only


# ---------------------------------------------------------------- etat du cycle
def load_state(slot=1, force_meals=None):
    ks, r, e = st.knights_from_save(slot)
    P.apply_save_modifiers(r, e)
    oc = P.save_modifier_outcomes(r, e, P.build_outcomes())
    quests = P.C('quests')
    by_file = {}
    for qid, q in quests.items():
        by_file.setdefault(q['path'].split('/')[-1].replace('.tres', ''), qid)
    live, current, unknown = {}, [], []
    for k, v in (e.get('current_quests') or {}).items():
        n = st._nm(k)
        qid = n if n in quests else by_file.get(n)
        if qid:
            live[qid] = v
            current.append(qid)
        else:
            unknown.append(n)
    busy = st.busy_knights(e)
    free = {n: k for n, k in ks.items() if n not in busy}
    meals = 'kitchen' in set(st._nm(x) for x in (e.get('unlocked_rooms') or []))
    if force_meals is not None:
        meals = force_meals
    ult = P.ultimatum_extra_conditions(r, e)
    ult_cycle, floor = None, 0
    if ult:
        ult_cycle = r.resources[e['current_ultimatum']['@sub']][1].get('targeted_cycle_index')
        # la condition MIN_FUNDS de l'ultimatum fixe l'or a ne pas depenser
        for txt, _ok in ult['list']:
            if txt.startswith('or >='):
                try:
                    floor = int(txt.split('>=')[1].split('(')[0].strip())
                except Exception:
                    pass
    return dict(ks=ks, r=r, e=e, oc=oc, live=live, current=current, unknown=unknown,
                busy=busy, free=free, meals=meals, ult=ult, ult_cycle=ult_cycle,
                cyc=(e.get('cycle_index') or 0) + 1,
                gold=e.get('current_funds') or 0,
                act=e.get('current_act') or 0,
                floor=floor,
                pend=P.pending_levels(e),
                avail=P.available_items(e, r))


# ---------------------------------------------------------------- faisabilite d'ordonnancement
def loss_cost(qid, q):
    """Ce que coute l'ECHEC de cette quete. Sert a choisir laquelle sacrifier quand
    le calendrier est infaisable -- surtout PAS le nombre de corps mobilises."""
    c = 1.0
    for rw in (q.get('failure') or []):
        t = rw.get('type')
        if t == 'LOCATION_DESTROYED':
            c += 8.0
        elif t == 'CHARACTER_DEATH':
            c += 25.0
        elif t == 'BOOL_STORY_VAR_MODIF':
            c += 4.0
        elif t == 'SATISFACTION':
            c += abs(rw.get('amount') or 0)
    if q.get('type') == 'ULTIMATUM_QUEST':
        c += 100.0
    # une quete a conditions supplementaires tue ses chevaliers si le score tombe <= 0
    if q.get('extra_conditions'):
        c += 20.0
    for rw in (q.get('success') or []):
        if rw.get('type') == 'FUNDS':
            c += (rw.get('amount') or 0) / 100.0
        elif rw.get('type') == 'SATISFACTION':
            c += abs(rw.get('amount') or 0) * 0.3
    return c


def min_viable_size(S, qid, q, budget=None):
    """Plus petite equipe qui REUSSIT ENCORE cette quete (le jeu autorise d'envoyer
    moins de chevaliers que demande : exigences +1 par manquant). Sert au calendrier :
    une urgence a 3 qui passe a 2 libere un corps pour une autre echeance."""
    names = sorted(S['free']) or sorted(S['ks'])
    src = S['free'] if S['free'] else S['ks']
    import itertools as _it
    for size in range(1, q['nb_knights'] + 1):
        for c in _it.combinations(names, size):
            team = [src[n] for n in c]
            try:
                if any(P.is_random(n) for n in c):
                    mc = P.mc_evaluate(qid, team, S['oc'], 1, n=200)
                    sc = mc['mean'] if mc else None
                else:
                    sc, _o = st.score(qid, team, meals=1, verbose=False, quest=q)
            except Exception:
                continue
            if sc is not None and sc > 0:
                return size, round(sc, 2), list(c)
    return q['nb_knights'], None, None


def failure_is_useful(qid, oc):
    """L'ECHEC de cette quete debloque-t-il quelque chose que la reussite ne donne pas ?
    Seule raison legitime de laisser tomber une quete."""
    br, only = branch_diff(qid, oc)
    return {x: w for x, w in only.items() if w == 'echec'}


def base_occupancy(S, horizon):
    """Chevaliers deja pris par cycle (quetes en cours)."""
    occ = {}
    for k, v in (S['e'].get('ongoing_quests') or {}).items():
        rem = v.get('duration') or 1
        n = len([x for x in (v.get('assigned_knights') or []) if isinstance(x, dict)])
        for c in range(S['cyc'], S['cyc'] + rem):
            occ[c] = occ.get(c, 0) + n
    return occ


def deadline_events(S, extra=None):
    """(dernier cycle utile, nom, nb chevaliers, duree) pour chaque echeance connue."""
    quests = P.C('quests')
    ev = []
    for qid in S['current']:
        q = P.MODS.get(qid) or quests[qid]
        if not q['deadline']:
            continue
        rem = S['live'].get(qid, {}).get('remaining_cycles_before_faillure')
        if rem is None:
            continue
        nb_min, sc_min, who_min = min_viable_size(S, qid, q)
        ev.append([S['cyc'] + rem - 1, st.tr(q['name_key']) or qid, nb_min, q['duration'],
                   loss_cost(qid, q), q['nb_knights'], sc_min])
    if S['ult_cycle']:
        uq = quests.get('quest_ultimatum_dragonknight') or {}
        ev.append([S['ult_cycle'], 'ULTIMATUM', uq.get('nb_knights', 4), uq.get('duration', 1), 100.0])
    if extra:
        ev.append(list(extra))
    ev.sort()
    return ev


def schedule_feasible(S, events, horizon=6):
    """Existe-t-il un calendrier ou TOUTES les echeances partent a temps sans
    depasser l'effectif ? Renvoie (ok, calendrier, quete sacrifiee la moins chere)."""
    total = len(S['ks'])
    occ0 = base_occupancy(S, horizon)
    starts = []
    for ev in events:
        starts.append([c for c in range(S['cyc'], ev[0] + 1)])
    best = None

    def rec(i, occ, acc):
        nonlocal best
        if best is not None:
            return
        if i == len(events):
            best = list(acc)
            return
        last, nm, nb, dur = events[i][0], events[i][1], events[i][2], events[i][3]
        for c in starts[i]:
            ok = True
            for d in range(c, c + dur):
                if occ.get(d, 0) + nb > total:
                    ok = False
                    break
            if not ok:
                continue
            o2 = dict(occ)
            for d in range(c, c + dur):
                o2[d] = o2.get(d, 0) + nb
            acc.append((nm, c, nb, dur))
            rec(i + 1, o2, acc)
            acc.pop()
            if best is not None:
                return

    rec(0, occ0, [])
    if best is not None:
        return True, best, None
    # infaisable : quelle echeance sacrifier coute le moins de "corps-cycles" ?
    worst = None
    for j in range(len(events)):
        sub = [e for k, e in enumerate(events) if k != j]
        ok2, sched2, _ = schedule_feasible(S, sub, horizon) if sub else (True, [], None)
        if ok2:
            cost = events[j][4] if len(events[j]) > 4 else 1.0
            if worst is None or cost < worst[0]:
                worst = (cost, events[j], sched2)
    return False, None, worst


def cost_of_choice(S, qid, nb, dur, last_useful, loss):
    """Cette version de la quete casse-t-elle le calendrier des autres echeances ?
    On REMPLACE l'entree existante de `qid` : sinon la quete serait comptee deux fois."""
    quests = P.C('quests')
    nm_self = st.tr(quests[qid]['name_key']) or qid
    ev = [e for e in deadline_events(S) if e[1] != nm_self]
    ev.append([last_useful, nm_self, nb, dur, loss])
    ev.sort()
    ok, sched, sacrifice = schedule_feasible(S, ev)
    return ok, sched, sacrifice


# ---------------------------------------------------------------- section 1 : audience
def show_audience(S, motifs):
    quests = P.C('quests')
    print('=' * 78)
    print('1. AUDIENCE -- quelle version prendre')
    print('=' * 78)
    if not motifs:
        print("  (aucune quete demandee : passe les noms proposes, ex. `st.py jour villador these`)")
        return
    for motif in motifs:
        hits = [qid for qid, q in quests.items()
                if motif.lower() in qid.lower() or motif.lower() in (st.tr(q['name_key']) or '').lower()]
        if not hits:
            print('  ?? aucune quete ne correspond a %r' % motif)
            continue
        for qid in hits[:2]:
            q0 = quests[qid]
            print()
            print('### %s  [%s]' % (st.tr(q0['name_key']) or qid, qid))
            for pop, amt in satisfaction_gates(qid):
                print('    PORTE : n\'est proposee que si %s >= %d' % (pop, amt))
            br, only = branch_diff(qid, S['oc'])
            if only:
                for x, who in only.items():
                    print('    /!\\ %s n\'existe QUE par la branche %s' % (x, who))
            labels = choice_labels(qid)
            for i, v in enumerate(quest_variants(qid)):
                q = v['q']
                idx = None if i == 0 else i - 1
                lab = labels.get(idx)
                best = best_team_for(qid, q, S)
                res = '%6.2f %s' % (best[0], best[1]) if best else '  -- injouable'
                who = '+'.join(best[2]) if best else ''
                print()
                print('    [%s] %s' % (v['name'], ('« %s »' % lab) if lab else '(libelle non trouve)'))
                print('        %s | %d chev. | %d cycle(s) | degats %s   (%s)'
                      % (st.stats_fr(q['stats']), q['nb_knights'], q['duration'], q['damages'], v['note']))
                print('        resultat  : %s  %s' % (res, who))
                print('        recompense: %s' % P.fmt_rewards(q['success']))
                if q.get('failure'):
                    print('        si echec  : %s' % P.fmt_rewards(q['failure']))
                # impact sur le calendrier des echeances
                rem = S['live'].get(qid, {}).get('remaining_cycles_before_faillure')
                if rem:
                    ok, sched, sac = cost_of_choice(S, qid, q['nb_knights'], q['duration'],
                                                    S['cyc'] + rem - 1, loss_cost(qid, q))
                    if ok:
                        print('        calendrier: TENABLE -- toutes les echeances peuvent partir a temps')
                    elif sac:
                        print('        calendrier: INFAISABLE -- il faudra sacrifier %s' % sac[1][1])
                    else:
                        print('        calendrier: INFAISABLE -- aucune combinaison ne tient')


def best_team_for(qid, q, S):
    """Meilleure equipe possible pour une version de quete, parmi les chevaliers libres."""
    names = sorted(S['free'])
    best = None
    for size in range(1, min(q['nb_knights'], len(names)) + 1):
        for c in itertools.combinations(names, size):
            team = [S['free'][n] for n in c]
            if any(P.is_random(n) for n in c):
                mc = P.mc_evaluate(qid, team, S['oc'], 0, n=300)
                if not mc:
                    continue
                sc, o = mc['mean'], max(mc['probs'].items(), key=lambda x: x[1])[0]
            else:
                try:
                    sc, o = st.score(qid, team, meals=0, verbose=False, quest=q)
                except Exception:
                    continue
            if best is None or sc > best[0]:
                best = (sc, o, list(c))
    return best


# ---------------------------------------------------------------- section 3 : projection
def project(S, horizon=5):
    """Effectif contre echeances, avec test de faisabilite reel."""
    cap = ACT_CAP.get(S['act'], 6)
    total = len(S['ks'])
    occ = base_occupancy(S, horizon)
    events = deadline_events(S)

    print()
    print('=' * 78)
    print("3. PROJECTION -- peut-on encore tout tenir ?   (plafond table ronde : %d, acte %d)"
          % (cap, S['act'] + 1))
    print('=' * 78)
    for c in range(S['cyc'], S['cyc'] + horizon + 1):
        libres = total - occ.get(c, 0)
        marks = [x for x in events if x[0] == c]
        line = '  cycle %-3d %d/%d libres' % (c, libres, total)
        if marks:
            line += '   DERNIER CYCLE UTILE : ' + ', '.join(
                '%s [%d chev., %d cycle(s)]' % (x[1], x[2], x[3]) for x in marks)
        print(line)

    if not events:
        print()
        print('  aucune echeance connue : rien ne peut rater par manque de corps.')
        return
    ok, sched, sacrifice = schedule_feasible(S, events)
    print()
    if ok:
        print('  CALENDRIER TENABLE -- toutes les echeances peuvent partir a temps :')
        for nm, c, nb, dur in sorted(sched, key=lambda x: x[1]):
            print('    cycle %-3d %-42s %d chevalier(s) pendant %d cycle(s)' % (c, nm, nb, dur))
        print()
        print('  >>> Ne bloque AUCUN chevalier au-dela de ce calendrier : chaque quete')
        print('      longue prise en plus fait tomber une echeance.')
    else:
        need = sum(x[2] for x in events)
        print('  CALENDRIER INFAISABLE : %d echeances reclament %d corps, tu en as %d.'
              % (len(events), need, total))
        for ev in events:
            nominal = ev[5] if len(ev) > 5 else ev[2]
            red = '' if nominal == ev[2] else '  (reduite de %d a %d, score %s)' % (nominal, ev[2], ev[6])
            print('    cycle %-3d %-40s %d chev. x %d cycle(s)  [perte : %.0f]%s'
                  % (ev[0], ev[1], ev[2], ev[3], ev[4] if len(ev) > 4 else 1, red))
        if sacrifice:
            cost, ev, sched2 = sacrifice
            print()
            print('  >>> Il FAUT en sacrifier une. La moins grave a perdre : %s' % ev[1])
            print('      (cout de sa perte : %.0f ; elle libere %d chevalier(s) x %d cycle(s))'
                  % (cost, ev[2], ev[3]))
            if sched2:
                print('      Le reste devient tenable ainsi :')
                for nm, c, nb, dur in sorted(sched2, key=lambda x: x[1]):
                    print('        cycle %-3d %-40s %d chev.' % (c, nm, nb))
        print()
        print("  Le recrutement ne peut PAS aider : plafond %d a l'acte %d." % (cap, S['act'] + 1))


# ---------------------------------------------------------------- commande
def cmd_day(*args):
    slot, motifs, force_meals = 1, [], None
    for a in args:
        if a == '--meals':
            force_meals = True
        elif a == '--no-meals':
            force_meals = False
        elif a.isdigit() and int(a) < 10:
            slot = int(a)
        else:
            motifs.append(a)
    S = load_state(slot, force_meals)
    print('CYCLE %d | %d or (dont %d a preserver -> %d depensables) | %d/%d chevaliers libres | repas %s'
          % (S['cyc'], S['gold'], S['floor'], max(0, S['gold'] - S['floor']),
             len(S['free']), len(S['ks']),
             'disponible' if S['meals'] else 'cuisine listee verrouillee (--meals pour forcer)'))
    if S['busy']:
        print('  indisponibles : %s' % ', '.join('%s (%s)' % (n, w) for n, w in S['busy'].items()))
    if S['pend']:
        print('  montees en attente : %s' % ', '.join('%s x%d' % (n, v) for n, v in S['pend'].items()))
    if S['unknown']:
        print('  !! quetes non reconnues : %s' % ', '.join(S['unknown']))
    if S['ult']:
        manque = [t for t, ok in S['ult']['list'] if not ok]
        print('  ULTIMATUM cycle %s -- bonus +%d ; conditions manquantes : %s'
              % (S['ult_cycle'], sum(S['ult']['met'].values()), '; '.join(manque) or 'AUCUNE'))
    print()
    show_audience(S, motifs)
    print()
    print('=' * 78)
    print('2. FIN DE JOURNEE -- qui part sur quoi')
    print('=' * 78)
    P.main([str(slot), '--floor=%d' % S['floor']] + (['--meals'] if S['meals'] else ['--no-meals']))
    project(S)
