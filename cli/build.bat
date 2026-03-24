@echo off
REM Copyright (c) 2026 Alessandro Ricco
REM Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
REM See LICENSE file for details.

REM Build script for TestCore CLI (Windows/MinGW)

echo.
echo ========================================
echo   Building TestCore CLI Client
echo ========================================
echo.

REM Check if gcc is available
where gcc >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] GCC not found! Please install MinGW-w64
    echo Download from: https://sourceforge.net/projects/mingw-w64/
    echo Or install via chocolatey: choco install mingw
    pause
    exit /b 1
)

echo [INFO] GCC found:
gcc --version | findstr "gcc"
echo.

echo [INFO] Compiling testcore_cli.c + linenoise...
gcc -Wall -O2 testcore_cli.c linenoise/linenoise.c linenoise/stringbuf.c linenoise/utf8.c -o testcore_cli.exe -lws2_32 -s

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] Build completed!
    echo.
    echo Executable: testcore_cli.exe
    echo Size:
    dir testcore_cli.exe | findstr "testcore_cli.exe"
    echo.
    echo Usage: testcore_cli.exe [-h host] [-p port]
    echo Default: 127.0.0.1:6399
) else (
    echo.
    echo [ERROR] Build failed!
    echo Check the error messages above.
    pause
    exit /b 1
)

echo.
pause
