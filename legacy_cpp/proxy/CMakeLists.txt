cmake_minimum_required(VERSION 3.16)
project(skillscale_proxy LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Find ZeroMQ
find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED libzmq)

# cppzmq is header-only; look for it
find_path(CPPZMQ_INCLUDE_DIR zmq.hpp
    HINTS /usr/include /usr/local/include /opt/homebrew/include
)

add_executable(skillscale_proxy main.cpp)

target_include_directories(skillscale_proxy PRIVATE
    ${ZMQ_INCLUDE_DIRS}
    ${CPPZMQ_INCLUDE_DIR}
)

target_link_directories(skillscale_proxy PRIVATE ${ZMQ_LIBRARY_DIRS})
target_link_libraries(skillscale_proxy PRIVATE ${ZMQ_LIBRARIES})
