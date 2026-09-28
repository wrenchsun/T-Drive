@echo off
rem T-Drive Toon (Maya tool) installer / updater. Put this file in your Maya project folder and double-click.
rem The folder it sits in becomes the T-Drive project if it is a Maya project (has workspace.mel) or has looks\.
rem Otherwise the last Maya project is used, or a folder picker is shown. See docs/13 section 2.
setlocal
chcp 65001 >nul
rem TD_DIR / TD_URL / TD_MODULES can be overridden (tests)
if not defined TD_DIR set "TD_DIR=%LOCALAPPDATA%\TDriveToon\T-Drive"
if not defined TD_URL set "TD_URL=https://github.com/wrenchsun/T-Drive.git"
set "GIT_LFS_SKIP_SMUDGE=1"

where git >nul 2>nul
if errorlevel 1 goto :nogit

if exist "%TD_DIR%\.git" goto :install
echo T-Drive Toon を取得しています（初回のみ）...
git clone "%TD_URL%" "%TD_DIR%"
if errorlevel 1 goto :noclone

:install
powershell -NoProfile -ExecutionPolicy Bypass -File "%TD_DIR%\tools\install.ps1" -Release -ProjectHint "%~dp0." %TD_EXTRA% %*
exit /b %errorlevel%

:nogit
echo git が見つかりません。Git for Windows を入れてから、もう一度実行してください: https://git-scm.com/
pause
exit /b 1

:noclone
echo 取得できませんでした。GitHub の wrenchsun/T-Drive を読む権限があるか確認してください。
pause
exit /b 1
