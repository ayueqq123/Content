@echo off
REM ── Nuitka build script for Telegram Bot Desktop ──
REM
REM Prerequisites:
REM   pip install nuitka
REM   A C compiler (MSVC / MinGW-w64) — Nuitka will prompt if missing
REM
REM Usage:
REM   build.bat              — build with console (for debugging)
REM   build.bat --release    — build without console window

setlocal

set RELEASE=0
if "%1"=="--release" set RELEASE=1

echo [*] Building TGBot with Nuitka ...

if %RELEASE%==1 (
    python -m nuitka ^
        --standalone ^
        --onefile ^
        --windows-console-mode=disable ^
        --enable-plugin=pyqt6 ^
        --include-package=core ^
        --include-package=bot ^
        --include-package=ui ^
        --include-package=license ^
        --output-filename=TGBot.exe ^
        --remove-output ^
        main.py
) else (
    python -m nuitka ^
        --standalone ^
        --onefile ^
        --enable-plugin=pyqt6 ^
        --include-package=core ^
        --include-package=bot ^
        --include-package=ui ^
        --include-package=license ^
        --output-filename=TGBot.exe ^
        --remove-output ^
        main.py
)

if %ERRORLEVEL%==0 (
    echo [+] Build succeeded: TGBot.exe
) else (
    echo [-] Build failed
    exit /b 1
)
