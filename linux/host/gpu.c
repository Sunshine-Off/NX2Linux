/* The GPU the NVN layer draws with (NX2Linux S7bis): a headless Vulkan device that renders the draws of a recompiled game into images, for Python (ctypes: engine/dm/tools/horizon_gpu.py).
 * Images are created once per NVN texture and stay in VK_IMAGE_LAYOUT_GENERAL (render target, sampled and transfer use need no layout changes). Commands are recorded into one command buffer
 * and submitted by nx2gpu_flush (a readback, a present). Data the draw reads (vertex streams, indices, uniform buffers) is copied into a host-visible ring when the draw is recorded, so the
 * game may overwrite its memory right after. Resources go through push descriptors: set 0 with the bindings the translated shader declares (Ryujinx.Graphics.Shader: binding 0 the
 * support buffer, then constant buffers, storage buffers and textures as dmsh_xlate numbers them).
 * Threads: every entry point takes one lock. */
#include <vulkan/vulkan.h>

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

#include "nx2linux/sdl_host.h"

#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_IMAGES 8192
#define MAX_SHADERS 16384
#define RING_BYTES (192u << 20)
#define STAGING_BYTES (96u << 20)
#define QUADS 100   /* the NVN quad primitive: Vulkan has none, drawn as two triangles through a generated index buffer */
#define LINE_LOOP 101   /* NVN's line loop: a line strip that returns to its first vertex, through a generated index buffer */
#define QUAD_STRIP 102   /* NVN's quad strip: vertices (2k, 2k+1, 2k+3, 2k+2) are quad k, drawn as two triangles */
#define GENERATED(t) ((t) == QUADS || (t) == LINE_LOOP || (t) == QUAD_STRIP)

#include "nx2gpu_types.h"


typedef struct { uint64_t key; VkImageView v; } View;
typedef struct {
    VkImage image; VkDeviceMemory mem; uint32_t w, h, d, layers, levels; VkFormat format; VkImageAspectFlags aspect; int is3d, live, storage;
    View *views; uint32_t nviews, cviews;
} Image;
typedef struct { VkShaderModule mod; int live; } Shader;
typedef struct { uint64_t key; VkPipeline pipe; VkPipelineLayout layout; VkDescriptorSetLayout set_layout; } Pipe;
typedef struct { uint64_t key; VkSampler s; } Samp;

static struct {
    int open, clip_control, have_swapchain, wide_lines; float line_min, line_max;
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
    uint64_t cpipe_key[256]; VkPipeline cpipe[256]; VkPipelineLayout cpipe_layout[256]; uint32_t ncpipes;
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
    /* the window system integration, when the loader has it: a window may be opened later (nx2gpu_window_open), on the presenting thread, and its surface needs these on the instance */
    const char *iexts[4]; uint32_t niexts = 0;
    {
        uint32_t nie = 0; vkEnumerateInstanceExtensionProperties(NULL, &nie, NULL);
        VkExtensionProperties *ie = calloc(nie ? nie : 1, sizeof *ie); vkEnumerateInstanceExtensionProperties(NULL, &nie, ie);
        static const char *want[] = { "VK_KHR_surface", "VK_KHR_xcb_surface", "VK_KHR_xlib_surface", "VK_KHR_wayland_surface" };
        for (uint32_t w = 0; w < 4; w++) for (uint32_t i = 0; i < nie; i++) if (!strcmp(ie[i].extensionName, want[w])) { iexts[niexts++] = want[w]; break; }
        free(ie);
        if (!niexts || strcmp(iexts[0], "VK_KHR_surface")) niexts = 0;   /* without the base surface extension none of them is of use */
    }
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app, .enabledExtensionCount = niexts, .ppEnabledExtensionNames = iexts };
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
    G.line_min = props.limits.lineWidthRange[0]; G.line_max = props.limits.lineWidthRange[1];
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
    G.wide_lines = avail.wideLines; f2.features.wideLines = avail.wideLines; f2.features.depthClamp = avail.depthClamp; f2.features.independentBlend = avail.independentBlend; f2.features.fillModeNonSolid = avail.fillModeNonSolid; f2.features.geometryShader = avail.geometryShader; f2.features.tessellationShader = avail.tessellationShader;
    f2.features.shaderStorageImageReadWithoutFormat = avail.shaderStorageImageReadWithoutFormat; f2.features.shaderStorageImageWriteWithoutFormat = avail.shaderStorageImageWriteWithoutFormat;
    f2.features.shaderTessellationAndGeometryPointSize = avail.shaderTessellationAndGeometryPointSize; f2.features.largePoints = avail.largePoints; f2.features.multiViewport = avail.multiViewport;
    f2.features.shaderImageGatherExtended = avail.shaderImageGatherExtended; f2.features.shaderStorageImageExtendedFormats = avail.shaderStorageImageExtendedFormats;
    f2.features.textureCompressionBC = avail.textureCompressionBC; f2.features.shaderClipDistance = avail.shaderClipDistance; f2.features.shaderCullDistance = avail.shaderCullDistance;
    const char *exts[3] = { VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME }; uint32_t next = 1;
    uint32_t nde = 0; vkEnumerateDeviceExtensionProperties(G.phys, NULL, &nde, NULL);
    VkExtensionProperties *dep = calloc(nde ? nde : 1, sizeof *dep); vkEnumerateDeviceExtensionProperties(G.phys, NULL, &nde, dep);
    for (uint32_t i = 0; i < nde; i++) if (!strcmp(dep[i].extensionName, VK_EXT_DEPTH_CLIP_CONTROL_EXTENSION_NAME)) G.clip_control = 1;
    for (uint32_t i = 0; i < nde; i++) if (niexts && !strcmp(dep[i].extensionName, VK_KHR_SWAPCHAIN_EXTENSION_NAME)) G.have_swapchain = 1;
    free(dep);
    if (G.have_swapchain) exts[next++] = VK_KHR_SWAPCHAIN_EXTENSION_NAME;
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

