// The replay of a command stream in C (NX2Linux S7bis): the state a draw reads is kept from the records of nvnrec.c (and of the commands Python records, encoded into the same records), and a draw record
// becomes an Nx2Draw here and goes straight to the GPU library, without Python: the conversion nvn_gpu.py _draw does, ported. What needs the game's objects (the shaders a program has, the GPU image
// of a texture, a texture binding with its sampler) is asked of Python through callbacks and kept, so Python runs once per program, image and binding rather than once per draw.
// A draw the C code does not do (a program with storage images, an indirect draw) is handed back to Python with the stream position; Python exports the state (nx2rep_export), does it, and goes on.
// Included by nvnrec.c: it shares the guest memory access and the record layouts.
#include "nx2gpu_types.h"

int nx2gpu_draw(const Nx2Draw *d);
int nx2gpu_flush(void);
int nx2gpu_ring_read(uint64_t offset, void *out, uint64_t bytes);

enum { OP_SAMP = 14, OP_IMG, OP_TEXPOOL, OP_SAMPPOOL, OP_MISC, OP_VIEWPORT_F };
enum { MISC_BLEND_COLOR = 1, MISC_DEPTH_RANGE, MISC_ALPHA_REF, MISC_POLY_OFFSET, MISC_PATCH_SIZE };
enum { NST = 6, NUBO = 16, NTEXS = 300, NIMGS = 16, MAX_VERTEX_BYTES = 64 << 20 };
enum { QUADS_TOPOLOGY = 100, PATCHES = 14 };

typedef struct { uint64_t addr, size; uint8_t bound; } Range;

static struct RState {
    uint64_t program, cs_program; int has_program, has_cs;
    Range ubo[NST][NUBO], ssbo[NST][NUBO], vbuf[16];
    uint32_t tex[NST][NTEXS]; uint64_t samp[NST][NTEXS], img[NST][NIMGS];
    uint64_t texpool, samppool;
    uint32_t nvattr, vattr[16], nvstream; uint64_t vstream[16];
    uint32_t color, chmask, poly; uint64_t ds; int ds_bound; uint64_t blend[8]; uint8_t blend_bound[8];
    float vp[4]; int has_vp; int32_t sc[4]; int has_sc;
    float blend_color[4], depth_range[2], alpha_ref, poly_off[3]; uint32_t patch_size, sten[2][3];
    uint32_t nrt; uint64_t rt[8]; uint8_t rt_view[8][17];   // per target: has, 16 bytes of the view
    uint64_t depth; uint8_t depth_view[17];
} RS;

static void rstate_reset(void) {
    memset(&RS, 0, sizeof RS);
    RS.color = 3 << 8 | 8 << 12;      // blend enables 0, logic op COPY, alpha function ALWAYS
    RS.chmask = 0xFFFFFFFFu;
    RS.poly = 1 << 2 | 2 << 3;        // no culling, counter-clockwise front, fill
    RS.ds = 1ull << 1 | 2ull << 2;    // depth test off, write on, function LESS (nvn_state.py defaults)
    RS.depth_range[1] = 1.0f; RS.patch_size = 3;
    for (int f = 0; f < 2; f++) { RS.sten[f][0] = 0xFF; RS.sten[f][1] = 0xFF; }
}

// ---- what Python tells: the functions it answers with, and the tables of the formats ----
typedef struct { uint32_t binding, slot; } CbInfo;
typedef struct { uint32_t binding, cb_slot, cb_offset, flags; } SbInfo;
typedef struct { uint32_t binding, type, handle, cbuf_slot; } TxInfo;
typedef struct {
    uint32_t module, kind /* 0 vs 1 ps 2 gs 3 tcs 4 tes */, nvn_stage, ncb, nsb, ntx, nim, pad;
    CbInfo cb[32]; SbInfo sb[32]; TxInfo tx[32]; const uint8_t *c1;
} StInfo;
typedef struct { int32_t status; /* 0 ok, 1 skip (Python counted it), 2 do it in Python */ uint32_t nst, alpha_known, alpha_module; uint64_t program, epoch; StInfo st[5]; } ProgInfo;
typedef struct { int32_t status; uint32_t id, w, h, depth, kind, layers, pad; } ImgInfo;
typedef int (*ProgCb)(uint64_t program, ProgInfo *out);
typedef int (*AlphaCb)(uint64_t program, uint32_t *module);
typedef int (*ImgCb)(uint64_t ptr, ImgInfo *out);
typedef int (*TexCb)(uint64_t texpool, uint64_t samppool, uint64_t handle, uint32_t type, Nx2Tex *out);

static ProgCb cb_prog; static AlphaCb cb_alpha; static ImgCb cb_img; static TexCb cb_tex;
static uint32_t vk_format[256]; static int16_t blend_factor[0x65]; static uint8_t wrap_mode[8];
static uint8_t support_buf[2048]; static uint32_t support_size, alpha_func_offset, alpha_ref_offset;
static uint32_t depth_neg_one = 1;
static uint64_t epoch_prog = 1, epoch_pool = 1, frame_no = 1;
static void (*draw_hook)(const Nx2Draw *);

