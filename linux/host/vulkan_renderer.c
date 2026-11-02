#include "nx2linux/vulkan_renderer.h"

#include <SDL3/SDL_vulkan.h>

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct QueueFamilies {
    uint32_t graphics;
    uint32_t present;
    bool graphics_found;
    bool present_found;
} QueueFamilies;

static int fail(const char *message, VkResult result)
{
    char text[160];
    snprintf(text, sizeof(text), "%s (Vulkan error %d)", message, result);
    SDL_SetError("%s", text);
    return -1;
}

static QueueFamilies find_queue_families(VkPhysicalDevice device, VkSurfaceKHR surface)
{
    QueueFamilies families = {0};
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(device, &count, NULL);
    VkQueueFamilyProperties *properties = calloc(count, sizeof(*properties));
    if (properties == NULL) {
        return families;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(device, &count, properties);
    for (uint32_t i = 0; i < count; ++i) {
        if ((properties[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) != 0) {
            families.graphics = i;
            families.graphics_found = true;
        }
        VkBool32 present = VK_FALSE;
        vkGetPhysicalDeviceSurfaceSupportKHR(device, i, surface, &present);
        if (present == VK_TRUE) {
            families.present = i;
            families.present_found = true;
        }
    }
    free(properties);
    return families;
}

static bool has_swapchain_extension(VkPhysicalDevice device)
{
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(device, NULL, &count, NULL) != VK_SUCCESS) {
        return false;
    }
    VkExtensionProperties *extensions = calloc(count, sizeof(*extensions));
    if (extensions == NULL) {
        return false;
    }
    bool found = false;
    if (vkEnumerateDeviceExtensionProperties(device, NULL, &count, extensions) == VK_SUCCESS) {
        for (uint32_t i = 0; i < count; ++i) {
            if (strcmp(extensions[i].extensionName, VK_KHR_SWAPCHAIN_EXTENSION_NAME) == 0) {
                found = true;
                break;
            }
        }
    }
    free(extensions);
    return found;
}

static bool choose_device(Nx2LinuxVulkanRenderer *renderer)
{
    uint32_t count = 0;
    if (vkEnumeratePhysicalDevices(renderer->instance, &count, NULL) != VK_SUCCESS || count == 0) {
        return false;
    }
    VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
    if (devices == NULL) {
        return false;
    }
    bool found = false;
    vkEnumeratePhysicalDevices(renderer->instance, &count, devices);
    for (uint32_t i = 0; i < count; ++i) {
        QueueFamilies families = find_queue_families(devices[i], renderer->surface);
        if (!families.graphics_found || !families.present_found || !has_swapchain_extension(devices[i])) {
            continue;
        }
        renderer->physical_device = devices[i];
        renderer->graphics_family = families.graphics;
        renderer->present_family = families.present;
        found = true;
        break;
    }
    free(devices);
    return found;
}

static VkSurfaceFormatKHR choose_format(const VkSurfaceFormatKHR *formats, uint32_t count)
{
    for (uint32_t i = 0; i < count; ++i) {
        if (formats[i].format == VK_FORMAT_B8G8R8A8_SRGB &&
            formats[i].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR) {
            return formats[i];
        }
    }
    return formats[0];
}

static VkPresentModeKHR choose_present_mode(const VkPresentModeKHR *modes, uint32_t count)
{
    for (uint32_t i = 0; i < count; ++i) {
        if (modes[i] == VK_PRESENT_MODE_MAILBOX_KHR) {
            return modes[i];
        }
    }
    return VK_PRESENT_MODE_FIFO_KHR;
}

static int load_shader(const char *path, uint32_t **words, size_t *word_count)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        SDL_SetError("cannot open shader %s", path);
        return -1;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return -1;
    }
    long size = ftell(file);
    rewind(file);
    if (size <= 0 || size % (long)sizeof(uint32_t) != 0) {
        fclose(file);
        SDL_SetError("invalid SPIR-V shader %s", path);
        return -1;
    }
    uint32_t *data = malloc((size_t)size);
    if (data == NULL || fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        SDL_SetError("cannot read shader %s", path);
        return -1;
    }
    fclose(file);
    *words = data;
    *word_count = (size_t)size / sizeof(uint32_t);
    return 0;
}

