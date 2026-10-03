cmake_minimum_required(VERSION 3.20)

project(jitter VERSION 0.1.0 LANGUAGES CXX)

add_executable(jitter src/main.cpp)
target_compile_features(jitter PRIVATE cxx_std_20)
set_target_properties(jitter PROPERTIES CXX_EXTENSIONS OFF)

if(MSVC)
    target_compile_options(jitter PRIVATE /W4)
else()
    target_compile_options(jitter PRIVATE -Wall -Wextra -Wpedantic)
endif()
