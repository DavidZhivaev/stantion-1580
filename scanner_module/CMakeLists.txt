cmake_minimum_required(VERSION 3.12)

project(ScannerModule
    VERSION 1.0.0
    DESCRIPTION "Cross-platform scanner module with Python integration"
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BUILD_SHARED_LIBS "Build shared libraries instead of static" OFF)
option(BUILD_PYTHON_MODULE "Build Python extension module (scanner_hal)" ON)
option(ENABLE_INSTALL "Enable installation rules" ON)
option(ENABLE_PACKAGING "Enable CPack packaging" ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type (Debug, Release, RelWithDebInfo, MinSizeRel)" FORCE)
endif()

message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "System: ${CMAKE_SYSTEM_NAME}")
message(STATUS "Processor: ${CMAKE_SYSTEM_PROCESSOR}")

if(WIN32)
    set(PLATFORM_NAME "Windows")
elseif(UNIX AND NOT APPLE)
    set(PLATFORM_NAME "Linux")
elseif(APPLE)
    set(PLATFORM_NAME "macOS")
else()
    set(PLATFORM_NAME "Unknown")
endif()

message(STATUS "Platform: ${PLATFORM_NAME}")

find_package(OpenCV REQUIRED)
if(OpenCV_FOUND)
    message(STATUS "OpenCV found: ${OpenCV_VERSION}")
    message(STATUS "OpenCV include dirs: ${OpenCV_INCLUDE_DIRS}")
    message(STATUS "OpenCV libraries: ${OpenCV_LIBS}")
else()
    message(FATAL_ERROR "OpenCV not found! Please install OpenCV.")
endif()

find_package(Python COMPONENTS Interpreter Development REQUIRED)
if(Python_FOUND)
    message(STATUS "Python found: ${Python_VERSION}")
    message(STATUS "Python include dirs: ${Python_INCLUDE_DIRS}")
    message(STATUS "Python libraries: ${Python_LIBRARIES}")
else()
    message(FATAL_ERROR "Python not found! Please install Python development files.")
endif()

find_package(pybind11 REQUIRED)
if(pybind11_FOUND)
    message(STATUS "pybind11 found: ${pybind11_VERSION}")
else()
    message(FATAL_ERROR "pybind11 not found! Please install pybind11.")
endif()

if(WIN32)
    message(STATUS "Configuring for Windows with DTWAIN...")

    set(DTWAIN_ROOT "" CACHE PATH "Root directory of DTWAIN library")

    if(DTWAIN_ROOT)
        set(DTWAIN_INCLUDE_DIR "${DTWAIN_ROOT}/include")
        if(CMAKE_SIZEOF_VOID_P EQUAL 8)
            set(DTWAIN_LIBRARY "${DTWAIN_ROOT}/lib/dtwain64.lib")
        else()
            set(DTWAIN_LIBRARY "${DTWAIN_ROOT}/lib/dtwain32.lib")
        endif()
    else()
        set(DTWAIN_INCLUDE_DIR "C:/Libraries/dtwain/include" CACHE PATH "DTWAIN include directory")
        if(CMAKE_SIZEOF_VOID_P EQUAL 8)
            set(DTWAIN_LIBRARY "C:/Libraries/dtwain/lib/dtwain64.lib" CACHE FILEPATH "DTWAIN library")
        else()
            set(DTWAIN_LIBRARY "C:/Libraries/dtwain/lib/dtwain32.lib" CACHE FILEPATH "DTWAIN library")
        endif()
    endif()

    if(NOT EXISTS ${DTWAIN_INCLUDE_DIR})
        message(WARNING "DTWAIN include directory not found: ${DTWAIN_INCLUDE_DIR}")
        message(WARNING "Please set DTWAIN_ROOT or DTWAIN_INCLUDE_DIR correctly")
    endif()

    if(NOT EXISTS ${DTWAIN_LIBRARY})
        message(WARNING "DTWAIN library not found: ${DTWAIN_LIBRARY}")
        message(WARNING "Please set DTWAIN_ROOT or DTWAIN_LIBRARY correctly")
    endif()

    message(STATUS "DTWAIN include: ${DTWAIN_INCLUDE_DIR}")
    message(STATUS "DTWAIN library: ${DTWAIN_LIBRARY}")

elseif(UNIX AND NOT APPLE)
    message(STATUS "Configuring for Linux with SANE...")

    find_path(SANE_INCLUDE_DIR NAMES sane/sane.h
        HINTS /usr/include /usr/local/include
    )
    find_library(SANE_LIBRARY NAMES sane libsane
        HINTS /usr/lib /usr/local/lib
    )

    if(NOT SANE_INCLUDE_DIR OR NOT SANE_LIBRARY)
        message(FATAL_ERROR
            "SANE library not found!\n"
            "Please install SANE development files:\n"
            "  Ubuntu/Debian: sudo apt install libsane-dev\n"
            "  Astra Linux: sudo apt install libsane-dev\n"
            "  RED OS: sudo dnf install sane-backends-devel\n"
            "  Fedora/CentOS: sudo dnf install sane-backends-devel"
        )
    endif()

    message(STATUS "SANE include: ${SANE_INCLUDE_DIR}")
    message(STATUS "SANE library: ${SANE_LIBRARY}")

