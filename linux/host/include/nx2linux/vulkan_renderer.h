#ifndef NX2LINUX_VULKAN_RENDERER_H
#define NX2LINUX_VULKAN_RENDERER_H

#include <stdbool.h>

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
    bool initialized;
} Nx2LinuxVulkanRenderer;

/* shader_dir must contain triangle.vert.spv and triangle.frag.spv. */
int nx2linux_vulkan_init(Nx2LinuxVulkanRenderer *renderer,
                         SDL_Window *window,
                         const char *shader_dir);

/* Draws one frame. Returns 0 on success, 1 when the swapchain needs recreation, -1 on error. */
int nx2linux_vulkan_draw_triangle(Nx2LinuxVulkanRenderer *renderer);

void nx2linux_vulkan_shutdown(Nx2LinuxVulkanRenderer *renderer);

const char *nx2linux_vulkan_gpu_name(const Nx2LinuxVulkanRenderer *renderer);

#endif