/* Submits what was recorded and waits for it. wait / signal: the semaphores of a present (acquire, render finished), or NULL */
static int submit_locked(VkSemaphore wait, VkSemaphore signal)
{
    if (!G.recording) { G.ring_off = 0; return 0; }
    end_rendering();
    VK(vkEndCommandBuffer(G.cb));
    VkCommandBufferSubmitInfo cbi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO, .commandBuffer = G.cb };
    VkSemaphoreSubmitInfo ws = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, .semaphore = wait, .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT };
    VkSemaphoreSubmitInfo ss = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, .semaphore = signal, .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT };
    VkSubmitInfo2 si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2, .waitSemaphoreInfoCount = wait ? 1 : 0, .pWaitSemaphoreInfos = &ws, .commandBufferInfoCount = 1, .pCommandBufferInfos = &cbi,
                         .signalSemaphoreInfoCount = signal ? 1 : 0, .pSignalSemaphoreInfos = &ss };
    VK(vkQueueSubmit2(G.queue, 1, &si, G.fence));
    VkResult w = vkWaitForFences(G.dev, 1, &G.fence, VK_TRUE, 8000000000ull);
    if (w != VK_SUCCESS) { G.open = 0; return fail(w == VK_TIMEOUT ? "the GPU did not finish in 8 s (hang), device dropped" : "vkWaitForFences", w); }
    VK(vkResetFences(G.dev, 1, &G.fence));
    VK(vkResetCommandBuffer(G.cb, 0));
    G.recording = 0; G.ring_off = 0; G.flushes++;
    return 0;
}

static int flush_locked(void) { return submit_locked(VK_NULL_HANDLE, VK_NULL_HANDLE); }

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
    { VkFormatProperties fp; vkGetPhysicalDeviceFormatProperties(G.phys, im->format, &fp); if (fp.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) { usage |= VK_IMAGE_USAGE_STORAGE_BIT; im->storage = 1; } }
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
/* mode: bit 1 a render attachment (a depth-stencil image then keeps its stencil aspect); swz: the component swizzle of the view or NULL */
static VkImageView view_of(Image *im, VkImageViewType type, uint32_t level, uint32_t nlevels, uint32_t layer, uint32_t nlayers, int mode, const uint32_t *swz)
{
    if (!nlevels || level + nlevels > im->levels) { if (level >= im->levels) level = 0; nlevels = im->levels - level; }
    if (im->is3d) { if (type == VK_IMAGE_VIEW_TYPE_2D || type == VK_IMAGE_VIEW_TYPE_2D_ARRAY) { if (layer >= im->d) layer = 0; if (!nlayers || layer + nlayers > im->d) nlayers = im->d - layer; } else { layer = 0; nlayers = 1; } }
    else { if (layer >= im->layers) layer = 0; if (!nlayers || layer + nlayers > im->layers) nlayers = im->layers - layer; }
    if (type == VK_IMAGE_VIEW_TYPE_2D) nlayers = 1;
    if (type == VK_IMAGE_VIEW_TYPE_CUBE) nlayers = 6;
    uint64_t key = (uint64_t)type | (uint64_t)level << 8 | (uint64_t)nlevels << 16 | (uint64_t)layer << 24 | (uint64_t)nlayers << 44 | (uint64_t)(mode & 3) << 62;
    uint64_t swzkey = swz ? (swz[0] & 15) | (swz[1] & 15) << 4 | (swz[2] & 15) << 8 | (swz[3] & 15) << 12 : 0;
    key ^= swzkey * 0x9E3779B97F4A7C15ull;
    if (mode & 4) key ^= 0x5851F42D4C957F2Dull;   /* the stencil aspect */
    for (uint32_t i = 0; i < im->nviews; i++) if (im->views[i].key == key) return im->views[i].v;
    VkImageViewCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = im->image, .viewType = type, .format = im->format,
                                 .components = { swz ? swz[0] : 0, swz ? swz[1] : 0, swz ? swz[2] : 0, swz ? swz[3] : 0 },
                                 .subresourceRange = { .aspectMask = (mode & 4) && (im->aspect & VK_IMAGE_ASPECT_STENCIL_BIT) ? VK_IMAGE_ASPECT_STENCIL_BIT : (mode & 2) ? im->aspect : im->aspect & VK_IMAGE_ASPECT_DEPTH_BIT ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT, .baseMipLevel = level, .levelCount = nlevels, .baseArrayLayer = layer, .layerCount = nlayers } };
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

