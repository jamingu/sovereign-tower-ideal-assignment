import struct, sys, json

class R:
    def __init__(s,d): s.d=d; s.p=0
    def u32(s):
        v,=struct.unpack_from('<I',s.d,s.p); s.p+=4; return v
    def i32(s):
        v,=struct.unpack_from('<i',s.d,s.p); s.p+=4; return v
    def u64(s):
        v,=struct.unpack_from('<Q',s.d,s.p); s.p+=8; return v
    def i64(s):
        v,=struct.unpack_from('<q',s.d,s.p); s.p+=8; return v
    def f32(s):
        v,=struct.unpack_from('<f',s.d,s.p); s.p+=4; return v
    def f64(s):
        v,=struct.unpack_from('<d',s.d,s.p); s.p+=8; return v
    def raw(s,n):
        v=s.d[s.p:s.p+n]; s.p+=n; return v
    def ustr(s):
        n=s.u32()
        b=s.raw(n)
        return b.split(b'\x00')[0].decode('utf-8','replace')
    def pad(s):
        while s.p % 4: s.p+=1

V={1:'NIL',2:'BOOL',3:'INT',4:'FLOAT',5:'STRING',10:'VECTOR2',11:'RECT2',12:'VECTOR3',
13:'PLANE',14:'QUAT',15:'AABB',16:'BASIS',17:'TRANSFORM3D',18:'TRANSFORM2D',20:'COLOR',
22:'NODE_PATH',23:'RID',24:'OBJECT',25:'INPUT_EVENT',26:'DICTIONARY',30:'ARRAY',
31:'PBA',32:'PI32',33:'PF32',34:'PSA',35:'PV3A',36:'PCA',37:'PV2A',40:'INT64',41:'DOUBLE',
42:'CALLABLE',43:'SIGNAL',44:'STRING_NAME',45:'VECTOR2I',46:'RECT2I',47:'VECTOR3I',
48:'PI64',49:'PF64',50:'VECTOR4',51:'VECTOR4I',52:'PROJECTION',53:'PV4A'}

class Res:
    def __init__(s, path, off_base=0):
        d=open(path,'rb').read()
        if d[:4]==b'RSCC':
            raise Exception('compressed res not supported')
        assert d[:4]==b'RSRC', d[:4]
        r=R(d); s.r=r
        r.p=4
        s.big=r.u32(); s.real64=r.u32()
        s.vmaj=r.u32(); s.vmin=r.u32(); s.vfmt=r.u32()
        s.type=r.ustr()
        s.md_off=r.u64()
        s.flags=r.u32()
        s.uid=r.u64() if (s.flags & 2) else 0   # FORMAT_FLAG_UIDS = 1<<1? try
        s.script_class = r.ustr() if (s.vfmt>=5 and (s.flags & 8)) else ''
        for _ in range(11): r.u32()
        n=r.u32()
        s.strings=[r.ustr() for _ in range(n)]
        ne=r.u32()
        s.ext=[]
        for _ in range(ne):
            t=r.ustr(); p=r.ustr()
            u = r.u64() if (s.flags & 2) else 0
            s.ext.append((t,p,u))
        ni=r.u32()
        s.intlist=[]
        for _ in range(ni):
            p=r.ustr(); off=r.u64()
            s.intlist.append((p,off))
        s.resources={}
        s.off_base=off_base
        for p,off in s.intlist:
            r.p=off+off_base
            rtype=r.ustr()
            pc=r.u32()
            props={}
            for _ in range(pc):
                name=s.gets()
                props[name]=s.var()
            s.resources[p]=(rtype,props)
    def gets(s):
        i=s.r.u32()
        if i & 0x80000000:
            n=i & 0x7FFFFFFF
            b=s.r.raw(n)
            return b.split(b'\x00')[0].decode('utf-8','replace')
        return s.strings[i]
    def var(s):
        r=s.r
        t=r.u32()
        n=V.get(t,'?%d'%t)
        if n=='NIL': return None
        if n=='BOOL': return bool(r.u32())
        if n=='INT': return r.i32()
        if n=='INT64': return r.i64()
        if n=='FLOAT': return r.f32()
        if n=='DOUBLE': return r.f64()
        if n in ('STRING','STRING_NAME'): return r.ustr()
        if n=='VECTOR2': return (r.f32(),r.f32())
        if n=='VECTOR2I': return (r.i32(),r.i32())
        if n=='VECTOR3': return (r.f32(),r.f32(),r.f32())
        if n=='VECTOR3I': return (r.i32(),r.i32(),r.i32())
        if n=='VECTOR4': return tuple(r.f32() for _ in range(4))
        if n=='COLOR': return tuple(r.f32() for _ in range(4))
        if n=='RECT2': return tuple(r.f32() for _ in range(4))
        if n=='NODE_PATH':
            names=struct.unpack_from('<H',r.d,r.p)[0]; r.p+=2
            sub=struct.unpack_from('<H',r.d,r.p)[0]; r.p+=2
            absolute=bool(sub&0x8000); sub&=0x7FFF
            parts=[s.gets_np() for _ in range(names+sub)]
            return {'@nodepath':('/' if absolute else '')+'/'.join(parts)}
        if n=='OBJECT':
            ot=r.u32()
            if ot==0: return None
            if ot==1:
                t2=r.ustr(); p2=r.ustr()
                return {'@ext':(t2,p2)}
            if ot==2:
                idx=r.u32()
                return {'@sub':s.intlist[idx][0] if idx<len(s.intlist) else idx}
            if ot==3:
                idx=r.u32()
                e=s.ext[idx] if idx<len(s.ext) else idx
                return {'@ext':e}
            raise Exception('obj type %d'%ot)
        if n=='DICTIONARY':
            ln=r.u32(); ln&=0x7FFFFFFF
            out={}
            for _ in range(ln):
                k=s.var(); v=s.var()
                out[str(k)]=v
            return out
        if n=='ARRAY':
            ln=r.u32(); ln&=0x7FFFFFFF
            return [s.var() for _ in range(ln)]
        if n=='PBA':
            ln=r.u32(); b=r.raw(ln); r.p+=(4-ln%4)%4; return list(b)
        if n=='PSA':
            ln=r.u32(); return [r.ustr() for _ in range(ln)]
        if n=='PI32':
            ln=r.u32(); return [r.i32() for _ in range(ln)]
        if n=='PI64':
            ln=r.u32(); return [r.i64() for _ in range(ln)]
        if n=='PF32':
            ln=r.u32(); return [r.f32() for _ in range(ln)]
        if n=='PF64':
            ln=r.u32(); return [r.f64() for _ in range(ln)]
        raise Exception('unhandled variant %s at %d'%(n,r.p))
    def gets_np(s):
        i=s.r.u32()
        if i & 0x80000000:
            nn=i & 0x7FFFFFFF; b=s.r.raw(nn)
            return b.split(b'\x00')[0].decode('utf-8','replace')
        return s.strings[i]

if __name__=='__main__':
    rr=Res(sys.argv[1])
    print('type',rr.type,'fmt',rr.vfmt,'flags',rr.flags,'script',rr.script_class)
    print('EXT:')
    for e in rr.ext: print('  ',e)
    for p,(t,pr) in rr.resources.items():
        print('--- %s (%s)'%(p,t))
        print(json.dumps(pr,ensure_ascii=False,indent=1,default=str))
