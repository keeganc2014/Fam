#!/usr/bin/env python3
"""
Packs a rigged FBX monster (binary FBX 7.x from Blender) into Assets/Resources/Creatures/<name>.bytes ('GTR2') for RiggedCreature.cs,
keeping the FBX's OWN skeleton and skin weights (nothing is rebuilt):

    python Tools/pack_fbx_rig.py barry    barry-my-custom-gth-monster.zip
    python Tools/pack_fbx_rig.py breacher breacher-my-custom-gth-monster.zip

Needs only numpy + Pillow. Reads the .zip directly (source/*.fbx + textures/*).
  - bones   : every skinned bone, rest pose = the FBX bind pose (cluster TransformLink), converted to Unity (x negated), metres.
  - skin    : the FBX skin weights (4 strongest per vertex).
  - parts   : meshes that are parented to a bone in the FBX (shotgun, machete, skull, ghost skeleton ...) are kept and bound 100% to that
              bone, with the offset they have in the FBX relative to it.
The FBX node transforms hold a POSED rig (arms aimed forward); the skin / bind pose hold the rest rig. The rest rig is what is used.
File: 'GTR2' | u32 jsonLength | json | blob   blob = pos f32[3n] nrm f32[3n] uv f32[2n] boneIdx u8[4n] boneW f32[4n] then i32 indices per sub-mesh.
"""
import sys, os, io, json, struct, zipfile, collections
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fbx_binary as fbxb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'Assets', 'Resources', 'Creatures')
UNIT = 0.01      # the FBX is in centimetres

# FBX material name -> texture file in the zip's textures/ folder (the FBX paths point at the author's PC, the zip has the files renamed)
CONFIG = {
    'barry': {
        'fbx': 'source/Barry1.fbx',
        'tex': {'GorillaTagColor': 'blood_gash.png', 'GorillaTagChest': 'blood_gash.png', 'GorillaTagFace': 'face5.jpeg',
                'Material': 'MEAT.png', 'Material.001': None, 'Material.002': None},
        'flat': {'Material.001': [0.02, 0.0, 0.0], 'Material.002': [0.8, 0.004, 0.0], 'GorillaTagColor': [1.0, 0.30, 0.27], 'GorillaTagChest': [1.0, 0.30, 0.27]},
    },
    'breacher': {
        'fbx': 'source/Breacher.fbx',
        'tex': {'GorillaTagColor': 'BreacherFur.png', 'GorillaTagChest': 'BreacherChestFur.png', 'GorillaTagFace': 'BreacherHead.png',
                'Material': 'MacheteTexture.png', 'shotgun': 'ShotGunTexture.png', 'SkeletonGhost': 'SkeletonDecoration.png'},
        'alpha': {'SkeletonGhost'},
        'flat': {},
    },
}


def props70(n):
    d = {}
    p = n.first('Properties70')
    if p:
        for c in p.children:
            if c.name == 'P': d[c.props[0]] = c.props[4:]
    return d


def euler(r):
    rx, ry, rz = np.radians(r)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]); Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Z @ Y @ X


def local_matrix(node):
    p = props70(node)
    g = lambda k, d: list(p.get(k, d))
    M = np.eye(4)
    T = np.eye(4); T[:3, 3] = g('Lcl Translation', [0, 0, 0])
    Rp = np.eye(4); Rp[:3, :3] = euler(g('PreRotation', [0, 0, 0]))
    R = np.eye(4); R[:3, :3] = euler(g('Lcl Rotation', [0, 0, 0]))
    Ro = np.eye(4); Ro[:3, :3] = np.linalg.inv(euler(g('PostRotation', [0, 0, 0])))
    S = np.diag(g('Lcl Scaling', [1, 1, 1]) + [1])
    return T @ Rp @ R @ Ro @ S