/* One region of one level of one layer (or of a 3D image, its slices z..z+d) read back, tightly packed (blocks for compressed formats): the copy of a texture to a buffer */
int nx2gpu_read_region(uint32_t id, void *out, uint64_t bytes, uint32_t level, uint32_t layer, int32_t x, int32_t y, int32_t z, uint32_t w, uint32_t h, uint32_t d)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || id >= G.nimages || !G.images[id].live || bytes > STAGING_BYTES) goto out;
    if (begin()) goto out;
    end_rendering();
    barrier_all();
    Image *im = &G.images[id];
    VkBufferImageCopy c = { .imageSubresource = { .aspectMask = im->aspect & VK_IMAGE_ASPECT_DEPTH_BIT ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT, .mipLevel = level, .baseArrayLayer = im->is3d ? 0 : layer, .layerCount = 1 },
                            .imageOffset = { x, y, z }, .imageExtent = { w, h, d ? d : 1 } };
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
static uint64_t layout_key(const Nx2Buf *bufs, uint32_t nb, const Nx2Tex *tex, uint32_t nt, const Nx2Img *imgs, uint32_t ni)
{
    uint64_t h = 1469598103934665603ull;
    for (uint32_t i = 0; i < nb; i++) { uint32_t v[2] = { bufs[i].binding, bufs[i].kind }; h = fnv(h, v, 8); }
    for (uint32_t i = 0; i < nt; i++) { uint32_t v = tex[i].binding | 0x80000000u; h = fnv(h, &v, 4); }
    for (uint32_t i = 0; i < ni; i++) { uint32_t v = imgs[i].binding | 0x40000000u; h = fnv(h, &v, 4); }
    return h;
}

/* The push descriptor set layout and pipeline layout of a set of resources */
static int make_layouts(const Nx2Buf *bufs, uint32_t nb, const Nx2Tex *tex, uint32_t nt, const Nx2Img *imgs, uint32_t ni, VkDescriptorSetLayout *sl, VkPipelineLayout *pl)
{
    VkDescriptorSetLayoutBinding lb[96]; uint32_t nlb = 0;
    for (uint32_t i = 0; i < nb && nlb < 96; i++)
        lb[nlb++] = (VkDescriptorSetLayoutBinding){ .binding = bufs[i].binding, .descriptorType = bufs[i].kind ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_ALL };
    for (uint32_t i = 0; i < nt && nlb < 96; i++)
        lb[nlb++] = (VkDescriptorSetLayoutBinding){ .binding = tex[i].binding, .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_ALL };
    for (uint32_t i = 0; i < ni && nlb < 96; i++)
        lb[nlb++] = (VkDescriptorSetLayoutBinding){ .binding = imgs[i].binding, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_ALL };
    VkDescriptorSetLayoutCreateInfo sli = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR, .bindingCount = nlb, .pBindings = lb };
    if (vkCreateDescriptorSetLayout(G.dev, &sli, NULL, sl) != VK_SUCCESS) return -1;
    VkPipelineLayoutCreateInfo pli = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1, .pSetLayouts = sl };
    if (vkCreatePipelineLayout(G.dev, &pli, NULL, pl) != VK_SUCCESS) return -1;
    return 0;
}

static uint64_t ring_alloc(uint64_t bytes, uint64_t align, uint8_t **ptr);

/* The descriptor writes of a draw or dispatch: buffers copied into the ring (bufs[i].ring_offset says where), textures and storage images through their views. 0, or 1 when a resource is missing */
static int fill_writes(Nx2Buf *bufs, uint32_t nb, const Nx2Tex *tex, uint32_t nt, const Nx2Img *imgs, uint32_t ni, VkDescriptorBufferInfo *bi, VkDescriptorImageInfo *ii, VkWriteDescriptorSet *wr, uint32_t *nw_out)
{
    uint32_t nw = 0;
    for (uint32_t i = 0; i < nb; i++) {
        uint8_t *p; uint64_t sz = bufs[i].size ? bufs[i].size : 16;
        uint64_t o = ring_alloc(sz, G.ubo_align, &p);
        if (o == UINT64_MAX) return 1;
        memcpy(p, bufs[i].data, bufs[i].size);
        bufs[i].ring_offset = o;
        bi[i] = (VkDescriptorBufferInfo){ G.ring, o, sz };
        wr[nw++] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = bufs[i].binding, .descriptorCount = 1,
                                           .descriptorType = bufs[i].kind ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, .pBufferInfo = &bi[i] };
    }
    for (uint32_t i = 0; i < nt; i++) {
        uint32_t id = tex[i].image;
        if (id >= G.nimages || !G.images[id].live) return 1;
        VkSampler s = sampler_for(&tex[i]);
        if (!s) return -1;
        VkImageView v = view_of(&G.images[id], (VkImageViewType)tex[i].view_type, tex[i].base_level, tex[i].nlevels, tex[i].base_layer, tex[i].nlayers, tex[i].aspect ? 4 : 0, tex[i].swz);
        if (!v) return -1;
        ii[i] = (VkDescriptorImageInfo){ s, v, VK_IMAGE_LAYOUT_GENERAL };
        wr[nw++] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = tex[i].binding, .descriptorCount = 1,
                                           .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .pImageInfo = &ii[i] };
    }
    for (uint32_t i = 0; i < ni; i++) {
        uint32_t id = imgs[i].image;
        if (id >= G.nimages || !G.images[id].live || !G.images[id].storage) return 1;
        VkImageView v = view_of(&G.images[id], (VkImageViewType)imgs[i].view_type, imgs[i].level, 1, imgs[i].base_layer, imgs[i].nlayers, 0, NULL);
        if (!v) return -1;
        ii[nt + i] = (VkDescriptorImageInfo){ VK_NULL_HANDLE, v, VK_IMAGE_LAYOUT_GENERAL };
        wr[nw++] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = imgs[i].binding, .descriptorCount = 1,
                                           .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .pImageInfo = &ii[nt + i] };
    }
    *nw_out = nw;
    return 0;
}

