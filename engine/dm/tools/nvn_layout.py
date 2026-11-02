"""Storage size, alignment and level offsets of NVN textures (Tegra X1 block-linear layout): a port of engine/dm/gfx/nvn/nvn_tex.c, which ports deko3d's dk_image.cpp (zlib licence, (C) 2018-2020 fincs).
One GOB is 64 bytes x 8 rows = 512 bytes; the block height (tileH, in GOBs) is picked from height x 1.5; levels accumulate with block alignment. A game sizes its memory pools with these numbers,
so they must match the real driver's. Usage: layout(Desc(...)) -> Layout(size, align, layer_size, tileH, tileD, bpb)."""
from dataclasses import dataclass, field

SPARSE_TILE = 0x10000
ASTC = [(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8), (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)]
UNCOMPRESSED = [(0x01, 0x04, 1), (0x05, 0x09, 2), (0x0A, 0x0C, 4), (0x0D, 0x10, 2), (0x11, 0x15, 4), (0x16, 0x18, 8),
                (0x19, 0x1C, 3), (0x1D, 0x21, 6), (0x22, 0x24, 12), (0x25, 0x28, 4), (0x29, 0x2D, 8), (0x2E, 0x30, 16),
                (0x31, 0x31, 1), (0x32, 0x32, 2), (0x33, 0x35, 4), (0x36, 0x36, 8), (0x37, 0x38, 4), (0x39, 0x3C, 2), (0x3D, 0x40, 4),
                (0x51, 0x52, 1), (0x53, 0x54, 2), (0x55, 0x56, 4), (0x57, 0x58, 2), (0x59, 0x5A, 4), (0x5B, 0x5C, 8),
                (0x5D, 0x5E, 3), (0x5F, 0x60, 6), (0x61, 0x62, 12), (0x63, 0x64, 4), (0x65, 0x66, 8), (0x67, 0x68, 16),
                (0x69, 0x6C, 4), (0x6D, 0x70, 4), (0x71, 0x75, 8), (0x76, 0x78, 16), (0x95, 0x98, 2), (0x99, 0x9C, 4)]


@dataclass
class Desc:
    """What a texture builder holds (nvn_texdesc)"""
    target: int = 1
    width: int = 0
    height: int = 0
    depth: int = 0
    levels: int = 1
    samples: int = 0
    format: int = 0
    flags: int = 0
    stride: int = 0
    pkg_block_h: int = 0


@dataclass
class Layout:
    size: int = 0
    align: int = 512
    layer_size: int = 0
    tileH: int = 0
    tileD: int = 0
    bpb: int = 0
    layers: int = 1


def fmt_of(f):
    """(bytes per block, block width, block height); bpb 0 for a format that is not known"""
    if 0x79 <= f <= 0x94:
        bw, bh = ASTC[(f - 0x79) % 14]
        return 16, bw, bh
    if 0x41 <= f <= 0x50:   # DXT / RGTC / BPTC: 4x4 blocks
        b8 = f <= 0x42 or f in (0x45, 0x46) or f in (0x49, 0x4A)
        return (8 if b8 else 16), 4, 4
    for lo, hi, bpb in UNCOMPRESSED:
        if lo <= f <= hi: return bpb, 1, 1
    return 0, 1, 1


def bpb_of(f): return fmt_of(f)[0]
def mip(s, level): return max(1, s >> level)
def blk(s, b): return (s + b - 1) // b


def pick_tile(gobs): return 4 if gobs >= 16 else 3 if gobs >= 8 else 2 if gobs >= 4 else 1 if gobs >= 2 else 0


def adj_tile(shift, unit, dim):
    if not shift: return 0
    x = unit << (shift - 1)
    if x >= dim:
        while True:
            shift -= 1
            if not shift: break
            x >>= 1
            if x < dim: break
    return shift


def shape(d):
    """(dims per layer, layered, w, h, z)"""
    w, h, z, layered = d.width, d.height, d.depth, False
    t = d.target
    if t == 0: dpl, h, z = 1, 1, 1
    elif t == 3: dpl, z, h, layered = 1, d.height, 1, True
    elif t == 2: dpl = 3
    elif t in (4, 6): dpl, layered = 2, True
    elif t == 8: dpl, z, layered = 2, 6, True
    elif t == 9: dpl, layered = 2, True
    else: dpl, z = 2, 1
    return dpl, layered, w, h, (z or 1)


def samples_xy(s):
    return (2, 1) if s == 2 else (2, 2) if s == 4 else (4, 2) if s == 8 else (1, 1)


