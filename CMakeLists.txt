cmake_minimum_required(VERSION 3.20)
project(jitter VERSION 0.1.0 LANGUAGES CXX)

if(NOT APPLE AND NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "Phase 1 resource collection supports Linux and macOS")
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

if(CMAKE_CONFIGURATION_TYPES)
    message(FATAL_ERROR "Phase 1 requires a single-configuration CMake generator")
endif()

find_package(Threads REQUIRED)
find_package(Git QUIET)
set(JITTER_SOURCE_REVISION "unknown")
set(JITTER_SOURCE_DIRTY "true")
if(Git_FOUND AND EXISTS "${CMAKE_SOURCE_DIR}/.git")
    execute_process(COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}" OUTPUT_VARIABLE JITTER_SOURCE_REVISION OUTPUT_STRIP_TRAILING_WHITESPACE)
    execute_process(COMMAND "${GIT_EXECUTABLE}" status --porcelain
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}" OUTPUT_VARIABLE JITTER_GIT_STATUS OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(JITTER_GIT_STATUS STREQUAL "")
        set(JITTER_SOURCE_DIRTY "false")
    endif()
endif()
string(TOUPPER "${CMAKE_BUILD_TYPE}" JITTER_BUILD_TYPE_UPPER)
set(JITTER_INFO_FLAGS "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${JITTER_BUILD_TYPE_UPPER}} -Wall -Wextra -Wpedantic; C++20; extensions=off")
string(REPLACE "\\" "\\\\" JITTER_INFO_FLAGS "${JITTER_INFO_FLAGS}")
string(REPLACE "\"" "\\\"" JITTER_INFO_FLAGS "${JITTER_INFO_FLAGS}")
configure_file(include/jitter/build_info.hpp.in generated/jitter/build_info.hpp @ONLY)

add_library(jitter_core src/application.cpp src/experiment.cpp)
target_include_directories(jitter_core PUBLIC include)
target_compile_features(jitter_core PUBLIC cxx_std_20)
set_target_properties(jitter_core PROPERTIES CXX_EXTENSIONS OFF)
target_compile_options(jitter_core PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(jitter_core PUBLIC Threads::Threads)

add_executable(jitter src/main.cpp)
target_include_directories(jitter PRIVATE "${CMAKE_BINARY_DIR}/generated")
target_link_libraries(jitter PRIVATE jitter_core)
set_target_properties(jitter PROPERTIES CXX_EXTENSIONS OFF)
target_compile_options(jitter PRIVATE -Wall -Wextra -Wpedantic)

include(CTest)
if(BUILD_TESTING)
    add_executable(jitter_core_tests tests/core_tests.cpp)
    target_link_libraries(jitter_core_tests PRIVATE jitter_core)
    set_target_properties(jitter_core_tests PROPERTIES CXX_EXTENSIONS OFF)
    add_test(NAME core COMMAND jitter_core_tests)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME foundation COMMAND "${CMAKE_COMMAND}" -E env
        "JITTER_TEST_BINARY=$<TARGET_FILE:jitter>"
        "${Python3_EXECUTABLE}" -m unittest discover
        -s "${CMAKE_SOURCE_DIR}/tests" -p "test_*.py" -v)
    set_tests_properties(core foundation PROPERTIES TIMEOUT 60)
endif()