void nx2rep_callbacks(ProgCb p, AlphaCb a, ImgCb i, TexCb t) { cb_prog = p; cb_alpha = a; cb_img = i; cb_tex = t; }
void nx2rep_tables(const uint32_t *vk, const int16_t *bf, const uint8_t *wrap, const uint8_t *support, uint32_t ssize, uint32_t afunc, uint32_t aref) {
    memcpy(vk_format, vk, sizeof vk_format); memcpy(blend_factor, bf, sizeof blend_factor); memcpy(wrap_mode, wrap, sizeof wrap_mode);
    support_size = ssize < sizeof support_buf ? ssize : sizeof support_buf; memcpy(support_buf, support, support_size); alpha_func_offset = afunc; alpha_ref_offset = aref;
}
void nx2rep_epoch(int which) { if (which == 0) __atomic_add_fetch(&epoch_prog, 1, __ATOMIC_RELAXED); else __atomic_add_fetch(&epoch_pool, 1, __ATOMIC_RELAXED); }
void nx2rep_frame(uint64_t n) { frame_no = n; }
void nx2rep_depth_mode(uint32_t neg_one) { depth_neg_one = neg_one; }
void nx2rep_hook(void (*h)(const Nx2Draw *)) { draw_hook = h; }
void nx2rep_begin(void) { rstate_reset(); }

// ---- counters, and the skip reasons C itself finds (Python names them: nvn_replay.py SKIPS) ----
enum { SK_NO_PROGRAM, SK_TARGETS, SK_NO_TARGET, SK_STREAM_UNBOUND, SK_VERTEX_FORMAT, SK_UNREADABLE, SK_INDEX, SK_PRIMITIVE, SK_PATCHES, SK_TESS, SK_BUFFERS, SK_NO_ALPHA, SK_NO_TEXTURE_SLOT, SK_PROG_STATUS, SK_IMAGE_STATUS, SK_TEX_STATUS, SK_EMPTY, SK_N };
static uint64_t st_draws, st_lib_errors, st_write_backs, st_skip[SK_N], st_prim[32], st_prog_cb, st_img_cb, st_tex_cb, st_fallbacks;
void nx2rep_stats(uint64_t *out) {
    uint64_t v[] = { st_draws, st_lib_errors, st_write_backs, st_prog_cb, st_img_cb, st_tex_cb, st_fallbacks };
    memcpy(out, v, sizeof v); memcpy(out + 8, st_skip, sizeof st_skip); memcpy(out + 8 + SK_N, st_prim, sizeof st_prim);
}

// ---- caches ----
enum { PCAP = 8192, ICAP = 8192, TCAP = 32768 };
static ProgInfo *prog_tab[PCAP];                                          // by program pointer
static struct { uint64_t ptr; ImgInfo info; } img_tab[ICAP];
typedef struct { uint64_t texpool, samppool, handle; uint32_t type; int used; int32_t status; uint64_t epoch, frame; Nx2Tex tex; } TexEnt;
static TexEnt tex_tab[TCAP]; static uint32_t tex_used;

static uint32_t hash64(uint64_t a, uint64_t b) { uint64_t h = (a * 0x9E3779B97F4A7C15ull) ^ (b + 0x7F4A7C15ull + (a << 6)); h *= 0xC2B2AE3D27D4EB4Full; return (uint32_t)(h >> 40); }

static ProgInfo *program_info(uint64_t ptr) {
    uint32_t i = hash64(ptr, 1) & (PCAP - 1);
    for (uint32_t n = 0; n < PCAP; n++, i = (i + 1) & (PCAP - 1)) {
        ProgInfo *p = prog_tab[i];
        if (!p) { p = prog_tab[i] = calloc(1, sizeof *p); p->program = ptr; p->epoch = 0; }
        if (p->program != ptr) continue;
        uint64_t ep = __atomic_load_n(&epoch_prog, __ATOMIC_RELAXED);
        if (p->epoch != ep) {
            memset(p, 0, sizeof *p); p->program = ptr; p->epoch = ep;
            st_prog_cb++;
            p->status = cb_prog ? cb_prog(ptr, p) : 1;
            p->program = ptr; p->epoch = ep;
        }
        return p;
    }
    return NULL;
}

static ImgInfo *image_info(uint64_t ptr) {
    uint32_t i = hash64(ptr, 2) & (ICAP - 1);
    for (uint32_t n = 0; n < ICAP; n++, i = (i + 1) & (ICAP - 1)) {
        if (img_tab[i].ptr == ptr) return &img_tab[i].info;
        if (!img_tab[i].ptr) {
            ImgInfo info; memset(&info, 0, sizeof info); st_img_cb++;
            info.status = cb_img ? cb_img(ptr, &info) : 1;
            if (info.status) { static ImgInfo bad; bad = info; return &bad; }   // not kept: the next draw asks again
            img_tab[i].ptr = ptr; img_tab[i].info = info; return &img_tab[i].info;
        }
    }
    return NULL;
}

static TexEnt *texture_binding(uint64_t texpool, uint64_t samppool, uint64_t handle, uint32_t type) {
    uint32_t h32 = (uint32_t)handle;
    uint64_t ep = __atomic_load_n(&epoch_pool, __ATOMIC_RELAXED);
    uint32_t i = hash64(texpool ^ (samppool << 7), h32 ^ ((uint64_t)type << 32)) & (TCAP - 1);
    for (uint32_t n = 0; n < 64; n++, i = (i + 1) & (TCAP - 1)) {
        TexEnt *e = &tex_tab[i];
        if (e->used && !(e->texpool == texpool && e->samppool == samppool && e->handle == h32 && e->type == type)) continue;
        if (!e->used) { if (tex_used > TCAP / 2) { memset(tex_tab, 0, sizeof tex_tab); tex_used = 0; e = &tex_tab[hash64(texpool ^ (samppool << 7), h32 ^ ((uint64_t)type << 32)) & (TCAP - 1)]; } e->used = 1; e->texpool = texpool; e->samppool = samppool; e->handle = h32; e->type = type; e->epoch = 0; tex_used++; }
        if (e->epoch != ep || e->frame != frame_no) {   // once a frame per binding: Python re-reads a texture the game has written since (it streams them in)
            st_tex_cb++;
            memset(&e->tex, 0, sizeof e->tex);
            e->status = cb_tex ? cb_tex(texpool, samppool, h32, type, &e->tex) : 1;
            e->epoch = ep; e->frame = frame_no;
        }
        return e;
    }
    return NULL;
}

