/* The decoders behind movie::Decoder on the Linux host (NX2Linux S7bis, milestone E): VP8 / VP9 pictures through libvpx (loaded at run time: libvpx.so.9, no build-time dependency) as NV12, and Vorbis packets through stb_vorbis as
 * PCM 16-bit. horizon_movie.py keeps the Decoder protocol (inputs, outputs, events); this file does the work that loops over pixels and samples. The conversions and the Ogg framing are those of the Mach host's hle_movie.c. */
#include <dlfcn.h>
#include <stdarg.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if __has_include("vpx/vpx_decoder.h")
#include "vpx/vpx_decoder.h"
#include "vpx/vp8dx.h"
#define HAVE_VPX_HEADERS 1
#endif
#define STB_VORBIS_NO_STDIO
#define STB_VORBIS_NO_PUSHDATA_API_UNUSED
#include "stb_vorbis.c"

static char g_err[256];
const char *nx2mv_error(void) { return g_err; }

#ifdef HAVE_VPX_HEADERS
static struct {
    void *so; int tried;
    vpx_codec_err_t (*init)(vpx_codec_ctx_t *, vpx_codec_iface_t *, const vpx_codec_dec_cfg_t *, vpx_codec_flags_t, int);
    vpx_codec_err_t (*decode)(vpx_codec_ctx_t *, const uint8_t *, unsigned, void *, long);
    vpx_image_t *(*get_frame)(vpx_codec_ctx_t *, vpx_codec_iter_t *);
    vpx_codec_err_t (*destroy)(vpx_codec_ctx_t *);
    vpx_codec_err_t (*control)(vpx_codec_ctx_t *, int, ...);
    vpx_codec_iface_t *(*vp8)(void); vpx_codec_iface_t *(*vp9)(void);
    const char *(*errstr)(vpx_codec_err_t); const char *(*version)(void);
} V;

static int vpx_load(void)
{
    if (V.tried) return V.so != NULL;
    V.tried = 1;
    V.so = dlopen("libvpx.so.9", RTLD_NOW);
    if (!V.so) V.so = dlopen("libvpx.so", RTLD_NOW);
    if (!V.so) { snprintf(g_err, sizeof g_err, "libvpx is not installed (libvpx.so.9): %s", dlerror()); return 0; }
    V.init = dlsym(V.so, "vpx_codec_dec_init_ver"); V.decode = dlsym(V.so, "vpx_codec_decode"); V.get_frame = dlsym(V.so, "vpx_codec_get_frame");
    V.destroy = dlsym(V.so, "vpx_codec_destroy"); V.control = (vpx_codec_err_t (*)(vpx_codec_ctx_t *, int, ...))dlsym(V.so, "vpx_codec_control_"); V.vp8 = dlsym(V.so, "vpx_codec_vp8_dx"); V.vp9 = dlsym(V.so, "vpx_codec_vp9_dx");
    V.errstr = dlsym(V.so, "vpx_codec_err_to_string"); V.version = dlsym(V.so, "vpx_codec_version_str");
    if (!V.init || !V.decode || !V.get_frame || !V.destroy || !V.vp8 || !V.vp9) { snprintf(g_err, sizeof g_err, "libvpx lacks a decoder entry point"); V.so = NULL; return 0; }
    return 1;
}

/* the headers' typed control wrappers call vpx_codec_control_: the library is loaded at run time, so the call is forwarded to the one the library has */
vpx_codec_err_t vpx_codec_control_(vpx_codec_ctx_t *ctx, int ctrl_id, ...)
{
    va_list ap; va_start(ap, ctrl_id); uintptr_t arg = va_arg(ap, uintptr_t); va_end(ap);
    return V.control ? V.control(ctx, ctrl_id, arg) : VPX_CODEC_ERROR;
}

typedef struct { vpx_codec_ctx_t ctx; int vp9; } Vpx;

const char *nx2mv_vpx_version(void) { return vpx_load() && V.version ? V.version() : ""; }

void *nx2mv_vpx_open(int vp9, int threads, int w, int h)
{
    if (!vpx_load()) return NULL;
    Vpx *d = calloc(1, sizeof *d); if (!d) return NULL;
    vpx_codec_dec_cfg_t cfg = { .threads = (unsigned)threads, .w = (unsigned)w, .h = (unsigned)h };
    vpx_codec_err_t e = V.init(&d->ctx, vp9 ? V.vp9() : V.vp8(), &cfg, 0, VPX_DECODER_ABI_VERSION);
    if (e) { snprintf(g_err, sizeof g_err, "libvpx cannot create the %s decoder: %s", vp9 ? "VP9" : "VP8", V.errstr ? V.errstr(e) : "error"); free(d); return NULL; }
    if (vp9) vpx_codec_control(&d->ctx, VP9D_SET_ROW_MT, 1);
    d->vp9 = vp9;
    return d;
}

