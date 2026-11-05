#include "nx2linux/gl_vulkan.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef unsigned int GLuint;
typedef int GLint;
typedef int GLsizei;
typedef unsigned int GLenum;
typedef float GLfloat;

#define GL_FRAMEBUFFER 0x8D40
#define GL_COLOR_ATTACHMENT0 0x8CE0
#define GL_TEXTURE_2D 0x0DE1
#define GL_RGBA8 0x8058
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define GL_COLOR_BUFFER_BIT 0x4000
#define GL_FRAMEBUFFER_COMPLETE 0x8CD5
#define GL_TRIANGLES 0x0004
#define GL_VERTEX_SHADER 0x8B31
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_COMPILE_STATUS 0x8B81
#define GL_LINK_STATUS 0x8B82

/* a GL entry point: the pointer comes back as void *, which only a memcpy converts without a pedantic warning */
#define LOAD(name, ret, args) \
    ret(*name) args;          \
    do {                      \
        void *p_##name = nx2linux_gl_get_proc(#name); \
        memcpy(&name, &p_##name, sizeof(name));       \
    } while (0)

static int check(int ok, const char *what)
{
    if (!ok) {
        fprintf(stderr, "FAIL: %s\n", what);
    }
    return ok;
}

int main(void)
{
    Nx2LinuxGl gl;
    const Nx2LinuxGlConfig config = {.allow_software = true, .require_zink = true};
    int rc = nx2linux_gl_init(&gl, &config);
    if (rc > 0) {
        fprintf(stderr, "SKIP: GL on Vulkan unavailable: %s\n", nx2linux_gl_error());
        return 77;
    }
    if (rc < 0) {
        fprintf(stderr, "FAIL: %s\n", nx2linux_gl_error());
        return 1;
    }
    printf("GL %s on %s\n", gl.version, gl.renderer);

    int status = 0;
    status |= !check(gl.zink, "renderer is Zink, so OpenGL runs on Vulkan");
    status |= !check(nx2linux_gl_missing(nx2linux_gl_required, nx2linux_gl_required_count, true) == 0,
                     "every GL function of the core profile list resolves");

    LOAD(glGenFramebuffers, void, (GLsizei, GLuint *));
    LOAD(glBindFramebuffer, void, (GLenum, GLuint));
    LOAD(glGenTextures, void, (GLsizei, GLuint *));
    LOAD(glBindTexture, void, (GLenum, GLuint));
    LOAD(glTexStorage2D, void, (GLenum, GLsizei, GLenum, GLsizei, GLsizei));
    LOAD(glFramebufferTexture2D, void, (GLenum, GLenum, GLenum, GLuint, GLint));
    LOAD(glCheckFramebufferStatus, GLenum, (GLenum));
    LOAD(glViewport, void, (GLint, GLint, GLsizei, GLsizei));
    LOAD(glClearColor, void, (GLfloat, GLfloat, GLfloat, GLfloat));
    LOAD(glClear, void, (unsigned int));
    LOAD(glCreateShader, GLuint, (GLenum));
    LOAD(glShaderSource, void, (GLuint, GLsizei, const char *const *, const GLint *));
    LOAD(glCompileShader, void, (GLuint));
    LOAD(glGetShaderiv, void, (GLuint, GLenum, GLint *));
    LOAD(glCreateProgram, GLuint, (void));
    LOAD(glAttachShader, void, (GLuint, GLuint));
    LOAD(glLinkProgram, void, (GLuint));
    LOAD(glGetProgramiv, void, (GLuint, GLenum, GLint *));
    LOAD(glUseProgram, void, (GLuint));
    LOAD(glGenVertexArrays, void, (GLsizei, GLuint *));
    LOAD(glBindVertexArray, void, (GLuint));
    LOAD(glDrawArrays, void, (GLenum, GLint, GLsizei));
    LOAD(glReadPixels, void, (GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *));
    LOAD(glFinish, void, (void));

    enum { W = 64, H = 64 };
    GLuint fbo = 0, tex = 0, vao = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, W, H);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    status |= !check(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE, "framebuffer complete");

    /* A full-screen triangle in a flat colour, from GLSL 330 core, the dialect LOVE sends. */
    static const char *vs =
        "#version 330 core\n"
        "void main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);"
        " gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }\n";
    static const char *fs =
        "#version 330 core\nout vec4 c;\nvoid main() { c = vec4(1.0, 0.5, 0.0, 1.0); }\n";
    GLuint v = glCreateShader(GL_VERTEX_SHADER), f = glCreateShader(GL_FRAGMENT_SHADER), prog = glCreateProgram();
    GLint ok = 0;
    glShaderSource(v, 1, &vs, NULL);
    glCompileShader(v);
    glGetShaderiv(v, GL_COMPILE_STATUS, &ok);
    status |= !check(ok, "vertex shader compiles");
    glShaderSource(f, 1, &fs, NULL);
    glCompileShader(f);
    glGetShaderiv(f, GL_COMPILE_STATUS, &ok);
    status |= !check(ok, "fragment shader compiles");
    glAttachShader(prog, v);
    glAttachShader(prog, f);
    glLinkProgram(prog);
    glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    status |= !check(ok, "program links");

    glViewport(0, 0, W, H);
    glClearColor(0.f, 0.f, 1.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glGenVertexArrays(1, &vao);
    glBindVertexArray(vao);
    glUseProgram(prog);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glFinish();

    uint8_t pixels[W * H * 4];
    glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    const uint8_t *centre = pixels + ((H / 2) * W + W / 2) * 4;
    status |= !check(centre[0] == 255 && (centre[1] >= 127 && centre[1] <= 128) && centre[2] == 0 && centre[3] == 255,
                     "the centre pixel is the triangle's orange, drawn through Vulkan");

    nx2linux_gl_shutdown(&gl);
    printf(status ? "FAILED\n" : "OK\n");
    return status ? 1 : 0;
}