// ---- applying records to the state ----
__extension__ typedef unsigned __int128 u128v;
static uint32_t view_u(const uint8_t *raw, int off, int w) { u128v v = 0; memcpy(&v, raw, 16); return (uint32_t)((v >> off) & (((u128v)1 << w) - 1)); }

static void apply_record(uint32_t op, const uint64_t *p, uint32_t n) {
    switch (op) {
    case OP_UBO: if (p[0] < NST && p[1] < NUBO) RS.ubo[p[0]][p[1]] = (Range){ p[2], p[3], 1 }; break;
    case OP_SSBO: if (p[0] < NST && p[1] < NUBO) RS.ssbo[p[0]][p[1]] = (Range){ p[2], p[3], 1 }; break;
    case OP_VBUF: if (p[0] < 16) RS.vbuf[p[0]] = (Range){ p[1], p[2], 1 }; break;
    case OP_TEX: if (p[0] < NST && p[1] < NTEXS) RS.tex[p[0]][p[1]] = (uint32_t)p[2]; break;
    case OP_SAMP: if (p[0] < NST && p[1] < NTEXS) RS.samp[p[0]][p[1]] = p[2]; break;
    case OP_IMG: if (p[0] < NST && p[1] < NIMGS) RS.img[p[0]][p[1]] = p[2]; break;
    case OP_VIEWPORT: for (int i = 0; i < 4; i++) RS.vp[i] = (float)(int64_t)p[i]; RS.has_vp = 1; break;
    case OP_VIEWPORT_F: for (int i = 0; i < 4; i++) { uint32_t b = (uint32_t)p[i]; memcpy(&RS.vp[i], &b, 4); } RS.has_vp = 1; break;
    case OP_SCISSOR: for (int i = 0; i < 4; i++) RS.sc[i] = (int32_t)(int64_t)p[i]; RS.has_sc = 1; break;
    case OP_PROGRAM:
        if (p[1] & 0x1F) { RS.program = p[0]; RS.has_program = 1; }
        if (p[1] & 0x20) { RS.cs_program = p[0]; RS.has_cs = 1; }
        break;
    case OP_BIND_STATE: {
        uint64_t v = p[1];
        switch (p[0]) {
        case 0: { uint32_t t = v & 7; RS.blend[t] = v; RS.blend_bound[t] = 1; break; }
        case 1: RS.color = (uint32_t)v; break;
        case 2: RS.chmask = (uint32_t)v; break;
        case 4: RS.poly = (uint32_t)v; break;
        case 5: RS.ds = v; RS.ds_bound = 1; break;
        default: break;
        }
        break;
    }
    case OP_VATTR: RS.nvattr = (uint32_t)p[0]; if (RS.nvattr > 16) RS.nvattr = 0; for (uint32_t i = 0; i < RS.nvattr; i++) { uint32_t w; memcpy(&w, (const uint8_t *)&p[1] + 4 * i, 4); RS.vattr[i] = w; } break;
    case OP_VSTREAM: RS.nvstream = (uint32_t)p[0]; if (RS.nvstream > 16) RS.nvstream = 0; for (uint32_t i = 0; i < RS.nvstream; i++) RS.vstream[i] = p[1 + i]; break;
    case OP_TARGETS: {
        uint32_t k = (uint32_t)p[0]; RS.nrt = k > 8 ? 0 : k; RS.depth = p[1];
        RS.depth_view[0] = p[2] != 0; memcpy(RS.depth_view + 1, &p[3], 16);
        for (uint32_t i = 0; i < RS.nrt; i++) { const uint64_t *q = p + 5 + 4 * i; RS.rt[i] = q[0]; RS.rt_view[i][0] = q[1] != 0; memcpy(RS.rt_view[i] + 1, &q[2], 16); }
        break;
    }
    case OP_STENCIL: {
        uint32_t which = (uint32_t)p[0], faces = (uint32_t)p[1]; uint32_t slot = which == 0 ? 2 : which == 1 ? 1 : 0;   // sten[face] = value mask, mask, reference
        for (int f = 0; f < 2; f++) if (faces >> f & 1) RS.sten[f][slot] = (uint32_t)p[2];
        break;
    }
    case OP_TEXPOOL: RS.texpool = p[0]; break;
    case OP_SAMPPOOL: RS.samppool = p[0]; break;
    case OP_MISC: {
        float f[4]; for (int i = 0; i < 4; i++) { uint32_t b = (uint32_t)p[1 + i]; memcpy(&f[i], &b, 4); }
        switch (p[0]) {
        case MISC_BLEND_COLOR: memcpy(RS.blend_color, f, 16); break;
        case MISC_DEPTH_RANGE: RS.depth_range[0] = f[0]; RS.depth_range[1] = f[1]; break;
        case MISC_ALPHA_REF: RS.alpha_ref = f[0]; break;
        case MISC_POLY_OFFSET: memcpy(RS.poly_off, f, 12); break;
        case MISC_PATCH_SIZE: RS.patch_size = (uint32_t)p[1]; break;
        }
        break;
    }
    default: break;
    }
    (void)n;
}