static int has_stencil(VkFormat f) { return f == VK_FORMAT_D32_SFLOAT_S8_UINT || f == VK_FORMAT_D24_UNORM_S8_UINT; }

/* primitive restart is a property of the pipeline for the strip and fan topologies (the list topologies, and the ones drawn through generated indices, do not restart) */
static uint32_t restarts(const Nx2Draw *d, uint32_t topology)
{
    return d->restart_enable && (topology == VK_PRIMITIVE_TOPOLOGY_LINE_STRIP || topology == VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP || topology == VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN || topology == VK_PRIMITIVE_TOPOLOGY_LINE_STRIP_WITH_ADJACENCY || topology == VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP_WITH_ADJACENCY) && d->index_type && !GENERATED(d->topology);
}

static Pipe *pipeline_for(const Nx2Draw *d, VkFormat color_fmt[MAX_TARGETS], VkFormat depth_fmt, uint32_t topology, VkPipelineLayout *layout_out)
{
    uint64_t key = layout_key(d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs);
    key = fnv(key, &d->vs, 6 * 4);   /* the shaders and the patch size */
    key = fnv(key, color_fmt, sizeof(VkFormat) * d->ntargets); key = fnv(key, &depth_fmt, 4); key = fnv(key, &topology, 4);
    key = fnv(key, d->attrs, sizeof(Nx2Attr) * d->nattrs);
    for (uint32_t i = 0; i < d->nstreams; i++) { uint32_t v[3] = { d->streams[i].binding, d->streams[i].stride, d->streams[i].divisor }; key = fnv(key, v, 12); }
    key = fnv(key, d->blend, sizeof(Nx2Blend) * d->ntargets);
    key = fnv(key, &d->cull, 8 * 4);
    key = fnv(key, &d->stencil_test, 2 * 4); key = fnv(key, d->stencil_front, 8 * 4);
    { uint32_t rk[2] = { restarts(d, topology), d->rasterizer_discard }; key = fnv(key, rk, 8); }
    for (uint32_t i = 0; i < G.npipes; i++) if (G.pipes[i].key == key) { *layout_out = G.pipes[i].layout; return &G.pipes[i]; }

    VkDescriptorSetLayout sl; VkPipelineLayout pl;
    if (make_layouts(d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs, &sl, &pl)) return NULL;

    VkPipelineShaderStageCreateInfo st[5]; uint32_t nst = 0;
    const struct { uint32_t id; VkShaderStageFlagBits stage; } stages[5] = { { d->vs, VK_SHADER_STAGE_VERTEX_BIT }, { d->tcs, VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT },
                                                                              { d->tes, VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT }, { d->gs, VK_SHADER_STAGE_GEOMETRY_BIT }, { d->ps, VK_SHADER_STAGE_FRAGMENT_BIT } };
    for (int i = 0; i < 5; i++)
        if (stages[i].id) st[nst++] = (VkPipelineShaderStageCreateInfo){ .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = stages[i].stage, .module = G.shaders[stages[i].id].mod, .pName = "main" };
    VkVertexInputBindingDescription vb[16]; VkVertexInputAttributeDescription va[16];
    for (uint32_t i = 0; i < d->nstreams; i++) vb[i] = (VkVertexInputBindingDescription){ d->streams[i].binding, d->streams[i].stride, d->streams[i].divisor ? VK_VERTEX_INPUT_RATE_INSTANCE : VK_VERTEX_INPUT_RATE_VERTEX };
    for (uint32_t i = 0; i < d->nattrs; i++) va[i] = (VkVertexInputAttributeDescription){ d->attrs[i].location, d->attrs[i].binding, (VkFormat)d->attrs[i].format, d->attrs[i].offset };
    VkPipelineVertexInputStateCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO, .vertexBindingDescriptionCount = d->nstreams, .pVertexBindingDescriptions = vb,
                                                .vertexAttributeDescriptionCount = d->nattrs, .pVertexAttributeDescriptions = va };
    VkPipelineInputAssemblyStateCreateInfo ia = { .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO, .topology = (VkPrimitiveTopology)topology, .primitiveRestartEnable = restarts(d, topology) };
    VkPipelineTessellationStateCreateInfo tess = { .sType = VK_STRUCTURE_TYPE_PIPELINE_TESSELLATION_STATE_CREATE_INFO, .patchControlPoints = d->patch_points ? d->patch_points : 3 };
    VkPipelineViewportDepthClipControlCreateInfoEXT ccs = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_DEPTH_CLIP_CONTROL_CREATE_INFO_EXT, .negativeOneToOne = VK_TRUE };
    VkPipelineViewportStateCreateInfo vp = { .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO, .pNext = G.clip_control && d->depth_neg_one ? &ccs : NULL, .viewportCount = 1, .scissorCount = 1 };
    VkPipelineRasterizationStateCreateInfo rs = { .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO, .depthClampEnable = d->depth_clamp, .rasterizerDiscardEnable = d->rasterizer_discard, .polygonMode = (VkPolygonMode)d->polygon_mode,
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
    VkDynamicState dyn[] = { VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR, VK_DYNAMIC_STATE_BLEND_CONSTANTS, VK_DYNAMIC_STATE_DEPTH_BIAS, VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK, VK_DYNAMIC_STATE_STENCIL_WRITE_MASK, VK_DYNAMIC_STATE_STENCIL_REFERENCE, VK_DYNAMIC_STATE_LINE_WIDTH };
    VkPipelineDynamicStateCreateInfo dy = { .sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO, .dynamicStateCount = 8, .pDynamicStates = dyn };
    VkPipelineRenderingCreateInfo ri = { .sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO, .colorAttachmentCount = d->ntargets, .pColorAttachmentFormats = color_fmt,
                                         .depthAttachmentFormat = depth_fmt, .stencilAttachmentFormat = has_stencil(depth_fmt) ? depth_fmt : VK_FORMAT_UNDEFINED };
    VkGraphicsPipelineCreateInfo gi = { .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &ri, .stageCount = nst, .pStages = st, .pTessellationState = d->tcs || d->tes ? &tess : NULL, .pVertexInputState = &vi, .pInputAssemblyState = &ia,
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
    case 4: case QUADS: case QUAD_STRIP: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    case LINE_LOOP: return VK_PRIMITIVE_TOPOLOGY_LINE_STRIP;
    case 5: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP;
    case 6: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN;
    case 10: return VK_PRIMITIVE_TOPOLOGY_LINE_LIST_WITH_ADJACENCY;
    case 11: return VK_PRIMITIVE_TOPOLOGY_LINE_STRIP_WITH_ADJACENCY;
    case 12: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST_WITH_ADJACENCY;
    case 13: return VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP_WITH_ADJACENCY;
    case 14: return VK_PRIMITIVE_TOPOLOGY_PATCH_LIST;
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
    if (!G.open || !d->vs || d->vs >= G.nshaders || (d->ps && d->ps >= G.nshaders) || (d->tcs && d->tcs >= G.nshaders) || (d->tes && d->tes >= G.nshaders) || (d->gs && d->gs >= G.nshaders) || d->ntargets > MAX_TARGETS) goto out;
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
    if (d->index_type || GENERATED(d->topology)) {
        uint32_t isz = d->index_type == 1 ? 2 : 4;
        uint32_t src_n = d->index_type ? (uint32_t)(d->index_size / isz) : count;
        if (d->index_type && src_n > count) src_n = count;
        /* the vertex i of the draw: the index buffer's entry, or first + i without one */
        #define VTX(i) (!d->index_type ? (uint32_t)d->first + (i) : isz == 2 ? ((const uint16_t *)d->index_data)[i] : ((const uint32_t *)d->index_data)[i])
        uint32_t out_n = d->topology == QUADS ? src_n / 4 * 6 : d->topology == QUAD_STRIP ? (src_n >= 4 ? (src_n - 2) / 2 * 6 : 0) : d->topology == LINE_LOOP ? (src_n >= 2 ? src_n + 1 : 0) : src_n;
        if (GENERATED(d->topology) && !out_n) { r = 0; goto out; }   /* too few vertices for one primitive: nothing is drawn */
        uint8_t *p; uint64_t need = (uint64_t)out_n * (GENERATED(d->topology) ? 4 : isz);
        ib_off = ring_alloc(need ? need : 4, 4, &p);
        if (ib_off == UINT64_MAX) { if (flush_locked() || begin()) goto out; ib_off = ring_alloc(need ? need : 4, 4, &p); if (ib_off == UINT64_MAX) { G.skipped++; r = 1; goto out; } }
        if (GENERATED(d->topology)) {
            uint32_t *o = (uint32_t *)p;
            if (d->topology == QUADS) {
                for (uint32_t q = 0; q < src_n / 4; q++) {
                    uint32_t v0 = VTX(q * 4), v1 = VTX(q * 4 + 1), v2 = VTX(q * 4 + 2), v3 = VTX(q * 4 + 3);
                    o[q * 6 + 0] = v0; o[q * 6 + 1] = v1; o[q * 6 + 2] = v2; o[q * 6 + 3] = v0; o[q * 6 + 4] = v2; o[q * 6 + 5] = v3;
                }
            } else if (d->topology == QUAD_STRIP) {
                for (uint32_t q = 0; q < (src_n - 2) / 2; q++) {
                    uint32_t a = VTX(2 * q), b = VTX(2 * q + 1), c = VTX(2 * q + 3), e = VTX(2 * q + 2);   /* the quad is a, b, c, e in this order */
                    o[q * 6 + 0] = a; o[q * 6 + 1] = b; o[q * 6 + 2] = c; o[q * 6 + 3] = a; o[q * 6 + 4] = c; o[q * 6 + 5] = e;
                }
            } else {
                for (uint32_t i = 0; i < src_n; i++) o[i] = VTX(i);
                o[src_n] = VTX(0);
            }
            itype = VK_INDEX_TYPE_UINT32; index_count = out_n;
        } else {
            memcpy(p, d->index_data, (size_t)src_n * isz); itype = isz == 2 ? VK_INDEX_TYPE_UINT16 : VK_INDEX_TYPE_UINT32; index_count = src_n;
            if (restarts(d, topo) && d->restart_index != (isz == 2 ? 0xFFFFu : 0xFFFFFFFFu)) {   /* Vulkan restarts on the all-ones index: the game's own restart index becomes that one */
                for (uint32_t i = 0; i < src_n; i++) {
                    if (isz == 2) { if (((uint16_t *)p)[i] == (uint16_t)d->restart_index) ((uint16_t *)p)[i] = 0xFFFF; }
                    else if (((uint32_t *)p)[i] == d->restart_index) ((uint32_t *)p)[i] = 0xFFFFFFFFu;
                }
            }
        }
        #undef VTX
    }
    VkDescriptorBufferInfo bi[32]; VkDescriptorImageInfo ii[40]; VkWriteDescriptorSet wr[72]; uint32_t nw = 0;
    { int fr = fill_writes((Nx2Buf *)d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs, bi, ii, wr, &nw); if (fr) { if (fr > 0) { G.skipped++; r = 1; } goto out; } }

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
            VkImageView v = view_of(ti, VK_IMAGE_VIEW_TYPE_2D, d->targets[i].level, 1, d->targets[i].layer, 1, 2, NULL);
            if (!v) goto out;
            ca[i] = (VkRenderingAttachmentInfo){ .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = v, .imageLayout = VK_IMAGE_LAYOUT_GENERAL, .loadOp = VK_ATTACHMENT_LOAD_OP_LOAD, .storeOp = VK_ATTACHMENT_STORE_OP_STORE };
        }
        if (d->depth.image) {
            VkImageView v = view_of(&G.images[d->depth.image], VK_IMAGE_VIEW_TYPE_2D, d->depth.level, 1, d->depth.layer, 1, 2, NULL);
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
    vkCmdSetLineWidth(G.cb, d->line_width > 0.0f && G.wide_lines ? fminf(fmaxf(d->line_width, G.line_min), G.line_max) : 1.0f);
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

/* ---- the window: a swapchain of this device, so a frame goes from the game's image to the screen on the GPU (no copy through the CPU) ---- */
static struct {
    Nx2LinuxHost host; int open; VkSurfaceKHR surface; VkSwapchainKHR sc; VkFormat format; VkExtent2D extent; uint32_t nimg; VkImage imgs[8]; int srgb;
    VkSemaphore acquire, done; uint64_t frames;
} W;

static void window_drop_swapchain(void)
{
    if (W.sc) { vkDeviceWaitIdle(G.dev); vkDestroySwapchainKHR(G.dev, W.sc, NULL); W.sc = VK_NULL_HANDLE; }
}

/* The present mode. FIFO (what the display refresh paces, and the one every driver has) unless NX2LINUX_PRESENT_MODE names another that the surface supports: fifo, relaxed, mailbox or immediate.
 * It exists to tell whether the display's pacing is what limits the frame rate of a run: a measurement with `immediate` shows what a run does without it. Never the default.
 * have: the modes the surface reports (VkPresentModeKHR values: immediate 0, mailbox 1, fifo 2, fifo relaxed 3). Returns the mode to use, and in *used the name of what was chosen and why. */
int nx2gpu_pick_present_mode(const char *want, const int *have, int n, const char **used)
{
    static const struct { const char *name; int mode; } known[] = { { "fifo", 2 }, { "relaxed", 3 }, { "mailbox", 1 }, { "immediate", 0 } };
    if (!want || !*want) { *used = "fifo"; return 2; }
    for (unsigned k = 0; k < sizeof known / sizeof known[0]; k++) {
        if (strcmp(want, known[k].name)) continue;
        for (int i = 0; i < n; i++) if (have[i] == known[k].mode) { *used = known[k].name; return known[k].mode; }
        *used = "fifo (the surface does not support the mode asked for)"; return 2;
    }
    *used = "fifo (NX2LINUX_PRESENT_MODE is not fifo, relaxed, mailbox or immediate)"; return 2;
}

static int window_make_swapchain(int srgb)
{
    VkSurfaceCapabilitiesKHR caps;
    VK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(G.phys, W.surface, &caps));
    uint32_t nf = 0; vkGetPhysicalDeviceSurfaceFormatsKHR(G.phys, W.surface, &nf, NULL);
    VkSurfaceFormatKHR fmts[32]; if (nf > 32) nf = 32;
    vkGetPhysicalDeviceSurfaceFormatsKHR(G.phys, W.surface, &nf, fmts);
    VkSurfaceFormatKHR pick = { VK_FORMAT_UNDEFINED, 0 };
    const VkFormat want[2][2] = { { VK_FORMAT_B8G8R8A8_UNORM, VK_FORMAT_R8G8B8A8_UNORM }, { VK_FORMAT_B8G8R8A8_SRGB, VK_FORMAT_R8G8B8A8_SRGB } };
    for (int k = 0; k < 2 && pick.format == VK_FORMAT_UNDEFINED; k++) for (uint32_t i = 0; i < nf; i++) if (fmts[i].format == want[srgb][k]) { pick = fmts[i]; break; }
    if (pick.format == VK_FORMAT_UNDEFINED) { snprintf(G.err, sizeof G.err, "the surface has no 8-bit format"); return -1; }
    VkExtent2D ext = caps.currentExtent;
    if (ext.width == 0xFFFFFFFFu) { ext.width = 1280; ext.height = 720; }
    uint32_t npm = 0; VkPresentModeKHR modes[8]; int have[8];
    vkGetPhysicalDeviceSurfacePresentModesKHR(G.phys, W.surface, &npm, NULL); if (npm > 8) npm = 8;
    vkGetPhysicalDeviceSurfacePresentModesKHR(G.phys, W.surface, &npm, modes);
    for (uint32_t i = 0; i < npm; i++) have[i] = (int)modes[i];
    const char *mode_used = NULL; const char *asked = getenv("NX2LINUX_PRESENT_MODE");
    VkPresentModeKHR present_mode = (VkPresentModeKHR)nx2gpu_pick_present_mode(asked, have, (int)npm, &mode_used);
    if (asked && *asked) fprintf(stderr, "[gpu] present mode: %s\n", mode_used);
    window_drop_swapchain();
    VkSwapchainCreateInfoKHR ci = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, .surface = W.surface, .minImageCount = caps.minImageCount + 1 > 8 ? 8 : caps.minImageCount + 1, .imageFormat = pick.format, .imageColorSpace = pick.colorSpace,
                                    .imageExtent = ext, .imageArrayLayers = 1, .imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE,
                                    .preTransform = caps.currentTransform, .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR, .presentMode = present_mode, .clipped = VK_TRUE };
    if (ci.minImageCount > caps.maxImageCount && caps.maxImageCount) ci.minImageCount = caps.maxImageCount;
    VK(vkCreateSwapchainKHR(G.dev, &ci, NULL, &W.sc));
    W.nimg = 8; VK(vkGetSwapchainImagesKHR(G.dev, W.sc, &W.nimg, W.imgs));
    W.format = pick.format; W.extent = ext; W.srgb = srgb;
    return 0;
}

