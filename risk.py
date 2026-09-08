# -*- coding: utf-8 -*-
"""st.py risk : ce que tu es en train de PERDRE sans le voir.

Deux causes de fermeture d'un fil narratif, constatees en partie :
  1. une issue inattendue exige un chevalier precis, absent du roster ;
  2. un recrutement encore ouvert que personne ne relance.
"""
import glob, os, json
import st, plan as P
from gdres import Res


def _outcomes_by_knight():
    """{quest_id: [(knight, fichier_outcome)]} pour toutes les issues scriptees."""
    out = {}
    for f in glob.glob(os.path.join(st.CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        main = None
        specs = []
        for p, (t, pr) in r.resources.items():
            if pr.get('quest_id'):
                main = pr
            if pr.get('knights') is not None and 'rewards' in pr:
                specs.append(pr)
        if not main or not specs:
            continue
        for s in specs:
            for k in (s.get('knights') or []):
                if isinstance(k, dict) and '@ext' in k:
                    who = k['@ext'][1].split('/')[-1].replace('.tres', '')
                    out.setdefault(main['quest_id'], []).append(who)
    return out


def cmd_risk(argv=()):
    ks, r, e = st.knights_from_save()
    quests = P.C('quests')
    by = {v['path'].split('/')[-1].replace('.tres', ''): k for k, v in quests.items()}
    roster = set(ks)
    current = []
    for k in (e.get('current_quests') or {}):
        n = st._nm(k)
        current.append(n if n in quests else by.get(n))

    spec = _outcomes_by_knight()

    print('=== ISSUES INATTENDUES HORS DE PORTEE (quetes SUR LA TABLE) ===')
    hit = False
    for qid in current:
        for who in spec.get(qid, []):
            if who not in roster:
                hit = True
                print('  %-44s exige %-12s -> ABSENT du roster'
                      % ((st.tr(quests[qid]['name_key']) or qid), who))
    if not hit:
        print('  (aucune : toutes les issues des quetes en cours sont atteignables)')

    print()
    print('=== CHEVALIERS QUI VERROUILLENT LE PLUS DE CONTENU ===')
    tally = {}
    for qid, whos in spec.items():
        for w in whos:
            if w not in roster:
                tally.setdefault(w, []).append(qid)
    for w, qs in sorted(tally.items(), key=lambda x: -len(x[1]))[:8]:
        print('  %-12s %2d issue(s) inaccessible(s)  ex: %s'
              % (w, len(qs), ', '.join(qs[:2])))

    print()
    print('=== RECRUTEMENTS ENCORE OUVERTS ===')
    for k in ('available_knights_call_backs', 'available_audience_requests',
              'waiting_audience_requests'):
        v = [st._nm(x) for x in (e.get(k) or [])]
        print('  %-32s %s' % (k, ', '.join(v) if v else '-'))
    print()
    print('roster (%d) : %s' % (len(roster), ', '.join(sorted(roster))))