// Python records some commands itself: it encodes them into the same records and applies them here
void nx2rep_apply(const uint8_t *buf, uint32_t len) {
    for (uint32_t pos = 0; pos + 8 <= len;) {
        uint64_t head; memcpy(&head, buf + pos, 8); uint32_t op = (uint32_t)head, nw = (uint32_t)(head >> 32);
        if (!nw || pos + 8ull * nw > len) break;
        apply_record(op, (const uint64_t *)(buf + pos + 8), nw);
        pos += 8 * nw;
    }
}

// ---- the draw ----
static const uint8_t zero_segment[4096];

typedef struct { uint32_t index; uint64_t addr; uint32_t size; } WriteBack;

static int32_t mx(int32_t a, int32_t b) { return a > b ? a : b; }

static int seen_before(uint32_t *seen, uint32_t *n, uint32_t b) {   // a binding two stages declare is given once: the first stage keeps it
    for (uint32_t i = 0; i < *n; i++) if (seen[i] == b) return 1;
    if (*n < 96) seen[(*n)++] = b;
    return 0;
}

static int skip(int why) { st_skip[why]++; return 0; }

// The bytes of a range of the game's memory, in place: size limited, an empty range is not an error (the shader reads zeros)
static int guest_span(uint64_t addr, uint64_t size, uint64_t limit, const uint8_t **out, uint64_t *got) {
    if (!addr || !size) { *out = NULL; *got = 0; return 1; }
    if (size > limit) size = limit;
    const uint8_t *p = rd(addr, size);
    if (!p) return 0;
    *out = p; *got = size; return 1;
}

static uint64_t stencil_field(uint64_t ds, int off) { return ds >> off & 15; }

