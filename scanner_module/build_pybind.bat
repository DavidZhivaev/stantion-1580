@echo off
echo ========================================
echo Building scanner_hal Python module
echo ========================================
echo.

:: Set paths for dependencies
set "OpenCV_DIR=C:\Libraries\opencv\build"
set "DTWAIN_ROOT=C:\Libraries\dtwain"

:: Create build directory
mkdir build 2>nul
cd build

echo [INFO] Configuring CMake...
cmake -DBUILD_PYTHON_MODULE=ON -DOpenCV_DIR="%OpenCV_DIR%" -DDTWAIN_ROOT="%DTWAIN_ROOT%" ..
if %ERRORLEVEL% neq 0 (
    echo [ERROR] CMake configuration failed!
    cd ..
    pause
    exit /b 1
)

echo.
echo [INFO] Building...
cmake --build . --config Release
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed!
    cd ..
    pause
    exit /b 1
)

echo.
echo [INFO] Copying scanner_hal.pyd to project root...
copy Release\scanner_hal*.pyd ..\..\ 2>nul
if %ERRORLEVEL% neq 0 (
    copy lib\Release\scanner_hal*.pyd ..\..\ 2>nul
)

cd ..

echo.
echo ========================================
echo Build complete!
echo scanner_hal.pyd copied to project root
echo ========================================
echo.
pause
