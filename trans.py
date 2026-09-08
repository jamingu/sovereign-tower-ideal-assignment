import sys, struct
sys.path.insert(0,'.')
from gdres import Res

RCB = [
" ", "the", "e", "t", "a", "of", "o", "and", "i", "n", "s", "e ", "r", " th",
" t", "in", "he", "th", "h", "he ", "to", "\r\n", "l", "s ", "d", " a", "an",
"er", "c", " o", "d ", "on", " of", "re", "of ", "t ", ", ", "is", "u", "at",
"   ", "n ", "or", "which", "f", "m", "as", "it", "that", "\n", "was", "en",
"  ", " w", "es", " an", " i", "\r", "f ", "g", "p", "nd", " s", "nd ", "ed ",
"w", "ed", "http://", "for", "te", "ing", "y ", "The", " c", "ti", "r ", "his",
"st", " in", "ar", "nt", ",", " to", "y", "ng", " h", "with", "le", "al", "to ",
"b", "ou", "be", "were", " b", "se", "o ", "ent", "ha", "ng ", "their", "\"",
"hi", "from", " f", "in ", "de", "ion", "me", "v", ".", "ve", "all", "re ",
"ri", "ro", "is ", "co", "f t", "are", "ea", ". ", "her", " m", "er ", " p",
"es ", "by", "they", "di", "ra", "ic", "not", "s, ", "d t", "at ", "ce", "la",
"h ", "ne", "as ", "tio", "on ", "n t", "io", "we", " a ", "om", ", a", "s o",
"ur", "li", "ll", "ch", "had", "this", "e t", "g ", "e\r\n", " wh", "ere",
" co", "e o", "a ", "us", " d", "ss", "\n\r\n", "\r\n\r", "=\"", " be", " e",
"s a", "ma", "one", "t t", "or ", "but", "el", "so", "l ", "e s", "s,", "no",
"ter", " wa", "iv", "ho", "e a", " r", "hat", "s t", "ns", "ch ", "wh", "tr",
"ut", "/", "have", "ly ", "ta", " ha", " on", "tha", "-", " l", "ati", "en ",
"pe", " re", "there", "ass", "si", " fo", "wa", "ec", "our", "who", "its", "z",
"fo", "rs", ">", "ot", "un", "<", "im", "th ", "nc", "ate", "><", "ver", "ad",
" we", "ly", "ee", " n", "id", " cl", "ac", "il", "</", "rt", " wi", "div",
"e, ", " it", "whi", " ma", "ge", "x", "e c", "men", ".com"
]
assert len(RCB)==254, len(RCB)

def smaz_decompress(data):
    out=bytearray(); i=0
    while i < len(data):
        b=data[i]
        if b==254:
            out.append(data[i+1]); i+=2
        elif b==255:
            n=data[i+1]+1
            out += data[i+2:i+2+n]; i+=2+n
        else:
            out += RCB[b].encode('latin1'); i+=1
    return bytes(out)

def h(d, bs):
    if d==0: d=0x1000193
    for c in bs:
        d = ((d * 0x1000193) & 0xFFFFFFFF) ^ c
    return d & 0xFFFFFFFF

class T:
    def __init__(s,path):
        r=Res(path)
        props=None
        for p,(t,pr) in r.resources.items():
            if 'hash_table' in pr: props=pr
        s.ht=props['hash_table']; s.bt=props['bucket_table']; s.st=bytes(props['strings'])
        s.btu=[x & 0xFFFFFFFF for x in s.bt]
        s.htu=[x & 0xFFFFFFFF for x in s.ht]
    def get(s,key):
        kb=key.encode('utf-8')
        hh=h(0,kb)
        htsize=len(s.htu)
        p=s.htu[hh % htsize]
        if p==0xFFFFFFFF: return None
        size=s.btu[p]; func=s.btu[p+1]
        h2=h(func,kb)
        for i in range(size):
            base=p+2+i*4
            key_,off,csz,usz = s.btu[base],s.btu[base+1],s.btu[base+2],s.btu[base+3]
            if key_==h2:
                raw=s.st[off:off+csz]
                if csz==usz: return raw.decode('utf-8','replace')
                return smaz_decompress(raw).decode('utf-8','replace')
        return None
    def all(s):
        res=[]
        seen=set()
        for p in s.htu:
            if p==0xFFFFFFFF or p in seen: continue
            seen.add(p)
            size=s.btu[p]
            for i in range(size):
                base=p+2+i*4
                key_,off,csz,usz=s.btu[base],s.btu[base+1],s.btu[base+2],s.btu[base+3]
                raw=s.st[off:off+csz]
                v = raw.decode('utf-8','replace') if csz==usz else smaz_decompress(raw).decode('utf-8','replace')
                res.append((key_,v))
        return res

if __name__=='__main__':
    t=T(sys.argv[1])
    for k in sys.argv[2:]:
        print(k,'=>',repr(t.get(k)))
