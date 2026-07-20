#!/usr/bin/env python3
"""
oras_anim_tool.py  —  ORAS .pb / .pk animation editor
=======================================================
Reverse-engineered from SPICA (Wambosa fork):
  SPICA/Formats/GFL/Motion/GF1Motion.cs
  SPICA/Formats/GFL/Motion/GF1MotBone.cs
  SPICA/Formats/GFL/GF1MotionPack.cs
  SPICA.WinForms/Formats/GFPkmnSklAnim.cs
  SPICA.WinForms/Formats/GFPackage.cs

Format summary:
  .pb / .pk  =  GFPackage container
    Entry[0] =  GF1MotionPack  (all skeletal animations)
      offset[0] = GF1MotBone skeleton (bone names, parent hierarchy, rest poses)
      offset[1..N] = GF1Motion clips  (octal-compressed keyframe animation)
    Entry[1] =  bounding box data (auto-preserved)
    Entry[2+] = BCH (material / visibility animations, auto-preserved)

Usage:
  python anim.py info    <file.pb|.pk>
  python anim.py export  <file.pb|.pk> <out.json>  [--clip N]
  python anim.py inspect <file.pb|.pk>              [--clip N]
  python anim.py tpose   <exported.json> <out.json>
  python anim.py patch   <original.pb|.pk> <in.json> <out.pb|.pk>

JSON values:
  Rotations = RADIANS (Euler XYZ) — same as Blender default
  Translations = model units (match .pc bone rest poses, ~cm scale)
  Slopes = Hermite tangents (null = linear, omit for constant)

Requirements: Python 3.7+, standard library only.
"""

import struct, math, json, sys, os, argparse

# ─── Constants ───────────────────────────────────────────────────────────────
CHANNEL_NAMES = [
    'TranslationX','TranslationY','TranslationZ',
    'RotationX',   'RotationY',   'RotationZ',
    'ScaleX',      'ScaleY',      'ScaleZ',
]
PI, HALF_PI, NEG_HPI = math.pi, math.pi*0.5, math.pi*-0.5
CONST_TOL = 1e-4
MAGIC_CONSTS = [(HALF_PI,2),(PI,3),(NEG_HPI,4)]

# ─── Binary helpers ──────────────────────────────────────────────────────────
def r16(d,o): return struct.unpack_from('<H',d,o)[0]
def r32(d,o): return struct.unpack_from('<I',d,o)[0]
def rf(d,o):  return struct.unpack_from('<f',d,o)[0]
def align4(n): return (n+3)&~3

def read_cstr(data, pos):
    end = pos
    while data[end] != 0: end += 1
    return data[pos:end].decode('ascii','replace'), end+1

# ─── GFPackage ───────────────────────────────────────────────────────────────
def parse_gfpkg(data):
    magic = data[0:2].decode('ascii')
    n     = r16(data,2)
    tbl   = [r32(data,4+i*4) for i in range(n+1)]
    return magic, [{'addr':tbl[i],'length':tbl[i+1]-tbl[i]} for i in range(n)]

def build_gfpkg(magic, blobs):
    n = len(blobs)
    hdr = 4+(n+1)*4
    offsets, cur = [], hdr
    for b in blobs: offsets.append(cur); cur+=len(b)
    offsets.append(cur)
    out = magic.encode('ascii')+struct.pack('<H',n)
    for o in offsets: out+=struct.pack('<I',o)
    for b in blobs: out+=b
    return out

# ─── GF1MotBone skeleton ─────────────────────────────────────────────────────
def decode_skeleton(data, off):
    pos = off
    bc = data[pos]; fsi = data[pos+1]; pos+=2
    bones = [{'name':'Origin','parent':-1,'flags':0,'childs':0,
               'fsi':fsi,'t':(0.,0.,0.),'q':(1.,0.,0.,0.)}]
    for _ in range(1,bc):
        bones.append({'name':'','parent':data[pos],'flags':data[pos+1],'childs':data[pos+2],'t':(0.,0.,0.),'q':(1.,0.,0.,0.)})
        pos+=3
    for i in range(1,bc):
        nm,pos = read_cstr(data,pos)
        bones[i]['name']=nm
    while pos%4: pos+=1
    for i in range(bc):
        tx,ty,tz = rf(data,pos),rf(data,pos+4),rf(data,pos+8)
        qx,qy,qz,qw = rf(data,pos+12),rf(data,pos+16),rf(data,pos+20),rf(data,pos+24)
        bones[i]['t']=(tx,ty,tz); bones[i]['q']=(qw,qx,qy,qz)
        pos+=28
    return bones, pos-off