static void destroy_swapchain_objects(Nx2LinuxVulkanRenderer *renderer)
{
    if (renderer->device == VK_NULL_HANDLE) {
        return;
    }
    for (uint32_t i = 0; i < renderer->image_count; ++i) {
        if (renderer->framebuffers != NULL && renderer->framebuffers[i] != VK_NULL_HANDLE) {
            vkDestroyFramebuffer(renderer->device, renderer->framebuffers[i], NULL);
        }
        if (renderer->image_views != NULL && renderer->image_views[i] != VK_NULL_HANDLE) {
            vkDestroyImageView(renderer->device, renderer->image_views[i], NULL);
        }
    }
    free(renderer->framebuffers);
    free(renderer->image_views);
    free(renderer->images);
    renderer->framebuffers = NULL;
    renderer->image_views = NULL;
    renderer->images = NULL;
    renderer->image_count = 0;
    if (renderer->swapchain != VK_NULL_HANDLE) {
        vkDestroySwapchainKHR(renderer->device, renderer->swapchain, NULL);
        renderer->swapchain = VK_NULL_HANDLE;
    }
}

static int create_swapchain(Nx2LinuxVulkanRenderer *renderer)
{
    VkSurfaceCapabilitiesKHR capabilities;
    VkResult result = vkGetPhysicalDeviceSurfaceCapabilitiesKHR(renderer->physical_device,
                                                                  renderer->surface, &capabilities);
    if (result != VK_SUCCESS) {
        return fail("cannot query surface capabilities", result);
    }
    uint32_t format_count = 0;
    uint32_t mode_count = 0;
    vkGetPhysicalDeviceSurfaceFormatsKHR(renderer->physical_device, renderer->surface, &format_count, NULL);
    vkGetPhysicalDeviceSurfacePresentModesKHR(renderer->physical_device, renderer->surface, &mode_count, NULL);
    if (format_count == 0 || mode_count == 0) {
        SDL_SetError("Vulkan surface has no formats or present modes");
        return -1;
    }
    VkSurfaceFormatKHR *formats = calloc(format_count, sizeof(*formats));
    VkPresentModeKHR *modes = calloc(mode_count, sizeof(*modes));
    if (formats == NULL || modes == NULL) {
        free(formats);
        free(modes);
        SDL_SetError("out of memory while creating Vulkan swapchain");
        return -1;
    }
    vkGetPhysicalDeviceSurfaceFormatsKHR(renderer->physical_device, renderer->surface, &format_count, formats);
    vkGetPhysicalDeviceSurfacePresentModesKHR(renderer->physical_device, renderer->surface, &mode_count, modes);
    VkSurfaceFormatKHR format = choose_format(formats, format_count);
    VkExtent2D extent = capabilities.currentExtent;
    if (extent.width == UINT32_MAX) {
        int width = 0;
        int height = 0;
        SDL_GetWindowSizeInPixels(renderer->window, &width, &height);
        extent.width = (uint32_t)width;
        extent.height = (uint32_t)height;
    }
    if (extent.width == 0 || extent.height == 0) {
        free(formats);
        free(modes);
        SDL_SetError("Vulkan surface has no drawable extent");
        return 1;
    }
    uint32_t image_count = capabilities.minImageCount + 1;
    if (capabilities.maxImageCount != 0 && image_count > capabilities.maxImageCount) {
        image_count = capabilities.maxImageCount;
    }
    uint32_t queue_indices[] = {renderer->graphics_family, renderer->present_family};
    VkSwapchainCreateInfoKHR info = {
        .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
        .surface = renderer->surface,
        .minImageCount = image_count,
        .imageFormat = format.format,
        .imageColorSpace = format.colorSpace,
        .imageExtent = extent,
        .imageArrayLayers = 1,
        .imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                       (capabilities.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT),
        .preTransform = capabilities.currentTransform,
        .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
        .presentMode = choose_present_mode(modes, mode_count),
        .clipped = VK_TRUE,
    };
    if (renderer->graphics_family != renderer->present_family) {
        info.imageSharingMode = VK_SHARING_MODE_CONCURRENT;
        info.queueFamilyIndexCount = 2;
        info.pQueueFamilyIndices = queue_indices;
    } else {
        info.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
    }
    result = vkCreateSwapchainKHR(renderer->device, &info, NULL, &renderer->swapchain);
    free(formats);
    free(modes);
    if (result != VK_SUCCESS) {
        return fail("cannot create Vulkan swapchain", result);
    }
    renderer->swapchain_format = format.format;
    renderer->swapchain_extent = extent;
    vkGetSwapchainImagesKHR(renderer->device, renderer->swapchain, &renderer->image_count, NULL);
    renderer->images = calloc(renderer->image_count, sizeof(*renderer->images));
    renderer->image_views = calloc(renderer->image_count, sizeof(*renderer->image_views));
    renderer->framebuffers = calloc(renderer->image_count, sizeof(*renderer->framebuffers));
    if (renderer->images == NULL || renderer->image_views == NULL || renderer->framebuffers == NULL) {
        SDL_SetError("out of memory while creating Vulkan swapchain images");
        return -1;
    }
    vkGetSwapchainImagesKHR(renderer->device, renderer->swapchain, &renderer->image_count, renderer->images);
    for (uint32_t i = 0; i < renderer->image_count; ++i) {
        VkImageViewCreateInfo view = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
            .image = renderer->images[i],
            .viewType = VK_IMAGE_VIEW_TYPE_2D,
            .format = renderer->swapchain_format,
            .subresourceRange = {
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = 1,
            },
        };
        result = vkCreateImageView(renderer->device, &view, NULL, &renderer->image_views[i]);
        if (result != VK_SUCCESS) {
            return fail("cannot create Vulkan image view", result);
        }
    }
    return 0;
}

