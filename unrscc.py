import struct, sys
from compression import zstd
import zlib
def decompress(path,out):
    d=open(path,'rb').read()
    assert d[:4]==b'RSCC', d[:4]
    cmode,bs,total=struct.unpack_from('<III',d,4)
    bc=total//bs+1
    p=16
    sizes=[struct.unpack_from('<I',d,p+i*4)[0] for i in range(bc)]
    p+=bc*4
    res=bytearray()
    for i,cs in enumerate(sizes):
        blk=d[p:p+cs]; p+=cs
        want=bs if i<bc-1 else total-(bc-1)*bs
        if cs==want:
            res+=blk
        elif cmode==2:
            res+=zstd.decompress(blk)
        elif cmode==1:
            res+=zlib.decompressobj(-15).decompress(blk)
        else:
            raise Exception('mode %d'%cmode)
    print('mode',cmode,'blocks',bc,'total',total,'got',len(res))
    open(out,'wb').write(bytes(res))
if __name__=='__main__':
    decompress(sys.argv[1],sys.argv[2])