class Scene:
    def __init__(s, data):
        s.root = fbxb.parse_bytes(data)
        s.obj = {c.props[0]: c for c in s.root.first('Objects').children}
        s.parent = collections.defaultdict(list); s.kids = collections.defaultdict(list); s.conns = []
        for c in s.root.first('Connections').children:
            if c.props[0] in ('OO', 'OP'):
                s.parent[c.props[1]].append(c.props[2]); s.kids[c.props[2]].append(c.props[1]); s.conns.append(c)

    def name(s, i): return s.obj[i].props[1].split('\x00')[0]
    def kind(s, i): return s.obj[i].name
    def models(s): return [i for i, n in s.obj.items() if n.name == 'Model']

    def model_parent(s, i):
        for p in s.parent.get(i, []):
            if p in s.obj and s.obj[p].name == 'Model': return p
        return None

    def of_kind(s, i, kind, sub=None):
        return [k for k in s.kids[i] if k in s.obj and s.obj[k].name == kind and (sub is None or s.obj[k].props[2] == sub)]

    def geometry(s, g):
        geo = s.obj[g]
        V = geo.first('Vertices').props[0].reshape(-1, 3).astype(float)
        PVI = geo.first('PolygonVertexIndex').props[0].astype(np.int64)
        le = geo.first('LayerElementNormal'); Nn = le.first('Normals').props[0].reshape(-1, 3).astype(float)
        nmap = le.first('MappingInformationType').props[0]; nref = le.first('ReferenceInformationType').props[0]
        nidx = le.first('NormalsIndex').props[0].astype(np.int64) if le.first('NormalsIndex') else None
        lu = geo.first('LayerElementUV'); UVv = lu.first('UV').props[0].reshape(-1, 2).astype(float)
        umap = lu.first('MappingInformationType').props[0]; uref = lu.first('ReferenceInformationType').props[0]
        UVi = lu.first('UVIndex').props[0].astype(np.int64) if lu.first('UVIndex') else None
        lm = geo.first('LayerElementMaterial')
        mm = lm.first('Materials').props[0].astype(int) if lm else np.zeros(1, int)
        mmap = lm.first('MappingInformationType').props[0] if lm else 'AllSame'
        polys = []; cur = []
        for k, v in enumerate(PVI):
            cur.append((k, v if v >= 0 else ~v))
            if v < 0: polys.append(cur); cur = []
        ck, cv, cp, tm = [], [], [], []
        for pi, pl in enumerate(polys):
            for a in range(1, len(pl) - 1):
                for c in (pl[0], pl[a], pl[a + 1]): ck.append(c[0]); cv.append(c[1]); cp.append(pi)
                tm.append(mm[pi] if mmap == 'ByPolygon' else mm[0])
        ck, cv, cp, tm = map(np.array, (ck, cv, cp, tm))

        def pick(arr, idx, mp, ref):
            key = {'ByPolygonVertex': ck, 'ByVertice': cv, 'ByVertex': cv, 'ByPolygon': cp}[mp]
            if ref == 'IndexToDirect' and idx is not None: key = idx[key]
            return arr[key]
        return V[cv], pick(Nn, nidx, nmap, nref), pick(UVv, UVi, umap, uref), tm, cv

    def skin_weights(s, g, nverts):
        """FBX skin of geometry g -> {bone model id: (indices, weights)} plus bind (world) matrix per bone"""
        out = {}; bind = {}
        for d in s.of_kind(g, 'Deformer', 'Skin'):
            for cl in s.of_kind(d, 'Deformer', 'Cluster'):
                bones = s.of_kind(cl, 'Model')
                if not bones: continue
                n = s.obj[cl]
                ix = n.first('Indexes'); wt = n.first('Weights')
                out[bones[0]] = (ix.props[0].astype(int), wt.props[0].astype(float)) if ix else (np.zeros(0, int), np.zeros(0))
                bind[bones[0]] = np.array(n.first('TransformLink').props[0]).reshape(4, 4).T
        return out, bind


