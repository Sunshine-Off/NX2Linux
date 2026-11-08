cmake_minimum_required(VERSION 3.20)

project(nx2linux_host LANGUAGES C)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)   # the static libraries end up in libnx2linux_display.so

# Sanitizer build, off by default: the normal build has none of these flags. Use a directory of its own and clang, so one sanitizer runtime serves the whole process
# (the libraries are loaded into Python by ctypes, with the runtime preloaded). `python3 linux/cli/nx2linux.py sanitize` does all of it, see README.md.
set(NX2LINUX_SANITIZE "" CACHE STRING "comma-separated -fsanitize list, for example address,undefined (empty: no sanitizer)")
if(NX2LINUX_SANITIZE)
    add_compile_options(-fsanitize=${NX2LINUX_SANITIZE} -fno-omit-frame-pointer -g)
    add_link_options(-fsanitize=${NX2LINUX_SANITIZE})
endif()

include(CTest)
find_package(PkgConfig REQUIRED)
find_package(Vulkan REQUIRED)
pkg_check_modules(SDL3 REQUIRED IMPORTED_TARGET sdl3)
pkg_check_modules(EGL REQUIRED IMPORTED_TARGET egl)
find_program(GLSLC_EXECUTABLE glslc REQUIRED)

set(NX2LINUX_SHADER_DIR "${CMAKE_CURRENT_BINARY_DIR}/shaders")
set(NX2LINUX_VERTEX_SHADER "${NX2LINUX_SHADER_DIR}/triangle.vert.spv")
set(NX2LINUX_FRAGMENT_SHADER "${NX2LINUX_SHADER_DIR}/triangle.frag.spv")
file(MAKE_DIRECTORY "${NX2LINUX_SHADER_DIR}")
add_custom_command(
    OUTPUT "${NX2LINUX_VERTEX_SHADER}"
    COMMAND "${GLSLC_EXECUTABLE}" -fshader-stage=vertex
            "${CMAKE_CURRENT_SOURCE_DIR}/shaders/triangle.vert"
            -o "${NX2LINUX_VERTEX_SHADER}"
    DEPENDS shaders/triangle.vert
    VERBATIM
)
add_custom_command(
    OUTPUT "${NX2LINUX_FRAGMENT_SHADER}"
    COMMAND "${GLSLC_EXECUTABLE}" -fshader-stage=fragment
            "${CMAKE_CURRENT_SOURCE_DIR}/shaders/triangle.frag"
            -o "${NX2LINUX_FRAGMENT_SHADER}"
    DEPENDS shaders/triangle.frag
    VERBATIM
)
add_custom_target(nx2linux_shaders DEPENDS
    "${NX2LINUX_VERTEX_SHADER}" "${NX2LINUX_FRAGMENT_SHADER}"
)

add_library(nx2linux_host STATIC
    sdl_host.c
)