else()
    message(FATAL_ERROR "Unsupported platform: ${CMAKE_SYSTEM_NAME}")
endif()

set(SCANNER_SOURCES
    main.cpp
)

set(SCANNER_HEADERS
    ScannerHAL.hpp
)

add_executable(scanner_app ${SCANNER_SOURCES} ${SCANNER_HEADERS})

set_target_properties(scanner_app PROPERTIES
    OUTPUT_NAME "scanner_module"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

target_include_directories(scanner_app PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${OpenCV_INCLUDE_DIRS}
    ${Python_INCLUDE_DIRS}
)

if(WIN32)
    target_include_directories(scanner_app PRIVATE ${DTWAIN_INCLUDE_DIR})
else()
    target_include_directories(scanner_app PRIVATE ${SANE_INCLUDE_DIR})
endif()

target_link_libraries(scanner_app PRIVATE
    ${OpenCV_LIBS}
    pybind11::embed
)

if(WIN32)
    target_link_libraries(scanner_app PRIVATE ${DTWAIN_LIBRARY})
else()
    target_link_libraries(scanner_app PRIVATE ${SANE_LIBRARY})
endif()

if(BUILD_PYTHON_MODULE)
    pybind11_add_module(scanner_hal python_bindings.cpp)

    target_include_directories(scanner_hal PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${OpenCV_INCLUDE_DIRS}
    )

    if(WIN32)
        target_include_directories(scanner_hal PRIVATE ${DTWAIN_INCLUDE_DIR})
    else()
        target_include_directories(scanner_hal PRIVATE ${SANE_INCLUDE_DIR})
    endif()

    target_link_libraries(scanner_hal PRIVATE ${OpenCV_LIBS})

    if(WIN32)
        target_link_libraries(scanner_hal PRIVATE ${DTWAIN_LIBRARY})
    else()
        target_link_libraries(scanner_hal PRIVATE ${SANE_LIBRARY})
    endif()

    set_target_properties(scanner_hal PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
    )

    if(MSVC)
        target_compile_options(scanner_hal PRIVATE /W4 /utf-8)
    else()
        target_compile_options(scanner_hal PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    message(STATUS "Python module scanner_hal will be built")
endif()

if(MSVC)
    target_compile_options(scanner_app PRIVATE
        /W4
        /utf-8
    )
    set_target_properties(scanner_app PROPERTIES
        WIN32_EXECUTABLE FALSE
    )
else()
    target_compile_options(scanner_app PRIVATE
        -Wall
        -Wextra
        -Wpedantic
    )
endif()

if(UNIX)
    set_target_properties(scanner_app PROPERTIES
        INSTALL_RPATH "$ORIGIN/../lib"
        BUILD_WITH_INSTALL_RPATH FALSE
        INSTALL_RPATH_USE_LINK_PATH TRUE
    )
endif()

if(ENABLE_INSTALL)
    include(GNUInstallDirs)

    install(TARGETS scanner_app
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        COMPONENT runtime
    )

    install(FILES ${SCANNER_HEADERS}
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/scanner_module
        COMPONENT development
    )

    if(BUILD_PYTHON_MODULE)
        install(TARGETS scanner_hal
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
            COMPONENT runtime
        )
    endif()

    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/recognizer.py")
        install(FILES recognizer.py
            DESTINATION ${CMAKE_INSTALL_BINDIR}
            COMPONENT runtime
        )
    endif()

    if(WIN32)
        if(DTWAIN_ROOT)
            if(CMAKE_SIZEOF_VOID_P EQUAL 8)
                set(DTWAIN_DLL "${DTWAIN_ROOT}/bin/dtwain64.dll")
            else()
                set(DTWAIN_DLL "${DTWAIN_ROOT}/bin/dtwain32.dll")
            endif()

            if(EXISTS ${DTWAIN_DLL})
                install(FILES ${DTWAIN_DLL}
                    DESTINATION ${CMAKE_INSTALL_BINDIR}
                    COMPONENT runtime
                )
            endif()
        endif()
    endif()

    message(STATUS "Installation enabled:")
    message(STATUS "  Install prefix: ${CMAKE_INSTALL_PREFIX}")
    message(STATUS "  Binary dir: ${CMAKE_INSTALL_BINDIR}")
    message(STATUS "  Include dir: ${CMAKE_INSTALL_INCLUDEDIR}")
endif()

if(ENABLE_PACKAGING)
    set(CPACK_PACKAGE_NAME "ScannerModule")
    set(CPACK_PACKAGE_VENDOR "Your Company")
    set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Cross-platform scanner module with OCR")
    set(CPACK_PACKAGE_VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
    set(CPACK_PACKAGE_VERSION_MINOR ${PROJECT_VERSION_MINOR})
    set(CPACK_PACKAGE_VERSION_PATCH ${PROJECT_VERSION_PATCH})
    set(CPACK_PACKAGE_VERSION ${PROJECT_VERSION})
    set(CPACK_PACKAGE_INSTALL_DIRECTORY "ScannerModule")

    if(WIN32)
        set(CPACK_GENERATOR "NSIS;ZIP")
        set(CPACK_NSIS_DISPLAY_NAME "Scanner Module")
        set(CPACK_NSIS_PACKAGE_NAME "Scanner Module ${PROJECT_VERSION}")
        set(CPACK_NSIS_MODIFY_PATH ON)
        set(CPACK_NSIS_ENABLE_UNINSTALL_BEFORE_INSTALL ON)

        set(CPACK_NSIS_CREATE_ICONS_EXTRA
            "CreateShortCut '$SMPROGRAMS\\\\$STARTMENU_FOLDER\\\\Scanner Module.lnk' '$INSTDIR\\\\bin\\\\scanner_module.exe'"
        )
        set(CPACK_NSIS_DELETE_ICONS_EXTRA
            "Delete '$SMPROGRAMS\\\\$STARTMENU_FOLDER\\\\Scanner Module.lnk'"
        )

    elseif(UNIX AND NOT APPLE)
        set(CPACK_GENERATOR "DEB;RPM;TGZ")

        set(CPACK_DEBIAN_PACKAGE_MAINTAINER "Your Name <your.email@example.com>")
        set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
        set(CPACK_DEBIAN_PACKAGE_DEPENDS "libopencv-dev, python3-dev, libsane")
        set(CPACK_DEBIAN_PACKAGE_DESCRIPTION
            "Scanner Module - Cross-platform document scanner with OCR\n"
            " This package provides a hardware abstraction layer for document\n"
            " scanners using TWAIN (Windows) and SANE (Linux) backends."
        )

        set(CPACK_RPM_PACKAGE_LICENSE "Proprietary")
        set(CPACK_RPM_PACKAGE_GROUP "Applications/Productivity")
        set(CPACK_RPM_PACKAGE_REQUIRES "opencv, python3-devel, sane-backends")
        set(CPACK_RPM_PACKAGE_DESCRIPTION
            "Scanner Module - Cross-platform document scanner with OCR capabilities"
        )

    elseif(APPLE)
        set(CPACK_GENERATOR "DragNDrop;TGZ")
        set(CPACK_DMG_VOLUME_NAME "Scanner Module ${PROJECT_VERSION}")
    endif()

    set(CPACK_COMPONENTS_ALL runtime development)
    set(CPACK_COMPONENT_RUNTIME_DISPLAY_NAME "Runtime")
    set(CPACK_COMPONENT_RUNTIME_DESCRIPTION "Scanner application executable")
    set(CPACK_COMPONENT_DEVELOPMENT_DISPLAY_NAME "Development Files")
    set(CPACK_COMPONENT_DEVELOPMENT_DESCRIPTION "Header files for development")

    include(CPack)

    message(STATUS "Packaging enabled:")
    message(STATUS "  Package name: ${CPACK_PACKAGE_NAME}")
    message(STATUS "  Package version: ${CPACK_PACKAGE_VERSION}")
    if(WIN32)
        message(STATUS "  Generators: NSIS, ZIP")
    elseif(UNIX AND NOT APPLE)
        message(STATUS "  Generators: DEB, RPM, TGZ")
    endif()
endif()

message(STATUS "")
message(STATUS "========================================")
message(STATUS "Scanner Module Configuration Summary")
message(STATUS "========================================")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Platform: ${PLATFORM_NAME}")
message(STATUS "C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "")
message(STATUS "Dependencies:")
message(STATUS "  OpenCV: ${OpenCV_VERSION}")
message(STATUS "  Python: ${Python_VERSION}")
message(STATUS "  pybind11: ${pybind11_VERSION}")
if(WIN32)
    message(STATUS "  DTWAIN: ${DTWAIN_INCLUDE_DIR}")
else()
    message(STATUS "  SANE: ${SANE_LIBRARY}")
endif()
message(STATUS "")
message(STATUS "Options:")
message(STATUS "  Python module: ${BUILD_PYTHON_MODULE}")
message(STATUS "  Install: ${ENABLE_INSTALL}")
message(STATUS "  Packaging: ${ENABLE_PACKAGING}")
if(ENABLE_INSTALL)
    message(STATUS "  Install prefix: ${CMAKE_INSTALL_PREFIX}")
endif()
message(STATUS "========================================")
message(STATUS "")
