// NVN command recording in C (NX2Linux S7bis): the host functions of the hot NVN calls, called straight from the recompiled game (aot_register_import takes a C function), without Python.
// A command buffer call appends a record to the stream of that command buffer; a state object setter edits the packed struct in guest memory. Python (nvn_rec.py) takes the stream when the
// commands are needed and turns it into the same tuples horizon_nvn.py records itself, so the replay does not change. The layouts of the records are mirrored in nvn_rec.py.
// A host function gets x0..x30 then the low halves of v0..v7 (aot_runtime.cpp import_call); the integer arguments are masked as the Python versions do.
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t *(*GuestPtr)(uint64_t addr, uint64_t size);   // aot_guest_ptr: the host address of a guest range, or NULL
static GuestPtr g_guest;

#define M32 0xFFFFFFFFull

enum { OP_UBO = 1, OP_SSBO, OP_VBUF, OP_TEX, OP_VIEWPORT, OP_SCISSOR, OP_PROGRAM, OP_BIND_STATE, OP_VATTR, OP_VSTREAM, OP_DRAW, OP_TARGETS, OP_STENCIL };

typedef struct { uint64_t addr; uint8_t *buf; uint32_t len, cap; } Stream;
enum { MAX_STREAMS = 64 };
static Stream g_streams[MAX_STREAMS];
static int g_nstreams;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

static Stream *stream_of(uint64_t cb) {
    static __thread Stream *last;
    if (last && last->addr == cb) return last;
    pthread_mutex_lock(&g_mu);
    Stream *s = NULL;
    for (int i = 0; i < g_nstreams; i++) if (g_streams[i].addr == cb) { s = &g_streams[i]; break; }
    if (!s && g_nstreams < MAX_STREAMS) { s = &g_streams[g_nstreams++]; s->addr = cb; }
    pthread_mutex_unlock(&g_mu);
    if (s) last = s;
    return s;
}

// A record is a header word (op in the low half, the length in words, header included, in the high half) and payload words. Returns the payload.
static uint64_t *emit(Stream *s, uint32_t op, uint32_t nwords) {
    uint32_t need = s->len + 8 * nwords;
    if (need > s->cap) {
        uint32_t cap = s->cap ? s->cap : 4096;
        while (cap < need) cap *= 2;
        uint8_t *b = realloc(s->buf, cap);
        if (!b) abort();
        s->buf = b; s->cap = cap;
    }
    uint64_t *p = (uint64_t *)(s->buf + s->len);
    p[0] = op | (uint64_t)nwords << 32;
    s->len = need;
    return p + 1;
}

static const uint8_t *rd(uint64_t a, uint64_t n) { return g_guest ? g_guest(a, n) : NULL; }
static int64_t s32(uint64_t v) { return (int32_t)(uint32_t)v; }

typedef struct Fld { uint8_t off, width, mode; } Fld;   // a field of a packed state struct: bit offset, width, how an argument becomes its value
enum { F_MASK, F_BOOL, F_ADV_MODE, F_NOT };
enum { SP_NONE, SP_BLEND_ENABLE, SP_CHMASK, SP_STENCIL_FUNC, SP_STENCIL_OP };
typedef struct SetDesc { uint32_t size, special, nf; Fld f[8]; } SetDesc;

typedef struct Ctx {
    uint64_t count;
    uint32_t op;       // the record, or the draw / state kind, the function writes
    SetDesc set;       // state setters
} Ctx;

typedef void (*Fn)(uint64_t *r, void *ctx);
#define COUNT(c) __atomic_fetch_add(&((Ctx *)(c))->count, 1, __ATOMIC_RELAXED)