static int create_render_pass(Nx2LinuxVulkanRenderer *renderer)
{
    VkAttachmentDescription attachment = {
        .format = renderer->swapchain_format,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR,
        .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
    };
    VkAttachmentReference color_ref = {.attachment = 0, .layout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription subpass = {
        .pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
        .colorAttachmentCount = 1,
        .pColorAttachments = &color_ref,
    };
    VkRenderPassCreateInfo render_pass_info = {
        .sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
        .attachmentCount = 1,
        .pAttachments = &attachment,
        .subpassCount = 1,
        .pSubpasses = &subpass,
    };
    VkResult result = vkCreateRenderPass(renderer->device, &render_pass_info, NULL, &renderer->render_pass);
    return result == VK_SUCCESS ? 0 : fail("cannot create Vulkan render pass", result);
}

static int create_framebuffers(Nx2LinuxVulkanRenderer *renderer)
{
    for (uint32_t i = 0; i < renderer->image_count; ++i) {
        VkImageView attachments[] = {renderer->image_views[i]};
        VkFramebufferCreateInfo framebuffer = {
            .sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
            .renderPass = renderer->render_pass,
            .attachmentCount = 1,
            .pAttachments = attachments,
            .width = renderer->swapchain_extent.width,
            .height = renderer->swapchain_extent.height,
            .layers = 1,
        };
        VkResult result = vkCreateFramebuffer(renderer->device, &framebuffer, NULL,
                                               &renderer->framebuffers[i]);
        if (result != VK_SUCCESS) {
            return fail("cannot create Vulkan framebuffer", result);
        }
    }
    return 0;
}

static int create_pipeline(Nx2LinuxVulkanRenderer *renderer, const char *shader_dir)
{
    char vertex_path[PATH_MAX];
    char fragment_path[PATH_MAX];
    snprintf(vertex_path, sizeof(vertex_path), "%s/triangle.vert.spv", shader_dir);
    snprintf(fragment_path, sizeof(fragment_path), "%s/triangle.frag.spv", shader_dir);
    uint32_t *vertex_words = NULL;
    uint32_t *fragment_words = NULL;
    size_t vertex_count = 0;
    size_t fragment_count = 0;
    if (load_shader(vertex_path, &vertex_words, &vertex_count) != 0 ||
        load_shader(fragment_path, &fragment_words, &fragment_count) != 0) {
        free(vertex_words);
        free(fragment_words);
        return -1;
    }
    VkShaderModuleCreateInfo vertex_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = vertex_count * sizeof(uint32_t),
        .pCode = vertex_words,
    };
    VkShaderModuleCreateInfo fragment_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = fragment_count * sizeof(uint32_t),
        .pCode = fragment_words,
    };
    VkShaderModule vertex_module = VK_NULL_HANDLE;
    VkShaderModule fragment_module = VK_NULL_HANDLE;
    VkResult result = vkCreateShaderModule(renderer->device, &vertex_info, NULL, &vertex_module);
    if (result == VK_SUCCESS) {
        result = vkCreateShaderModule(renderer->device, &fragment_info, NULL, &fragment_module);
    }
    free(vertex_words);
    free(fragment_words);
    if (result != VK_SUCCESS) {
        if (vertex_module != VK_NULL_HANDLE) {
            vkDestroyShaderModule(renderer->device, vertex_module, NULL);
        }
        return fail("cannot create Vulkan shader module", result);
    }
    VkPipelineShaderStageCreateInfo stages[] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vertex_module, .pName = "main"},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = fragment_module, .pName = "main"},
    };
    VkPipelineVertexInputStateCreateInfo vertex_input = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
    };
    VkPipelineInputAssemblyStateCreateInfo assembly = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,
    };
    VkPipelineViewportStateCreateInfo viewport = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1,
        .scissorCount = 1,
    };
    VkPipelineRasterizationStateCreateInfo raster = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL,
        .cullMode = VK_CULL_MODE_NONE,
        .frontFace = VK_FRONT_FACE_CLOCKWISE,
        .lineWidth = 1.0f,
    };
    VkPipelineMultisampleStateCreateInfo multisample = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT,
    };
    VkPipelineColorBlendAttachmentState blend_attachment = {
        .colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT |
                          VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT,
    };
    VkPipelineColorBlendStateCreateInfo blend = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1,
        .pAttachments = &blend_attachment,
    };
    VkDynamicState dynamic_states[] = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
    VkPipelineDynamicStateCreateInfo dynamic = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO,
        .dynamicStateCount = 2,
        .pDynamicStates = dynamic_states,
    };
    VkPipelineLayoutCreateInfo layout = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
    };
    result = vkCreatePipelineLayout(renderer->device, &layout, NULL, &renderer->pipeline_layout);
    if (result == VK_SUCCESS) {
        VkGraphicsPipelineCreateInfo pipeline = {
            .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
            .stageCount = 2,
            .pStages = stages,
            .pVertexInputState = &vertex_input,
            .pInputAssemblyState = &assembly,
            .pViewportState = &viewport,
            .pRasterizationState = &raster,
            .pMultisampleState = &multisample,
            .pColorBlendState = &blend,
            .pDynamicState = &dynamic,
            .layout = renderer->pipeline_layout,
            .renderPass = renderer->render_pass,
            .subpass = 0,
        };
        result = vkCreateGraphicsPipelines(renderer->device, VK_NULL_HANDLE, 1, &pipeline, NULL,
                                           &renderer->pipeline);
    }
    vkDestroyShaderModule(renderer->device, vertex_module, NULL);
    vkDestroyShaderModule(renderer->device, fragment_module, NULL);
    if (result != VK_SUCCESS) {
        return fail("cannot create Vulkan triangle pipeline", result);
    }
    return 0;
}