def sparse_of(d): return bool(d.flags & 0x20) and not (d.flags & 0x50) and d.target != 10


def sparse_tile_h(bpb): return 5 if bpb == 1 else 4 if bpb in (2, 4) else 3 if bpb in (8, 16) else -1


def tile_shape_h(d, dpl, bpb, tileH):
    return sparse_tile_h(bpb) if dpl == 2 and not d.pkg_block_h and sparse_tile_h(bpb) >= 0 else tileH


def level_walk(d, level, dpl, w, h, z, tileH, tileD, t):
    bpb, bw, bh = t
    sx, sy = samples_xy(d.samples)
    sh = tile_shape_h(d, dpl, bpb, tileH)
    tw = max(1, 128 >> (sh + tileD))
    tiled = sparse_of(d)
    off, i = 0, 0
    while True:
        lw = blk(mip(w * sx, i), bw)
        lh = blk(mip(h * sy, i), bh) if dpl >= 2 else 1
        ld = mip(z, i) if dpl >= 3 else 1
        lwb = lw * bpb
        th, td = adj_tile(tileH, 8, lh), adj_tile(tileD, 1, ld)
        wg, hg = (lwb + 63) // 64, (lh + 7) // 8
        tiled = tiled and lwb >= tw * 64 and lh >= (8 << sh) and ld >= (1 << tileD)
        if tiled: wg = (wg + tw - 1) // tw * tw
        if i == level: return off, wg
        ht, dt = (hg + (1 << th) - 1) >> th, (ld + (1 << td) - 1) >> td
        off += (wg * ht * dt) << (9 + th + td)
        i += 1


def layer_bytes(d, dpl, layered, w, h, z, tileH, tileD, t):
    ls, _ = level_walk(d, d.levels or 1, dpl, w, h, z, tileH, tileD, t)
    if sparse_of(d): ls = (ls + SPARSE_TILE - 1) & ~(SPARSE_TILE - 1)
    if layered:
        hh = blk(h, t[2]); th, td = tileH, tileD
        while th and hh <= (8 << (th - 1)): th -= 1
        while td and z <= (1 << (td - 1)): td -= 1
        sh = th + td + 9
        g = ls >> sh
        if ls != g << sh: ls = (g + 1) << sh
    return ls


def layout(d):
    t = fmt_of(d.format)
    bpb, bw, bh = t
    dpl, layered, w, h, z = shape(d)
    sx, sy = samples_xy(d.samples)
    o = Layout(bpb=bpb, layers=z if layered else 1)
    if not bpb: o.size, o.align, o.layer_size = 0, 512, 0; return o
    if d.target == 10:   # BUFFER
        o.layer_size = o.size = bpb * d.width
        o.align = 4 if bpb == 12 else bpb
        return o
    if d.flags & 0x50:   # LINEAR or LINEAR_RENDER_TARGET
        o.align = 128 if d.flags & 0x40 else 32
        stride = d.stride or ((bpb * blk(w, bw) + o.align - 1) & ~(o.align - 1))
        o.layer_size = o.size = stride * h
        return o
    tileH = tileD = 0
    if d.pkg_block_h:   # packaged byte 0: low nibble block height, high nibble block depth
        pl = (d.pkg_block_h - 1) & 0xff
        if dpl >= 2: tileH = pl & 0xf
        if dpl == 3: tileD = pl >> 4
    elif dpl == 2 and sparse_of(d) and sparse_tile_h(bpb) >= 0 and blk(w * sx, bw) * bpb >= (128 >> sparse_tile_h(bpb)) * 64 and blk(h * sy, bh) >= (8 << sparse_tile_h(bpb)):
        tileH = sparse_tile_h(bpb)
    elif dpl == 2:
        ah = blk(h * sy, bh); tileH = pick_tile(ah * 3 // 16)
    elif dpl == 3:
        tileD = pick_tile(z + z // 2)
    ls = layer_bytes(d, dpl, layered, w, h, z, tileH, tileD, t)
    if (d.flags & 0x200) and not d.pkg_block_h and not sparse_of(d) and (tileH or tileD):   # MINIMAL_LAYOUT: keep the automatic block only when no larger than with 1-GOB blocks
        one = layer_bytes(d, dpl, layered, w, h, z, 0, 0, t)
        if one != ls: tileH = tileD = 0; ls = one
    o.tileH, o.tileD, o.layer_size = tileH, tileD, ls
    o.size = ls * z if layered else ls
    o.align = SPARSE_TILE if sparse_of(d) else 512
    return o
