/* The GPU the NVN layer draws with (NX2Linux S7bis): a headless Vulkan device that renders the draws of a recompiled game into images, for Python (ctypes: engine/dm/tools/horizon_gpu.py).
 * Images are created once per NVN texture and stay in VK_IMAGE_LAYOUT_GENERAL (render target, sampled and transfer use need no layout changes). Commands are recorded into one command buffer
 * and submitted by nx2gpu_flush (a readback, a present). Data the draw reads (vertex streams, indices, uniform buffers) is copied into a host-visible ring when the draw is recorded, so the
 * game may overwrite its memory right after. Resources go through push descriptors: set 0 with the bindings the translated shader declares (Ryujinx.Graphics.Shader: binding 0 the
 * support buffer, then constant buffers, storage buffers and textures as dmsh_xlate numbers them).
 * Threads: every entry point takes one lock. */
#include <vulkan/vulkan.h>

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_IMAGES 8192
#define MAX_SHADERS 16384
#define MAX_TARGETS 8
#define RING_BYTES (192u << 20)
#define STAGING_BYTES (96u << 20)
#define QUADS 100   /* the NVN quad primitive: Vulkan has none, drawn as two triangles through a generated index buffer */

typedef struct { uint32_t binding, stride, divisor, pad; const void *data; uint64_t size; } Nx2Stream;
typedef struct { uint32_t location, binding, format, offset; } Nx2Attr;
typedef struct { uint32_t enable, src_rgb, dst_rgb, op_rgb, src_a, dst_a, op_a, mask; } Nx2Blend;
typedef struct { uint32_t binding, kind, pad0, pad1; const void *data; uint64_t size; } Nx2Buf;   /* kind 0: uniform, 1: storage */
typedef struct {
    uint32_t binding, image, min_filter, mag_filter, mip_mode, wrap_s, wrap_t, wrap_r, compare_enable, compare_op;
    float lod_min, lod_max, lod_bias, aniso;
    uint32_t view_type /* VkImageViewType */, base_level, nlevels, base_layer, nlayers, border /* VkBorderColor */, alpha_one /* RGBX formats: alpha reads 1 */, pad;
} Nx2Tex;
typedef struct { uint32_t image, layer, level, pad; } Nx2Target;   /* layer: the array layer, cube face or 3D slice drawn into */
typedef struct {
    uint32_t vs, ps;
    uint32_t ntargets; Nx2Target targets[MAX_TARGETS]; Nx2Target depth;
    uint32_t nattrs; Nx2Attr attrs[16];
    uint32_t nstreams; Nx2Stream streams[16];
    uint32_t topology, index_type /* 0 none, 1 u16, 2 u32 */, pad0, pad1; const void *index_data; uint64_t index_size;
    int32_t first, count, base_vertex, base_instance; uint32_t instances, pad2;
    Nx2Blend blend[MAX_TARGETS];
    uint32_t cull, front_face, polygon_mode, depth_test, depth_write, depth_func, depth_clamp, depth_neg_one /* clip z in -w..w (NVN near = -w) */;
    uint32_t stencil_test, depth_bias_on, pad4, pad5;
    uint32_t stencil_front[4], stencil_back[4];   /* VkStencilOp fail, depth fail, pass, VkCompareOp */
    uint32_t stencil_masks[6];                    /* front: compare mask, write mask, reference; back: the same */
    float blend_color[4], depth_bias[3], pad6;    /* depth_bias: constant factor (units), clamp, slope factor */
    float viewport[4], depth_range[2]; int32_t scissor[4];
    uint32_t nbufs; Nx2Buf bufs[32];
    uint32_t ntex; Nx2Tex tex[32];
} Nx2Draw;

typedef struct { uint64_t key; VkImageView v; } View;
typedef struct {
    VkImage image; VkDeviceMemory mem; uint32_t w, h, d, layers, levels; VkFormat format; VkImageAspectFlags aspect; int is3d, live;
    View *views; uint32_t nviews, cviews;
} Image;
typedef struct { VkShaderModule mod; int live; } Shader;
typedef struct { uint64_t key; VkPipeline pipe; VkPipelineLayout layout; VkDescriptorSetLayout set_layout; } Pipe;
typedef struct { uint64_t key; VkSampler s; } Samp;

static struct {
    int open, clip_control;
    pthread_mutex_t mu;
    VkInstance inst; VkPhysicalDevice phys; VkDevice dev; VkQueue queue; uint32_t family;
    VkPhysicalDeviceMemoryProperties mem;
    char name[256], err[512];
    VkCommandPool pool; VkCommandBuffer cb; VkFence fence; int recording, rendering;
    VkBuffer ring; VkDeviceMemory ring_mem; uint8_t *ring_ptr; uint64_t ring_off; VkDeviceSize ubo_align;
    VkBuffer stage; VkDeviceMemory stage_mem; uint8_t *stage_ptr;
    Image *images; Shader *shaders; uint32_t nimages, nshaders;
    Pipe *pipes; uint32_t npipes, cpipes; Samp *samps; uint32_t nsamps, csamps;
    uint32_t rt[MAX_TARGETS + 1], nrt, rt_depth; uint64_t rt_hash;
    PFN_vkCmdPushDescriptorSetKHR push;
    PFN_vkCmdBeginRendering begin_rendering; PFN_vkCmdEndRendering end_rendering;
    uint64_t draws, flushes, skipped;
} G = { .mu = PTHREAD_MUTEX_INITIALIZER };

const char *nx2gpu_error(void) { return G.err; }
const char *nx2gpu_name(void) { return G.name; }
static int fail(const char *what, VkResult r) { snprintf(G.err, sizeof G.err, "%s: VkResult %d", what, (int)r); return -1; }
#define VK(call) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) return fail(#call, r_); } while (0)
#define VK0(call) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { fail(#call, r_); return 0; } } while (0)

static uint64_t fnv(uint64_t h, const void *p, size_t n) { const uint8_t *b = p; while (n--) { h ^= *b++; h *= 1099511628211ull; } return h; }

static int find_mem(uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t i = 0; i < G.mem.memoryTypeCount; i++)
        if ((bits >> i & 1) && (G.mem.memoryTypes[i].propertyFlags & want) == want) return (int)i;
    return -1;
}