def encode_skeleton(bones):
    out = bytearray()
    n = len(bones)
    out+=bytes([n, bones[0].get('fsi',0)])
    for i in range(1,n): b=bones[i]; out+=bytes([b['parent'],b['flags'],b['childs']])
    for i in range(1,n): out+=bones[i]['name'].encode('ascii')+b'\x00'
    while len(out)%4: out+=b'\x00'
    for b in bones:
        tx,ty,tz=b['t']; qw,qx,qy,qz=b['q']
        out+=struct.pack('<fff',tx,ty,tz)
        out+=struct.pack('<ffff',qx,qy,qz,qw)
    return bytes(out)

# ─── GF1Motion decoder ───────────────────────────────────────────────────────
def decode_clip(data, off, skeleton):
    pos = off
    oct_cnt = r16(data,pos); fc = r16(data,pos+2); pos+=4

    # Packed octal bitstream: 8 octals per 3-byte group
    octals=[]; kfc=0; cur=0
    for i in range(oct_cnt):
        if i%8==0:
            cur=data[pos]|(data[pos+1]<<8)|(data[pos+2]<<16); pos+=3
        octals.append(cur&7); cur>>=3
        if octals[-1]>5: kfc+=1

    f16=fc>0xff
    if f16 and pos%2: pos+=1

    # Keyframe index tables
    kfs=[]
    for _ in range(kfc):
        cnt=r16(data,pos) if f16 else data[pos]; pos+=2 if f16 else 1
        k=[0]*(cnt+2); k[cnt+1]=fc
        for j in range(1,cnt+1):
            k[j]=r16(data,pos) if f16 else data[pos]; pos+=2 if f16 else 1
        kfs.append(k)
    while pos%4: pos+=1

    # Data pass
    result=[]; cur_bone=None; ckfl=0; oidx=2; eidx=0; old=-1
    while oidx<oct_cnt:
        oc=octals[oidx]; oidx+=1
        if oc!=1:
            bi=eidx//9
            if bi!=old:
                cur_bone={'name':skeleton[bi]['name'],'channels':{c:[] for c in CHANNEL_NAMES}}
                result.append(cur_bone); old=bi
            ch=CHANNEL_NAMES[eidx%9]; lst=cur_bone['channels'][ch]
            if   oc==0: pass  # constant zero — leave channel empty so encoder picks octal 0
            elif oc==2: lst.append({'frame':0,'value':HALF_PI,'slope':None})
            elif oc==3: lst.append({'frame':0,'value':PI,'slope':None})
            elif oc==4: lst.append({'frame':0,'value':NEG_HPI,'slope':None})
            elif oc==5: lst.append({'frame':0,'value':rf(data,pos),'slope':None}); pos+=4
            elif oc==6:
                for fr in kfs[ckfl]: lst.append({'frame':fr,'value':rf(data,pos),'slope':None}); pos+=4
                ckfl+=1
            elif oc==7:
                for fr in kfs[ckfl]:
                    v=rf(data,pos); s=rf(data,pos+4); pos+=8
                    lst.append({'frame':fr,'value':v,'slope':s})
                ckfl+=1
            eidx+=1
        else: eidx+=3
    return fc, result

# ─── GF1Motion encoder ───────────────────────────────────────────────────────
def _best_octal(kfs):
    if not kfs: return 0,[]
    if len(kfs)==1:
        v=kfs[0]['value']
        for mv,mc in MAGIC_CONSTS:
            if abs(v-mv)<CONST_TOL: return mc,[]
        return 5,[kfs[0]]
    has_slope=any(k['slope'] is not None for k in kfs)
    return (7 if has_slope else 6), kfs

