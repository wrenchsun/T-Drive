@echo off
rem T-Drive Toon (Maya tool) installer / updater.
rem Put this file in your Maya project folder and double-click it.
rem  - The folder it sits in becomes the T-Drive project if it is a Maya project (workspace.mel) or has looks\.
rem  - Otherwise the last Maya project is used, or a folder picker is shown.
rem  - Running it again updates the tool to the latest release.
rem Keep this file ASCII only: cmd misreads multi-byte (UTF-8) lines in batch files. Messages in Japanese are in install.ps1.
rem TD_DIR / TD_URL / TD_EXTRA (extra install.ps1 arguments) can be overridden for tests. See docs/13 section 2.
setlocal
if not defined TD_DIR set "TD_DIR=%LOCALAPPDATA%\TDriveToon\T-Drive"
if not defined TD_URL set "TD_URL=https://github.com/wrenchsun/T-Drive.git"
set "GIT_LFS_SKIP_SMUDGE=1"

where git >nul 2>nul
if errorlevel 1 goto :nogit

if exist "%TD_DIR%\.git" goto :install
echo Downloading T-Drive Toon (first time only)...
git clone "%TD_URL%" "%TD_DIR%"
if errorlevel 1 goto :noclone

:install
powershell -NoProfile -ExecutionPolicy Bypass -File "%TD_DIR%\tools\install.ps1" -Release -ProjectHint "%~dp0." %TD_EXTRA% %*
exit /b %errorlevel%

:nogit
echo [ERROR] git was not found. Install Git for Windows (https://git-scm.com/) and run this again.
pause
exit /b 1

:noclone
echo [ERROR] Could not download. Check that you can access github.com/wrenchsun/T-Drive (private repository).
pause
exit /b 1