static int make_buffer(VkDeviceSize size, VkBufferUsageFlags usage, VkBuffer *b, VkDeviceMemory *m, uint8_t **ptr) {
    VkBufferCreateInfo bi = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size, .usage = usage, .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    VK(vkCreateBuffer(G.dev, &bi, NULL, b));
    VkMemoryRequirements rq; vkGetBufferMemoryRequirements(G.dev, *b, &rq);
    int t = find_mem(rq.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (t < 0) { snprintf(G.err, sizeof G.err, "no host-visible memory"); return -1; }
    VkMemoryAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = rq.size, .memoryTypeIndex = (uint32_t)t };
    VK(vkAllocateMemory(G.dev, &ai, NULL, m));
    VK(vkBindBufferMemory(G.dev, *b, *m, 0));
    VK(vkMapMemory(G.dev, *m, 0, VK_WHOLE_SIZE, 0, (void **)ptr));
    return 0;
}

int nx2gpu_open(int prefer_software)
{
    if (G.open) return 0;
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "NX2Linux", .apiVersion = VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
    VK(vkCreateInstance(&ici, NULL, &G.inst));
    uint32_t n = 0; vkEnumeratePhysicalDevices(G.inst, &n, NULL);
    VkPhysicalDevice devs[16]; if (n > 16) n = 16;
    vkEnumeratePhysicalDevices(G.inst, &n, devs);
    int best = -1, score = -1;
    for (uint32_t i = 0; i < n; i++) {
        VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(devs[i], &p);
        if (p.apiVersion < VK_API_VERSION_1_3) continue;
        int s = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 3 : p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ? 2 : p.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU ? 1 : 0;
        if (prefer_software) s = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU ? 9 : s;
        if (s > score) { score = s; best = (int)i; }
    }
    if (best < 0) { snprintf(G.err, sizeof G.err, "no Vulkan 1.3 device"); return -1; }
    G.phys = devs[best];
    VkPhysicalDeviceProperties props; vkGetPhysicalDeviceProperties(G.phys, &props);
    snprintf(G.name, sizeof G.name, "%s", props.deviceName);
    G.ubo_align = props.limits.minUniformBufferOffsetAlignment > 256 ? props.limits.minUniformBufferOffsetAlignment : 256;
    vkGetPhysicalDeviceMemoryProperties(G.phys, &G.mem);
    uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(G.phys, &nq, NULL);
    VkQueueFamilyProperties qf[32]; if (nq > 32) nq = 32;
    vkGetPhysicalDeviceQueueFamilyProperties(G.phys, &nq, qf);
    G.family = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { G.family = i; break; }
    if (G.family == UINT32_MAX) { snprintf(G.err, sizeof G.err, "no graphics queue"); return -1; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qi = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = G.family, .queueCount = 1, .pQueuePriorities = &prio };
    VkPhysicalDeviceVulkan13Features f13 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .dynamicRendering = VK_TRUE, .synchronization2 = VK_TRUE };
    VkPhysicalDeviceFeatures2 f2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f13 };
    VkPhysicalDeviceFeatures avail; vkGetPhysicalDeviceFeatures(G.phys, &avail);
    f2.features.samplerAnisotropy = avail.samplerAnisotropy; f2.features.shaderFloat64 = avail.shaderFloat64; f2.features.shaderInt64 = avail.shaderInt64;
    f2.features.fragmentStoresAndAtomics = avail.fragmentStoresAndAtomics; f2.features.vertexPipelineStoresAndAtomics = avail.vertexPipelineStoresAndAtomics;
    f2.features.depthClamp = avail.depthClamp; f2.features.independentBlend = avail.independentBlend; f2.features.fillModeNonSolid = avail.fillModeNonSolid;
    f2.features.shaderImageGatherExtended = avail.shaderImageGatherExtended; f2.features.shaderStorageImageExtendedFormats = avail.shaderStorageImageExtendedFormats;
    f2.features.textureCompressionBC = avail.textureCompressionBC; f2.features.shaderClipDistance = avail.shaderClipDistance; f2.features.shaderCullDistance = avail.shaderCullDistance;
    const char *exts[2] = { VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME }; uint32_t next = 1;
    uint32_t nde = 0; vkEnumerateDeviceExtensionProperties(G.phys, NULL, &nde, NULL);
    VkExtensionProperties *dep = calloc(nde ? nde : 1, sizeof *dep); vkEnumerateDeviceExtensionProperties(G.phys, NULL, &nde, dep);
    for (uint32_t i = 0; i < nde; i++) if (!strcmp(dep[i].extensionName, VK_EXT_DEPTH_CLIP_CONTROL_EXTENSION_NAME)) G.clip_control = 1;
    free(dep);
    VkPhysicalDeviceDepthClipControlFeaturesEXT clip = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DEPTH_CLIP_CONTROL_FEATURES_EXT, .depthClipControl = VK_TRUE };
    if (G.clip_control) { exts[next++] = VK_EXT_DEPTH_CLIP_CONTROL_EXTENSION_NAME; f13.pNext = &clip; }
    VkDeviceCreateInfo di = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &f2, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi, .enabledExtensionCount = next, .ppEnabledExtensionNames = exts };
    VK(vkCreateDevice(G.phys, &di, NULL, &G.dev));
    vkGetDeviceQueue(G.dev, G.family, 0, &G.queue);
    G.push = (PFN_vkCmdPushDescriptorSetKHR)vkGetDeviceProcAddr(G.dev, "vkCmdPushDescriptorSetKHR");
    G.begin_rendering = (PFN_vkCmdBeginRendering)vkGetDeviceProcAddr(G.dev, "vkCmdBeginRendering");
    G.end_rendering = (PFN_vkCmdEndRendering)vkGetDeviceProcAddr(G.dev, "vkCmdEndRendering");
    VkCommandPoolCreateInfo pi = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = G.family };
    VK(vkCreateCommandPool(G.dev, &pi, NULL, &G.pool));
    VkCommandBufferAllocateInfo ci = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = G.pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
    VK(vkAllocateCommandBuffers(G.dev, &ci, &G.cb));
    VkFenceCreateInfo fi = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK(vkCreateFence(G.dev, &fi, NULL, &G.fence));
    if (make_buffer(RING_BYTES, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT, &G.ring, &G.ring_mem, &G.ring_ptr)) return -1;
    if (make_buffer(STAGING_BYTES, VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT, &G.stage, &G.stage_mem, &G.stage_ptr)) return -1;
    G.images = calloc(MAX_IMAGES, sizeof(Image)); G.shaders = calloc(MAX_SHADERS, sizeof(Shader));
    G.nimages = G.nshaders = 1;   /* id 0 is "none" */
    G.open = 1;
    return 0;
}