void nx2mv_vpx_close(void *h) { Vpx *d = h; if (d) { V.destroy(&d->ctx); free(d); } }

/* I420 into NV12 of w x h (stride = width, plane height = height): a smaller picture is padded with black, a larger one cropped */
static void nv12(const vpx_image_t *im, uint8_t *dst, int w, int h)
{
    int cw = (int)im->d_w < w ? (int)im->d_w : w, ch = (int)im->d_h < h ? (int)im->d_h : h;
    if (cw < w || ch < h) { memset(dst, 16, (size_t)w * h); memset(dst + (size_t)w * h, 128, (size_t)w * h / 2); }
    for (int y = 0; y < ch; y++) memcpy(dst + (size_t)y * w, im->planes[0] + (size_t)y * im->stride[0], (size_t)cw);
    int cw2 = (cw + 1) / 2, ch2 = (ch + 1) / 2;
    for (int y = 0; y < ch2 && y < h / 2; y++) {
        const uint8_t *u = im->planes[1] + (size_t)y * im->stride[1], *v = im->planes[2] + (size_t)y * im->stride[2];
        uint8_t *o = dst + (size_t)w * h + (size_t)y * w;
        for (int x = 0; x < cw2 && 2 * x + 1 < w; x++) { o[2 * x] = u[x]; o[2 * x + 1] = v[x]; }
    }
}

/* One packet. 1: a picture came out and is in nv12 (w x h, 1.5 bytes a pixel); 0: none (a hidden frame); -1: error (nx2mv_error); -2: a picture that is not 8-bit 4:2:0 */
int nx2mv_vpx_decode(void *h, const uint8_t *p, int n, uint8_t *out, int w, int hh, int *dw, int *dh)
{
    Vpx *d = h;
    if (V.decode(&d->ctx, p, (unsigned)n, NULL, 0)) {
        const char *detail = d->ctx.err_detail ? d->ctx.err_detail : "";
        snprintf(g_err, sizeof g_err, "%s decode error (%d bytes): %s", d->vp9 ? "VP9" : "VP8", n, detail); return -1;
    }
    vpx_codec_iter_t it = NULL; vpx_image_t *im, *last = NULL;
    while ((im = V.get_frame(&d->ctx, &it))) last = im;
    if (!last) return 0;
    if (dw) *dw = (int)last->d_w;
    if (dh) *dh = (int)last->d_h;
    if (last->fmt != VPX_IMG_FMT_I420) { snprintf(g_err, sizeof g_err, "picture format %#x is not 8-bit 4:2:0", last->fmt); return -2; }
    nv12(last, out, w, hh);
    return 1;
}
#else
const char *nx2mv_vpx_version(void) { return ""; }
void *nx2mv_vpx_open(int vp9, int threads, int w, int h) { (void)vp9; (void)threads; (void)w; (void)h; snprintf(g_err, sizeof g_err, "built without the libvpx headers (engine/dm/third_party/libvpx)"); return NULL; }
void nx2mv_vpx_close(void *h) { (void)h; }
int nx2mv_vpx_decode(void *h, const uint8_t *p, int n, uint8_t *out, int w, int hh, int *dw, int *dh) { (void)h; (void)p; (void)n; (void)out; (void)w; (void)hh; (void)dw; (void)dh; return -1; }
#endif

/* ---- Vorbis ---- the demuxer hands over raw packets; stb_vorbis reads Ogg pages, so each packet goes in as a page of its own: the identification and setup headers first (with a made-up comment header: stb_vorbis only
 * checks its type), then one page per audio packet, with the page checksum filled in (stb_vorbis checks it while it resynchronises) */
static uint32_t ogg_crc(const uint8_t *p, size_t n)
{
    static uint32_t t[256]; static int init;
    if (!init) { for (uint32_t i = 0; i < 256; i++) { uint32_t r = i << 24; for (int k = 0; k < 8; k++) r = r & 0x80000000u ? r << 1 ^ 0x04c11db7u : r << 1; t[i] = r; } init = 1; }
    uint32_t c = 0; for (size_t i = 0; i < n; i++) c = c << 8 ^ t[(c >> 24 ^ p[i]) & 0xff];
    return c;
}

