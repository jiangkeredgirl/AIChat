cmake_minimum_required(VERSION 3.16)
cmake_policy(SET CMP0007 NEW)
set(CMAKE_UTF8_STRINGS ON)

project(BloodSamplingRobot VERSION 0.1 LANGUAGES CXX)

# 获取Git commit hash
execute_process(
    COMMAND git rev-parse HEAD
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_COMMIT_HASH
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
message(STATUS "GIT_COMMIT_HASH=${GIT_COMMIT_HASH}")
# 将Git commit hash添加到预处理器定义
add_definitions(-DGIT_COMMIT_HASH="${GIT_COMMIT_HASH}")

execute_process(
    COMMAND git rev-parse HEAD
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_COMMIT_HASH
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
execute_process(
    COMMAND git show -s --format=%ci HEAD
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_COMMIT_DATE
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
execute_process(
    COMMAND git rev-parse --abbrev-ref HEAD
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_BRANCH
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
set(GIT_COMMIT_INFO "${GIT_COMMIT_HASH} | ${GIT_COMMIT_DATE} | ${GIT_BRANCH}")
message(STATUS "GIT_COMMIT_INFO:${GIT_COMMIT_INFO}")
# execute_process(
#     COMMAND echo "$(git rev-parse HEAD) | $(git show -s --format=%ci HEAD) | $(git rev-parse --abbrev-ref HEAD)"
#     WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
#     OUTPUT_VARIABLE GIT_COMMIT_INFO
#     OUTPUT_STRIP_TRAILING_WHITESPACE
# )
# message(STATUS "GIT_COMMIT_INFO:${GIT_COMMIT_INFO}")
# 将Git commit hash添加到预处理器定义
add_definitions(-DGIT_COMMIT_INFO="${GIT_COMMIT_INFO}")


# 设置Qt目录
if(WIN32)
    # Windows特定设置
    if(NOT DEFINED Qt6_INSTALL_DIR)
        set(Qt6_INSTALL_DIR "D:/X/Qt/6.7.3/msvc2022_64") # 默认Qt安装路径
    endif()
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /DWIN32 /D_WINDOWS")
endif()
message(STATUS "Qt6_INSTALL_DIR is: ${Qt6_INSTALL_DIR}")
set(CMAKE_PREFIX_PATH "${Qt6_INSTALL_DIR}/lib/cmake/Qt6")

set(CMAKE_INCLUDE_CURRENT_DIR ON)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Network Sql SerialPort Multimedia MultimediaWidgets PrintSupport Concurrent)
qt_standard_project_setup()

# 如果外部没指定，默认使用`Debug`构建
if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug" CACHE STRING "Build type (Debug, Release, etc.)" FORCE)
endif()
message(STATUS "CMAKE_CONFIGURATION_TYPES: ${CMAKE_CONFIGURATION_TYPES}; CMAKE_BUILD_TYPE: ${CMAKE_BUILD_TYPE}")

# 统一采用`utf-8`编码进行编译
if(MSVC)
    add_compile_options(/utf-8)
else()
    add_compile_options(-finput-charset=UTF-8 -fexec-charset=UTF-8)
endif()

# 源文件
file(GLOB MAIN_SOURCES
    "${CMAKE_SOURCE_DIR}/*.cpp"
    "${CMAKE_SOURCE_DIR}/*.h"
    "${CMAKE_SOURCE_DIR}/*.hpp"
    "${CMAKE_SOURCE_DIR}/*.ui"
)
file(GLOB_RECURSE MODULE_SOURCES
    "${CMAKE_SOURCE_DIR}/kcommonhpp/*.h"
    "${CMAKE_SOURCE_DIR}/kcommonhpp/*.hpp"

    "${CMAKE_SOURCE_DIR}/kprojecthpp/*.h"
    "${CMAKE_SOURCE_DIR}/kprojecthpp/*.hpp"

    "${CMAKE_SOURCE_DIR}/algorithm/*.cpp"
    "${CMAKE_SOURCE_DIR}/algorithm/*.h"
    "${CMAKE_SOURCE_DIR}/algorithm/*.hpp"

    "${CMAKE_SOURCE_DIR}/device/*.cpp"
    "${CMAKE_SOURCE_DIR}/device/*.h"
    "${CMAKE_SOURCE_DIR}/device/*.hpp"

    "${CMAKE_SOURCE_DIR}/devicesim/*.cpp"
    "${CMAKE_SOURCE_DIR}/devicesim/*.h"
    "${CMAKE_SOURCE_DIR}/devicesim/*.hpp"

    "${CMAKE_SOURCE_DIR}/deviceui/*.cpp"
    "${CMAKE_SOURCE_DIR}/deviceui/*.h"
    "${CMAKE_SOURCE_DIR}/deviceui/*.hpp"
    "${CMAKE_SOURCE_DIR}/deviceui/*.ui"

    "${CMAKE_SOURCE_DIR}/StateMachine/*.cpp"
    "${CMAKE_SOURCE_DIR}/StateMachine/*.h"
    "${CMAKE_SOURCE_DIR}/StateMachine/*.hpp"

    "${CMAKE_SOURCE_DIR}/myoverloadui/*.h"
    "${CMAKE_SOURCE_DIR}/myoverloadui/*.cpp"

    "${CMAKE_SOURCE_DIR}/BloodDrawStepUI/*.h"
    "${CMAKE_SOURCE_DIR}/BloodDrawStepUI/*.cpp"
)
list(REMOVE_ITEM MODULE_SOURCES "${CMAKE_SOURCE_DIR}/algorithm/main.cpp") # 排除特定文件
list(REMOVE_ITEM MODULE_SOURCES "${CMAKE_SOURCE_DIR}/device/NIRDevice.cpp") # 排除特定文件
set(SOURCES
    ${MAIN_SOURCES}
    ${MODULE_SOURCES}
)

# 资源文件
qt_add_resources(SOURCES "${CMAKE_SOURCE_DIR}/resource.qrc")

# 可执行文件
qt_add_executable(${PROJECT_NAME} ${SOURCES} ${CMAKE_SOURCE_DIR}/appicon.rc)

# 指定结果输出目录
set(OUTPUT_DIR "${CMAKE_BINARY_DIR}/$<CONFIG>")
set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY "${OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY "${OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${OUTPUT_DIR}"
)

# 屏蔽特定警告
target_compile_options(${PROJECT_NAME} PRIVATE /wd4996 /wd4930 /wd4834)

# 添加预编译头支持
target_precompile_headers(${PROJECT_NAME} PRIVATE
    "${CMAKE_SOURCE_DIR}/kprojecthpp/precompiled.h"
)

# 头文件目录
set(INCLUDE_DIRS
    "${CMAKE_SOURCE_DIR}"
    "${CMAKE_SOURCE_DIR}/myoverloadui"
    "${CMAKE_SOURCE_DIR}/algorithm"
    "${CMAKE_SOURCE_DIR}/algorithm/include"
    "${CMAKE_SOURCE_DIR}/device"
    "${CMAKE_SOURCE_DIR}/devicesim"
    "${CMAKE_SOURCE_DIR}/deviceui"
    "${CMAKE_SOURCE_DIR}/StateMachine"
    "${CMAKE_SOURCE_DIR}/kcommonhpp"
    "${CMAKE_SOURCE_DIR}/kprojecthpp"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/sqlite/include"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/OrbbecSDK_v2/OrbbecSDK/include"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/include"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/sourcecode/json/include"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/XCore/xCoreSDK/include"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/sourcecode/eigen-3.4.0"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/sourcecode/asio-1.30.2/include"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/NetworkSDK/include"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/sourcecode/spdlog-1.x/include"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/DahengSDK/inc"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/include"
)
target_include_directories(${PROJECT_NAME} PRIVATE ${INCLUDE_DIRS})

# 链接目录
set(LINK_DIRS
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/sqlite/lib"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/OrbbecSDK_v2/OrbbecSDK/lib"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/lib"
    "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/NetworkSDK/lib"
    "${CMAKE_SOURCE_DIR}/thirdpartylibrary/DahengSDK/lib/x64"
)
target_link_directories(${PROJECT_NAME} PRIVATE
    ${LINK_DIRS}
    "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug>"
    "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/thirdpartylibrary/XCore/xCoreSDK/lib/Windows/Release/64bit,${CMAKE_SOURCE_DIR}/thirdpartylibrary/XCore/xCoreSDK/lib/Windows/Debug/64bit>"
    "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/lib/Release,${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/lib/Debug>"
)

# 链接库
target_link_libraries(${PROJECT_NAME} PRIVATE
    Qt6::Widgets Qt6::Core Qt6::Gui Qt6::Network Qt6::Sql Qt6::SerialPort Qt6::Multimedia Qt6::MultimediaWidgets Qt6::PrintSupport Qt6::Concurrent
    $<$<CONFIG:Debug>:sqlite3>
    $<$<CONFIG:Release>:sqlite3>
    $<$<CONFIG:Debug>:OrbbecSDK>
    $<$<CONFIG:Release>:OrbbecSDK>
    $<$<CONFIG:Debug>:opencv_world4100d>
    $<$<CONFIG:Release>:opencv_world4100>
    $<$<CONFIG:Debug>:xCoreSDK>
    $<$<CONFIG:Release>:xCoreSDK>
    $<$<CONFIG:Debug>:LibNetwork_x64_Debug>
    $<$<CONFIG:Release>:LibNetwork_x64_Release>
    $<$<CONFIG:Debug>:m1dll>
    $<$<CONFIG:Release>:m1dll>
    GxIAPICPPEx
    MobileCommunication
)

# windows下配置生成PDB调试文件
if(MSVC)
    # 为Debug模式生成PDB文件
    set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} /Zi")
    set(CMAKE_EXE_LINKER_FLAGS_DEBUG "${CMAKE_EXE_LINKER_FLAGS_DEBUG} /DEBUG")

    # 为Release模式生成PDB文件；调试用，最终发布可考虑去掉
    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} /Zi")
    set(CMAKE_EXE_LINKER_FLAGS_RELEASE "${CMAKE_EXE_LINKER_FLAGS_RELEASE} /DEBUG")
endif()

if(WIN32)
    # 添加Qt依赖项
    add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND "${Qt6_INSTALL_DIR}/bin/windeployqt.exe" --force $<TARGET_FILE:${PROJECT_NAME}> # 添加 Qt 依赖库
        COMMENT "Deploying Qt dependencies using windeployqt"
    )

    # 以防adb进程没结束影响编译
    add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND taskkill /IM adb.exe /F 2>nul || cmake -E true
    )

    # 添加第三方库依赖项
    add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/sqlite/lib/sqlite3.dll"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/thirdpartylibrary/XCore/xCoreSDK/lib/Windows/Release/64bit/xCoreSDK.dll,${CMAKE_SOURCE_DIR}/thirdpartylibrary/XCore/xCoreSDK/lib/Windows/Debug/64bit/xCoreSDK.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/NetworkSDK/bin/LibNetwork_x64_Release.dll,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/NetworkSDK/bin/LibNetwork_x64_Debug.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/bin/opencv_world4100.dll,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/bin/opencv_world4100d.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/bin/opencv_videoio_msmf4100_64.dll,${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/bin/opencv_videoio_msmf4100_64d.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/m1dll.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/m1dll.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/m1dll.pdb,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/m1dll.pdb>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/opencv_world480.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/opencv_world480d.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/onnxruntime.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/onnxruntime.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/onnxruntime_providers_cuda.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/onnxruntime_providers_cuda.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/onnxruntime_providers_shared.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/onnxruntime_providers_shared.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/onnxruntime_providers_tensorrt.dll,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/onnxruntime_providers_tensorrt.dll>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/coco8.yaml,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/coco8.yaml>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/yolov8n.onnx,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/yolov8n.onnx>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/vessel.onnx,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/vessel.onnx>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/wy.onnx,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/wy.onnx>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/color-arm-point.onnx,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/color-arm-point.onnx>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/params.json,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/params.json>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/algorithm/lib/Release/calib_param.bin,${CMAKE_SOURCE_DIR}/algorithm/lib/Debug/calib_param.bin>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_SOURCE_DIR}/opensourcelibrary/bin/opencv/build/x64/vc16/bin/opencv_videoio_ffmpeg4100_64.dll"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/klog/"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/dependencies/"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/thirdpartylibrary/DahengSDK/dll/x64/"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "$<IF:$<CONFIG:Release>,${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/lib/Release/,${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/lib/Debug/>"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/thirdpartylibrary/StorkSDK/DLL/"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_SOURCE_DIR}/thirdpartylibrary/OrbbecSDK_v2/OrbbecSDK/lib/OrbbecSDK.dll"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/thirdpartylibrary/OrbbecSDK_v2/OrbbecSDK/lib/extensions"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>/extensions"
        COMMENT "Copying dependencies to the output directory"
    )