// 1 done (drawn or skipped for a reason counted), 2 hand the draw to Python
static int do_draw(const uint64_t *r) {
    uint64_t elements = r[0], prim = r[1], first = r[2], count = r[3], index_type = r[4], index_buffer = r[5], base_vertex = r[6], base_instance = r[7], instances = r[8];
    st_draws++; st_prim[prim & 31]++;
    if (!RS.has_program) return skip(SK_NO_PROGRAM) + 1;
    ProgInfo *pi = program_info(RS.program);
    if (!pi) return skip(SK_NO_PROGRAM) + 1;
    if (pi->status == 1) return skip(SK_PROG_STATUS) + 1;
    if (pi->status == 2) return 2;
    int has_tes = 0, has_tcs = 0;
    for (uint32_t s = 0; s < pi->nst; s++) { if (pi->st[s].kind == 4) has_tes = 1; if (pi->st[s].kind == 3) has_tcs = 1; }
    if (has_tcs != has_tes) return skip(SK_TESS) + 1;
    if (prim == PATCHES && !has_tes) return skip(SK_PATCHES) + 1;
    uint32_t vs_id = 0, ps_id = 0; Nx2Draw d; memset(&d, 0, sizeof d);
    for (uint32_t s = 0; s < pi->nst; s++) {
        const StInfo *st = &pi->st[s];
        switch (st->kind) { case 0: vs_id = st->module; break; case 1: ps_id = st->module; break; case 2: d.gs = st->module; break; case 3: d.tcs = st->module; break; default: d.tes = st->module; }
    }
    uint8_t support[2048]; const uint8_t *support_use = support_buf; uint32_t alpha_func = RS.color >> 12 & 15;
    if (ps_id && alpha_func != 8) {
        if (!pi->alpha_known) { uint32_t m = 0; if (cb_alpha) cb_alpha(pi->program, &m); pi->alpha_module = m; pi->alpha_known = 1; }
        if (pi->alpha_module) {
            ps_id = pi->alpha_module; memcpy(support, support_buf, support_size);
            memcpy(support + alpha_func_offset, &alpha_func, 4); memcpy(support + alpha_ref_offset, &RS.alpha_ref, 4); support_use = support;
        } else skip(SK_NO_ALPHA);
    }
    d.vs = vs_id; d.ps = ps_id; d.patch_points = RS.patch_size;
    uint64_t rts[8]; uint32_t nrts = 0;
    for (uint32_t i = 0; i < RS.nrt; i++) if (RS.rt[i]) rts[nrts++] = RS.rt[i];
    if (nrts > MAX_TARGETS) return skip(SK_TARGETS) + 1;
    d.ntargets = nrts;
    uint32_t tw = 0, th = 0; int have_extent = 0;
    for (uint32_t i = 0; i < nrts; i++) {
        ImgInfo *im = image_info(rts[i]); if (im->status) return skip(SK_IMAGE_STATUS) + 1;
        const uint8_t *v = i < RS.nrt && RS.rt_view[i][0] ? RS.rt_view[i] + 1 : NULL;   // Python indexes the views by the position among the non-empty targets
        uint32_t layer = v && view_u(v, 1, 1) ? view_u(v, 48, 16) : 0, level = v && view_u(v, 0, 1) ? view_u(v, 16, 8) : 0;
        d.targets[i].image = im->id; d.targets[i].layer = layer; d.targets[i].level = level;
        if (!have_extent) { have_extent = 1; tw = im->w >> level; th = im->h >> level; if (!tw) tw = 1; if (!th) th = 1; }
    }
    if (RS.depth) {
        ImgInfo *im = image_info(RS.depth); if (im->status) return skip(SK_IMAGE_STATUS) + 1;
        const uint8_t *v = RS.depth_view[0] ? RS.depth_view + 1 : NULL;
        uint32_t layer = v && view_u(v, 1, 1) ? view_u(v, 48, 16) : 0, level = v && view_u(v, 0, 1) ? view_u(v, 16, 8) : 0;
        d.depth.image = im->id; d.depth.layer = layer; d.depth.level = level;
        if (!have_extent) { have_extent = 1; tw = im->w >> level; th = im->h >> level; if (!tw) tw = 1; if (!th) th = 1; }
    }
    if (!have_extent) return skip(SK_NO_TARGET) + 1;

    // fixed-function state
    for (uint32_t i = 0; i < nrts; i++) {
        uint32_t en = RS.color >> i & 1 & (i < 8), m = RS.chmask >> (4 * i) & 0xF; Nx2Blend *bl = &d.blend[i];
        bl->mask = m;
        if (en && i < 8 && RS.blend_bound[i]) {
            uint64_t b = RS.blend[i];
            uint32_t src_rgb = b >> 3 & 127, dst_rgb = b >> 10 & 127, src_a = b >> 17 & 127, dst_a = b >> 24 & 127, eq_rgb = b >> 31 & 7, eq_a = b >> 34 & 7;
            bl->enable = 1;
            bl->src_rgb = src_rgb < 0x65 && blend_factor[src_rgb] >= 0 ? (uint32_t)blend_factor[src_rgb] : 1;
            bl->dst_rgb = dst_rgb < 0x65 && blend_factor[dst_rgb] >= 0 ? (uint32_t)blend_factor[dst_rgb] : 0;
            bl->op_rgb = eq_rgb - 1;
            bl->src_a = src_a < 0x65 && blend_factor[src_a] >= 0 ? (uint32_t)blend_factor[src_a] : 1;
            bl->dst_a = dst_a < 0x65 && blend_factor[dst_a] >= 0 ? (uint32_t)blend_factor[dst_a] : 0;
            bl->op_a = eq_a - 1;
        }
    }
    {
        uint32_t cull = RS.poly & 3, front = RS.poly >> 2 & 1, mode = RS.poly >> 3 & 3, off_en = RS.poly >> 5 & 7;
        d.cull = cull; d.front_face = front; d.polygon_mode = mode == 0 ? 2 : mode == 1 ? 1 : 0;   // {0: 2, 1: 1, 2: 0}.get(mode, 0)
        d.depth_test = RS.ds & 1; d.depth_write = RS.ds >> 1 & 1; uint32_t df = RS.ds >> 2 & 15; d.depth_func = df > 0 ? df - 1 : 0;
        d.depth_neg_one = depth_neg_one;
        if (RS.ds_bound) {
            d.stencil_test = RS.ds >> 6 & 1;
            uint32_t *arr[2] = { d.stencil_front, d.stencil_back };
            static const int offs[2][4] = { { 15, 19, 23, 7 }, { 27, 31, 35, 11 } };   // fail, depth fail, pass, function: front then back
            for (int f = 0; f < 2; f++) for (int k = 0; k < 4; k++) { uint64_t x = stencil_field(RS.ds, offs[f][k]); arr[f][k] = x > 0 ? (uint32_t)(x - 1) : 0; }
        }
        for (int f = 0; f < 2; f++) for (int k = 0; k < 3; k++) d.stencil_masks[3 * f + k] = RS.sten[f][k];
        memcpy(d.blend_color, RS.blend_color, 16);
        if (off_en) { d.depth_bias_on = 1; d.depth_bias[0] = RS.poly_off[1]; d.depth_bias[1] = RS.poly_off[2]; d.depth_bias[2] = RS.poly_off[0]; }
    }
    {
        float vp[4] = { 0, 0, (float)tw, (float)th }; if (RS.has_vp) memcpy(vp, RS.vp, 16);
        d.viewport[0] = vp[0]; d.viewport[1] = vp[1] + vp[3]; d.viewport[2] = vp[2]; d.viewport[3] = -vp[3];
        d.depth_range[0] = RS.depth_range[0]; d.depth_range[1] = RS.depth_range[1];
        int32_t sc[4] = { 0, 0, (int32_t)tw, (int32_t)th }; if (RS.has_sc) memcpy(sc, RS.sc, 16);
        for (int i = 0; i < 4; i++) d.scissor[i] = mx(0, sc[i]);
    }

    // vertex input
    int32_t icount = (int32_t)count; uint32_t ninst = (uint32_t)instances;
    if (icount <= 0 || (int32_t)ninst <= 0) return skip(SK_EMPTY) + 1;
    const uint8_t *idx_ptr = NULL; uint64_t idx_bytes = 0; uint32_t idx_max = 0, idx_kind = 0; static __thread uint8_t *idx_scratch; static __thread size_t idx_cap;
    if (elements) {
        uint32_t isz = index_type < 3 ? (1u << index_type) : 4;
        uint64_t nbytes = (uint64_t)(uint32_t)icount * isz;
        const uint8_t *src; uint64_t got;
        if (!guest_span(index_buffer, nbytes, 1 << 26, &src, &got)) return skip(SK_UNREADABLE) + 1;
        if (!src || got < nbytes) return skip(SK_INDEX) + 1;
        if (isz == 1) {   // byte indices become 16-bit ones
            if (idx_cap < 2 * nbytes) { idx_cap = 2 * nbytes + 4096; idx_scratch = realloc(idx_scratch, idx_cap); }
            uint16_t *o = (uint16_t *)idx_scratch; for (uint64_t i = 0; i < nbytes; i++) { o[i] = src[i]; if (src[i] > idx_max) idx_max = src[i]; }
            idx_ptr = idx_scratch; idx_bytes = 2 * nbytes; idx_kind = 1;
        } else {
            if (isz == 2) { const uint16_t *a = (const uint16_t *)src; for (uint64_t i = 0; i < (uint64_t)icount; i++) if (a[i] > idx_max) idx_max = a[i]; }
            else { const uint32_t *a = (const uint32_t *)src; for (uint64_t i = 0; i < (uint64_t)icount; i++) if (a[i] > idx_max) idx_max = a[i]; }
            idx_ptr = src; idx_bytes = nbytes; idx_kind = isz == 2 ? 1 : 2;
        }
    }
    uint32_t used = 0;   // bit per stream
    for (uint32_t loc = 0; loc < RS.nvattr; loc++) {
        uint32_t a = RS.vattr[loc], fmt = a & 0xFF, offset = a >> 8 & 0x7FFFF, stream = a >> 27 & 31;
        if (!fmt) continue;
        if (!vk_format[fmt]) return skip(SK_VERTEX_FORMAT) + 1;
        if (stream >= RS.nvstream || stream >= 16 || !RS.vbuf[stream].bound) return skip(SK_STREAM_UNBOUND) + 1;
        d.attrs[d.nattrs].location = loc; d.attrs[d.nattrs].binding = stream; d.attrs[d.nattrs].format = vk_format[fmt]; d.attrs[d.nattrs].offset = offset; d.nattrs++;
        used |= 1u << stream;
    }
    for (uint32_t s = 0; s < 16; s++) {
        if (!(used >> s & 1)) continue;
        uint32_t stride = (uint32_t)RS.vstream[s], div = (uint32_t)(RS.vstream[s] >> 32);
        uint64_t addr = RS.vbuf[s].addr, size = RS.vbuf[s].size, need;
        if (elements) need = !div ? ((uint64_t)idx_max + (uint64_t)mx((int32_t)base_vertex, 0) + 1) * stride : size;
        else need = !div ? ((uint64_t)((int64_t)(int32_t)first + icount)) * stride : size;
        uint64_t want = need && need < size ? need : size;
        const uint8_t *src; uint64_t got;
        if (!guest_span(addr, want, MAX_VERTEX_BYTES, &src, &got)) return skip(SK_UNREADABLE) + 1;
        static const uint8_t one[16] = { 0 };
        d.streams[d.nstreams].binding = s; d.streams[d.nstreams].stride = stride; d.streams[d.nstreams].divisor = div;
        d.streams[d.nstreams].data = src ? src : one; d.streams[d.nstreams].size = got; d.nstreams++;
    }
    if (prim == 2 || prim == 8 || prim == 9) return skip(SK_PRIMITIVE) + 1;
    d.topology = prim == 7 ? QUADS_TOPOLOGY : (uint32_t)prim;
    d.first = elements ? 0 : (int32_t)first; d.count = icount; d.base_vertex = elements ? (int32_t)base_vertex : 0; d.base_instance = (int32_t)base_instance; d.instances = ninst;
    if (idx_ptr) { d.index_type = idx_kind; d.index_data = idx_ptr; d.index_size = idx_bytes; }

    // resources
    uint8_t scratch[4][4096]; uint32_t nscratch = 0;
    WriteBack wb[8]; uint32_t nwb = 0; uint32_t seen_n = 0; uint32_t seen[96];
#define SEEN(b) seen_before(seen, &seen_n, (b))
#define ADD_BUF(bind, kind_, ptr_, size_) do { if (d.nbufs >= 32) return skip(SK_BUFFERS) + 1; Nx2Buf *b_ = &d.bufs[d.nbufs++]; b_->binding = (bind); b_->kind = (kind_); b_->data = (ptr_); b_->size = (size_); } while (0)
    ADD_BUF(0, 0, support_use, support_size);
    uint8_t pad_scratch[8][16]; uint32_t npad = 0;
    for (uint32_t s = 0; s < pi->nst; s++) {
        const StInfo *st = &pi->st[s]; uint32_t stage = st->nvn_stage;
        for (uint32_t k = 0; k < st->ncb; k++) {
            const CbInfo *cb = &st->cb[k]; if (SEEN(cb->binding)) continue;
            if (cb->slot >= 3 && stage < NST && cb->slot - 3 < NUBO && RS.ubo[stage][cb->slot - 3].bound) {
                const Range *u = &RS.ubo[stage][cb->slot - 3]; const uint8_t *src; uint64_t got;
                if (!guest_span(u->addr, u->size, 1 << 16, &src, &got)) return skip(SK_UNREADABLE) + 1;
                if (got < 16) { if (npad >= 8) return skip(SK_BUFFERS) + 1; memset(pad_scratch[npad], 0, 16); if (src) memcpy(pad_scratch[npad], src, got); ADD_BUF(cb->binding, 0, pad_scratch[npad], 16); npad++; }
                else ADD_BUF(cb->binding, 0, src, got);
            } else if (cb->slot == 1) ADD_BUF(cb->binding, 0, st->c1 ? st->c1 : zero_segment, 4096);
            else if (cb->slot == 0) {
                if (nscratch >= 4) return skip(SK_BUFFERS) + 1;
                uint8_t *b = scratch[nscratch++]; memset(b, 0, 4096);
                float ps = 2047.0f; uint32_t bi = (uint32_t)base_instance; memcpy(b + 0x610, &ps, 4); memcpy(b + 0x644, &bi, 4); if (st->kind == 1) b[0x620] = 0x88;
                ADD_BUF(cb->binding, 0, b, 4096);
            } else ADD_BUF(cb->binding, 0, zero_segment, 4096);
        }
        for (uint32_t k = 0; k < st->nsb; k++) {
            const SbInfo *sb = &st->sb[k]; if (SEEN(sb->binding)) continue;
            uint64_t addr = 0, size = 0; uint32_t off = sb->cb_offset * 4;
            if (sb->cb_slot == 0) {
                static const int32_t base_of[5] = { 0x110, 0x510, 0x410, -1, -1 };   // vs ps gs tcs tes: the storage buffer tables of c0
                int32_t base = base_of[st->kind];
                if (base >= 0 && (int32_t)off - base >= 0 && (int32_t)off - base < 0x100 && (off - base) % 16 == 0) { uint32_t slot = (off - base) / 16; if (stage < NST && slot < NUBO) { addr = RS.ssbo[stage][slot].addr; size = RS.ssbo[stage][slot].size; } }
            } else if (stage < NST && sb->cb_slot - 3 < NUBO && RS.ubo[stage][sb->cb_slot - 3].bound && off + 8 <= RS.ubo[stage][sb->cb_slot - 3].size) {
                const uint8_t *w = rd(RS.ubo[stage][sb->cb_slot - 3].addr + off, 8); if (!w) return skip(SK_UNREADABLE) + 1;
                memcpy(&addr, w, 8); size = 1 << 18;
            }
            const uint8_t *src; uint64_t got;
            if (!guest_span(addr, size, 1 << 24, &src, &got)) return skip(SK_UNREADABLE) + 1;
            if (got < 16) { if (npad >= 8) return skip(SK_BUFFERS) + 1; memset(pad_scratch[npad], 0, 16); if (src) memcpy(pad_scratch[npad], src, got); ADD_BUF(sb->binding, 1, pad_scratch[npad], 16); npad++; }
            else ADD_BUF(sb->binding, 1, src, got);
            if ((sb->flags & 1) && got && nwb < 8) { wb[nwb].index = d.nbufs - 1; wb[nwb].addr = addr; wb[nwb].size = (uint32_t)got; nwb++; }
        }
        for (uint32_t k = 0; k < st->ntx; k++) {
            const TxInfo *tx = &st->tx[k]; if (SEEN(tx->binding)) continue;
            uint64_t handle;
            if ((int32_t)tx->cbuf_slot >= 3) {
                handle = 0;
                if (stage < NST && tx->cbuf_slot - 3 < NUBO && RS.ubo[stage][tx->cbuf_slot - 3].bound) {
                    const Range *u = &RS.ubo[stage][tx->cbuf_slot - 3]; uint64_t o = (uint64_t)tx->handle * 4;
                    if (u->addr && o + 4 <= u->size) { const uint8_t *w = rd(u->addr + o, 4); if (!w) return skip(SK_UNREADABLE) + 1; uint32_t h; memcpy(&h, w, 4); handle = h; }
                }
            } else {
                uint32_t w = tx->handle; int slot = w < 90 ? ((int)w - 8) / 2 : 128 + ((int)w - 90) / 2;   // Python's floor division: the words below 8 never occur
                handle = 0;
                if (w >= 8 && stage < NST && slot >= 0 && slot < NTEXS) { handle = RS.tex[stage][slot]; if (slot >= 128) handle |= RS.samp[stage][(w - 90) / 2]; }
            }
            TexEnt *e = texture_binding(RS.texpool, RS.samppool, handle, tx->type);
            if (!e || e->status) return skip(SK_TEX_STATUS) + 1;
            if (d.ntex >= 32) return skip(SK_BUFFERS) + 1;
            d.tex[d.ntex] = e->tex; d.tex[d.ntex].binding = tx->binding; d.ntex++;
        }
        if (st->nim) return 2;
    }
    if (draw_hook) { draw_hook(&d); return 1; }
    int lr = nx2gpu_draw(&d);
    if (lr < 0) st_lib_errors++;
    if (nwb && lr == 0) {
        nx2gpu_flush(); st_write_backs++;
        for (uint32_t i = 0; i < nwb; i++) { uint8_t *dst = g_guest ? g_guest(wb[i].addr, wb[i].size) : NULL; if (dst) nx2gpu_ring_read(d.bufs[wb[i].index].ring_offset, dst, wb[i].size); }
    }
    return 1;
}