/* Opens the window (on the calling thread: SDL wants events and window on one). 0, or -1 with nx2gpu_error */
int nx2gpu_window_open(const char *title, int width, int height, int hidden)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || !G.have_swapchain) { snprintf(G.err, sizeof G.err, "this Vulkan device or loader has no window system integration"); goto out; }
    if (W.open) { r = 0; goto out; }
    const Nx2LinuxHostConfig cfg = { .title = title ? title : "NX2Linux", .width = width, .height = height, .hidden = hidden != 0, .vulkan = true };
    if (nx2linux_host_init(&W.host, &cfg) != 0) { snprintf(G.err, sizeof G.err, "window: %s", SDL_GetError()); goto out; }
    if (!SDL_Vulkan_CreateSurface(nx2linux_host_window(&W.host), G.inst, NULL, &W.surface)) { snprintf(G.err, sizeof G.err, "surface: %s", SDL_GetError()); nx2linux_host_shutdown(&W.host); goto out; }
    VkBool32 ok = VK_FALSE;
    vkGetPhysicalDeviceSurfaceSupportKHR(G.phys, G.family, W.surface, &ok);
    if (!ok) { snprintf(G.err, sizeof G.err, "the graphics queue cannot present to this window"); goto out; }
    VkSemaphoreCreateInfo si = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    if (vkCreateSemaphore(G.dev, &si, NULL, &W.acquire) != VK_SUCCESS || vkCreateSemaphore(G.dev, &si, NULL, &W.done) != VK_SUCCESS) goto out;
    W.open = 1; r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

