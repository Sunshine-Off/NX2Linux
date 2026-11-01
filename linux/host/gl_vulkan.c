#include "nx2linux/gl_vulkan.h"

#include <EGL/egl.h>
#include <EGL/eglext.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef GL_RENDERER
#define GL_RENDERER 0x1F01
#define GL_VERSION 0x1F02
#endif
typedef const unsigned char *(*GetStringFn)(unsigned int);

static char g_error[384];

const char *nx2linux_gl_error(void)
{
    return g_error;
}

static int fail(const char *what)
{
    snprintf(g_error, sizeof(g_error), "%s (EGL error 0x%x)", what, (unsigned)eglGetError());
    return -1;
}

/* Mesa's EGL vendor file, so that GLVND does not pick another vendor's EGL (NVIDIA's has no Zink). */
static void select_mesa_egl(void)
{
    static const char *const candidates[] = {
        "/usr/share/glvnd/egl_vendor.d/50_mesa.json",
        "/etc/glvnd/egl_vendor.d/50_mesa.json",
        "/usr/local/share/glvnd/egl_vendor.d/50_mesa.json",
    };
    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); ++i) {
        if (access(candidates[i], R_OK) == 0) {
            setenv("__EGL_VENDOR_LIBRARY_FILENAMES", candidates[i], 0);
            return;
        }
    }
}

int nx2linux_gl_init(Nx2LinuxGl *gl, const Nx2LinuxGlConfig *config)
{
    memset(gl, 0, sizeof(*gl));
    g_error[0] = 0;
    select_mesa_egl();
    setenv("MESA_LOADER_DRIVER_OVERRIDE", "zink", 0);
    setenv("GALLIUM_DRIVER", "zink", 0);
    if (config && config->allow_software) {
        setenv("LIBGL_ALWAYS_SOFTWARE", "1", 0);  /* lets Zink take lavapipe */
    }

    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!get_display) {
        snprintf(g_error, sizeof(g_error), "EGL has no eglGetPlatformDisplayEXT");
        return 1;
    }
    EGLDisplay display = get_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    EGLint major = 0, minor = 0;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor)) {
        fail("no surfaceless EGL display");
        return 1;
    }
    if (!eglBindAPI(EGL_OPENGL_API)) {
        eglTerminate(display);
        return fail("eglBindAPI(OpenGL)");
    }
    const EGLint attribs[] = {
        EGL_CONTEXT_MAJOR_VERSION, 4,
        EGL_CONTEXT_MINOR_VERSION, 5,
        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
        EGL_NONE,
    };
    EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attribs);
    if (context == EGL_NO_CONTEXT) {
        fail("no OpenGL 4.5 core context");
        eglTerminate(display);
        return 1;
    }
    if (!eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context)) {
        fail("eglMakeCurrent");
        eglDestroyContext(display, context);
        eglTerminate(display);
        return 1;
    }
    GetStringFn get_string = (GetStringFn)eglGetProcAddress("glGetString");
    const char *renderer = get_string ? (const char *)get_string(GL_RENDERER) : NULL;
    const char *version = get_string ? (const char *)get_string(GL_VERSION) : NULL;
    snprintf(gl->renderer, sizeof(gl->renderer), "%s", renderer ? renderer : "");
    snprintf(gl->version, sizeof(gl->version), "%s", version ? version : "");
    gl->display = display;
    gl->context = context;
    gl->zink = strstr(gl->renderer, "zink") != NULL;
    if (config && config->require_zink && !gl->zink) {
        snprintf(g_error, sizeof(g_error), "GL renderer is \"%s\", not Zink on Vulkan", gl->renderer);
        nx2linux_gl_shutdown(gl);
        return 1;
    }
    return 0;
}

void nx2linux_gl_shutdown(Nx2LinuxGl *gl)
{
    if (gl->display) {
        eglMakeCurrent(gl->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (gl->context) {
            eglDestroyContext(gl->display, gl->context);
        }
        eglTerminate(gl->display);
    }
    memset(gl, 0, sizeof(*gl));
}

void *nx2linux_gl_get_proc(const char *name)
{
    __eglMustCastToProperFunctionPointerType fn = eglGetProcAddress(name);
    void *p;
    memcpy(&p, &fn, sizeof(p));  /* a function pointer to an object pointer: only memcpy is portable C */
    return p;
}

size_t nx2linux_gl_missing(const char *const *names, size_t count, bool verbose)
{
    size_t missing = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!nx2linux_gl_get_proc(names[i])) {
            ++missing;
            if (verbose) {
                fprintf(stderr, "missing GL function: %s\n", names[i]);
            }
        }
    }
    return missing;
}

const char *const nx2linux_gl_required[] = {
    "glGetString", "glGetStringi", "glGetIntegerv", "glGetFloatv", "glGetError", "glEnable", "glDisable",
    "glViewport", "glScissor", "glClear", "glClearColor", "glClearBufferfv", "glBlendFunc", "glBlendFuncSeparate",
    "glBlendEquation", "glBlendEquationSeparate", "glColorMask", "glDepthMask", "glStencilMask",
    "glGenBuffers", "glDeleteBuffers", "glBindBuffer", "glBufferData", "glBufferSubData", "glBufferStorage",
    "glMapBufferRange", "glUnmapBuffer", "glFlushMappedBufferRange", "glFenceSync", "glClientWaitSync", "glDeleteSync",
    "glGenVertexArrays", "glBindVertexArray", "glDeleteVertexArrays", "glVertexAttribPointer",
    "glEnableVertexAttribArray", "glDisableVertexAttribArray", "glVertexAttrib4f", "glVertexAttrib4fv",
    "glBindAttribLocation", "glCreateShader", "glShaderSource", "glCompileShader", "glGetShaderiv",
    "glGetShaderInfoLog", "glDeleteShader", "glCreateProgram", "glAttachShader", "glLinkProgram",
    "glGetProgramiv", "glGetProgramInfoLog", "glUseProgram", "glDeleteProgram", "glGetActiveUniform",
    "glGetUniformLocation", "glGetUniformfv", "glGetUniformiv", "glUniform1f", "glUniform1i", "glUniform1fv",
    "glUniform2fv", "glUniform3fv", "glUniform4fv", "glUniform1iv", "glUniform1uiv", "glUniformMatrix3fv",
    "glUniformMatrix4fv", "glGenTextures", "glDeleteTextures", "glBindTexture", "glActiveTexture",
    "glTexParameteri", "glTexStorage2D", "glTexStorage3D", "glTexSubImage2D", "glTexSubImage3D",
    "glGenerateMipmap", "glPixelStorei", "glReadPixels", "glGenFramebuffers", "glDeleteFramebuffers",
    "glBindFramebuffer", "glFramebufferTexture2D", "glCheckFramebufferStatus", "glInvalidateFramebuffer",
    "glBlitFramebuffer", "glDrawArrays", "glDrawElements", "glDrawElementsBaseVertex", "glDrawArraysInstanced",
    "glDrawElementsInstanced", "glDepthFunc", "glStencilFunc", "glStencilOp", "glCullFace", "glFrontFace",
};
const size_t nx2linux_gl_required_count = sizeof(nx2linux_gl_required) / sizeof(nx2linux_gl_required[0]);
