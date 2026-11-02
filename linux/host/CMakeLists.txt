cmake_minimum_required(VERSION 3.20)

project(nx2linux_host LANGUAGES C)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)   # the static libraries end up in libnx2linux_display.so

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
add_library(nx2linux_gpu SHARED gpu.c)
target_compile_options(nx2linux_gpu PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nx2linux_gpu PRIVATE Vulkan::Vulkan pthread)

if(BUILD_TESTING)
    add_executable(nx2linux_sdl_host_test tests/test_sdl_host.c)
    target_compile_features(nx2linux_sdl_host_test PRIVATE c_std_11)
    target_compile_options(nx2linux_sdl_host_test PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(nx2linux_sdl_host_test PRIVATE nx2linux_host)

    add_test(NAME nx2linux_sdl_host_smoke COMMAND nx2linux_sdl_host_test)
    set_tests_properties(nx2linux_sdl_host_smoke PROPERTIES
        ENVIRONMENT "SDL_VIDEODRIVER=dummy;SDL_AUDIODRIVER=dummy"
    )

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