/* The next input event as four numbers (nx2linux_event_pack in sdl_host.h says what they are for each type); 0 when none is waiting */
int nx2gpu_window_poll(int32_t out[4])
{
    Nx2LinuxInputEvent ev;
    if (!W.open || nx2linux_host_poll_event(&W.host, &ev) != 1) return 0;
    nx2linux_event_pack(&ev, out);
    return 1;
}

uint64_t nx2gpu_window_frames(void) { return W.frames; }

/* Shows an image (level 0, layer 0 of a colour image) in the window, scaled to it by a blit: everything recorded so far runs first, in the same submission. 0, or -1 */
int nx2gpu_present(uint32_t id)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || !W.open || id >= G.nimages || !G.images[id].live || is_depth_format(G.images[id].format)) goto out;
    Image *im = &G.images[id];
    int srgb = im->format == VK_FORMAT_R8G8B8A8_SRGB || im->format == VK_FORMAT_B8G8R8A8_SRGB;
    if ((!W.sc || W.srgb != srgb) && window_make_swapchain(srgb)) goto out;
    uint32_t idx = 0;
    for (int attempt = 0;; attempt++) {
        VkResult ar = vkAcquireNextImageKHR(G.dev, W.sc, UINT64_MAX, W.acquire, VK_NULL_HANDLE, &idx);
        if (ar == VK_SUCCESS || ar == VK_SUBOPTIMAL_KHR) break;
        if (ar != VK_ERROR_OUT_OF_DATE_KHR || attempt > 2 || window_make_swapchain(srgb)) { fail("vkAcquireNextImageKHR", ar); goto out; }
        /* the acquire semaphore of a failed acquire is unsignalled: nothing to clean */
    }
    if (begin()) goto out;
    end_rendering();
    barrier_all();
    VkImage dst = W.imgs[idx];
    VkImageMemoryBarrier2 to_dst = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2, .srcStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, .dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                     .dstAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                     .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = dst,
                                     .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } };
    VkDependencyInfo dep = { .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &to_dst };
    vkCmdPipelineBarrier2(G.cb, &dep);
    VkImageBlit blit = { .srcSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 }, .srcOffsets = { { 0, 0, 0 }, { (int32_t)im->w, (int32_t)im->h, 1 } },
                         .dstSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 }, .dstOffsets = { { 0, 0, 0 }, { (int32_t)W.extent.width, (int32_t)W.extent.height, 1 } } };
    vkCmdBlitImage(G.cb, im->image, VK_IMAGE_LAYOUT_GENERAL, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit, VK_FILTER_LINEAR);
    VkImageMemoryBarrier2 to_present = to_dst;
    to_present.srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT; to_present.dstAccessMask = 0; to_present.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL; to_present.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    dep.pImageMemoryBarriers = &to_present;
    vkCmdPipelineBarrier2(G.cb, &dep);
    if (submit_locked(W.acquire, W.done)) goto out;
    VkPresentInfoKHR pi = { .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .waitSemaphoreCount = 1, .pWaitSemaphores = &W.done, .swapchainCount = 1, .pSwapchains = &W.sc, .pImageIndices = &idx };
    VkResult pr = vkQueuePresentKHR(G.queue, &pi);
    if (pr == VK_ERROR_OUT_OF_DATE_KHR || pr == VK_SUBOPTIMAL_KHR) window_make_swapchain(srgb);   /* resized: the next frame uses the new one */
    else if (pr != VK_SUCCESS) { fail("vkQueuePresentKHR", pr); goto out; }
    W.frames++;
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