int nx2linux_vulkan_init(Nx2LinuxVulkanRenderer *renderer, SDL_Window *window, const char *shader_dir)
{
    if (renderer == NULL || window == NULL || shader_dir == NULL) {
        SDL_SetError("invalid Vulkan renderer configuration");
        return -1;
    }
    memset(renderer, 0, sizeof(*renderer));
    renderer->window = window;
    size_t shader_dir_length = strlen(shader_dir) + 1;
    renderer->shader_dir = malloc(shader_dir_length);
    if (renderer->shader_dir == NULL) {
        SDL_SetError("out of memory while storing Vulkan shader path");
        return -1;
    }
    memcpy(renderer->shader_dir, shader_dir, shader_dir_length);
    if (!SDL_Vulkan_LoadLibrary(NULL)) {
        return -1;
    }
    uint32_t extension_count = 0;
    const char *const *extensions = SDL_Vulkan_GetInstanceExtensions(&extension_count);
    if (extensions == NULL) {
        return -1;
    }
    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "NX2Linux",
        .applicationVersion = VK_MAKE_VERSION(0, 1, 0),
        .pEngineName = "NX2Linux",
        .engineVersion = VK_MAKE_VERSION(0, 1, 0),
        .apiVersion = VK_API_VERSION_1_0,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
        .enabledExtensionCount = extension_count,
        .ppEnabledExtensionNames = extensions,
    };
    VkResult result = vkCreateInstance(&instance_info, NULL, &renderer->instance);
    if (result != VK_SUCCESS) {
        return fail("cannot create Vulkan instance", result);
    }
    if (!SDL_Vulkan_CreateSurface(window, renderer->instance, NULL, &renderer->surface)) {
        SDL_SetError("cannot create SDL Vulkan surface: %s", SDL_GetError());
        return -1;
    }
    if (!choose_device(renderer)) {
        SDL_SetError("no Vulkan GPU with graphics, presentation and swapchain support");
        return -1;
    }
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_infos[2] = {0};
    queue_infos[0] = (VkDeviceQueueCreateInfo){
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = renderer->graphics_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    uint32_t queue_count = 1;
    if (renderer->present_family != renderer->graphics_family) {
        queue_infos[1] = (VkDeviceQueueCreateInfo){
            .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
            .queueFamilyIndex = renderer->present_family,
            .queueCount = 1,
            .pQueuePriorities = &priority,
        };
        queue_count = 2;
    }
    const char *device_extensions[] = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = queue_count,
        .pQueueCreateInfos = queue_infos,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = device_extensions,
    };
    result = vkCreateDevice(renderer->physical_device, &device_info, NULL, &renderer->device);
    if (result != VK_SUCCESS) {
        return fail("cannot create Vulkan device", result);
    }
    vkGetDeviceQueue(renderer->device, renderer->graphics_family, 0, &renderer->graphics_queue);
    vkGetDeviceQueue(renderer->device, renderer->present_family, 0, &renderer->present_queue);
    if (create_swapchain(renderer) != 0) {
        return -1;
    }
    if (create_render_pass(renderer) != 0) {
        return -1;
    }
    if (create_pipeline(renderer, shader_dir) != 0) {
        return -1;
    }
    if (create_framebuffers(renderer) != 0) {
        return -1;
    }
    VkCommandPoolCreateInfo pool = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = renderer->graphics_family,
    };
    result = vkCreateCommandPool(renderer->device, &pool, NULL, &renderer->command_pool);
    if (result == VK_SUCCESS) {
        VkCommandBufferAllocateInfo allocation = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
            .commandPool = renderer->command_pool,
            .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
            .commandBufferCount = 1,
        };
        result = vkAllocateCommandBuffers(renderer->device, &allocation, &renderer->command_buffer);
    }
    VkSemaphoreCreateInfo semaphore = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    if (result == VK_SUCCESS) {
        result = vkCreateSemaphore(renderer->device, &semaphore, NULL, &renderer->image_available);
    }
    if (result == VK_SUCCESS) {
        result = vkCreateSemaphore(renderer->device, &semaphore, NULL, &renderer->render_finished);
    }
    VkFenceCreateInfo fence = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, .flags = VK_FENCE_CREATE_SIGNALED_BIT};
    if (result == VK_SUCCESS) {
        result = vkCreateFence(renderer->device, &fence, NULL, &renderer->in_flight);
    }
    if (result != VK_SUCCESS) {
        return fail("cannot create Vulkan frame synchronization", result);
    }
    renderer->initialized = true;
    return 0;
}