def encode_clip(fc, bone_anims, skeleton):
    by_name={b['name']:b['channels'] for b in bone_anims}
    skel_names=[b['name'] for b in skeleton]
    n=len(skeleton)

    octs=[]; data_items=[]
    for bi in range(n):
        chans=by_name.get(skel_names[bi],{})
        for gs in range(0,9,3):
            grp=CHANNEL_NAMES[gs:gs+3]
            empty=all(not chans.get(c) for c in grp)
            if empty:
                octs.append(1)
            else:
                for c in grp:
                    code,used=_best_octal(chans.get(c,[]))
                    octs.append(code)
                    if code in(5,6,7): data_items.append((code,used))

    full=[0,0]+octs
    oc=len(full)

    oct_bytes=bytearray()
    for i in range(0,len(full),8):
        grp=full[i:i+8]
        while len(grp)<8: grp.append(0)
        v=0
        for j,x in enumerate(grp): v|=(x&7)<<(j*3)
        oct_bytes+=struct.pack('<I',v)[:3]

    f16=fc>0xff
    if f16 and (4+len(oct_bytes))%2: oct_bytes+=b'\x00'

    kft=bytearray()
    for code,kfs_used in data_items:
        if code in(6,7):
            inner=[k['frame'] for k in kfs_used if k['frame']!=0 and k['frame']!=fc]
            cnt=len(inner)
            kft+=(struct.pack('<H',cnt) if f16 else bytes([cnt]))
            for fr in inner: kft+=(struct.pack('<H',fr) if f16 else bytes([fr]))

    pre=4+len(oct_bytes)+len(kft)
    kft+=b'\x00'*((4-pre%4)%4)

    flt=bytearray()
    for code,kfs_used in data_items:
        if   code==5: flt+=struct.pack('<f',kfs_used[0]['value'])
        elif code==6:
            for k in kfs_used: flt+=struct.pack('<f',k['value'])
        elif code==7:
            for k in kfs_used:
                flt+=struct.pack('<f',k['value'])
                flt+=struct.pack('<f',k['slope'] or 0.)

    out=bytearray()
    out+=struct.pack('<H',oc)
    out+=struct.pack('<H',fc)
    out+=oct_bytes+kft+flt
    return bytes(out)

# ─── GF1MotionPack ───────────────────────────────────────────────────────────
def decode_motionpack(data, off):
    """
    Returns (skeleton, anims, raw_clips, skel_blob, ac, skel_offset).

    skel_blob   Raw bytes from header-end to first clip start.  Contains the
                skeleton, any pre-skeleton fields, and any post-skeleton data
                (bounding volumes etc.) that we don't parse.  Preserved as-is.
    raw_clips   { clip_index -> bytes }  Original binary for every non-empty clip.
    anims       { clip_index -> (fc, bones) }  Decoded for inspection / editing.
    skel_offset Skeleton's absolute offset inside the motionpack (for the header).
    """
    ac          = r32(data, off)
    header_size = 4 + ac * 4
    offsets     = [r32(data, off + 4 + i*4) for i in range(ac)]
    skel_offset = offsets[0]
    skeleton, _ = decode_skeleton(data, off + skel_offset)

    # Everything from header-end to first clip = skeleton blob (preserve raw)
    first_clip_off = next((o for o in offsets[1:] if o > 0), len(data) - off)
    skel_blob = data[off + header_size : off + first_clip_off]

    anims = {}; raw_clips = {}
    for i in range(1, ac):
        if offsets[i]:
            clip_end = len(data) - off
            for j in range(i+1, ac):
                if offsets[j]: clip_end = offsets[j]; break
            raw_clips[i] = data[off + offsets[i] : off + clip_end]
            fc, bones    = decode_clip(data, off + offsets[i], skeleton)
            anims[i]     = (fc, bones)

    return skeleton, anims, raw_clips, skel_blob, ac, skel_offset


def build_motionpack(skel_blob, raw_clips, replacements, ac, skel_offset):
    """
    Rebuild the GF1MotionPack byte-perfectly for unchanged parts.

    skel_blob    Raw bytes (header-end → first clip) — used unchanged except
                 the embedded 4-byte total-size field is updated automatically.
    raw_clips    { clip_index -> original_bytes }  — unchanged clips
    replacements { clip_index -> new_bytes }        — changed clips (override)
    """
    header_size = 4 + ac * 4
    all_clips   = {**raw_clips, **replacements}

    new_offsets    = [0] * ac
    new_offsets[0] = skel_offset          # skeleton stays at same relative pos
    cur = header_size + len(skel_blob)    # clips start right after the blob
    for i in range(1, ac):
        if i in all_clips:
            new_offsets[i] = cur
            cur += len(all_clips[i])

    total_size = cur   # total motionpack size

    # Patch the 4-byte size field that lives at the start of skel_blob
    # (bytes header_size..header_size+3 in the original pack = skel_blob[0:4])
    blob = bytearray(skel_blob)
    struct.pack_into('<I', blob, 0, total_size)

    out = bytearray(struct.pack('<I', ac))
    for o in new_offsets: out += struct.pack('<I', o)
    out += blob
    for i in range(1, ac):
        if i in all_clips: out += all_clips[i]
    return bytes(out)

