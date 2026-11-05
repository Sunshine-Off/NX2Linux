#ifndef NX2LINUX_VULKAN_RENDERER_H
#define NX2LINUX_VULKAN_RENDERER_H

#include <stdbool.h>
#include <stdint.h>

#include <vulkan/vulkan.h>

#include "nx2linux/sdl_host.h"

typedef struct Nx2LinuxVulkanRenderer {
    SDL_Window *window;
    VkInstance instance;
    VkPhysicalDevice physical_device;
    VkDevice device;
    VkQueue graphics_queue;
    VkQueue present_queue;
    VkSurfaceKHR surface;
    VkSwapchainKHR swapchain;
    VkFormat swapchain_format;
    VkExtent2D swapchain_extent;
    uint32_t graphics_family;
    uint32_t present_family;
    uint32_t image_count;
    VkImage *images;
    VkImageView *image_views;
    VkFramebuffer *framebuffers;
    VkRenderPass render_pass;
    VkPipelineLayout pipeline_layout;
    VkPipeline pipeline;
    VkCommandPool command_pool;
    VkCommandBuffer command_buffer;
    VkSemaphore image_available;
    VkSemaphore render_finished;
    VkFence in_flight;
    char *shader_dir;
    uint64_t frames_rendered;
    bool initialized;
} Nx2LinuxVulkanRenderer;

/* shader_dir must contain triangle.vert.spv and triangle.frag.spv. */
int nx2linux_vulkan_init(Nx2LinuxVulkanRenderer *renderer,
                         SDL_Window *window,
                         const char *shader_dir);

/* Draws one frame. Returns 0 on success, 1 when the swapchain needs recreation, -1 on error. */
int nx2linux_vulkan_draw_triangle(Nx2LinuxVulkanRenderer *renderer);

/* Shows a frame: width x height RGBA8 pixels (stride bytes per row, 0: tightly packed), scaled to the
 * swapchain by nearest sampling and copied to it. Same return codes as nx2linux_vulkan_draw_triangle. */
int nx2linux_vulkan_present_rgba(Nx2LinuxVulkanRenderer *renderer, const uint8_t *pixels,
                                 uint32_t width, uint32_t height, uint32_t stride);

/* The CPU part of the above, on its own: nearest-neighbour scale of RGBA8 (src_stride bytes per row)
 * to dst_w x dst_h, optionally swapping to BGRA, alpha forced opaque. */
void nx2linux_scale_rgba(const uint8_t *src, uint32_t src_w, uint32_t src_h, uint32_t src_stride,
                         uint8_t *dst, uint32_t dst_w, uint32_t dst_h, bool bgra);

/* Rebuilds swapchain-dependent resources after a resize or surface loss. */
int nx2linux_vulkan_recreate_swapchain(Nx2LinuxVulkanRenderer *renderer);

void nx2linux_vulkan_shutdown(Nx2LinuxVulkanRenderer *renderer);

const char *nx2linux_vulkan_gpu_name(const Nx2LinuxVulkanRenderer *renderer);

uint64_t nx2linux_vulkan_frames_rendered(const Nx2LinuxVulkanRenderer *renderer);

#endif