int nx2linux_vulkan_recreate_swapchain(Nx2LinuxVulkanRenderer *renderer)
{
    if (renderer == NULL || !renderer->initialized || renderer->device == VK_NULL_HANDLE) {
        return -1;
    }
    VkResult result = vkDeviceWaitIdle(renderer->device);
    if (result != VK_SUCCESS) {
        return fail("cannot wait for Vulkan swapchain recreation", result);
    }
    destroy_swapchain_objects(renderer);
    if (renderer->pipeline != VK_NULL_HANDLE) {
        vkDestroyPipeline(renderer->device, renderer->pipeline, NULL);
        renderer->pipeline = VK_NULL_HANDLE;
    }
    if (renderer->pipeline_layout != VK_NULL_HANDLE) {
        vkDestroyPipelineLayout(renderer->device, renderer->pipeline_layout, NULL);
        renderer->pipeline_layout = VK_NULL_HANDLE;
    }
    if (renderer->render_pass != VK_NULL_HANDLE) {
        vkDestroyRenderPass(renderer->device, renderer->render_pass, NULL);
        renderer->render_pass = VK_NULL_HANDLE;
    }
    int status = create_swapchain(renderer);
    if (status != 0) {
        return status;
    }
    if (create_render_pass(renderer) != 0 || create_pipeline(renderer, renderer->shader_dir) != 0 ||
        create_framebuffers(renderer) != 0) {
        return -1;
    }
    return 0;
}