def extract_clips(sc, order, idx_of, bind):
    """The FBX's own Blender actions -> per-bone local rotation keys (Unity space), sampled on the union of the key times."""
    KT = 46186158000.0                     # FBX ticks per second
    rig_root = None
    for m in sc.models():
        if sc.name(m) == 'Gorilla_Rig': rig_root = m
    R_rig = euler(list(props70(sc.obj[rig_root]).get('Lcl Rotation', [0, 0, 0]))) if rig_root else np.eye(3)
    Cx = np.diag([-1.0, 1.0, 1.0])
    clips = []
    for st, stn in [(i, n) for i, n in sc.obj.items() if n.name == 'AnimationStack']:
        if not sc.name(st).startswith('Gorilla_Rig|'): continue          # skip the eye-light actions
        layers = sc.of_kind(st, 'AnimationLayer')
        if not layers: continue
        cnodes = sc.of_kind(layers[0], 'AnimationCurveNode')
        rot_curves = {}            # model id -> {0:(t,v),1:..,2:..}
        for cn in cnodes:
            target = None
            for c in sc.conns:
                if c.props[0] == 'OP' and c.props[1] == cn and c.props[2] in sc.obj and sc.obj[c.props[2]].name == 'Model' and c.props[3] == 'Lcl Rotation':
                    target = c.props[2]
            if target is None: continue
            for c in sc.conns:
                if c.props[0] == 'OP' and c.props[2] == cn and c.props[1] in sc.obj and sc.obj[c.props[1]].name == 'AnimationCurve':
                    ax = {'d|X': 0, 'd|Y': 1, 'd|Z': 2}.get(c.props[3])
                    if ax is None: continue
                    cur = sc.obj[c.props[1]]
                    rot_curves.setdefault(target, {})[ax] = (cur.first('KeyTime').props[0] / KT, cur.first('KeyValueFloat').props[0].astype(float))
        if not rot_curves: continue
        times = sorted(set(round(float(t), 5) for ax in rot_curves.values() for (tt, _) in ax.values() for t in tt))
        t0 = times[0]; times = [t - t0 for t in times]
        tracks = []
        for b in order:
            static = list(props70(sc.obj[b]).get('Lcl Rotation', [0, 0, 0]))
            keys = []
            for t in times:
                e = list(static)
                for ax, (tt, vv) in rot_curves.get(b, {}).items(): e[ax] = float(np.interp(t + t0, tt, vv))
                R = euler(e)
                if sc.name(b) == 'torso': R = R_rig @ R              # the rig root's -90 degree turn belongs to the first bone
                q = quat_from_matrix(Cx @ R @ Cx)
                if keys and np.dot(keys[-1], q) < 0: q = -q
                keys.append(q)
            tracks.append([[round(float(x), 5) for x in q] for q in keys])
        flat = [x for tr in tracks for q in tr for x in q]            # bone-major, then key, then xyzw (JsonUtility cannot read nested arrays)
        clips.append({'name': sc.name(st).split('|')[-1], 'times': [round(t, 5) for t in times], 'q': flat})
    return clips


def to_unity_pos(P):            # FBX centimetres (right handed) -> Unity metres (x mirrored)
    P = P * UNIT; P = P.copy(); P[..., 0] *= -1; return P


def quat_from_matrix(R):
    from math import sqrt
    t = np.trace(R)
    if t > 0:
        s = sqrt(t + 1) * 2; w = 0.25 * s; x = (R[2, 1] - R[1, 2]) / s; y = (R[0, 2] - R[2, 0]) / s; z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2; w = (R[2, 1] - R[1, 2]) / s; x = 0.25 * s; y = (R[0, 1] + R[1, 0]) / s; z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2; w = (R[0, 2] - R[2, 0]) / s; x = (R[0, 1] + R[1, 0]) / s; y = 0.25 * s; z = (R[1, 2] + R[2, 1]) / s
    else:
        s = sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2; w = (R[1, 0] - R[0, 1]) / s; x = (R[0, 2] + R[2, 0]) / s; y = (R[1, 2] + R[2, 1]) / s; z = 0.25 * s
    q = np.array([x, y, z, w]); return q / np.linalg.norm(q)