static int begin(void)
{
    if (G.recording) return 0;
    VkCommandBufferBeginInfo bi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
    VK(vkBeginCommandBuffer(G.cb, &bi));
    G.recording = 1;
    return 0;
}

static void barrier_all(void)
{
    VkMemoryBarrier2 mb = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2, .srcStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, .srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT,
                            .dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, .dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT };
    VkDependencyInfo dep = { .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .memoryBarrierCount = 1, .pMemoryBarriers = &mb };
    vkCmdPipelineBarrier2(G.cb, &dep);
}

static void end_rendering(void)
{
    if (!G.rendering) return;
    G.end_rendering(G.cb); G.rendering = 0; G.rt_hash = 0;
    barrier_all();
}

static int flush_locked(void)
{
    if (!G.recording) { G.ring_off = 0; return 0; }
    end_rendering();
    VK(vkEndCommandBuffer(G.cb));
    VkCommandBufferSubmitInfo cbi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO, .commandBuffer = G.cb };
    VkSubmitInfo2 si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2, .commandBufferInfoCount = 1, .pCommandBufferInfos = &cbi };
    VK(vkQueueSubmit2(G.queue, 1, &si, G.fence));
    VkResult w = vkWaitForFences(G.dev, 1, &G.fence, VK_TRUE, 8000000000ull);
    if (w != VK_SUCCESS) { G.open = 0; return fail(w == VK_TIMEOUT ? "the GPU did not finish in 8 s (hang), device dropped" : "vkWaitForFences", w); }
    VK(vkResetFences(G.dev, 1, &G.fence));
    VK(vkResetCommandBuffer(G.cb, 0));
    G.recording = 0; G.ring_off = 0; G.flushes++;
    return 0;
}

int nx2gpu_flush(void)
{
    pthread_mutex_lock(&G.mu); int r = G.open ? flush_locked() : -1; pthread_mutex_unlock(&G.mu); return r;
}

uint32_t nx2gpu_shader(const void *spv, uint32_t bytes)
{
    pthread_mutex_lock(&G.mu);
    uint32_t id = 0;
    if (G.open && G.nshaders < MAX_SHADERS) {
        VkShaderModuleCreateInfo si = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = bytes, .pCode = spv };
        VkShaderModule m;
        VkResult r = vkCreateShaderModule(G.dev, &si, NULL, &m);
        if (r == VK_SUCCESS) { id = G.nshaders++; G.shaders[id].mod = m; G.shaders[id].live = 1; } else fail("vkCreateShaderModule", r);
    }
    pthread_mutex_unlock(&G.mu);
    return id;
}

static int is_depth_format(VkFormat f) { return f == VK_FORMAT_D16_UNORM || f == VK_FORMAT_D32_SFLOAT || f == VK_FORMAT_D32_SFLOAT_S8_UINT || f == VK_FORMAT_D24_UNORM_S8_UINT; }
static int is_compressed(VkFormat f) { return f >= VK_FORMAT_BC1_RGB_UNORM_BLOCK && f <= VK_FORMAT_BC7_SRGB_BLOCK; }

static VkImageAspectFlags aspect_of(VkFormat f)
{
    if (!is_depth_format(f)) return VK_IMAGE_ASPECT_COLOR_BIT;
    return f == VK_FORMAT_D32_SFLOAT || f == VK_FORMAT_D16_UNORM ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
}

/* kind: 0 a 2D image (layers: array layers, a multiple of 6 may be sampled as a cube), 1 a 3D image (depth slices) */
uint32_t nx2gpu_image(uint32_t w, uint32_t h, uint32_t depth, uint32_t layers, uint32_t levels, uint32_t format, int kind)
{
    pthread_mutex_lock(&G.mu);
    uint32_t id = 0;
    if (!G.open || G.nimages >= MAX_IMAGES) goto out;
    Image *im = &G.images[G.nimages];
    memset(im, 0, sizeof *im);
    im->w = w; im->h = h; im->d = kind ? depth : 1; im->layers = kind ? 1 : (layers ? layers : 1); im->levels = levels ? levels : 1; im->format = (VkFormat)format; im->is3d = kind == 1;
    im->aspect = aspect_of(im->format);
    int depth_fmt = is_depth_format(im->format);
    VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (depth_fmt) usage |= VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT; else if (!is_compressed(im->format)) usage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    VkImageCreateFlags fl = 0;
    if (!kind && im->layers >= 6 && w == h) fl |= VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
    if (kind) fl |= VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT;
    VkImageCreateInfo ii = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .flags = fl, .imageType = kind ? VK_IMAGE_TYPE_3D : VK_IMAGE_TYPE_2D, .format = im->format, .extent = { w, h, im->d },
                             .mipLevels = im->levels, .arrayLayers = im->layers, .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = usage,
                             .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED };
    VkResult r = vkCreateImage(G.dev, &ii, NULL, &im->image);
    if (r != VK_SUCCESS) { fail("vkCreateImage", r); goto out; }
    VkMemoryRequirements rq; vkGetImageMemoryRequirements(G.dev, im->image, &rq);
    int t = find_mem(rq.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (t < 0) t = find_mem(rq.memoryTypeBits, 0);
    VkMemoryAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = rq.size, .memoryTypeIndex = (uint32_t)t };
    if ((r = vkAllocateMemory(G.dev, &ai, NULL, &im->mem)) != VK_SUCCESS || (r = vkBindImageMemory(G.dev, im->image, im->mem, 0)) != VK_SUCCESS) { fail("image memory", r); goto out; }
    if (begin()) goto out;
    end_rendering();
    VkImageMemoryBarrier2 b = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2, .srcStageMask = VK_PIPELINE_STAGE_2_NONE, .dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                .dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_GENERAL,
                                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = im->image,
                                .subresourceRange = { .aspectMask = im->aspect, .levelCount = VK_REMAINING_MIP_LEVELS, .layerCount = VK_REMAINING_ARRAY_LAYERS } };
    VkDependencyInfo dep = { .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &b };
    vkCmdPipelineBarrier2(G.cb, &dep);
    im->live = 1; id = G.nimages++;
out:
    pthread_mutex_unlock(&G.mu);
    return id;
}