int nx2linux_vulkan_draw_triangle(Nx2LinuxVulkanRenderer *renderer)
{
    if (renderer == NULL || !renderer->initialized) {
        return -1;
    }
    VkResult result = vkWaitForFences(renderer->device, 1, &renderer->in_flight, VK_TRUE, UINT64_MAX);
    if (result != VK_SUCCESS) {
        return fail("cannot wait for Vulkan frame", result);
    }
    uint32_t image_index = 0;
    result = vkAcquireNextImageKHR(renderer->device, renderer->swapchain, UINT64_MAX,
                                   renderer->image_available, VK_NULL_HANDLE, &image_index);
    if (result == VK_ERROR_OUT_OF_DATE_KHR) {
        return 1;
    }
    if (result != VK_SUCCESS && result != VK_SUBOPTIMAL_KHR) {
        return fail("cannot acquire Vulkan swapchain image", result);
    }
    vkResetFences(renderer->device, 1, &renderer->in_flight);
    vkResetCommandBuffer(renderer->command_buffer, 0);
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    result = vkBeginCommandBuffer(renderer->command_buffer, &begin);
    if (result == VK_SUCCESS) {
        VkClearValue clear = {.color = {{0.03f, 0.04f, 0.08f, 1.0f}}};
        VkRenderPassBeginInfo render = {
            .sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
            .renderPass = renderer->render_pass,
            .framebuffer = renderer->framebuffers[image_index],
            .renderArea = {.offset = {0, 0}, .extent = renderer->swapchain_extent},
            .clearValueCount = 1,
            .pClearValues = &clear,
        };
        vkCmdBeginRenderPass(renderer->command_buffer, &render, VK_SUBPASS_CONTENTS_INLINE);
        VkViewport viewport = {0.0f, 0.0f, (float)renderer->swapchain_extent.width,
                               (float)renderer->swapchain_extent.height, 0.0f, 1.0f};
        VkRect2D scissor = {.offset = {0, 0}, .extent = renderer->swapchain_extent};
        vkCmdSetViewport(renderer->command_buffer, 0, 1, &viewport);
        vkCmdSetScissor(renderer->command_buffer, 0, 1, &scissor);
        vkCmdBindPipeline(renderer->command_buffer, VK_PIPELINE_BIND_POINT_GRAPHICS, renderer->pipeline);
        vkCmdDraw(renderer->command_buffer, 3, 1, 0, 0);
        vkCmdEndRenderPass(renderer->command_buffer);
        result = vkEndCommandBuffer(renderer->command_buffer);
    }
    if (result != VK_SUCCESS) {
        return fail("cannot record Vulkan triangle", result);
    }
    VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &renderer->image_available,
        .pWaitDstStageMask = &wait_stage,
        .commandBufferCount = 1,
        .pCommandBuffers = &renderer->command_buffer,
        .signalSemaphoreCount = 1,
        .pSignalSemaphores = &renderer->render_finished,
    };
    result = vkQueueSubmit(renderer->graphics_queue, 1, &submit, renderer->in_flight);
    if (result != VK_SUCCESS) {
        return fail("cannot submit Vulkan triangle", result);
    }
    VkPresentInfoKHR present = {
        .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &renderer->render_finished,
        .swapchainCount = 1,
        .pSwapchains = &renderer->swapchain,
        .pImageIndices = &image_index,
    };
    result = vkQueuePresentKHR(renderer->present_queue, &present);
    if (result == VK_ERROR_OUT_OF_DATE_KHR || result == VK_SUBOPTIMAL_KHR) {
        return 1;
    }
    if (result != VK_SUCCESS) {
        return fail("cannot present Vulkan triangle", result);
    }
    renderer->frames_rendered++;
    return 0;
}

void nx2linux_scale_rgba(const uint8_t *src, uint32_t src_w, uint32_t src_h, uint32_t src_stride,
                         uint8_t *dst, uint32_t dst_w, uint32_t dst_h, bool bgra)
{
    for (uint32_t y = 0; y < dst_h; ++y) {
        const uint8_t *row = src + (uint64_t)(y * (uint64_t)src_h / dst_h) * src_stride;
        uint8_t *out = dst + (uint64_t)y * dst_w * 4;
        for (uint32_t x = 0; x < dst_w; ++x) {
            const uint8_t *px = row + (uint64_t)(x * (uint64_t)src_w / dst_w) * 4;
            out[0] = bgra ? px[2] : px[0];
            out[1] = px[1];
            out[2] = bgra ? px[0] : px[2];
            out[3] = 255;
            out += 4;
        }
    }
}

