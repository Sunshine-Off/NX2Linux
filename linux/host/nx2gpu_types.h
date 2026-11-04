/* The structures of the GPU library's draw and dispatch calls (gpu.c, nvnrec.c): what horizon_gpu.py mirrors with ctypes, field by field. */
#pragma once
#include <stdint.h>

#define MAX_TARGETS 8

typedef struct { uint32_t binding, stride, divisor, pad; const void *data; uint64_t size; } Nx2Stream;
typedef struct { uint32_t location, binding, format, offset; } Nx2Attr;
typedef struct { uint32_t enable, src_rgb, dst_rgb, op_rgb, src_a, dst_a, op_a, mask; } Nx2Blend;
typedef struct { uint32_t binding, kind, pad0, pad1; const void *data; uint64_t size; uint64_t ring_offset; /* out: where the data sits in the ring until the next flush */ } Nx2Buf;   /* kind 0: uniform, 1: storage */
typedef struct { uint32_t binding, image, view_type, level, base_layer, nlayers, pad0, pad1; } Nx2Img;   /* a storage image */
typedef struct {
    uint32_t binding, image, min_filter, mag_filter, mip_mode, wrap_s, wrap_t, wrap_r, compare_enable, compare_op;
    float lod_min, lod_max, lod_bias, aniso;
    uint32_t view_type /* VkImageViewType */, base_level, nlevels, base_layer, nlayers, border /* VkBorderColor */;
    uint32_t swz[4];   /* VkComponentSwizzle of r, g, b, a (0 identity) */
    uint32_t aspect, pad_a;   /* aspect 1: the stencil of a depth-stencil image (NVN's STENCIL depth-stencil mode), read as raw bits in x */
} Nx2Tex;
typedef struct { uint32_t image, layer, level, pad; } Nx2Target;   /* layer: the array layer, cube face or 3D slice drawn into */
typedef struct {
    uint32_t vs, ps, tcs, tes, gs, patch_points;
    uint32_t ntargets; Nx2Target targets[MAX_TARGETS]; Nx2Target depth;
    uint32_t nattrs; Nx2Attr attrs[16];
    uint32_t nstreams; Nx2Stream streams[16];
    uint32_t topology, index_type /* 0 none, 1 u16, 2 u32 */, restart_enable /* NVN primitive restart */, restart_index /* the index that ends a strip */; const void *index_data; uint64_t index_size;
    int32_t first, count, base_vertex, base_instance; uint32_t instances, rasterizer_discard;
    Nx2Blend blend[MAX_TARGETS];
    uint32_t cull, front_face, polygon_mode, depth_test, depth_write, depth_func, depth_clamp, depth_neg_one /* clip z in -w..w (NVN near = -w) */;
    uint32_t stencil_test, depth_bias_on, pad4, pad5;
    uint32_t stencil_front[4], stencil_back[4];   /* VkStencilOp fail, depth fail, pass, VkCompareOp */
    uint32_t stencil_masks[6];                    /* front: compare mask, write mask, reference; back: the same */
    float blend_color[4], depth_bias[3], line_width /* 0: 1.0 */;    /* depth_bias: constant factor (units), clamp, slope factor */
    float viewport[4], depth_range[2]; int32_t scissor[4];
    uint32_t nbufs; Nx2Buf bufs[32];
    uint32_t ntex; Nx2Tex tex[32];
    uint32_t nimgs, pad7; Nx2Img imgs[8];
} Nx2Draw;

typedef struct {
    uint32_t cs, groups[3];
    uint32_t nbufs; Nx2Buf bufs[32];
    uint32_t ntex; Nx2Tex tex[32];
    uint32_t nimgs, pad; Nx2Img imgs[8];
} Nx2Dispatch;