def pack(name, zpath):
    cfg = CONFIG[name]
    z = zipfile.ZipFile(zpath)
    sc = Scene(z.read(cfg['fbx']))
    models = sc.models()

    # ---- the skinned body: the mesh that has a skin
    skinned = None
    for m in models:
        for g in sc.of_kind(m, 'Geometry'):
            if sc.of_kind(g, 'Deformer', 'Skin'): skinned = (m, g)
    assert skinned, 'no skinned mesh in the FBX'
    body_model, body_geo = skinned
    weights_by_bone, bind = sc.skin_weights(body_geo, 0)

    # ---- bones: those with a skin cluster, in hierarchy order. Rest pose = bind pose (TransformLink)
    def bone_parent(b):
        p = sc.model_parent(b)
        while p is not None and p not in bind: p = sc.model_parent(p)
        return p
    order = []
    def visit(b):
        order.append(b)
        for c in sorted([k for k in bind if bone_parent(k) == b], key=lambda k: sc.name(k)): visit(c)
    for r in [b for b in bind if bone_parent(b) is None]: visit(r)
    idx_of = {b: i for i, b in enumerate(order)}
    assert len(order) <= 255

    # the body mesh -> bind space through the Pose node (the bind pose of the mesh)
    pose = [n for n in sc.obj.values() if n.name == 'Pose'][0]
    pm = {pn.first('Node').props[0]: np.array(pn.first('Matrix').props[0]).reshape(4, 4).T for pn in pose.find('PoseNode')}
    Mbody = pm[body_model]

    def to_world(P, M): return (np.c_[P, np.ones(len(P))] @ M.T)[:, :3]

    # bone world rest matrices in Unity space (rotation only, positions in metres)
    Cx = np.diag([-1.0, 1.0, 1.0])
    wpos, wrot = [], []
    for b in order:
        M = bind[b].copy(); sc_ = np.linalg.norm(M[:3, :3], axis=0)
        R = M[:3, :3] / sc_
        assert np.linalg.det(R) > 0, 'mirrored bone'
        wpos.append(to_unity_pos(M[:3, 3])); wrot.append(Cx @ R @ Cx)

    P_all, N_all, U_all, J_all, W_all = [], [], [], [], []
    subs = []          # (key, indices) ; key = (material name)
    base = 0
    mats_used = []

    def material_slots(m):
        return [sc.name(k) for k in sc.of_kind(m, 'Material')]

    def add_mesh(P, N, U, J, W, tm, slots):
        nonlocal base
        P_all.append(P); N_all.append(N); U_all.append(U); J_all.append(J); W_all.append(W)
        tris = np.arange(len(P)).reshape(-1, 3)[:, [0, 2, 1]]                  # x was mirrored: swap the winding
        for slot in sorted(set(tm.tolist())):
            sel = tris[tm == slot]
            mname = slots[slot] if slot < len(slots) else slots[-1]
            subs.append((mname, (sel + base).ravel()))
        base += len(P)

    # ---- skinned body
    P, N, U, tm, cv = sc.geometry(body_geo)
    nv = int(cv.max()) + 1
    wl = [[] for _ in range(nv)]
    for b, (ix, wt) in weights_by_bone.items():
        for i_, w_ in zip(ix, wt):
            if w_ > 1e-5 and i_ < nv: wl[i_].append((idx_of[b], w_))
    J = np.zeros((len(cv), 4), np.uint8); W = np.zeros((len(cv), 4), np.float32)
    torso_i = [idx_of[b] for b in order if sc.name(b) == 'torso'][0]
    for k, v in enumerate(cv):
        l = sorted(wl[v], key=lambda t: -t[1])[:4]
        if not l: l = [(torso_i, 1.0)]
        tot = sum(w for _, w in l)
        for q, (bi, w) in enumerate(l): J[k, q] = bi; W[k, q] = w / tot
    Pw = to_world(P, Mbody); Nw = N @ np.linalg.inv(Mbody[:3, :3]).T
    Nw /= np.maximum(np.linalg.norm(Nw, axis=1, keepdims=True), 1e-9)
    Pu = to_unity_pos(Pw); Nu = Nw.copy(); Nu[:, 0] *= -1
    add_mesh(Pu, Nu, np.c_[U[:, 0], U[:, 1]], J, W, tm, material_slots(body_model))

    # ---- rigid parts: other meshes -> 100% on the bone they hang from, keeping their offset to it
    parts = []
    for m in models:
        if m == body_model: continue
        geos = sc.of_kind(m, 'Geometry')
        if not geos: continue
        b = sc.model_parent(m); chain = [m]
        while b is not None and b not in bind: chain.append(b); b = sc.model_parent(b)
        if b is None:
            # hangs from the rig root: put on the torso
            b = [x for x in order if sc.name(x) == 'torso'][0]
        rel = np.eye(4)
        for node in reversed(chain): rel = rel @ local_matrix(sc.obj[node])      # the part relative to its bone, as authored
        parts.append(sc.name(m))
        P, N, U, tm, cv = sc.geometry(geos[0])
        Wb = bind[b] @ rel                                                         # FBX world of the part at the REST pose
        Pw = to_world(P, Wb); Nw = N @ np.linalg.inv(Wb[:3, :3]).T
        Nw /= np.maximum(np.linalg.norm(Nw, axis=1, keepdims=True), 1e-9)
        Pu = to_unity_pos(Pw); Nu = Nw.copy(); Nu[:, 0] *= -1
        J = np.zeros((len(P), 4), np.uint8); W = np.zeros((len(P), 4), np.float32)
        J[:, 0] = idx_of[b]; W[:, 0] = 1.0
        add_mesh(Pu, Nu, np.c_[U[:, 0], U[:, 1]], J, W, tm, material_slots(m))

    P = np.vstack(P_all).astype('<f4'); N = np.vstack(N_all).astype('<f4'); U = np.vstack(U_all).astype('<f4')
    J = np.vstack(J_all).astype('u1'); W = np.vstack(W_all).astype('<f4')
    # stand on the floor (lowest point of the body, not the loose parts), centred on x
    nbody = len(P_all[0])
    shift = np.array([-(P[:nbody, 0].min() + P[:nbody, 0].max()) / 2, -P[:nbody, 1].min(), -(P[:nbody, 2].min() + P[:nbody, 2].max()) / 2])
    P += shift.astype('f4')
    wpos = [p + shift for p in wpos]

    bones = []
    for i, b in enumerate(order):
        par = idx_of[bone_parent(b)] if bone_parent(b) is not None else -1
        if par < 0: lp, lr = wpos[i], wrot[i]
        else: lp, lr = wrot[par].T @ (wpos[i] - wpos[par]), wrot[par].T @ wrot[i]
        bones.append({'name': sc.name(b), 'parent': par, 'pos': [float(x) for x in lp], 'rot': [float(x) for x in quat_from_matrix(lr)]})

    # ---- materials / textures
    os.makedirs(os.path.join(RES, 'Textures'), exist_ok=True)
    mats = []; mat_index = {}
    sub_json = []; blob_idx = bytearray()
    merged = collections.OrderedDict()
    for mname, idx in subs: merged.setdefault(mname, []).append(idx)
    for mname, lst in merged.items():
        idx = np.concatenate(lst)
        tex = cfg['tex'].get(mname)
        texres = ''
        has_alpha = mname in cfg.get('alpha', set())
        if tex:
            img = Image.open(io.BytesIO(z.read('textures/' + tex)))
            out = 'Creatures/Textures/%s_%s' % (name, os.path.splitext(tex)[0])
            if has_alpha:
                img.convert('RGBA').save(os.path.join(ROOT, 'Assets', 'Resources', out + '.png')); 
            else:
                img.convert('RGB').save(os.path.join(ROOT, 'Assets', 'Resources', out + '.png'))
            texres = out
        color = cfg['flat'].get(mname, [1, 1, 1])
        sub_json.append({'count': int(len(idx)), 'tex': texres, 'name': mname, 'alpha': bool(has_alpha), 'color': [float(c) for c in color]})
        blob_idx += np.asarray(idx, '<i4').tobytes()

    clips = extract_clips(sc, order, idx_of, bind)
    height = float(P[:nbody, 1].max())
    head_json = {'bones': bones, 'n': int(len(P)), 'subs': sub_json, 'height': height, 'parts': parts, 'clips': clips}
    js = json.dumps(head_json, separators=(',', ':')).encode()
    blob = P.tobytes() + N.tobytes() + U.tobytes() + J.tobytes() + W.tobytes() + bytes(blob_idx)
    path = os.path.join(RES, name + '.bytes')
    with open(path, 'wb') as f: f.write(b'GTR2' + struct.pack('<I', len(js)) + js + blob)
    print('wrote', path, os.path.getsize(path) // 1024, 'KB |', len(bones), 'bones,', len(P), 'verts,', sum(s['count'] for s in sub_json) // 3, 'tris | height %.2f m' % height,
          '| parts:', parts)
    for c_ in clips: print('   clip', c_['name'], len(c_['times']), 'keys,', c_['times'][-1], 's')
    for s_ in sub_json: print('   sub', s_['name'], s_['count'] // 3, 'tris', s_['tex'] or '(flat colour)')


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] not in CONFIG: print(__doc__); sys.exit(1)
    pack(sys.argv[1], sys.argv[2])