static bool find_memory_type(VkPhysicalDevice device, uint32_t bits, VkMemoryPropertyFlags wanted, uint32_t *index)
{
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(device, &memory);
    for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) != 0 && (memory.memoryTypes[i].propertyFlags & wanted) == wanted) {
            *index = i;
            return true;
        }
    }
    return false;
}

int nx2linux_vulkan_present_rgba(Nx2LinuxVulkanRenderer *renderer, const uint8_t *pixels,
                                 uint32_t width, uint32_t height, uint32_t stride)
{
    if (renderer == NULL || !renderer->initialized || pixels == NULL || width == 0 || height == 0) {
        return -1;
    }
    VkResult result = vkWaitForFences(renderer->device, 1, &renderer->in_flight, VK_TRUE, UINT64_MAX);
    if (result != VK_SUCCESS) {
        return fail("cannot wait for Vulkan frame", result);
    }
    uint32_t image_index = 0;
    result = vkAcquireNextImageKHR(renderer->device, renderer->swapchain, UINT64_MAX,
                                   renderer->image_available, VK_NULL_HANDLE, &image_index);
    if (result == VK_ERROR_OUT_OF_DATE_KHR) {
        return 1;
    }
    if (result != VK_SUCCESS && result != VK_SUBOPTIMAL_KHR) {
        return fail("cannot acquire Vulkan swapchain image", result);
    }
    const VkExtent2D extent = renderer->swapchain_extent;
    const VkDeviceSize size = (VkDeviceSize)extent.width * extent.height * 4;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkBufferCreateInfo buffer_info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size,
        .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    result = vkCreateBuffer(renderer->device, &buffer_info, NULL, &buffer);
    if (result != VK_SUCCESS) {
        return fail("cannot create the frame staging buffer", result);
    }
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(renderer->device, buffer, &requirements);
    uint32_t type = 0;
    if (!find_memory_type(renderer->physical_device, requirements.memoryTypeBits,
                          VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, &type)) {
        vkDestroyBuffer(renderer->device, buffer, NULL);
        SDL_SetError("no host-visible memory for the frame");
        return -1;
    }
    VkMemoryAllocateInfo allocate = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size,
        .memoryTypeIndex = type,
    };
    result = vkAllocateMemory(renderer->device, &allocate, NULL, &memory);
    void *mapped = NULL;
    if (result == VK_SUCCESS) result = vkBindBufferMemory(renderer->device, buffer, memory, 0);
    if (result == VK_SUCCESS) result = vkMapMemory(renderer->device, memory, 0, size, 0, &mapped);
    if (result != VK_SUCCESS) {
        if (memory != VK_NULL_HANDLE) vkFreeMemory(renderer->device, memory, NULL);
        vkDestroyBuffer(renderer->device, buffer, NULL);
        return fail("cannot map the frame staging buffer", result);
    }
    const bool bgra = renderer->swapchain_format == VK_FORMAT_B8G8R8A8_UNORM ||
                      renderer->swapchain_format == VK_FORMAT_B8G8R8A8_SRGB;
    nx2linux_scale_rgba(pixels, width, height, stride ? stride : width * 4, mapped, extent.width, extent.height, bgra);
    vkUnmapMemory(renderer->device, memory);

    vkResetFences(renderer->device, 1, &renderer->in_flight);
    vkResetCommandBuffer(renderer->command_buffer, 0);
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    result = vkBeginCommandBuffer(renderer->command_buffer, &begin);
    if (result == VK_SUCCESS) {
        VkImageMemoryBarrier barrier = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = renderer->images[image_index],
            .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
            .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        };
        vkCmdPipelineBarrier(renderer->command_buffer, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &barrier);
        VkBufferImageCopy copy = {
            .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
            .imageExtent = {extent.width, extent.height, 1},
        };
        vkCmdCopyBufferToImage(renderer->command_buffer, buffer, renderer->images[image_index],
                               VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
        barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
        barrier.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
        barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        barrier.dstAccessMask = 0;
        vkCmdPipelineBarrier(renderer->command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0, NULL, 1, &barrier);
        result = vkEndCommandBuffer(renderer->command_buffer);
    }
    if (result == VK_SUCCESS) {
        VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TRANSFER_BIT;
        VkSubmitInfo submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .waitSemaphoreCount = 1,
            .pWaitSemaphores = &renderer->image_available,
            .pWaitDstStageMask = &wait_stage,
            .commandBufferCount = 1,
            .pCommandBuffers = &renderer->command_buffer,
            .signalSemaphoreCount = 1,
            .pSignalSemaphores = &renderer->render_finished,
        };
        result = vkQueueSubmit(renderer->graphics_queue, 1, &submit, renderer->in_flight);
    }
    if (result == VK_SUCCESS) {
        /* the staging memory is freed once the copy ran */
        result = vkWaitForFences(renderer->device, 1, &renderer->in_flight, VK_TRUE, UINT64_MAX);
    }
    vkFreeMemory(renderer->device, memory, NULL);
    vkDestroyBuffer(renderer->device, buffer, NULL);
    if (result != VK_SUCCESS) {
        return fail("cannot copy the frame to the swapchain", result);
    }
    VkPresentInfoKHR present = {
        .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &renderer->render_finished,
        .swapchainCount = 1,
        .pSwapchains = &renderer->swapchain,
        .pImageIndices = &image_index,
    };
    result = vkQueuePresentKHR(renderer->present_queue, &present);
    if (result == VK_ERROR_OUT_OF_DATE_KHR || result == VK_SUBOPTIMAL_KHR) {
        return 1;
    }
    if (result != VK_SUCCESS) {
        return fail("cannot present the frame", result);
    }
    renderer->frames_rendered++;
    return 0;
}