static void r_ubo(uint64_t *r, void *c) {   // BindUniformBuffer / BindStorageBuffer (stage, index, address, size)
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, ((Ctx *)c)->op, 5);
    p[0] = r[1] & M32; p[1] = r[2] & M32; p[2] = r[3]; p[3] = r[4];
}
static void r_vbuf(uint64_t *r, void *c) {
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, OP_VBUF, 4);
    p[0] = r[1] & M32; p[1] = r[2]; p[2] = r[3];
}
static void r_tex(uint64_t *r, void *c) {
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, OP_TEX, 4);
    p[0] = r[1] & M32; p[1] = r[2] & M32; p[2] = r[3] & M32;
}
static void r_rect(uint64_t *r, void *c) {   // SetViewport / SetScissor
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, ((Ctx *)c)->op, 5);
    for (int i = 0; i < 4; i++) p[i] = (uint64_t)s32(r[1 + i]);
}
static void r_program(uint64_t *r, void *c) {
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, OP_PROGRAM, 3);
    p[0] = r[1]; p[1] = r[2] & M32;
}
static void r_bind_state(uint64_t *r, void *c) {   // the packed struct of a state object, by kind (the order of nvn_rec.py STATE_KINDS)
    static const uint8_t sizes[] = {8, 4, 4, 10, 4, 8};
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint32_t kind = ((Ctx *)c)->op, size = sizes[kind];
    const uint8_t *src = rd(r[1], size); if (!src) return;
    uint64_t *p = emit(s, OP_BIND_STATE, 4);
    p[0] = kind; p[1] = p[2] = 0; memcpy(&p[1], src, size);
}
static void r_array(uint64_t *r, void *c) {   // BindVertexAttribState (4 bytes each) / BindVertexStreamState (8 bytes each): the count and the raw entries
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint32_t k = r[1] & M32, unit = ((Ctx *)c)->op == OP_VATTR ? 4 : 8;
    const uint8_t *src = k > 0 && k <= 16 ? rd(r[2], (uint64_t)unit * k) : NULL;
    if (!src) { *emit(s, ((Ctx *)c)->op, 2) = 0; return; }
    uint32_t words = (unit * k + 7) / 8;
    uint64_t *p = emit(s, ((Ctx *)c)->op, 2 + words);
    p[0] = k; p[words] = 0; memcpy(&p[1], src, (size_t)unit * k);
}
static void r_draw(uint64_t *r, void *c) {   // payload: elements?, prim, first, count, index type, index buffer, base vertex, base instance, instances
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, OP_DRAW, 10);
    memset(p, 0, 9 * 8);
    p[1] = r[1] & M32;
    switch (((Ctx *)c)->op) {
    case 0: p[2] = (uint64_t)s32(r[2]); p[3] = (uint64_t)s32(r[3]); p[8] = 1; break;                                                  // DrawArrays
    case 1: p[2] = (uint64_t)s32(r[2]); p[3] = (uint64_t)s32(r[3]); p[7] = (uint64_t)s32(r[4]); p[8] = (uint64_t)s32(r[5]); break;   // DrawArraysInstanced
    case 2: p[0] = 1; p[4] = r[2] & M32; p[3] = (uint64_t)s32(r[3]); p[5] = r[4]; p[8] = 1; break;                                   // DrawElements
    case 3: p[0] = 1; p[4] = r[2] & M32; p[3] = (uint64_t)s32(r[3]); p[5] = r[4]; p[6] = (uint64_t)s32(r[5]); p[8] = 1; break;      // DrawElementsBaseVertex
    default: p[0] = 1; p[4] = r[2] & M32; p[3] = (uint64_t)s32(r[3]); p[5] = r[4]; p[6] = (uint64_t)s32(r[5]); p[7] = (uint64_t)s32(r[6]); p[8] = (uint64_t)s32(r[7]);   // DrawElementsInstanced
    }
}
static void r_targets(uint64_t *r, void *c) {   // SetRenderTargets (count, targets, views, depth target, depth view): count, depth target, depth view (has, 16 bytes), then per target: pointer, has view, view (16 bytes)
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint32_t k = r[1] & M32;
    const uint8_t *ptrs = k && k <= 8 ? rd(r[2], 8ull * k) : NULL;
    if (k && !ptrs) return;
    const uint8_t *vptrs = k && r[3] ? rd(r[3], 8ull * k) : NULL;
    uint64_t *p = emit(s, OP_TARGETS, 1 + 5 + 4 * k);
    const uint8_t *dv = r[5] ? rd(r[5], 16) : NULL;
    p[0] = k; p[1] = r[4]; p[2] = dv != NULL; p[3] = p[4] = 0;
    if (dv) memcpy(&p[3], dv, 16);
    for (uint32_t i = 0; i < k; i++) {
        uint64_t vp = 0, *q = p + 5 + 4 * i;
        memcpy(&q[0], ptrs + 8 * i, 8);
        if (vptrs) memcpy(&vp, vptrs + 8 * i, 8);
        const uint8_t *v = vp ? rd(vp, 16) : NULL;
        q[1] = v != NULL; q[2] = q[3] = 0;
        if (v) memcpy(&q[2], v, 16);
    }
}
static void r_stencil(uint64_t *r, void *c) {   // SetStencilRef / Mask / ValueMask: which, faces, value
    COUNT(c); Stream *s = stream_of(r[0]); if (!s) return;
    uint64_t *p = emit(s, OP_STENCIL, 4);
    p[0] = ((Ctx *)c)->op; p[1] = r[1] & 3; p[2] = r[2] & M32;
}

