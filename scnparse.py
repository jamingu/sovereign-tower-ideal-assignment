import sys, json
sys.path.insert(0,'.')
from gdres import Res
def parse_scene(path):
    r=Res(path)
    ps=None
    for p,(t,pr) in r.resources.items():
        if t=='PackedScene': ps=pr['_bundled']
    names=ps['names']; variants=ps['variants']; nodes=ps['nodes']
    out=[]; idx=0
    for i in range(ps['node_count']):
        parent=nodes[idx]; idx+=1
        owner=nodes[idx]; idx+=1
        typ=nodes[idx]; idx+=1
        ni=nodes[idx]; idx+=1
        name=names[ni & ((1<<16)-1)]
        inst=nodes[idx]; idx+=1
        pc=nodes[idx]; idx+=1
        props={}
        for _ in range(pc):
            pn=nodes[idx]; idx+=1
            pv=nodes[idx]; idx+=1
            props[names[pn & 0x3FFFFFFF]]=variants[pv]
        gc=nodes[idx]; idx+=1
        idx+=gc
        out.append(dict(i=i,parent=parent,name=name,type=(names[typ] if typ>=0 and typ<len(names) else typ),props=props))
    return r,names,variants,out
if __name__=='__main__':
    r,names,variants,nodes=parse_scene(sys.argv[1])
    for n in nodes:
        print(n['i'],'parent',n['parent'],n['name'],json.dumps(n['props'],ensure_ascii=False,default=str))