void nx2linux_vulkan_shutdown(Nx2LinuxVulkanRenderer *renderer)
{
    if (renderer == NULL) {
        return;
    }
    if (renderer->device != VK_NULL_HANDLE) {
        vkDeviceWaitIdle(renderer->device);
        if (renderer->in_flight != VK_NULL_HANDLE) vkDestroyFence(renderer->device, renderer->in_flight, NULL);
        if (renderer->render_finished != VK_NULL_HANDLE) vkDestroySemaphore(renderer->device, renderer->render_finished, NULL);
        if (renderer->image_available != VK_NULL_HANDLE) vkDestroySemaphore(renderer->device, renderer->image_available, NULL);
        if (renderer->command_pool != VK_NULL_HANDLE) vkDestroyCommandPool(renderer->device, renderer->command_pool, NULL);
        if (renderer->pipeline != VK_NULL_HANDLE) vkDestroyPipeline(renderer->device, renderer->pipeline, NULL);
        if (renderer->pipeline_layout != VK_NULL_HANDLE) vkDestroyPipelineLayout(renderer->device, renderer->pipeline_layout, NULL);
        if (renderer->render_pass != VK_NULL_HANDLE) vkDestroyRenderPass(renderer->device, renderer->render_pass, NULL);
        destroy_swapchain_objects(renderer);
        vkDestroyDevice(renderer->device, NULL);
    }
    if (renderer->surface != VK_NULL_HANDLE && renderer->instance != VK_NULL_HANDLE) {
        SDL_Vulkan_DestroySurface(renderer->instance, renderer->surface, NULL);
    }
    if (renderer->instance != VK_NULL_HANDLE) {
        vkDestroyInstance(renderer->instance, NULL);
    }
    SDL_Vulkan_UnloadLibrary();
    free(renderer->shader_dir);
    memset(renderer, 0, sizeof(*renderer));
}

const char *nx2linux_vulkan_gpu_name(const Nx2LinuxVulkanRenderer *renderer)
{
    static char name[VK_MAX_PHYSICAL_DEVICE_NAME_SIZE];
    if (renderer == NULL || renderer->physical_device == VK_NULL_HANDLE) {
        return NULL;
    }
    VkPhysicalDeviceProperties properties;
    vkGetPhysicalDeviceProperties(renderer->physical_device, &properties);
    snprintf(name, sizeof(name), "%s", properties.deviceName);
    return name;
}

uint64_t nx2linux_vulkan_frames_rendered(const Nx2LinuxVulkanRenderer *renderer)
{
    return renderer != NULL ? renderer->frames_rendered : 0;
}
