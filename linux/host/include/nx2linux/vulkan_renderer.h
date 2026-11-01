#ifndef NX2LINUX_VULKAN_RENDERER_H
#define NX2LINUX_VULKAN_RENDERER_H

#include <stdbool.h>
#include <stdatomic.h>

#include <vulkan/vulkan.h>

#include "nx2linux/sdl_host.h"

typedef struct Nx2LinuxVulkanRenderer {
    SDL_Window *window;
    VkInstance instance;
    VkDebugUtilsMessengerEXT debug_messenger;
    atomic_uint validation_errors;
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
    VkSemaphore *render_finished; /* Indexed by acquired swapchain image, not frame. */
    VkFence in_flight;
    char *shader_dir;
    uint64_t frames_rendered;
    bool initialized;
    bool fatal_error;
    bool vulkan_library_loaded;
} Nx2LinuxVulkanRenderer;

/* shader_dir must contain triangle.vert.spv and triangle.frag.spv. */
int nx2linux_vulkan_init(Nx2LinuxVulkanRenderer *renderer,
                         SDL_Window *window,
                         const char *shader_dir);

/* Draws one frame. Returns 0 on success, 1 when the swapchain needs recreation, -1 on error. */
int nx2linux_vulkan_draw_triangle(Nx2LinuxVulkanRenderer *renderer);

/* Rebuilds after resize/out-of-date. 1 means drawable suspended, -1 is fatal.
 * Surface loss requires full reinitialization, not just swapchain recreation. */
int nx2linux_vulkan_recreate_swapchain(Nx2LinuxVulkanRenderer *renderer);

void nx2linux_vulkan_shutdown(Nx2LinuxVulkanRenderer *renderer);

const char *nx2linux_vulkan_gpu_name(const Nx2LinuxVulkanRenderer *renderer);

uint64_t nx2linux_vulkan_frames_rendered(const Nx2LinuxVulkanRenderer *renderer);

#endif