// ---- no-ops and trivial results ----
static void r_noop(uint64_t *r, void *c) { (void)r; COUNT(c); }
static void r_texture_handle(uint64_t *r, void *c) { COUNT(c); r[0] = (r[1] & M32) | (r[2] & M32) << 20; }
static void r_texel_handle(uint64_t *r, void *c) { COUNT(c); r[0] = r[1] & M32; }

// ---- state object setters: read, change and write the packed struct (the same arithmetic as nvn_state.py) ----
__extension__ typedef unsigned __int128 u128;
static u128 get_bits(u128 v, int off, int w) { return (v >> off) & (((u128)1 << w) - 1); }
static void put_bits(u128 *v, int off, int w, uint64_t x) { u128 m = (((u128)1 << w) - 1) << off; *v = (*v & ~m) | (((u128)x << off) & m); }
static uint64_t field_value(const Fld *f, uint64_t arg) {
    arg &= M32;
    switch (f->mode) {
    case F_BOOL: return (arg & 0xFF) != 0;
    case F_ADV_MODE: return arg <= 0x1F ? arg : 0;
    case F_NOT: return !(arg & 0xFF);
    default: return arg;
    }
}
static void r_set(uint64_t *r, void *c) {
    Ctx *x = c; const SetDesc *d = &x->set; COUNT(c);
    uint8_t *mem = g_guest ? g_guest(r[0], d->size) : NULL; if (!mem) return;
    u128 v = 0; memcpy(&v, mem, d->size);
    switch (d->special) {
    case SP_BLEND_ENABLE: {
        uint64_t i = r[1] & M32; u128 cur = get_bits(v, d->f[0].off, d->f[0].width);
        if (i < 8) { cur = (r[2] & 0xFF) ? cur | ((u128)1 << i) : cur & ~((u128)1 << i); put_bits(&v, d->f[0].off, d->f[0].width, (uint64_t)cur); }
        break;
    }
    case SP_CHMASK: {
        uint64_t i = r[1] & M32; uint64_t cur = (uint64_t)get_bits(v, d->f[0].off, d->f[0].width);
        if (i < 8) { cur = (cur & ~(0xFull << 4 * i)) | (((r[2] & 1) | (r[3] & 1) << 1 | (r[4] & 1) << 2 | (r[5] & 1) << 3) << 4 * i); put_bits(&v, d->f[0].off, d->f[0].width, cur); }
        break;
    }
    case SP_STENCIL_FUNC: {
        uint64_t faces = r[1] & M32;
        if (faces & 1) put_bits(&v, d->f[0].off, d->f[0].width, r[2] & M32);
        if (faces & 2) put_bits(&v, d->f[1].off, d->f[1].width, r[2] & M32);
        break;
    }
    case SP_STENCIL_OP: {
        uint64_t faces = r[1] & M32;
        for (int i = 0; i < 3; i++) {
            if (faces & 1) put_bits(&v, d->f[i].off, d->f[i].width, r[2 + i] & M32);
            if (faces & 2) put_bits(&v, d->f[3 + i].off, d->f[3 + i].width, r[2 + i] & M32);
        }
        break;
    }
    default:
        for (uint32_t i = 0; i < d->nf && i < 5; i++) put_bits(&v, d->f[i].off, d->f[i].width, field_value(&d->f[i], r[1 + i]));
    }
    memcpy(mem, &v, d->size);
}

// ---- the table the Python side asks: a name to a function and its context ----
typedef struct Native { char name[64]; Fn fn; Ctx ctx; } Native;
enum { MAX_NATIVES = 256 };
static Native g_nat[MAX_NATIVES];
static int g_nnat;