# ─── File helpers ────────────────────────────────────────────────────────────
def get_mp(data):
    _,ents=parse_gfpkg(data); e=ents[0]
    return data[e['addr']:e['addr']+e['length']]

def patch_mp(orig, new_mp):
    magic,ents=parse_gfpkg(orig)
    blobs=[new_mp]+[orig[e['addr']:e['addr']+e['length']] for e in ents[1:]]
    return build_gfpkg(magic,blobs)

# ─── JSON helpers ─────────────────────────────────────────────────────────────
def _build_hierarchy(skel):
    def node(i):
        n = {'name': skel[i]['name']}
        children = [node(j) for j in range(len(skel)) if skel[j].get('parent') == i]
        if children:
            n['children'] = children
        return n
    roots = [i for i,b in enumerate(skel) if b.get('parent', -1) == -1]
    return [node(r) for r in roots]

def to_json(cidx,fc,bones,skel):
    rest={b['name']:{'translation':list(b['t']),'quaternion_wxyz':list(b['q'])} for b in skel}
    out_bones=[]
    for b in bones:
        chans={ch:[{'frame':k['frame'],'value':round(k['value'],7),
                    'slope':round(k['slope'],7) if k['slope'] is not None else None}
                   for k in kfs]
               for ch,kfs in b['channels'].items() if kfs}
        out_bones.append({'name':b['name'],'channels':chans})
    return {
        '_notes':[
            'Rotations in RADIANS (Euler XYZ). Translations in model units.',
            'null slope = linear interpolation; omit slope key for constant channels.',
            "Don't change clip_index, frame_count, or bone names.",
            'patch cmd: python anim.py patch orig.pb this.json out.pb',
        ],
        'skeleton_hierarchy':_build_hierarchy(skel),
        'clip_index':cidx,'frame_count':fc,
        'skeleton_rest_poses':rest,'bones':out_bones,
    }

def from_json(j):
    bones=[{'name':b['name'],'channels':{
        ch:[{'frame':k['frame'],'value':float(k['value']),
             'slope':float(k['slope']) if k['slope'] is not None else None}
            for k in kfs]
        for ch,kfs in b.get('channels',{}).items()}}
        for b in j['bones']]
    return j['clip_index'],j['frame_count'],bones

# ─── Commands ────────────────────────────────────────────────────────────────
def cmd_info(a):
    data=open(a.file,'rb').read()
    magic,ents=parse_gfpkg(data)
    print(f'\nFile: {a.file}  ({len(data):,} bytes)  Magic: {magic}')
    print(f'Entries: {len(ents)}')
    for i,e in enumerate(ents):
        tag=(' [GF1MotionPack]' if i==0 else
             ' [BCH]' if data[e["addr"]:e["addr"]+4]==b'BCH\x00' else '')
        print(f'  [{i:2d}] addr={e["addr"]:7d} len={e["length"]:6d}{tag}')
    mp=get_mp(data); sk,anims,_,_,ac,_=decode_motionpack(mp,0)
    print(f'\nSkeleton: {len(sk)} bones')
    print(f'Names: {[b["name"] for b in sk]}')
    print(f'\nAnimation clips ({len(anims)} non-empty of {ac-1} slots):')
    for i in range(1,ac):
        if i in anims:
            fc,bs=anims[i]; print(f'  clip[{i:2d}]: {fc:3d} frames, {len(bs):2d} animated bones')
        else:
            print(f'  clip[{i:2d}]: (empty)')
    print()

def cmd_export(a):
    data=open(a.file,'rb').read()
    mp=get_mp(data); sk,anims,_,_,ac,_=decode_motionpack(mp,0)
    if a.clip not in anims:
        print(f'ERROR: clip {a.clip} is empty. Available: {sorted(anims.keys())}'); sys.exit(1)
    fc,bones=anims[a.clip]
    j=to_json(a.clip,fc,bones,sk)
    open(a.output,'w',encoding='utf-8').write(json.dumps(j,indent=2))
    print(f'\nExported clip[{a.clip}]: {fc} frames, {len(bones)} bones → {a.output}')
    print(f'Bones: {[b["name"] for b in bones]}\n')