/* The view of an image the shader or the render pass asks for, made once. aspect: depth only for a depth-stencil image (shaders sample the depth) */
/* mode: bit 0 the alpha channel reads 1, bit 1 a render attachment (a depth-stencil image then keeps its stencil aspect) */
static VkImageView view_of(Image *im, VkImageViewType type, uint32_t level, uint32_t nlevels, uint32_t layer, uint32_t nlayers, int mode)
{
    if (!nlevels || level + nlevels > im->levels) { if (level >= im->levels) level = 0; nlevels = im->levels - level; }
    if (im->is3d) { if (type == VK_IMAGE_VIEW_TYPE_2D || type == VK_IMAGE_VIEW_TYPE_2D_ARRAY) { if (layer >= im->d) layer = 0; if (!nlayers || layer + nlayers > im->d) nlayers = im->d - layer; } else { layer = 0; nlayers = 1; } }
    else { if (layer >= im->layers) layer = 0; if (!nlayers || layer + nlayers > im->layers) nlayers = im->layers - layer; }
    if (type == VK_IMAGE_VIEW_TYPE_2D) nlayers = 1;
    if (type == VK_IMAGE_VIEW_TYPE_CUBE) nlayers = 6;
    uint64_t key = (uint64_t)type | (uint64_t)level << 8 | (uint64_t)nlevels << 16 | (uint64_t)layer << 24 | (uint64_t)nlayers << 44 | (uint64_t)(mode & 3) << 62;
    for (uint32_t i = 0; i < im->nviews; i++) if (im->views[i].key == key) return im->views[i].v;
    VkImageViewCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = im->image, .viewType = type, .format = im->format,
                                 .components = { VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, (mode & 1) ? VK_COMPONENT_SWIZZLE_ONE : VK_COMPONENT_SWIZZLE_IDENTITY },
                                 .subresourceRange = { .aspectMask = (mode & 2) ? im->aspect : im->aspect & VK_IMAGE_ASPECT_DEPTH_BIT ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT, .baseMipLevel = level, .levelCount = nlevels, .baseArrayLayer = layer, .layerCount = nlayers } };
    VkImageView v;
    VkResult r = vkCreateImageView(G.dev, &vi, NULL, &v);
    if (r != VK_SUCCESS) { fail("vkCreateImageView", r); return VK_NULL_HANDLE; }
    if (im->nviews == im->cviews) { im->cviews = im->cviews ? im->cviews * 2 : 4; im->views = realloc(im->views, im->cviews * sizeof(View)); }
    im->views[im->nviews++] = (View){ key, v };
    return v;
}

void nx2gpu_image_free(uint32_t id)
{
    pthread_mutex_lock(&G.mu);
    if (G.open && id && id < G.nimages && G.images[id].live) {
        flush_locked();   /* nothing recorded may still use it */
        Image *im = &G.images[id];
        for (uint32_t i = 0; i < im->nviews; i++) vkDestroyImageView(G.dev, im->views[i].v, NULL);
        free(im->views); vkDestroyImage(G.dev, im->image, NULL); vkFreeMemory(G.dev, im->mem, NULL);
        memset(im, 0, sizeof *im);
    }
    pthread_mutex_unlock(&G.mu);
}

/* Block-linear (Tegra X1) to linear: row_bytes bytes per row, rows rows, slices slices, blocks of 2^tile_h GOBs high and 2^tile_d deep, the row padded to gobs_x GOBs (0: row_bytes / 64 rounded up);
 * engine/dm/gfx/tex/nvn_deswizzle.c. dst is tightly packed */
int nx2gpu_deswizzle(const uint8_t *src, uint8_t *dst, uint32_t row_bytes, uint32_t rows, uint32_t slices, uint32_t tile_h, uint32_t tile_d, uint32_t gobs_x_in)
{
    static const uint16_t kox[4] = { 0, 32, 256, 288 }, koy[8] = { 0, 16, 64, 80, 128, 144, 192, 208 };
    uint64_t bh = 1ull << tile_h, bd = 1ull << tile_d, block = 512ull * bh * bd, gobs_x = gobs_x_in ? gobs_x_in : (row_bytes + 63) >> 6;
    uint64_t blocks_y = (rows + 8 * bh - 1) >> (3 + tile_h), nfull = row_bytes >> 4, tail = row_bytes & 15;
    for (uint32_t z = 0; z < slices; z++) {
        uint64_t zb = (uint64_t)(z >> tile_d) * gobs_x * blocks_y * block + (uint64_t)(z & (bd - 1)) * bh * 512;
        for (uint32_t y = 0; y < rows; y++) {
            uint64_t yb = zb + (uint64_t)(y >> (3 + tile_h)) * gobs_x * block + (uint64_t)((y & (8 * bh - 1)) >> 3) * 512 + koy[y & 7];
            uint8_t *row = dst + ((uint64_t)z * rows + y) * row_bytes;
            for (uint64_t j = 0; j < nfull; j++) memcpy(row + (j << 4), src + yb + (j >> 2) * block + kox[j & 3], 16);
            if (tail) memcpy(row + (nfull << 4), src + yb + (nfull >> 2) * block + kox[nfull & 3], tail);
        }
    }
    return 0;
}

/* Pixels (tightly packed) into one level of one layer: the region x, y, z with size w, h, d. The data goes through the ring and the copy is recorded: no wait, the draws that follow see it */
static uint64_t ring_alloc(uint64_t bytes, uint64_t align, uint8_t **ptr);

int nx2gpu_upload(uint32_t id, const void *pixels, uint64_t bytes, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t z, uint32_t w, uint32_t h, uint32_t d)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || id >= G.nimages || !G.images[id].live || bytes + 64 > RING_BYTES) goto out;
    if (begin()) goto out;
    end_rendering();
    uint8_t *p; uint64_t o = ring_alloc(bytes ? bytes : 4, 16, &p);
    if (o == UINT64_MAX) { if (flush_locked() || begin()) goto out; o = ring_alloc(bytes ? bytes : 4, 16, &p); if (o == UINT64_MAX) goto out; }
    memcpy(p, pixels, bytes);
    Image *im = &G.images[id];
    VkBufferImageCopy c = { .bufferOffset = o, .imageSubresource = { .aspectMask = im->aspect & VK_IMAGE_ASPECT_DEPTH_BIT ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT, .mipLevel = level, .baseArrayLayer = im->is3d ? 0 : layer, .layerCount = 1 },
                            .imageOffset = { (int32_t)x, (int32_t)y, (int32_t)z }, .imageExtent = { w, h, d ? d : 1 } };
    vkCmdCopyBufferToImage(G.cb, G.ring, im->image, VK_IMAGE_LAYOUT_GENERAL, 1, &c);
    barrier_all();
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