static Native *find(const char *name) {
    for (int i = 0; i < g_nnat; i++) if (!strcmp(g_nat[i].name, name)) return &g_nat[i];
    return NULL;
}
static Native *add(const char *name, Fn fn, uint32_t op) {
    Native *n = find(name);
    if (!n && g_nnat < MAX_NATIVES) { n = &g_nat[g_nnat++]; snprintf(n->name, sizeof n->name, "%s", name); }
    if (n) { n->fn = fn; n->ctx.op = op; }
    return n;
}

void nx2rec_init(GuestPtr guest) {
    g_guest = guest;
    if (g_nnat) return;
    static const char *const kinds[] = {"Blend", "Color", "ChannelMask", "Multisample", "Polygon", "DepthStencil"};
    for (uint32_t i = 0; i < 6; i++) { char n[96]; snprintf(n, sizeof n, "nvnCommandBufferBind%sState", kinds[i]); add(n, r_bind_state, i); }
    add("nvnCommandBufferBindUniformBuffer", r_ubo, OP_UBO); add("nvnCommandBufferBindStorageBuffer", r_ubo, OP_SSBO);
    add("nvnCommandBufferBindVertexBuffer", r_vbuf, 0); add("nvnCommandBufferBindTexture", r_tex, 0);
    add("nvnCommandBufferSetViewport", r_rect, OP_VIEWPORT); add("nvnCommandBufferSetScissor", r_rect, OP_SCISSOR);
    add("nvnCommandBufferBindProgram", r_program, 0);
    add("nvnCommandBufferBindVertexAttribState", r_array, OP_VATTR); add("nvnCommandBufferBindVertexStreamState", r_array, OP_VSTREAM);
    add("nvnCommandBufferDrawArrays", r_draw, 0); add("nvnCommandBufferDrawArraysInstanced", r_draw, 1);
    add("nvnCommandBufferDrawElements", r_draw, 2); add("nvnCommandBufferDrawElementsBaseVertex", r_draw, 3); add("nvnCommandBufferDrawElementsInstanced", r_draw, 4);
    add("nvnCommandBufferSetRenderTargets", r_targets, 0);
    add("nvnCommandBufferSetStencilRef", r_stencil, 0); add("nvnCommandBufferSetStencilMask", r_stencil, 1); add("nvnCommandBufferSetStencilValueMask", r_stencil, 2);
    add("nvnCommandBufferBarrier", r_noop, 0);
    add("nvnDeviceGetTextureHandle", r_texture_handle, 0); add("nvnDeviceGetTexelFetchHandle", r_texel_handle, 0);
}

// A state setter is defined by Python from nvn_state.py (the layout is theirs), then asked for like any other native
int nx2rec_define_setter(const char *name, const SetDesc *d) {
    Native *n = add(name, r_set, 0);
    if (!n) return 1;
    n->ctx.set = *d;
    return 0;
}

// 0 and the function and context to register, or 1 when there is no native of that name
int nx2rec_native(const char *name, Fn *fn, void **ctx) {
    Native *n = find(name);
    if (!n) return 1;
    *fn = n->fn; *ctx = &n->ctx;
    return 0;
}

uint64_t nx2rec_count(const char *name) { Native *n = find(name); return n ? n->ctx.count : 0; }
int nx2rec_names(char *out, uint64_t cap) {   // the names of every native, newline separated
    uint64_t o = 0;
    for (int i = 0; i < g_nnat; i++) { size_t l = strlen(g_nat[i].name); if (o + l + 1 >= cap) break; memcpy(out + o, g_nat[i].name, l); o += l; out[o++] = '\n'; }
    if (o < cap) out[o] = 0;
    return (int)o;
}

uint32_t nx2rec_len(uint64_t cb) {
    Stream *s = stream_of(cb);
    return s ? s->len : 0;
}
// Copies the stream out (the caller sized the buffer with nx2rec_len) and empties it; nx2rec_reset empties it
uint32_t nx2rec_take(uint64_t cb, uint8_t *out, uint32_t cap) {
    Stream *s = stream_of(cb);
    if (!s || s->len > cap) return 0;
    uint32_t n = s->len; memcpy(out, s->buf, n); s->len = 0;
    return n;
}
void nx2rec_reset(uint64_t cb) { Stream *s = stream_of(cb); if (s) s->len = 0; }

#include "nvnreplay.inc"