target_compile_features(nx2linux_host PUBLIC c_std_11)
target_compile_options(nx2linux_host PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(nx2linux_host
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_link_libraries(nx2linux_host PUBLIC PkgConfig::SDL3)

add_library(nx2linux_vulkan STATIC
    vulkan_renderer.c
)
add_dependencies(nx2linux_vulkan nx2linux_shaders)
target_compile_features(nx2linux_vulkan PUBLIC c_std_11)
target_compile_options(nx2linux_vulkan PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(nx2linux_vulkan
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_link_libraries(nx2linux_vulkan PUBLIC nx2linux_host PkgConfig::SDL3 Vulkan::Vulkan)

add_library(nx2linux_gl_vulkan STATIC
    gl_vulkan.c
)
target_compile_features(nx2linux_gl_vulkan PUBLIC c_std_11)
target_compile_options(nx2linux_gl_vulkan PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(nx2linux_gl_vulkan
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_link_libraries(nx2linux_gl_vulkan PUBLIC PkgConfig::EGL)

# The window a recompiled game draws into, for Python (ctypes): tools/horizon_display.py
add_library(nx2linux_display SHARED display.c)
target_compile_options(nx2linux_display PRIVATE -Wall -Wextra -Wpedantic)
target_compile_definitions(nx2linux_display PRIVATE NX2LINUX_SHADER_DIR="${NX2LINUX_SHADER_DIR}")
target_link_libraries(nx2linux_display PRIVATE nx2linux_vulkan)

# The GPU the NVN layer draws with, for Python (ctypes): engine/dm/tools/horizon_gpu.py. Headless Vulkan 1.3 (dynamic rendering, push descriptors)
add_library(nx2linux_gpu SHARED gpu.c nvnrec.c)
target_compile_options(nx2linux_gpu PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nx2linux_gpu PRIVATE nx2linux_host Vulkan::Vulkan pthread PkgConfig::SDL3)

# The kernel synchronisation calls in C, for the host (ctypes): engine/dm/tools/horizon_sync.py. It needs neither SDL nor Vulkan
add_library(nx2linux_sync SHARED svcsync.c)
target_compile_features(nx2linux_sync PRIVATE c_std_11)
target_compile_options(nx2linux_sync PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nx2linux_sync PRIVATE pthread)

if(BUILD_TESTING)
    add_executable(nx2linux_sdl_host_test tests/test_sdl_host.c)
    target_compile_features(nx2linux_sdl_host_test PRIVATE c_std_11)
    target_compile_options(nx2linux_sdl_host_test PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(nx2linux_sdl_host_test PRIVATE nx2linux_host)

    add_test(NAME nx2linux_sdl_host_smoke COMMAND nx2linux_sdl_host_test)
    set_tests_properties(nx2linux_sdl_host_smoke PROPERTIES
        ENVIRONMENT "SDL_VIDEODRIVER=dummy;SDL_AUDIODRIVER=dummy"
    )

    add_executable(nx2linux_sync_test tests/test_svcsync.c)
    target_compile_features(nx2linux_sync_test PRIVATE c_std_11)
    target_compile_options(nx2linux_sync_test PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(nx2linux_sync_test PRIVATE nx2linux_sync pthread)
    add_test(NAME nx2linux_sync COMMAND nx2linux_sync_test)

    add_executable(nx2linux_vulkan_test tests/test_vulkan_renderer.c)
    target_compile_features(nx2linux_vulkan_test PRIVATE c_std_11)
    target_compile_options(nx2linux_vulkan_test PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_definitions(nx2linux_vulkan_test PRIVATE
        NX2LINUX_SHADER_DIR="${NX2LINUX_SHADER_DIR}"
    )
    target_link_libraries(nx2linux_vulkan_test PRIVATE nx2linux_vulkan)
    add_test(NAME nx2linux_vulkan_triangle COMMAND nx2linux_vulkan_test)
    set_tests_properties(nx2linux_vulkan_triangle PROPERTIES SKIP_RETURN_CODE 77)

    add_executable(nx2linux_vulkan_demo demo_vulkan_triangle.c)
    target_compile_features(nx2linux_vulkan_demo PRIVATE c_std_11)
    target_compile_options(nx2linux_vulkan_demo PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_definitions(nx2linux_vulkan_demo PRIVATE
        NX2LINUX_SHADER_DIR="${NX2LINUX_SHADER_DIR}"
    )
    target_link_libraries(nx2linux_vulkan_demo PRIVATE nx2linux_vulkan)

    add_executable(nx2linux_gl_vulkan_test tests/test_gl_vulkan.c)
    target_compile_features(nx2linux_gl_vulkan_test PRIVATE c_std_11)
    target_compile_options(nx2linux_gl_vulkan_test PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(nx2linux_gl_vulkan_test PRIVATE nx2linux_gl_vulkan)
    add_test(NAME nx2linux_gl_on_vulkan COMMAND nx2linux_gl_vulkan_test)
    set_tests_properties(nx2linux_gl_on_vulkan PROPERTIES SKIP_RETURN_CODE 77)
endif()

# the audio mixer and output (audio.c): loaded by dm/tools/horizon_mix.py beside the display and the GPU libraries
add_library(nx2linux_audio SHARED audio.c)
target_compile_options(nx2linux_audio PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nx2linux_audio PRIVATE PkgConfig::SDL3 m pthread)

# the movie decoders (movie.c): VP8 / VP9 through libvpx loaded at run time, Vorbis through stb_vorbis; their headers and source come with the engine tree
set(NX2_ENGINE_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/../../engine/dm/third_party")
if(EXISTS "${NX2_ENGINE_THIRD_PARTY}/stb_vorbis/stb_vorbis.c")
  add_library(nx2linux_movie SHARED movie.c)
  target_include_directories(nx2linux_movie PRIVATE "${NX2_ENGINE_THIRD_PARTY}/stb_vorbis" "${NX2_ENGINE_THIRD_PARTY}/libvpx")
  target_compile_options(nx2linux_movie PRIVATE -Wall -Wno-unused-function -Wno-unused-variable)
  target_link_libraries(nx2linux_movie PRIVATE ${CMAKE_DL_LIBS} m)
else()
  message(STATUS "movie decoders: engine/dm/third_party is not there (python3 tools/engine.py sync): libnx2linux_movie.so is not built")
endif()

