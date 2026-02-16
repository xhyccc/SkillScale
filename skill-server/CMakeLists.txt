cmake_minimum_required(VERSION 3.16)
project(skillscale_skill_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Find ZeroMQ
find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED libzmq)

# cppzmq — header-only
find_path(CPPZMQ_INCLUDE_DIR zmq.hpp
    HINTS /usr/include /usr/local/include /opt/homebrew/include
)

# nlohmann/json — header-only
find_path(NLOHMANN_JSON_INCLUDE_DIR nlohmann/json.hpp
    HINTS /usr/include /usr/local/include /opt/homebrew/include
)

add_executable(skillscale_skill_server
    main.cpp
    skill_executor.cpp
    skill_loader.cpp
    message_handler.cpp
)

target_include_directories(skillscale_skill_server PRIVATE
    ${ZMQ_INCLUDE_DIRS}
    ${CPPZMQ_INCLUDE_DIR}
    ${NLOHMANN_JSON_INCLUDE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_directories(skillscale_skill_server PRIVATE ${ZMQ_LIBRARY_DIRS})
target_link_libraries(skillscale_skill_server PRIVATE ${ZMQ_LIBRARIES})