/* The bytes of the ring at an offset, after a flush: what a shader wrote to the storage buffers of the last draw or dispatch (the host writes them back to the game's memory) */
int nx2gpu_ring_read(uint64_t offset, void *out, uint64_t bytes)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (G.open && !G.recording && offset + bytes <= RING_BYTES) { memcpy(out, G.ring_ptr + offset, bytes); r = 0; }
    pthread_mutex_unlock(&G.mu);
    return r;
}

/* A compute dispatch. 0: recorded, 1: skipped, -1: error */
int nx2gpu_dispatch(Nx2Dispatch *d)
{
    pthread_mutex_lock(&G.mu);
    int r = -1;
    if (!G.open || !d->cs || d->cs >= G.nshaders || begin()) goto out;
    {
        uint64_t need = 4096;
        for (uint32_t i = 0; i < d->nbufs; i++) need += d->bufs[i].size + G.ubo_align;
        if (need > RING_BYTES) { G.skipped++; r = 1; goto out; }
        if (G.ring_off + need > RING_BYTES && (flush_locked() || begin())) goto out;
    }
    VkDescriptorBufferInfo bi[32]; VkDescriptorImageInfo ii[40]; VkWriteDescriptorSet wr[72]; uint32_t nw = 0;
    { int fr = fill_writes(d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs, bi, ii, wr, &nw); if (fr) { if (fr > 0) { G.skipped++; r = 1; } goto out; } }
    uint64_t key = fnv(layout_key(d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs), &d->cs, 4);
    VkPipeline pipe = VK_NULL_HANDLE; VkPipelineLayout layout = VK_NULL_HANDLE;
    for (uint32_t i = 0; i < G.ncpipes; i++) if (G.cpipe_key[i] == key) { pipe = G.cpipe[i]; layout = G.cpipe_layout[i]; }
    if (!pipe) {
        VkDescriptorSetLayout sl;
        if (G.ncpipes >= 256 || make_layouts(d->bufs, d->nbufs, d->tex, d->ntex, d->imgs, d->nimgs, &sl, &layout)) goto out;
        VkComputePipelineCreateInfo ci = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, .layout = layout,
                                           .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = G.shaders[d->cs].mod, .pName = "main" } };
        VkResult vr = vkCreateComputePipelines(G.dev, VK_NULL_HANDLE, 1, &ci, NULL, &pipe);
        if (vr != VK_SUCCESS) { fail("vkCreateComputePipelines", vr); goto out; }
        G.cpipe_key[G.ncpipes] = key; G.cpipe[G.ncpipes] = pipe; G.cpipe_layout[G.ncpipes] = layout; G.ncpipes++;
    }
    end_rendering();
    barrier_all();
    vkCmdBindPipeline(G.cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    for (uint32_t i = 0; i < nw; i++) wr[i].dstSet = VK_NULL_HANDLE;
    if (nw) G.push(G.cb, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, nw, wr);
    vkCmdDispatch(G.cb, d->groups[0], d->groups[1], d->groups[2]);
    barrier_all();
    G.draws++;
    r = 0;
out:
    pthread_mutex_unlock(&G.mu);
    return r;
}

void nx2gpu_stats(uint64_t *out) { out[0] = G.draws; out[1] = G.flushes; out[2] = G.skipped; out[3] = G.nimages; out[4] = G.nshaders; out[5] = G.npipes; }
