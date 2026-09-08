import struct, sys, os
from compression import zstd

TT = """EMPTY ANNOTATION IDENTIFIER LITERAL LESS LESS_EQUAL GREATER GREATER_EQUAL EQUAL_EQUAL BANG_EQUAL
AND OR NOT AMPERSAND_AMPERSAND PIPE_PIPE BANG AMPERSAND PIPE TILDE CARET LESS_LESS GREATER_GREATER
PLUS MINUS STAR STAR_STAR SLASH PERCENT
EQUAL PLUS_EQUAL MINUS_EQUAL STAR_EQUAL STAR_STAR_EQUAL SLASH_EQUAL PERCENT_EQUAL LESS_LESS_EQUAL GREATER_GREATER_EQUAL AMPERSAND_EQUAL PIPE_EQUAL CARET_EQUAL
IF ELIF ELSE FOR WHILE BREAK CONTINUE PASS RETURN MATCH WHEN
AS ASSERT AWAIT BREAKPOINT CLASS CLASS_NAME CONST ENUM EXTENDS FUNC IN IS NAMESPACE PRELOAD SELF SIGNAL STATIC SUPER TRAIT VAR VOID YIELD
BRACKET_OPEN BRACKET_CLOSE BRACE_OPEN BRACE_CLOSE PARENTHESIS_OPEN PARENTHESIS_CLOSE
COMMA SEMICOLON PERIOD PERIOD_PERIOD PERIOD_PERIOD_PERIOD COLON DOLLAR FORWARD_ARROW UNDERSCORE
NEWLINE INDENT DEDENT CONST_PI CONST_TAU CONST_INF CONST_NAN
VCS_CONFLICT_MARKER BACKTICK QUESTION_MARK ERROR TK_EOF""".split()

TEXT = {'LESS':'<','LESS_EQUAL':'<=','GREATER':'>','GREATER_EQUAL':'>=','EQUAL_EQUAL':'==','BANG_EQUAL':'!=',
'AND':'and','OR':'or','NOT':'not','AMPERSAND_AMPERSAND':'&&','PIPE_PIPE':'||','BANG':'!',
'AMPERSAND':'&','PIPE':'|','TILDE':'~','CARET':'^','LESS_LESS':'<<','GREATER_GREATER':'>>',
'PLUS':'+','MINUS':'-','STAR':'*','STAR_STAR':'**','SLASH':'/','PERCENT':'%',
'EQUAL':'=','PLUS_EQUAL':'+=','MINUS_EQUAL':'-=','STAR_EQUAL':'*=','STAR_STAR_EQUAL':'**=','SLASH_EQUAL':'/=','PERCENT_EQUAL':'%=',
'LESS_LESS_EQUAL':'<<=','GREATER_GREATER_EQUAL':'>>=','AMPERSAND_EQUAL':'&=','PIPE_EQUAL':'|=','CARET_EQUAL':'^=',
'IF':'if','ELIF':'elif','ELSE':'else','FOR':'for','WHILE':'while','BREAK':'break','CONTINUE':'continue',
'PASS':'pass','RETURN':'return','MATCH':'match','WHEN':'when','AS':'as','ASSERT':'assert','AWAIT':'await',
'BREAKPOINT':'breakpoint','CLASS':'class','CLASS_NAME':'class_name','CONST':'const','ENUM':'enum','EXTENDS':'extends',
'FUNC':'func','IN':'in','IS':'is','NAMESPACE':'namespace','PRELOAD':'preload','SELF':'self','SIGNAL':'signal',
'STATIC':'static','SUPER':'super','TRAIT':'trait','VAR':'var','VOID':'void','YIELD':'yield',
'BRACKET_OPEN':'[','BRACKET_CLOSE':']','BRACE_OPEN':'{','BRACE_CLOSE':'}','PARENTHESIS_OPEN':'(','PARENTHESIS_CLOSE':')',
'COMMA':',','SEMICOLON':';','PERIOD':'.','PERIOD_PERIOD':'..','PERIOD_PERIOD_PERIOD':'...','COLON':':','DOLLAR':'$','FORWARD_ARROW':'->',
'UNDERSCORE':'_','CONST_PI':'PI','CONST_TAU':'TAU','CONST_INF':'INF','CONST_NAN':'NAN','TK_EOF':''}

def decode_variant(b,p):
    h,=struct.unpack_from('<I',b,p); p+=4
    t=h&0xFFFF; flag=h>>16
    if t==0: return None,p
    if t==1:
        v,=struct.unpack_from('<I',b,p); p+=4; return bool(v),p
    if t==2:
        if flag&1: v,=struct.unpack_from('<q',b,p); p+=8
        else: v,=struct.unpack_from('<i',b,p); p+=4
        return v,p
    if t==3:
        if flag&1: v,=struct.unpack_from('<d',b,p); p+=8
        else: v,=struct.unpack_from('<f',b,p); p+=4
        return v,p
    if t in (4,21):
        n,=struct.unpack_from('<I',b,p); p+=4
        s=b[p:p+n].decode('utf-8','replace'); p+=n
        while p%4: p+=1
        return s,p
    if t==28:  # PackedStringArray? unlikely
        raise Exception('t28')
    raise Exception('variant type %d at %d'%(t,p))

def decompile(path):
    d=open(path,'rb').read()
    assert d[:4]==b'GDSC'
    dsz=struct.unpack_from('<I',d,8)[0]
    b = d[12:] if dsz==0 else zstd.decompress(d[12:])
    idc,cc,tlc,tc=struct.unpack_from('<IIII',b,0)
    p=16
    ids=[]
    for i in range(idc):
        ln=struct.unpack_from('<I',b,p)[0]; p+=4
        chars=[struct.unpack('<I',bytes(x^0xb6 for x in b[p+j*4:p+j*4+4]))[0] for j in range(ln)]
        p+=ln*4
        ids.append(''.join(chr(c) for c in chars))
    consts=[]
    for i in range(cc):
        v,p=decode_variant(b,p); consts.append(v)
    p+=tlc*8*2
    toks=[]
    for i in range(tc):
        w,line=struct.unpack_from('<II',b,p); p+=8
        t=w&0x7F; val=(w>>8) if (w&0x80) else None
        toks.append((t,val,line))
    # render
    lines={}
    for t,val,line in toks:
        name=TT[t] if t<len(TT) else 'T%d'%t
        if name=='IDENTIFIER': s=ids[val]
        elif name=='LITERAL':
            c=consts[val]
            s=repr(c) if isinstance(c,str) else str(c)
        elif name=='ANNOTATION': s=ids[val] if val is not None and val<len(ids) else '@?'
        elif name in ('NEWLINE','INDENT','DEDENT','EMPTY'): continue
        else: s=TEXT.get(name,'<%s>'%name)
        lines.setdefault(line,[]).append(s)
    out=[]
    for ln in sorted(lines):
        toks_=lines[ln]
        s=''
        for i,t in enumerate(toks_):
            if i and (t not in ',):.]' and not (t.startswith('.')) and toks_[i-1] not in '(.[' ):
                s+=' '
            s+=t
        out.append('%5d  %s'%(ln,s))
    return '\n'.join(out)

if __name__=='__main__':
    print(decompile(sys.argv[1]))