/* One region of one image to another (the formats must be compatible) */
int nx2gpu_copy_image(uint32_t src, uint32_t dst, uint32_t slevel, uint32_t slayer, uint32_t dlevel, uint32_t dlayer, const int32_t *so, const int32_t *dof, const uint32_t *ext)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || src >= G.nimages || dst >= G.nimages || !G.images[src].live || !G.images[dst].live || begin()) goto out;
    end_rendering();
    barrier_all();
    Image *s = &G.images[src], *d = &G.images[dst];
    VkImageCopy c = { .srcSubresource = { s->aspect, slevel, s->is3d ? 0 : slayer, 1 }, .srcOffset = { so[0], so[1], so[2] }, .dstSubresource = { d->aspect, dlevel, d->is3d ? 0 : dlayer, 1 },
                      .dstOffset = { dof[0], dof[1], dof[2] }, .extent = { ext[0], ext[1], ext[2] } };
    vkCmdCopyImage(G.cb, s->image, VK_IMAGE_LAYOUT_GENERAL, d->image, VK_IMAGE_LAYOUT_GENERAL, 1, &c);
    barrier_all();
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

int nx2gpu_read(uint32_t id, void *out, uint64_t bytes)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || id >= G.nimages || !G.images[id].live || bytes > STAGING_BYTES) goto out;
    if (begin()) goto out;
    end_rendering();
    barrier_all();
    Image *im = &G.images[id];
    VkBufferImageCopy c = { .imageSubresource = { .aspectMask = im->aspect & VK_IMAGE_ASPECT_DEPTH_BIT ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT, .layerCount = 1 }, .imageExtent = { im->w, im->h, 1 } };
    vkCmdCopyImageToBuffer(G.cb, im->image, VK_IMAGE_LAYOUT_GENERAL, G.stage, 1, &c);
    barrier_all();
    if (flush_locked()) goto out;
    memcpy(out, G.stage_ptr, bytes);
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

/* Clears every level and layer of the image (a clear of a view's range: layer, nlayers) */
int nx2gpu_clear_color(uint32_t id, const float *rgba, uint32_t level, uint32_t nlevels, uint32_t layer, uint32_t nlayers)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || id >= G.nimages || !G.images[id].live || begin()) goto out;
    end_rendering();
    Image *im = &G.images[id];
    VkClearColorValue cv; memcpy(cv.float32, rgba, 16);
    VkImageSubresourceRange sr = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .baseMipLevel = level, .levelCount = nlevels ? nlevels : VK_REMAINING_MIP_LEVELS, .baseArrayLayer = im->is3d ? 0 : layer, .layerCount = nlayers ? nlayers : VK_REMAINING_ARRAY_LAYERS };
    vkCmdClearColorImage(G.cb, im->image, VK_IMAGE_LAYOUT_GENERAL, &cv, 1, &sr);
    barrier_all();
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

int nx2gpu_clear_depth(uint32_t id, float depth, uint32_t stencil)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || id >= G.nimages || !G.images[id].live || begin()) goto out;
    end_rendering();
    VkClearDepthStencilValue cv = { depth, stencil };
    VkImageSubresourceRange sr = { .aspectMask = G.images[id].aspect, .levelCount = VK_REMAINING_MIP_LEVELS, .layerCount = VK_REMAINING_ARRAY_LAYERS };
    vkCmdClearDepthStencilImage(G.cb, G.images[id].image, VK_IMAGE_LAYOUT_GENERAL, &cv, 1, &sr);
    barrier_all();
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

static VkSampler sampler_for(const Nx2Tex *t)
{
    uint64_t key = fnv(1469598103934665603ull, t, sizeof *t);
    for (uint32_t i = 0; i < G.nsamps; i++) if (G.samps[i].key == key) return G.samps[i].s;
    VkSamplerCreateInfo si = { .sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO, .magFilter = (VkFilter)t->mag_filter, .minFilter = (VkFilter)t->min_filter, .mipmapMode = (VkSamplerMipmapMode)t->mip_mode,
                               .addressModeU = (VkSamplerAddressMode)t->wrap_s, .addressModeV = (VkSamplerAddressMode)t->wrap_t, .addressModeW = (VkSamplerAddressMode)t->wrap_r,
                               .mipLodBias = t->lod_bias, .anisotropyEnable = t->aniso > 1.0f, .maxAnisotropy = t->aniso > 1.0f ? (t->aniso > 16.0f ? 16.0f : t->aniso) : 1.0f,
                               .compareEnable = t->compare_enable, .compareOp = (VkCompareOp)t->compare_op, .minLod = t->lod_min, .maxLod = t->lod_max, .borderColor = (VkBorderColor)t->border };
    VkSampler s;
    if (vkCreateSampler(G.dev, &si, NULL, &s) != VK_SUCCESS) return VK_NULL_HANDLE;
    if (G.nsamps == G.csamps) { G.csamps = G.csamps ? G.csamps * 2 : 64; G.samps = realloc(G.samps, G.csamps * sizeof(Samp)); }
    G.samps[G.nsamps++] = (Samp){ key, s };
    return s;
}

/* The descriptor set layout of a draw: one binding per buffer (uniform or storage) and per texture */
static uint64_t layout_key(const Nx2Draw *d)
{
    uint64_t h = 1469598103934665603ull;
    for (uint32_t i = 0; i < d->nbufs; i++) { uint32_t v[2] = { d->bufs[i].binding, d->bufs[i].kind }; h = fnv(h, v, 8); }
    for (uint32_t i = 0; i < d->ntex; i++) { uint32_t v = d->tex[i].binding | 0x80000000u; h = fnv(h, &v, 4); }
    return h;
}

static int has_stencil(VkFormat f) { return f == VK_FORMAT_D32_SFLOAT_S8_UINT || f == VK_FORMAT_D24_UNORM_S8_UINT; }

