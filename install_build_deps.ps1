# install_build_deps.ps1
# Installs all dependencies for building scanner_module on Windows
# Run as Administrator: powershell -ExecutionPolicy Bypass -File install_build_deps.ps1

param(
    [switch]$SkipVS,
    [switch]$SkipOpenCV,
    [switch]$SkipDTWAIN
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Scanner Module - Dependency Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[WARNING] Not running as Administrator. Some installations may fail." -ForegroundColor Yellow
    Write-Host "Consider re-running: powershell -ExecutionPolicy Bypass -File install_build_deps.ps1" -ForegroundColor Yellow
    Write-Host ""
}

# Create temp directory
$tempDir = "$env:TEMP\scanner_deps"
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

# ============================================
# 1. Install CMake
# ============================================
Write-Host "[1/6] Checking CMake..." -ForegroundColor Green

$cmakeInstalled = Get-Command cmake -ErrorAction SilentlyContinue
if ($cmakeInstalled) {
    Write-Host "  CMake already installed: $((cmake --version | Select-Object -First 1))" -ForegroundColor Gray
} else {
    Write-Host "  Installing CMake via winget..." -ForegroundColor Yellow
    winget install Kitware.CMake --silent --accept-package-agreements --accept-source-agreements

    # Add to PATH for current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "  CMake installed. Restart terminal after script completes." -ForegroundColor Green
}

# ============================================
# 2. Install Visual Studio Build Tools
# ============================================
Write-Host ""
Write-Host "[2/6] Checking Visual Studio Build Tools..." -ForegroundColor Green

if ($SkipVS) {
    Write-Host "  Skipping Visual Studio (--SkipVS flag)" -ForegroundColor Gray
} else {
    $vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $vsInstalled = $false

    if (Test-Path $vsWhere) {
        $vsPath = & $vsWhere -latest -property installationPath 2>$null
        if ($vsPath) {
            Write-Host "  Visual Studio found: $vsPath" -ForegroundColor Gray
            $vsInstalled = $true
        }
    }

    if (-not $vsInstalled) {
        Write-Host "  Installing Visual Studio Build Tools 2022..." -ForegroundColor Yellow
        Write-Host "  This may take 10-20 minutes..." -ForegroundColor Yellow

        winget install Microsoft.VisualStudio.2022.BuildTools --silent --accept-package-agreements --accept-source-agreements --override "--wait --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows11SDK.22621 --includeRecommended"

        Write-Host "  Visual Studio Build Tools installed." -ForegroundColor Green
    }
}

# ============================================
# 3. Install Python packages (pybind11)
# ============================================
Write-Host ""
Write-Host "[3/6] Installing Python packages..." -ForegroundColor Green

python -m pip install --upgrade pip
python -m pip install pybind11 numpy

Write-Host "  pybind11 installed." -ForegroundColor Gray

# ============================================
# 4. Install OpenCV
# ============================================
Write-Host ""
Write-Host "[4/6] Checking OpenCV..." -ForegroundColor Green

$opencvDir = "C:\Libraries\opencv"

