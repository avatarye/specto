# _spectoqt: native Qt Quick items for specto-app, built as the QML module plugin
# `Specto.Native` against the Qt SDK that matches the PySide6 wheel (6.12.0,
# msvc2022_64 on Windows, gcc_64 on Linux). Python loads it with
# QQmlEngine.addImportPath(<specto_app/qml>) and QML does `import Specto.Native`. Driven by build.py (do not run cmake by hand unless you
# know the SDK/wheel version pin).
cmake_minimum_required(VERSION 3.21)
project(spectoqt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.12 REQUIRED COMPONENTS Core Gui Network Qml Quick QuickPrivate OpenGL)
qt_standard_project_setup(REQUIRES 6.8)

# Shaders are precompiled to .qsb by build.py with the qsb shipped in the PySide6 wheel
# (the SDK's qtshadertools archive is not needed); they land in SPECTO_QSB_DIR.
if(NOT DEFINED SPECTO_QSB_DIR)
    message(FATAL_ERROR "SPECTO_QSB_DIR not set: run build.py, it compiles the shaders first")
endif()

qt_add_qml_module(spectoqt
    URI Specto.Native
    VERSION 1.0
    PLUGIN_TARGET spectoqt
    CLASS_NAME SpectoNativePlugin
    OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/qml/Specto/Native
    SOURCES
        galleryitem.h
        galleryitem.cpp
        gallerynode.h
        gallerynode.cpp
        atlas.h
        atlas.cpp
        shm.h
        shm.cpp
        mpvapi.h
        mpvapi.cpp
        mpvitem.h
        mpvitem.cpp
        roundedclip.h
        roundedclip.cpp
        frameitem.h
        frameitem.cpp
)
qt_add_resources(spectoqt "spectoqt_shaders"
    PREFIX "/specto/shaders"
    BASE ${SPECTO_QSB_DIR}
    FILES
        ${SPECTO_QSB_DIR}/tile.vert.qsb
        ${SPECTO_QSB_DIR}/tile.frag.qsb
)
target_compile_definitions(spectoqt PRIVATE SPECTONATIVE_BUILD)

# libmpv: headers only at build time (tools/fetch_libmpv.py puts them in
# <SPECTO_LIBMPV_DIR>/include); the DLL is loaded at runtime by mpvapi.cpp, never linked.
if(NOT DEFINED SPECTO_LIBMPV_DIR)
    message(FATAL_ERROR "SPECTO_LIBMPV_DIR not set: run build.py (it finds the libmpv fetch)")
endif()
target_include_directories(spectoqt PRIVATE ${SPECTO_LIBMPV_DIR}/include)
target_compile_definitions(spectoqt PRIVATE SPECTO_LIBMPV_DIR="${SPECTO_LIBMPV_DIR}")
# QuickPrivate: RoundedClip sets the radius of the item's own clip node (private API,
# pinned to the SDK version like the rest of this plugin)
target_link_libraries(spectoqt PRIVATE Qt6::Core Qt6::Gui Qt6::Network Qt6::Qml Qt6::Quick Qt6::QuickPrivate Qt6::OpenGL)

# The PySide6 wheel ships Qt6Core.dll etc. without import libs; at runtime the plugin
# resolves Qt6*.dll against the copies already loaded in the Python process.
if(MSVC)
    target_compile_options(spectoqt PRIVATE /W3 /utf-8)
endif()

# Linux and macOS: the wheel ships libQt6*.so.6 (Qt*.framework on macOS) under
# PySide6/Qt/lib. The plugin's RUNPATH (LC_RPATH) points there (build.py passes
# SPECTO_QT_RUNTIME_LIB), never at the SDK, so any Qt library the process has not loaded
# yet still comes from the wheel. dladdr (mpvapi.cpp) needs libdl on older glibc.
if(UNIX)
    if(NOT DEFINED SPECTO_QT_RUNTIME_LIB)
        message(FATAL_ERROR "SPECTO_QT_RUNTIME_LIB not set: run build.py (PySide6/Qt/lib)")
    endif()
    set_target_properties(spectoqt PROPERTIES
        BUILD_WITH_INSTALL_RPATH ON
        INSTALL_RPATH "${SPECTO_QT_RUNTIME_LIB}")
    target_link_libraries(spectoqt PRIVATE ${CMAKE_DL_LIBS})
endif()

install(DIRECTORY ${CMAKE_BINARY_DIR}/qml/ DESTINATION qml)