endif()

# 调试时删除stork的msvcp140d.dll，使用系统的，不然运行异常
if(CMAKE_BUILD_TYPE STREQUAL "Debug") 
    add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E rm -f "$<TARGET_FILE_DIR:${PROJECT_NAME}>/msvcp140d.dll"
        COMMENT "Deleting msvcp140d.dll"
    )
endif()

# 拷贝配置及资源
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    #    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_SOURCE_DIR}/config/" "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy
    "${CMAKE_SOURCE_DIR}/config/config.json"
    "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy
    "${CMAKE_SOURCE_DIR}/config/config_algo_image.json"
    "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy
    "${CMAKE_SOURCE_DIR}/config/config_algo_robot.json"
    "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy
    "${CMAKE_SOURCE_DIR}/config/readme-config.txt"
    "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy
    "${CMAKE_SOURCE_DIR}/config/readme-config_algo_image.txt"
    "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_SOURCE_DIR}/customer_content/" "$<TARGET_FILE_DIR:${PROJECT_NAME}>/customer_content"
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_SOURCE_DIR}/docs/" "$<TARGET_FILE_DIR:${PROJECT_NAME}>"
    COMMENT "Copying config and resource files to the output directory"
)

if(MSVC)
    # 删除临时文件
    add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E rm -f "$<TARGET_FILE_DIR:${PROJECT_NAME}>/${PROJECT_NAME}.ilk"
        COMMENT "Deleting temporary files"
    )
endif()

# 打印编译器相关的选项
message(STATUS "--- CXX Flags: ${CMAKE_CXX_FLAGS}")

# 打印 MSVC 特定的编译选项
if(MSVC)
    message(STATUS "--- CXX Debug Flags: ${CMAKE_CXX_FLAGS_DEBUG}")
    message(STATUS "--- CXX Release Flags: ${CMAKE_CXX_FLAGS_RELEASE}")
endif()
