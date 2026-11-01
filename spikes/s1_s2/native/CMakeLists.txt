# Native Qt Quick item for spike S1/S2, built as a QML module plugin against the
# Qt SDK that matches the PySide6 wheel (6.12.0 msvc2022_64). Loaded from Python
# with QQmlEngine.addImportPath(<install dir>) and `import Specto.Spike`.
cmake_minimum_required(VERSION 3.21)
project(spectospike LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.12 REQUIRED COMPONENTS Core Gui Qml Quick OpenGL)
qt_standard_project_setup(REQUIRES 6.8)

# One DLL that is both the backing library and the plugin (PLUGIN_TARGET == target).
qt_add_qml_module(spectospike
    URI Specto.Spike
    VERSION 1.0
    PLUGIN_TARGET spectospike
    CLASS_NAME SpectoSpikePlugin
    OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/qml/Specto/Spike
    SOURCES
        textureitem.h
        textureitem.cpp
)
target_compile_definitions(spectospike PRIVATE SPECTOSPIKE_BUILD)
target_link_libraries(spectospike PRIVATE Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::OpenGL)

# The PySide6 wheel ships Qt6Core.dll etc. without import libs; at runtime the
# plugin resolves Qt6*.dll against the copies already loaded in the Python process.
if(MSVC)
    target_compile_options(spectospike PRIVATE /W3 /utf-8)
endif()

install(DIRECTORY ${CMAKE_BINARY_DIR}/qml/ DESTINATION qml)