def cmd_inspect(a):
    data=open(a.file,'rb').read()
    mp=get_mp(data); sk,anims,_,_,ac,_=decode_motionpack(mp,0)
    if a.clip not in anims: print(f'Empty clip.'); sys.exit(1)
    fc,bones=anims[a.clip]
    print(f'\nClip[{a.clip}]: {fc} frames, {len(bones)} animated bones\n')
    for b in bones:
        print(f'  {b["name"]}:')
        for ch,kfs in b['channels'].items():
            if not kfs: continue
            smpl=', '.join(f'f{k["frame"]}={k["value"]:.4f}' for k in kfs[:4])
            if len(kfs)>4: smpl+=f' ...(+{len(kfs)-4})'
            print(f'    {ch:14s}: {len(kfs):2d} kf  [{smpl}]')
    print()

def cmd_tpose(a):
    j=json.load(open(a.input_json,encoding='utf-8'))
    fc=j['frame_count']
    new_bones=[]
    for b in j['bones']:
        chans={
            'TranslationX':[{'frame':0,'value':0.,'slope':None}],
            'TranslationY':[{'frame':0,'value':0.,'slope':None}],
            'TranslationZ':[{'frame':0,'value':0.,'slope':None}],
            'RotationX':   [{'frame':0,'value':0.,'slope':None}],
            'RotationY':   [{'frame':0,'value':0.,'slope':None}],
            'RotationZ':   [{'frame':0,'value':0.,'slope':None}],
            'ScaleX':      [{'frame':0,'value':1.,'slope':None}],
            'ScaleY':      [{'frame':0,'value':1.,'slope':None}],
            'ScaleZ':      [{'frame':0,'value':1.,'slope':None}],
        }
        new_bones.append({'name':b['name'],'channels':chans})
    j['bones']=new_bones
    j['_notes'].insert(0,'T-POSE: all bones at identity. Use to verify encoder works.')
    open(a.output_json,'w',encoding='utf-8').write(json.dumps(j,indent=2))
    print(f'\nT-pose JSON → {a.output_json}  (patch this first to verify pipeline)\n')

def cmd_patch(a):
    orig   = open(a.original,'rb').read()
    j      = json.load(open(a.json,encoding='utf-8'))
    cidx, fc, new_bones = from_json(j)

    mp = get_mp(orig)
    sk, anims, raw_clips, skel_blob, ac, skel_off = decode_motionpack(mp, 0)

    # Only re-encode the clip we're actually changing.
    # Skeleton blob and all other clips are preserved byte-for-byte.
    new_clip_bytes = encode_clip(fc, new_bones, sk)
    new_mp = build_motionpack(skel_blob, raw_clips, {cidx: new_clip_bytes}, ac, skel_off)
    out     = patch_mp(orig, new_mp)

    open(a.output,'wb').write(out)
    d = len(out)-len(orig)
    print(f'\nPatched clip[{cidx}] → {a.output}')
    print(f'Size: {len(orig):,} → {len(out):,} bytes ({"+" if d>=0 else ""}{d})')
    if d != 0:
        print(f'(size change is normal — only clip[{cidx}] was re-encoded)')
    print(f'\nNext: LZ11-compress → repack GARC a/0/0/8 → deploy via Luma3DS LayeredFS\n')

# ─── CLI ─────────────────────────────────────────────────────────────────────
def main():
    try: sys.stdout.reconfigure(encoding='utf-8')  # Windows cp1252 console can't print '→'
    except AttributeError: pass
    p=argparse.ArgumentParser(description='ORAS .pb/.pk GF1Motion editor',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    s=p.add_subparsers(dest='command',required=True)

    pi=s.add_parser('info');    pi.add_argument('file')
    pe=s.add_parser('export');  pe.add_argument('file'); pe.add_argument('output')
    pe.add_argument('--clip',type=int,default=1,help='Clip index (default: 1)')
    pv=s.add_parser('inspect'); pv.add_argument('file')
    pv.add_argument('--clip',type=int,default=1)
    pt=s.add_parser('tpose');   pt.add_argument('input_json'); pt.add_argument('output_json')
    pp=s.add_parser('patch');   pp.add_argument('original'); pp.add_argument('json')
    pp.add_argument('output')

    a=p.parse_args()
    {'info':cmd_info,'export':cmd_export,'inspect':cmd_inspect,
     'tpose':cmd_tpose,'patch':cmd_patch}[a.command](a)

if __name__=='__main__': main()