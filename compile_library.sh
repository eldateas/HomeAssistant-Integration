#!/bin/bash

# ELDAT RxModule C Library Compilation Script  
# This script compiles the RxModule.c library for use with the Home Assistant integration
# The library is now located in transceivers/rx11/ directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RX11_DIR="$SCRIPT_DIR/transceivers/rx11"

echo "=== ELDAT RxModule Library Compilation ==="

# Check if RX11 directory exists
if [ ! -d "$RX11_DIR" ]; then
    echo "Error: RX11 directory not found at $RX11_DIR"
    exit 1
fi

cd "$RX11_DIR"

# Check if source files exist in RX11 directory
if [ ! -f "RxModule.c" ]; then
    echo "Error: RxModule.c not found in $RX11_DIR"
    echo "Expected location: transceivers/rx11/RxModule.c"
    exit 1
fi

if [ ! -f "RxModule.h" ]; then
    echo "Error: RxModule.h not found in $RX11_DIR"
    echo "Expected location: transceivers/rx11/RxModule.h"
    exit 1
fi

echo "Source files found in RX11 directory:"
echo "  - $RX11_DIR/RxModule.c"
echo "  - $RX11_DIR/RxModule.h"

# Check for GCC
if ! command -v gcc &> /dev/null; then
    echo "Error: GCC not found. Please install build-essential:"
    echo "  sudo apt-get install build-essential"
    exit 1
fi

echo "Compiler: $(gcc --version | head -n1)"

# Compile the library
echo ""
echo "Compiling shared library..."

COMPILE_CMD=(
    gcc
    -shared
    -fPIC
    -pthread
    -O2
    -Wall
    -Wextra
    -o "RxModule.so"
    "RxModule.c"
)

echo "Command: ${COMPILE_CMD[*]}"

if "${COMPILE_CMD[@]}"; then
    echo ""
    echo "✅ Compilation successful!"
    echo "Created: RxModule.so"
    
    # Check if file was created and get size
    if [ -f "RxModule.so" ]; then
        SIZE=$(stat -c%s "RxModule.so" 2>/dev/null || stat -f%z "RxModule.so" 2>/dev/null || echo "unknown")
        echo "Size: $SIZE bytes"
        
        # Make executable
        chmod +x "RxModule.so"
        echo "Permissions set to executable"
        
        echo ""
        echo "The RX11 library is now ready for use with the ELDAT Home Assistant integration."
        echo ""
        echo "To test the library, you can run:"
        echo "  cd $RX11_DIR"
        echo "  python3 -c \"import ctypes; lib = ctypes.CDLL('./RxModule.so'); print('RX11 library loaded successfully')\""
    else
        echo "❌ Error: RxModule.so was not created"
        exit 1
    fi
else
    echo ""
    echo "❌ Compilation failed!"
    echo ""
    echo "Common solutions:"
    echo "1. Install build tools: sudo apt-get install build-essential"
    echo "2. Install threading library: sudo apt-get install libc6-dev"
    echo "3. Check if all source files are present and readable"
    exit 1
fi

echo ""
echo "=== Compilation Complete ==="