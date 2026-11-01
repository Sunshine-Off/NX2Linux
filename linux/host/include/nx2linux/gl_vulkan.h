#ifndef NX2LINUX_GL_VULKAN_H
#define NX2LINUX_GL_VULKAN_H

#include <stdbool.h>
#include <stddef.h>

/*
 * OpenGL on Vulkan for the games that render with OpenGL (Balatro's LOVE: a core profile context
 * and no NVN). The guest's GL calls are answered by a real GL 4.5 core context that Mesa Zink
 * runs on Vulkan, so every draw ends up in the Vulkan driver (lavapipe in CI). The context is
 * surfaceless: the host presents the frame it renders into a framebuffer object.
 *
 * Nothing here knows the guest. nx2linux_gl_get_proc is the resolver the loader hands to the
 * guest's imports (eglGetProcAddress, glad's loader).
 */

typedef struct Nx2LinuxGl {
    void *display;
    void *context;
    char renderer[256];
    char version[64];
    bool zink;
} Nx2LinuxGl;

typedef struct Nx2LinuxGlConfig {
    bool allow_software;  /* a CPU Vulkan device (lavapipe) is acceptable: CI and headless runs */
    bool require_zink;    /* fail instead of falling back to another GL driver */
} Nx2LinuxGlConfig;

/* 0 on success, -1 on failure (nx2linux_gl_error says why), 1 when no usable GL on Vulkan exists. */
int nx2linux_gl_init(Nx2LinuxGl *gl, const Nx2LinuxGlConfig *config);
void nx2linux_gl_shutdown(Nx2LinuxGl *gl);
const char *nx2linux_gl_error(void);

/* The address of a GL function of the current context, or NULL. */
void *nx2linux_gl_get_proc(const char *name);

/* How many of the names are missing from the context (listed to stderr when verbose). */
size_t nx2linux_gl_missing(const char *const *names, size_t count, bool verbose);

/* The functions Balatro's LOVE 11.4 reaches in a core profile (README of its profile): a loader that
 * resolves fewer would hand the guest a null pointer. */
extern const char *const nx2linux_gl_required[];
extern const size_t nx2linux_gl_required_count;

#endif