typedef struct { stb_vorbis *vb; uint32_t seq; int64_t gran; int ch, rate; } Vorbis;

static size_t ogg_page(Vorbis *d, uint8_t *out, const uint8_t *pkt, int n, int bos)   /* out needs n + 27 + 255 bytes; n < 65025 */
{
    int seg = n / 255 + 1; uint8_t *h = out;
    memcpy(h, "OggS", 4); h[4] = 0; h[5] = bos ? 2 : 0;
    int64_t g = d->gran; memcpy(h + 6, &g, 8); uint32_t sn = 0x444d5642u; memcpy(h + 14, &sn, 4);
    uint32_t q = d->seq++; memcpy(h + 18, &q, 4); memset(h + 22, 0, 4); h[26] = (uint8_t)seg;
    for (int i = 0; i < seg - 1; i++) h[27 + i] = 255;
    h[27 + seg - 1] = (uint8_t)(n % 255);
    memcpy(h + 27 + seg, pkt, (size_t)n);
    size_t len = 27 + (size_t)seg + (size_t)n; uint32_t c = ogg_crc(out, len); memcpy(h + 22, &c, 4);
    return len;
}

void *nx2mv_vorbis_open(const uint8_t *h0, int n0, const uint8_t *h1, int n1, int *channels, int *rate)
{
    static const uint8_t comment[] = { 3, 'v', 'o', 'r', 'b', 'i', 's', 0, 0, 0, 0, 0, 0, 0, 0, 1 };
    if (!h0 || !h1 || n0 <= 0 || n1 <= 0 || n0 >= 65025 || n1 >= 65025) { snprintf(g_err, sizeof g_err, "the Vorbis decoder got no headers (csd-0 / csd-1)"); return NULL; }
    Vorbis *d = calloc(1, sizeof *d); if (!d) return NULL;
    size_t cap = (size_t)n0 + n1 + sizeof comment + 3 * (27 + 255); uint8_t *buf = malloc(cap);
    if (!buf) { free(d); return NULL; }
    size_t n = ogg_page(d, buf, h0, n0, 1); n += ogg_page(d, buf + n, comment, sizeof comment, 0); n += ogg_page(d, buf + n, h1, n1, 0);
    int used = 0, err = 0; d->vb = stb_vorbis_open_pushdata(buf, (int)n, &used, &err, NULL); free(buf);
    if (!d->vb) { snprintf(g_err, sizeof g_err, "the Vorbis headers cannot be decoded (stb_vorbis error %d)", err); free(d); return NULL; }
    stb_vorbis_info in = stb_vorbis_get_info(d->vb); d->ch = in.channels; d->rate = (int)in.sample_rate;
    if (channels) *channels = d->ch;
    if (rate) *rate = d->rate;
    d->gran = 0;
    return d;
}

/* One packet to PCM 16-bit interleaved in out (cap_bytes). Returns the bytes written: 0 for none (the first packet of a stream only primes the overlap), -1 on an error, -2 when out is too small */
int nx2mv_vorbis_packet(void *h, const uint8_t *p, int n, int16_t *out, int cap_bytes)
{
    Vorbis *d = h;
    if (n >= 65025 || n <= 0) { snprintf(g_err, sizeof g_err, "Vorbis packet of %d bytes", n); return -1; }
    uint8_t *page = malloc((size_t)n + 27 + 255); if (!page) return -1;
    size_t len = ogg_page(d, page, p, n, 0);
    int ch = 0, ns = 0; float **pcm = NULL;
    stb_vorbis_decode_frame_pushdata(d->vb, page, (int)len, &ch, &pcm, &ns); free(page);
    if (ns <= 0 || ch <= 0 || !pcm) return 0;
    d->gran += ns;
    int need = ns * ch * 2;
    if (need > cap_bytes) return -2;
    for (int i = 0; i < ns; i++) for (int c = 0; c < ch; c++) { float v = pcm[c][i] * 32767.0f; out[i * ch + c] = (int16_t)(v > 32767.0f ? 32767 : v < -32768.0f ? -32768 : v); }
    return need;
}

void nx2mv_vorbis_close(void *h) { Vorbis *d = h; if (d) { stb_vorbis_close(d->vb); free(d); } }
