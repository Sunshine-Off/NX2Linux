cmake_minimum_required(VERSION 3.21)

project(nx2linux_host LANGUAGES C)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")
    message(FATAL_ERROR "This host currently targets Linux x86_64 only")
endif()

include(CTest)
option(NX2LINUX_GRAPHICS "Build SDL/Vulkan host components" ON)
find_package(Threads REQUIRED)
add_library(nx2linux_posix STATIC posix_host.c)
target_include_directories(nx2linux_posix PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(nx2linux_posix PUBLIC c_std_11)
target_compile_options(nx2linux_posix PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nx2linux_posix PUBLIC ${CMAKE_DL_LIBS})
add_executable(nx2linux_headless headless_main.c)
target_link_libraries(nx2linux_headless PRIVATE nx2linux_posix)
if(BUILD_TESTING)
    foreach(fixture native_fixture bad_abi_fixture failing_fixture)
        add_library(${fixture} MODULE tests/native_fixture.c)
        target_compile_features(${fixture} PRIVATE c_std_11)
        target_include_directories(${fixture} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
        target_link_libraries(${fixture} PRIVATE Threads::Threads)
    endforeach()
    target_compile_definitions(bad_abi_fixture PRIVATE BAD_ABI)
    target_compile_definitions(failing_fixture PRIVATE FAIL_ENTRY)
    add_executable(nx2linux_posix_test tests/test_posix_host.c)
    target_link_libraries(nx2linux_posix_test PRIVATE nx2linux_posix)
    add_test(NAME nx2linux_posix_host COMMAND nx2linux_posix_test
        $<TARGET_FILE:native_fixture> $<TARGET_FILE:bad_abi_fixture> $<TARGET_FILE:failing_fixture>)
    add_test(NAME nx2linux_headless_module COMMAND nx2linux_headless --module $<TARGET_FILE:native_fixture>)
endif()
if(NOT NX2LINUX_GRAPHICS)
    return()
endif()
find_package(PkgConfig REQUIRED)
find_package(Vulkan REQUIRED)
pkg_check_modules(SDL3 REQUIRED IMPORTED_TARGET sdl3>=3.2)
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

add_executable(nx2linux_vulkan_demo demo_vulkan_triangle.c)
target_compile_features(nx2linux_vulkan_demo PRIVATE c_std_11)
target_compile_options(nx2linux_vulkan_demo PRIVATE -Wall -Wextra -Wpedantic)
target_compile_definitions(nx2linux_vulkan_demo PRIVATE NX2LINUX_SHADER_DIR="${NX2LINUX_SHADER_DIR}")
target_link_libraries(nx2linux_vulkan_demo PRIVATE nx2linux_vulkan)

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
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_executable(nx2linux_demo_exit_fixture demo_vulkan_triangle.c tests/demo_failure_stubs.c)
    target_compile_features(nx2linux_demo_exit_fixture PRIVATE c_std_11)
    target_include_directories(nx2linux_demo_exit_fixture PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(nx2linux_demo_exit_fixture PRIVATE PkgConfig::SDL3 Vulkan::Vulkan)
    add_test(NAME nx2linux_demo_exit_status COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_demo_exit.py $<TARGET_FILE:nx2linux_demo_exit_fixture>)
    set_tests_properties(nx2linux_vulkan_triangle PROPERTIES
        FAIL_REGULAR_EXPRESSION "NX2Linux Vulkan validation error")

endif()