static Pipe *pipeline_for(const Nx2Draw *d, VkFormat color_fmt[MAX_TARGETS], VkFormat depth_fmt, uint32_t topology, VkPipelineLayout *layout_out)
{
    uint64_t key = layout_key(d);
    key = fnv(key, &d->vs, 8);   /* vs and ps */
    key = fnv(key, color_fmt, sizeof(VkFormat) * d->ntargets); key = fnv(key, &depth_fmt, 4); key = fnv(key, &topology, 4);
    key = fnv(key, d->attrs, sizeof(Nx2Attr) * d->nattrs);
    for (uint32_t i = 0; i < d->nstreams; i++) { uint32_t v[3] = { d->streams[i].binding, d->streams[i].stride, d->streams[i].divisor }; key = fnv(key, v, 12); }
    key = fnv(key, d->blend, sizeof(Nx2Blend) * d->ntargets);
    key = fnv(key, &d->cull, 8 * 4);
    key = fnv(key, &d->stencil_test, 2 * 4); key = fnv(key, d->stencil_front, 8 * 4);
    for (uint32_t i = 0; i < G.npipes; i++) if (G.pipes[i].key == key) { *layout_out = G.pipes[i].layout; return &G.pipes[i]; }

    VkDescriptorSetLayoutBinding lb[64]; uint32_t nlb = 0;
    for (uint32_t i = 0; i < d->nbufs && nlb < 64; i++)
        lb[nlb++] = (VkDescriptorSetLayoutBinding){ .binding = d->bufs[i].binding, .descriptorType = d->bufs[i].kind ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_ALL_GRAPHICS };
    for (uint32_t i = 0; i < d->ntex && nlb < 64; i++)
        lb[nlb++] = (VkDescriptorSetLayoutBinding){ .binding = d->tex[i].binding, .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_ALL_GRAPHICS };
    VkDescriptorSetLayoutCreateInfo sli = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR, .bindingCount = nlb, .pBindings = lb };
    VkDescriptorSetLayout sl; VkPipelineLayout pl;
    if (vkCreateDescriptorSetLayout(G.dev, &sli, NULL, &sl) != VK_SUCCESS) return NULL;
    VkPipelineLayoutCreateInfo pli = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1, .pSetLayouts = &sl };
    if (vkCreatePipelineLayout(G.dev, &pli, NULL, &pl) != VK_SUCCESS) return NULL;

    VkPipelineShaderStageCreateInfo st[2] = {
        { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = G.shaders[d->vs].mod, .pName = "main" },
        { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = d->ps ? G.shaders[d->ps].mod : VK_NULL_HANDLE, .pName = "main" } };
    VkVertexInputBindingDescription vb[16]; VkVertexInputAttributeDescription va[16];
    for (uint32_t i = 0; i < d->nstreams; i++) vb[i] = (VkVertexInputBindingDescription){ d->streams[i].binding, d->streams[i].stride, d->streams[i].divisor ? VK_VERTEX_INPUT_RATE_INSTANCE : VK_VERTEX_INPUT_RATE_VERTEX };
    for (uint32_t i = 0; i < d->nattrs; i++) va[i] = (VkVertexInputAttributeDescription){ d->attrs[i].location, d->attrs[i].binding, (VkFormat)d->attrs[i].format, d->attrs[i].offset };
    VkPipelineVertexInputStateCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO, .vertexBindingDescriptionCount = d->nstreams, .pVertexBindingDescriptions = vb,
                                                .vertexAttributeDescriptionCount = d->nattrs, .pVertexAttributeDescriptions = va };
    VkPipelineInputAssemblyStateCreateInfo ia = { .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO, .topology = (VkPrimitiveTopology)topology };
    VkPipelineViewportDepthClipControlCreateInfoEXT ccs = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_DEPTH_CLIP_CONTROL_CREATE_INFO_EXT, .negativeOneToOne = VK_TRUE };
    VkPipelineViewportStateCreateInfo vp = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO, .pNext = G.clip_control && d->depth_neg_one ? &ccs : NULL, .viewportCount = 1, .scissorCount = 1 };
    VkPipelineRasterizationStateCreateInfo rs = { .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO, .depthClampEnable = d->depth_clamp, .polygonMode = (VkPolygonMode)d->polygon_mode,
                                                  .cullMode = (VkCullModeFlags)d->cull, .frontFace = (VkFrontFace)d->front_face, .depthBiasEnable = d->depth_bias_on, .lineWidth = 1.0f };
    VkPipelineMultisampleStateCreateInfo ms = { .sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO, .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT };
    VkPipelineDepthStencilStateCreateInfo ds = { .sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO, .depthTestEnable = d->depth_test && depth_fmt, .depthWriteEnable = d->depth_write && depth_fmt, .depthCompareOp = (VkCompareOp)d->depth_func, .maxDepthBounds = 1.0f,
                                                    .stencilTestEnable = d->stencil_test && has_stencil(depth_fmt),
                                                    .front = { d->stencil_front[0], d->stencil_front[2], d->stencil_front[1], d->stencil_front[3], 0xFF, 0xFF, 0 },
                                                    .back = { d->stencil_back[0], d->stencil_back[2], d->stencil_back[1], d->stencil_back[3], 0xFF, 0xFF, 0 } };
    VkPipelineColorBlendAttachmentState ba[MAX_TARGETS];
    for (uint32_t i = 0; i < d->ntargets; i++)
        ba[i] = (VkPipelineColorBlendAttachmentState){ .blendEnable = d->blend[i].enable, .srcColorBlendFactor = d->blend[i].src_rgb, .dstColorBlendFactor = d->blend[i].dst_rgb, .colorBlendOp = d->blend[i].op_rgb,
                                                        .srcAlphaBlendFactor = d->blend[i].src_a, .dstAlphaBlendFactor = d->blend[i].dst_a, .alphaBlendOp = d->blend[i].op_a, .colorWriteMask = d->blend[i].mask };
    VkPipelineColorBlendStateCreateInfo cbs = { .sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO, .attachmentCount = d->ntargets, .pAttachments = ba };
    VkDynamicState dyn[] = { VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR, VK_DYNAMIC_STATE_BLEND_CONSTANTS, VK_DYNAMIC_STATE_DEPTH_BIAS, VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK, VK_DYNAMIC_STATE_STENCIL_WRITE_MASK, VK_DYNAMIC_STATE_STENCIL_REFERENCE };
    VkPipelineDynamicStateCreateInfo dy = { .sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO, .dynamicStateCount = 7, .pDynamicStates = dyn };
    VkPipelineRenderingCreateInfo ri = { .sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO, .colorAttachmentCount = d->ntargets, .pColorAttachmentFormats = color_fmt,
                                         .depthAttachmentFormat = depth_fmt, .stencilAttachmentFormat = has_stencil(depth_fmt) ? depth_fmt : VK_FORMAT_UNDEFINED };
    VkGraphicsPipelineCreateInfo gi = { .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &ri, .stageCount = d->ps ? 2 : 1, .pStages = st, .pVertexInputState = &vi, .pInputAssemblyState = &ia,
                                        .pViewportState = &vp, .pRasterizationState = &rs, .pMultisampleState = &ms, .pDepthStencilState = &ds, .pColorBlendState = &cbs, .pDynamicState = &dy, .layout = pl };
    VkPipeline pipe;
    VkResult r = vkCreateGraphicsPipelines(G.dev, VK_NULL_HANDLE, 1, &gi, NULL, &pipe);
    if (r != VK_SUCCESS) { fail("vkCreateGraphicsPipelines", r); return NULL; }
    if (G.npipes == G.cpipes) { G.cpipes = G.cpipes ? G.cpipes * 2 : 256; G.pipes = realloc(G.pipes, G.cpipes * sizeof(Pipe)); }
    G.pipes[G.npipes] = (Pipe){ key, pipe, pl, sl };
    *layout_out = pl;
    return &G.pipes[G.npipes++];
}

