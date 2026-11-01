cmake_minimum_required(VERSION 3.20)

project(nx2linux_host LANGUAGES C)

include(CTest)
find_package(PkgConfig REQUIRED)
pkg_check_modules(SDL3 REQUIRED IMPORTED_TARGET sdl3)

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

if(BUILD_TESTING)
    add_executable(nx2linux_sdl_host_test tests/test_sdl_host.c)
    target_compile_features(nx2linux_sdl_host_test PRIVATE c_std_11)
    target_compile_options(nx2linux_sdl_host_test PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(nx2linux_sdl_host_test PRIVATE nx2linux_host)

    add_test(NAME nx2linux_sdl_host_smoke COMMAND nx2linux_sdl_host_test)
    set_tests_properties(nx2linux_sdl_host_smoke PROPERTIES
        ENVIRONMENT "SDL_VIDEODRIVER=dummy;SDL_AUDIODRIVER=dummy"
    )
endif()