// Replays a stream. 0: all of it; 1: stopped at a draw that Python does (the offset of its record in *stop)
int nx2rep_run(const uint8_t *buf, uint32_t len, uint32_t *stop) {
    for (uint32_t pos = 0; pos + 8 <= len;) {
        uint64_t head; memcpy(&head, buf + pos, 8); uint32_t op = (uint32_t)head, nw = (uint32_t)(head >> 32);
        if (!nw || pos + 8ull * nw > len) break;
        const uint64_t *p = (const uint64_t *)(buf + pos + 8);
        if (op == OP_DRAW) {
            if (do_draw(p) == 2) { st_fallbacks++; st_draws--; st_prim[p[1] & 31]--; *stop = pos; return 1; }
        } else apply_record(op, p, nw);
        pos += 8 * nw;
    }
    return 0;
}

// The state as records, for what Python does itself (clears, compute, an indirect draw): the same layouts, ready for Recorder.decode
static uint32_t export_rest(uint8_t *out, uint32_t cap, uint32_t pos);
static uint32_t export_targets(uint8_t *out, uint32_t cap, uint32_t pos);
static uint32_t put(uint8_t *out, uint32_t cap, uint32_t pos, uint32_t op, const uint64_t *w, uint32_t nw) {
    if (pos + 8 * (nw + 1) > cap) return pos;
    uint64_t head = op | (uint64_t)(nw + 1) << 32; memcpy(out + pos, &head, 8); memcpy(out + pos + 8, w, 8 * nw);
    return pos + 8 * (nw + 1);
}
uint32_t nx2rep_export(uint8_t *out, uint32_t cap, uint32_t what) {   // what: 1 the render targets, 2 the rest
    uint32_t pos = 0;
    if (what & 2) for (uint32_t s = 0; s < NST; s++) {
        for (uint32_t i = 0; i < NUBO; i++) {
            if (RS.ubo[s][i].bound) { uint64_t w[4] = { s, i, RS.ubo[s][i].addr, RS.ubo[s][i].size }; pos = put(out, cap, pos, OP_UBO, w, 4); }
            if (RS.ssbo[s][i].bound) { uint64_t w[4] = { s, i, RS.ssbo[s][i].addr, RS.ssbo[s][i].size }; pos = put(out, cap, pos, OP_SSBO, w, 4); }
        }
        for (uint32_t i = 0; i < NTEXS; i++) {
            if (RS.tex[s][i]) { uint64_t w[3] = { s, i, RS.tex[s][i] }; pos = put(out, cap, pos, OP_TEX, w, 3); }
            if (RS.samp[s][i]) { uint64_t w[3] = { s, i, RS.samp[s][i] }; pos = put(out, cap, pos, OP_SAMP, w, 3); }
        }
        for (uint32_t i = 0; i < NIMGS; i++) if (RS.img[s][i]) { uint64_t w[3] = { s, i, RS.img[s][i] }; pos = put(out, cap, pos, OP_IMG, w, 3); }
    }
    if (what & 2) pos = export_rest(out, cap, pos);
    if (what & 1) pos = export_targets(out, cap, pos);
    return pos;
}
static uint32_t export_rest(uint8_t *out, uint32_t cap, uint32_t pos) {
    for (uint32_t i = 0; i < 16; i++) if (RS.vbuf[i].bound) { uint64_t w[3] = { i, RS.vbuf[i].addr, RS.vbuf[i].size }; pos = put(out, cap, pos, OP_VBUF, w, 3); }
    if (RS.has_program) { uint64_t w[2] = { RS.program, 0x1F }; pos = put(out, cap, pos, OP_PROGRAM, w, 2); }
    if (RS.has_cs) { uint64_t w[2] = { RS.cs_program, 0x20 }; pos = put(out, cap, pos, OP_PROGRAM, w, 2); }
    { uint64_t w[1] = { RS.texpool }; pos = put(out, cap, pos, OP_TEXPOOL, w, 1); }
    { uint64_t w[1] = { RS.samppool }; pos = put(out, cap, pos, OP_SAMPPOOL, w, 1); }
    { uint64_t w[17] = { RS.nvattr }; for (uint32_t i = 0; i < RS.nvattr; i++) memcpy((uint8_t *)&w[1] + 4 * i, &RS.vattr[i], 4); pos = put(out, cap, pos, OP_VATTR, w, 1 + (RS.nvattr * 4 + 7) / 8); }
    { uint64_t w[17] = { RS.nvstream }; for (uint32_t i = 0; i < RS.nvstream; i++) w[1 + i] = RS.vstream[i]; pos = put(out, cap, pos, OP_VSTREAM, w, 1 + RS.nvstream); }
    { uint64_t w[3] = { 1, RS.color, 0 }; pos = put(out, cap, pos, OP_BIND_STATE, w, 3); }
    { uint64_t w[3] = { 2, RS.chmask, 0 }; pos = put(out, cap, pos, OP_BIND_STATE, w, 3); }
    { uint64_t w[3] = { 4, RS.poly, 0 }; pos = put(out, cap, pos, OP_BIND_STATE, w, 3); }
    if (RS.ds_bound) { uint64_t w[3] = { 5, RS.ds, 0 }; pos = put(out, cap, pos, OP_BIND_STATE, w, 3); }
    for (uint32_t i = 0; i < 8; i++) if (RS.blend_bound[i]) { uint64_t w[3] = { 0, RS.blend[i], 0 }; pos = put(out, cap, pos, OP_BIND_STATE, w, 3); }
    if (RS.has_vp) { uint64_t w[4]; for (int i = 0; i < 4; i++) { uint32_t b; memcpy(&b, &RS.vp[i], 4); w[i] = b; } pos = put(out, cap, pos, OP_VIEWPORT_F, w, 4); }
    if (RS.has_sc) { uint64_t w[4]; for (int i = 0; i < 4; i++) w[i] = (uint64_t)(int64_t)RS.sc[i]; pos = put(out, cap, pos, OP_SCISSOR, w, 4); }
    for (int f = 0; f < 2; f++) for (int which = 0; which < 3; which++) {
        uint64_t w[3] = { (uint64_t)which, 1ull << f, RS.sten[f][which == 0 ? 2 : which == 1 ? 1 : 0] }; pos = put(out, cap, pos, OP_STENCIL, w, 3);
    }
    return pos;
}
static uint32_t export_targets(uint8_t *out, uint32_t cap, uint32_t pos) {
    {   // the render targets: count, depth target, depth view (has, 16 bytes), then per target the pointer, has, view
        uint64_t w[5 + 4 * 8]; memset(w, 0, sizeof w);
        w[0] = RS.nrt; w[1] = RS.depth; w[2] = RS.depth_view[0]; memcpy(&w[3], RS.depth_view + 1, 16);
        for (uint32_t i = 0; i < RS.nrt; i++) { uint64_t *q = w + 5 + 4 * i; q[0] = RS.rt[i]; q[1] = RS.rt_view[i][0]; memcpy(&q[2], RS.rt_view[i] + 1, 16); }
        pos = put(out, cap, pos, OP_TARGETS, w, 5 + 4 * RS.nrt);
    }
    return pos;
}