if ($SkipOpenCV) {
    Write-Host "  Skipping OpenCV (--SkipOpenCV flag)" -ForegroundColor Gray
} elseif (Test-Path "$opencvDir\build\OpenCVConfig.cmake") {
    Write-Host "  OpenCV already installed at $opencvDir" -ForegroundColor Gray
} else {
    Write-Host "  Downloading OpenCV 4.8.0..." -ForegroundColor Yellow

    $opencvUrl = "https://github.com/opencv/opencv/releases/download/4.8.0/opencv-4.8.0-windows.exe"
    $opencvInstaller = "$tempDir\opencv-4.8.0-windows.exe"

    if (-not (Test-Path $opencvInstaller)) {
        Invoke-WebRequest -Uri $opencvUrl -OutFile $opencvInstaller -UseBasicParsing
    }

    Write-Host "  Extracting OpenCV to C:\Libraries\opencv..." -ForegroundColor Yellow

    # Create directory
    New-Item -ItemType Directory -Force -Path "C:\Libraries" | Out-Null

    # Run self-extracting archive
    Start-Process -FilePath $opencvInstaller -ArgumentList "-o`"C:\Libraries`" -y" -Wait -NoNewWindow

    # Rename extracted folder
    if (Test-Path "C:\Libraries\opencv") {
        Remove-Item -Recurse -Force "C:\Libraries\opencv" -ErrorAction SilentlyContinue
    }
    Rename-Item "C:\Libraries\opencv-4.8.0-windows\opencv" "C:\Libraries\opencv" -ErrorAction SilentlyContinue

    Write-Host "  OpenCV installed to $opencvDir" -ForegroundColor Green

    # Set environment variable
    [System.Environment]::SetEnvironmentVariable("OpenCV_DIR", "$opencvDir\build", "User")
    $env:OpenCV_DIR = "$opencvDir\build"
}

# ============================================
# 5. Install DTWAIN SDK
# ============================================
Write-Host ""
Write-Host "[5/6] Checking DTWAIN SDK..." -ForegroundColor Green

$dtwainDir = "C:\Libraries\dtwain"

if ($SkipDTWAIN) {
    Write-Host "  Skipping DTWAIN (--SkipDTWAIN flag)" -ForegroundColor Gray
} elseif (Test-Path "$dtwainDir\include\dtwain.h") {
    Write-Host "  DTWAIN already installed at $dtwainDir" -ForegroundColor Gray
} else {
    Write-Host "  Downloading DTWAIN SDK (open source version)..." -ForegroundColor Yellow

    # DTWAIN open source from GitHub
    $dtwainUrl = "https://github.com/dynarithmic/twain_library/releases/download/v5.4.2/dtwain_library_5.4.2_binaries_windows.zip"
    $dtwainZip = "$tempDir\dtwain.zip"

    if (-not (Test-Path $dtwainZip)) {
        Write-Host "  Downloading from GitHub..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $dtwainUrl -OutFile $dtwainZip -UseBasicParsing
    }

    Write-Host "  Extracting DTWAIN to $dtwainDir..." -ForegroundColor Yellow

    # Create directory and extract
    New-Item -ItemType Directory -Force -Path $dtwainDir | Out-Null
    Expand-Archive -Path $dtwainZip -DestinationPath "$tempDir\dtwain_extract" -Force

    # Find and copy the contents
    $extractedDir = Get-ChildItem "$tempDir\dtwain_extract" -Directory | Select-Object -First 1
    if ($extractedDir) {
        Copy-Item -Path "$($extractedDir.FullName)\*" -Destination $dtwainDir -Recurse -Force
    }

    # Organize structure if needed (create include/lib/bin folders)
    if (-not (Test-Path "$dtwainDir\include")) {
        New-Item -ItemType Directory -Force -Path "$dtwainDir\include" | Out-Null
        # Move header files
        Get-ChildItem "$dtwainDir\*.h" -ErrorAction SilentlyContinue | Move-Item -Destination "$dtwainDir\include\" -Force
    }

    if (-not (Test-Path "$dtwainDir\lib")) {
        New-Item -ItemType Directory -Force -Path "$dtwainDir\lib" | Out-Null
        # Move lib files
        Get-ChildItem "$dtwainDir\*.lib" -Recurse -ErrorAction SilentlyContinue | Move-Item -Destination "$dtwainDir\lib\" -Force
    }

    if (-not (Test-Path "$dtwainDir\bin")) {
        New-Item -ItemType Directory -Force -Path "$dtwainDir\bin" | Out-Null
        # Move DLL files
        Get-ChildItem "$dtwainDir\*.dll" -Recurse -ErrorAction SilentlyContinue | Move-Item -Destination "$dtwainDir\bin\" -Force
    }

    Write-Host "  DTWAIN SDK installed to $dtwainDir" -ForegroundColor Green

    # Set environment variable
    [System.Environment]::SetEnvironmentVariable("DTWAIN_ROOT", $dtwainDir, "User")
    $env:DTWAIN_ROOT = $dtwainDir
}

# ============================================
# 6. Verify installations
# ============================================
Write-Host ""
Write-Host "[6/6] Verifying installations..." -ForegroundColor Green
Write-Host ""

$allGood = $true

# CMake
$cmakeCheck = Get-Command cmake -ErrorAction SilentlyContinue
if ($cmakeCheck) {
    Write-Host "  [OK] CMake: $(cmake --version | Select-Object -First 1)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] CMake - restart terminal and check PATH" -ForegroundColor Red
    $allGood = $false
}

# Visual Studio
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vsWhere) {
    $vsPath = & $vsWhere -latest -property installationPath 2>$null
    if ($vsPath) {
        Write-Host "  [OK] Visual Studio: $vsPath" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] Visual Studio Build Tools" -ForegroundColor Red
        $allGood = $false
    }
}

# pybind11
$pybind = python -c "import pybind11; print(pybind11.__version__)" 2>$null
if ($pybind) {
    Write-Host "  [OK] pybind11: $pybind" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] pybind11" -ForegroundColor Red
    $allGood = $false
}

# OpenCV
if (Test-Path "$opencvDir\build\OpenCVConfig.cmake") {
    Write-Host "  [OK] OpenCV: $opencvDir" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] OpenCV at $opencvDir" -ForegroundColor Red
    $allGood = $false
}

# DTWAIN
if (Test-Path "$dtwainDir\include") {
    Write-Host "  [OK] DTWAIN: $dtwainDir" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] DTWAIN at $dtwainDir" -ForegroundColor Red
    $allGood = $false
}

# ============================================
# Summary
# ============================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

if ($allGood) {
    Write-Host "All dependencies installed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. RESTART your terminal (to refresh PATH)" -ForegroundColor White
    Write-Host "  2. cd scanner_module" -ForegroundColor White
    Write-Host "  3. .\build_pybind.bat" -ForegroundColor White
    Write-Host ""
    Write-Host "Or run the full build:" -ForegroundColor Yellow
    Write-Host "  .\build_windows.bat" -ForegroundColor White
} else {
    Write-Host "Some dependencies are missing!" -ForegroundColor Red
    Write-Host "Check the [MISSING] items above and install manually." -ForegroundColor Yellow
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Cleanup temp files (optional)
# Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
