# -*- coding: utf-8 -*-
"""st.py meal [chevalier] -- plats aimes de chaque chevalier, instantanement.
Le user a du attendre une fouille manuelle des .res pour une question a 2 secondes."""
import glob
import json
import os
import st
from gdres import Res

CACHE = st.CACHE
J = os.path.join(CACHE, 'meals.json')


def build():
    enum = json.load(open(os.path.join(CACHE, 'enums.json'), encoding='utf-8'))['MealsID']
    out = {}
    for f in glob.glob(os.path.join(CACHE, 'res', '*.res')):
        try:
            r = Res(f)
        except Exception:
            continue
        for p, (_t, pr) in r.resources.items():
            if '/knights/' not in p or 'liked_meals' not in pr:
                continue
            n = p.split('/')[-1].replace('.tres', '')
            out[n] = [enum.get(str(x), str(x)) for x in (pr.get('liked_meals') or [])]
    json.dump(out, open(J, 'w', encoding='utf-8'), ensure_ascii=False)
    return out


def data(force=False):
    if os.path.exists(J) and not force:
        return json.load(open(J, encoding='utf-8'))
    return build()


def cmd(argv):
    d = data('--rebuild' in argv)
    who = [a for a in argv if not a.startswith('-')]
    fr = lambda m: st.tr(m + '_NAME') or m
    for n in sorted(d):
        if who and not any(w.lower() in n for w in who):
            continue
        print('%-10s %s' % (n.upper(), ', '.join(fr(m) for m in d[n]) or '-'))