static uint64_t ring_alloc(uint64_t bytes, uint64_t align, uint8_t **ptr)
{
    uint64_t off = (G.ring_off + align - 1) & ~(align - 1);
    if (off + bytes > RING_BYTES) return UINT64_MAX;
    G.ring_off = off + bytes; *ptr = G.ring_ptr + off;
    return off;
}

static uint32_t topology_of(uint32_t prim)
{
    switch (prim) {
    case 0: return VK_PRIMITIVE_TOPOLOGY_POINT_LIST;
    case 1: return VK_PRIMITIVE_TOPOLOGY_LINE_LIST;
    case 3: return VK_PRIMITIVE_TOPOLOGY_LINE_STRIP;
    case 4: case QUADS: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    case 5: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP;
    case 6: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN;
    default: return UINT32_MAX;
    }
}

/* 0: recorded, 1: skipped (something the draw needs is not supported yet), -1: error */
int nx2gpu_draw(const Nx2Draw *d)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    VkFormat color_fmt[MAX_TARGETS], depth_fmt = VK_FORMAT_UNDEFINED;
    uint32_t topo;
    if (!G.open || !d->vs || d->vs >= G.nshaders || (d->ps && d->ps >= G.nshaders) || d->ntargets > MAX_TARGETS) goto out;
    if ((topo = topology_of(d->topology)) == UINT32_MAX) { G.skipped++; r = 1; goto out; }
    for (uint32_t i = 0; i < d->ntargets; i++) {
        if (d->targets[i].image >= G.nimages || !G.images[d->targets[i].image].live) { G.skipped++; r = 1; goto out; }
        color_fmt[i] = G.images[d->targets[i].image].format;
    }
    if (d->depth.image) { if (d->depth.image >= G.nimages || !G.images[d->depth.image].live) { G.skipped++; r = 1; goto out; } depth_fmt = G.images[d->depth.image].format; }
    if (!d->ntargets && !d->depth.image) { G.skipped++; r = 1; goto out; }
    if (begin()) goto out;

    /* data the draw reads, into the ring: make room for all of it first (a flush in the middle would recycle what was already copied) */
    {
        uint64_t need = d->index_size * 2 + 4096;
        for (uint32_t i = 0; i < d->nstreams; i++) need += d->streams[i].size + 16;
        for (uint32_t i = 0; i < d->nbufs; i++) need += d->bufs[i].size + G.ubo_align;
        if (need > RING_BYTES) { G.skipped++; r = 1; goto out; }
        if (G.ring_off + need > RING_BYTES && (flush_locked() || begin())) goto out;
    }
    uint64_t vb_off[16]; VkBuffer vbufs[16];
    for (uint32_t i = 0; i < d->nstreams; i++) {
        uint8_t *p; uint64_t o = ring_alloc(d->streams[i].size ? d->streams[i].size : 16, 16, &p);
        if (o == UINT64_MAX) { if (flush_locked() || begin()) goto out; o = ring_alloc(d->streams[i].size ? d->streams[i].size : 16, 16, &p); if (o == UINT64_MAX) { G.skipped++; r = 1; goto out; } }
        if (d->streams[i].size) memcpy(p, d->streams[i].data, d->streams[i].size);
        vb_off[i] = o; vbufs[i] = G.ring;
    }
    uint64_t ib_off = 0; uint32_t index_count = 0; VkIndexType itype = VK_INDEX_TYPE_UINT32;
    uint32_t count = (uint32_t)d->count;
    if (d->index_type || d->topology == QUADS) {
        uint32_t isz = d->index_type == 1 ? 2 : 4;
        uint32_t src_n = d->index_type ? (uint32_t)(d->index_size / isz) : count;
        if (d->index_type && src_n > count) src_n = count;
        uint32_t out_n = d->topology == QUADS ? src_n / 4 * 6 : src_n;
        uint8_t *p; uint64_t need = (uint64_t)out_n * (d->topology == QUADS ? 4 : isz);
        ib_off = ring_alloc(need ? need : 4, 4, &p);
        if (ib_off == UINT64_MAX) { if (flush_locked() || begin()) goto out; ib_off = ring_alloc(need ? need : 4, 4, &p); if (ib_off == UINT64_MAX) { G.skipped++; r = 1; goto out; } }
        if (d->topology == QUADS) {
            uint32_t *o = (uint32_t *)p;
            for (uint32_t q = 0; q < src_n / 4; q++) {
                uint32_t v[4];
                for (int k = 0; k < 4; k++) v[k] = !d->index_type ? (uint32_t)d->first + q * 4 + (uint32_t)k : isz == 2 ? ((const uint16_t *)d->index_data)[q * 4 + k] : ((const uint32_t *)d->index_data)[q * 4 + k];
                o[q * 6 + 0] = v[0]; o[q * 6 + 1] = v[1]; o[q * 6 + 2] = v[2]; o[q * 6 + 3] = v[0]; o[q * 6 + 4] = v[2]; o[q * 6 + 5] = v[3];
            }
            itype = VK_INDEX_TYPE_UINT32; index_count = out_n;
        } else { memcpy(p, d->index_data, (size_t)src_n * isz); itype = isz == 2 ? VK_INDEX_TYPE_UINT16 : VK_INDEX_TYPE_UINT32; index_count = src_n; }
    }
    VkDescriptorBufferInfo bi[32]; VkDescriptorImageInfo ii[32]; VkWriteDescriptorSet wr[64]; uint32_t nw = 0;
    for (uint32_t i = 0; i < d->nbufs; i++) {
        uint8_t *p; uint64_t sz = d->bufs[i].size ? d->bufs[i].size : 16;
        uint64_t o = ring_alloc(sz, G.ubo_align, &p);
        if (o == UINT64_MAX) { G.skipped++; r = 1; goto out; }
        memcpy(p, d->bufs[i].data, d->bufs[i].size);
        bi[i] = (VkDescriptorBufferInfo){ G.ring, o, sz };
        wr[nw++] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = d->bufs[i].binding, .descriptorCount = 1,
                                           .descriptorType = d->bufs[i].kind ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, .pBufferInfo = &bi[i] };
    }
    for (uint32_t i = 0; i < d->ntex; i++) {
        uint32_t id = d->tex[i].image;
        if (id >= G.nimages || !G.images[id].live) { G.skipped++; r = 1; goto out; }
        VkSampler s = sampler_for(&d->tex[i]);
        if (!s) goto out;
        VkImageView v = view_of(&G.images[id], (VkImageViewType)d->tex[i].view_type, d->tex[i].base_level, d->tex[i].nlevels, d->tex[i].base_layer, d->tex[i].nlayers, d->tex[i].alpha_one);
        if (!v) goto out;
        ii[i] = (VkDescriptorImageInfo){ s, v, VK_IMAGE_LAYOUT_GENERAL };
        wr[nw++] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = d->tex[i].binding, .descriptorCount = 1,
                                           .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .pImageInfo = &ii[i] };
    }

    VkPipelineLayout layout;
    Pipe *pipe = pipeline_for(d, color_fmt, depth_fmt, topo, &layout);
    if (!pipe) goto out;

    /* the render pass: kept open while consecutive draws share their targets */
    uint64_t rh = fnv(1469598103934665603ull, d->targets, sizeof(Nx2Target) * d->ntargets);
    rh = fnv(rh, &d->depth, sizeof(Nx2Target));
    if (!G.rendering || G.rt_hash != rh) {
        end_rendering();
        VkRenderingAttachmentInfo ca[MAX_TARGETS], da = { .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO };
        for (uint32_t i = 0; i < d->ntargets; i++) {
            Image *ti = &G.images[d->targets[i].image];
            VkImageView v = view_of(ti, VK_IMAGE_VIEW_TYPE_2D, d->targets[i].level, 1, d->targets[i].layer, 1, 2);
            if (!v) goto out;
            ca[i] = (VkRenderingAttachmentInfo){ .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = v, .imageLayout = VK_IMAGE_LAYOUT_GENERAL, .loadOp = VK_ATTACHMENT_LOAD_OP_LOAD, .storeOp = VK_ATTACHMENT_STORE_OP_STORE };
        }
        if (d->depth.image) {
            VkImageView v = view_of(&G.images[d->depth.image], VK_IMAGE_VIEW_TYPE_2D, d->depth.level, 1, d->depth.layer, 1, 2);
            if (!v) goto out;
            da = (VkRenderingAttachmentInfo){ .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = v, .imageLayout = VK_IMAGE_LAYOUT_GENERAL, .loadOp = VK_ATTACHMENT_LOAD_OP_LOAD, .storeOp = VK_ATTACHMENT_STORE_OP_STORE };
        }
        const Nx2Target *t0 = d->ntargets ? &d->targets[0] : &d->depth;
        uint32_t w = G.images[t0->image].w >> t0->level, h = G.images[t0->image].h >> t0->level;
        if (!w) w = 1;
        if (!h) h = 1;
        VkRenderingInfo ri = { .sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = { { 0, 0 }, { w, h } }, .layerCount = 1, .colorAttachmentCount = d->ntargets, .pColorAttachments = ca,
                               .pDepthAttachment = d->depth.image ? &da : NULL, .pStencilAttachment = d->depth.image && has_stencil(depth_fmt) ? &da : NULL };
        G.begin_rendering(G.cb, &ri);
        G.rendering = 1; G.rt_hash = rh;
    }
    vkCmdBindPipeline(G.cb, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe->pipe);
    for (uint32_t i = 0; i < nw; i++) wr[i].dstSet = VK_NULL_HANDLE;
    if (nw) G.push(G.cb, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, nw, wr);
    VkViewport vp = { d->viewport[0], d->viewport[1], d->viewport[2], d->viewport[3], d->depth_range[0], d->depth_range[1] };
    VkRect2D sc = { { d->scissor[0], d->scissor[1] }, { (uint32_t)d->scissor[2], (uint32_t)d->scissor[3] } };
    vkCmdSetViewport(G.cb, 0, 1, &vp); vkCmdSetScissor(G.cb, 0, 1, &sc);
    vkCmdSetBlendConstants(G.cb, d->blend_color);
    vkCmdSetDepthBias(G.cb, d->depth_bias[0], d->depth_bias[1], d->depth_bias[2]);
    vkCmdSetStencilCompareMask(G.cb, VK_STENCIL_FACE_FRONT_BIT, d->stencil_masks[0]); vkCmdSetStencilWriteMask(G.cb, VK_STENCIL_FACE_FRONT_BIT, d->stencil_masks[1]); vkCmdSetStencilReference(G.cb, VK_STENCIL_FACE_FRONT_BIT, d->stencil_masks[2]);
    vkCmdSetStencilCompareMask(G.cb, VK_STENCIL_FACE_BACK_BIT, d->stencil_masks[3]); vkCmdSetStencilWriteMask(G.cb, VK_STENCIL_FACE_BACK_BIT, d->stencil_masks[4]); vkCmdSetStencilReference(G.cb, VK_STENCIL_FACE_BACK_BIT, d->stencil_masks[5]);
    for (uint32_t i = 0; i < d->nstreams; i++) { VkDeviceSize o = vb_off[i]; vkCmdBindVertexBuffers(G.cb, d->streams[i].binding, 1, &vbufs[i], &o); }
    uint32_t inst = d->instances ? d->instances : 1;
    if (index_count) {
        vkCmdBindIndexBuffer(G.cb, G.ring, ib_off, itype);
        vkCmdDrawIndexed(G.cb, index_count, inst, 0, d->base_vertex, (uint32_t)d->base_instance);   /* quads: the vertex offset applies to the generated indices too */
    } else {
        vkCmdDraw(G.cb, count, inst, (uint32_t)d->first, (uint32_t)d->base_instance);
    }
    G.draws++;
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

void nx2gpu_stats(uint64_t *out) { out[0] = G.draws; out[1] = G.flushes; out[2] = G.skipped; out[3] = G.nimages; out[4] = G.nshaders; out[5] = G.npipes; }
