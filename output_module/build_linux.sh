#!/bin/sh
# Build script for output_module on Linux (sh-compatible)

echo "========================================"
echo "Building output_module (PDF/ZIP generator)"
echo "========================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check for Python3
if ! command -v python3 > /dev/null 2>&1; then
    echo "[ERROR] Python3 not found!"
    echo "Install with: sudo apt install python3 python3-dev"
    exit 1
fi
echo "[OK] Python3 found: $(python3 --version)"

# Check for cmake
if ! command -v cmake > /dev/null 2>&1; then
    echo "[ERROR] CMake not found!"
    echo "Install with: sudo apt install cmake"
    exit 1
fi
echo "[OK] CMake found: $(cmake --version | head -n1)"

# Check for pybind11 and install if missing
if ! python3 -c "import pybind11" 2>/dev/null; then
    echo "[WARNING] pybind11 not found, installing..."
    pip3 install --user pybind11
    if ! python3 -c "import pybind11" 2>/dev/null; then
        echo "[ERROR] Failed to install pybind11"
        exit 1
    fi
fi
echo "[OK] pybind11 found"

# Get pybind11 cmake directory
PYBIND11_DIR=$(python3 -m pybind11 --cmakedir 2>/dev/null)
if [ -z "$PYBIND11_DIR" ]; then
    echo "[ERROR] Could not get pybind11 cmake directory"
    exit 1
fi
echo "[OK] pybind11 cmake dir: $PYBIND11_DIR"

# Check for Python development headers
PYTHON_INCLUDE=$(python3 -c "import sysconfig; print(sysconfig.get_path('include'))" 2>/dev/null)
if [ ! -f "$PYTHON_INCLUDE/Python.h" ]; then
    echo "[ERROR] Python development headers not found!"
    echo "Install with: sudo apt install python3-dev"
    exit 1
fi
echo "[OK] Python headers found: $PYTHON_INCLUDE"

# Clean and create build directory
echo ""
echo "[INFO] Preparing build directory..."
rm -rf build
mkdir -p build
cd build

# Configure
echo ""
echo "[INFO] Configuring CMake..."
cmake \
    -DCMAKE_BUILD_TYPE=Release \
    -Dpybind11_DIR="$PYBIND11_DIR" \
    ..

if [ $? -ne 0 ]; then
    echo "[ERROR] CMake configuration failed!"
    exit 1
fi

# Build
echo ""
echo "[INFO] Building..."
NPROC=$(nproc 2>/dev/null || echo 4)
cmake --build . --parallel "$NPROC"

if [ $? -ne 0 ]; then
    echo "[ERROR] Build failed!"
    exit 1
fi

# Copy .so to project root
echo ""
echo "[INFO] Looking for output_generator_cpp.so..."
SO_FILE=$(find . -name "output_generator_cpp*.so" -type f 2>/dev/null | head -1)

if [ -n "$SO_FILE" ]; then
    cp "$SO_FILE" ../../
    echo "[OK] Copied $(basename "$SO_FILE") to project root"
else
    echo "[WARNING] No .so file found!"
    echo ""
    echo "Build produced these files:"
    ls -la
    echo ""
    echo "CMake pybind11 status:"
    grep -i pybind CMakeCache.txt 2>/dev/null || echo "  (not found in cache)"
    echo ""
    echo "CMake Python3 status:"
    grep -i python CMakeCache.txt 2>/dev/null || echo "  (not found in cache)"
    exit 1
fi

echo ""
echo "========================================"
echo "Build complete!"
echo "========================================"
echo ""
echo "Test import with:"
echo "  cd $(dirname "$SCRIPT_DIR")"
echo "  python3 -c \"import output_generator_cpp; print('OK')\""
echo ""
