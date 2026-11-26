import struct, zlib, numpy as np
class Node:
    __slots__=('name','props','children')
    def __init__(s,n): s.name=n; s.props=[]; s.children=[]
    def find(s,n): return [c for c in s.children if c.name==n]
    def first(s,n):
        r=s.find(n); return r[0] if r else None
def parse(path):
    return parse_bytes(open(path,'rb').read())
def parse_bytes(d):
    ver=struct.unpack('<I',d[23:27])[0]
    wide=ver>=7500
    off=27
    root=Node('root')
    def rd_node(o):
        if wide: end,np_,pl,nl=struct.unpack('<QQQB',d[o:o+25]); o+=25
        else: end,np_,pl,nl=struct.unpack('<IIIB',d[o:o+13]); o+=13
        if end==0: return None,o
        n=Node(d[o:o+nl].decode('latin1')); o+=nl
        for _ in range(np_):
            t=chr(d[o]); o+=1
            if t=='Y': n.props.append(struct.unpack('<h',d[o:o+2])[0]); o+=2
            elif t=='C': n.props.append(d[o]); o+=1
            elif t=='I': n.props.append(struct.unpack('<i',d[o:o+4])[0]); o+=4
            elif t=='F': n.props.append(struct.unpack('<f',d[o:o+4])[0]); o+=4
            elif t=='D': n.props.append(struct.unpack('<d',d[o:o+8])[0]); o+=8
            elif t=='L': n.props.append(struct.unpack('<q',d[o:o+8])[0]); o+=8
            elif t in 'fdilb':
                ln,enc,cl=struct.unpack('<III',d[o:o+12]); o+=12
                raw=d[o:o+cl]; o+=cl
                if enc: raw=zlib.decompress(raw)
                dt={'f':'<f4','d':'<f8','i':'<i4','l':'<i8','b':'u1'}[t]
                n.props.append(np.frombuffer(raw,dt,ln))
            elif t in 'SR':
                ln=struct.unpack('<I',d[o:o+4])[0]; o+=4
                raw=d[o:o+ln]; o+=ln
                n.props.append(raw.decode('latin1') if t=='S' else raw)
            else: raise Exception('bad prop '+t)
        while o<end:
            c,o=rd_node(o)
            if c is None: break
            n.children.append(c)
        return n,end
    while True:
        n,o2=rd_node(off)
        if n is None: break
        root.children.append(n); off=o2
        if off>=len(d)-200: break
    return root
