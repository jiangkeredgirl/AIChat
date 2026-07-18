@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem Ensure script runs from its own directory
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Git is not installed or not in PATH.
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Current directory is not a Git repository.
    exit /b 1
)

for /f "delims=" %%i in ('git rev-parse --abbrev-ref HEAD') do set GIT_BRANCH=%%i
for /f "delims=" %%i in ('git rev-parse HEAD') do set GIT_COMMIT_HASH=%%i
for /f "delims=" %%i in ('git rev-parse --short HEAD') do set GIT_COMMIT_SHORT_HASH=%%i
for /f "delims=" %%i in ('git show -s --format^=%%ci HEAD') do set GIT_COMMIT_DATE=%%i

rem Prefer exact tag on HEAD; fallback to nearest describe value
for /f "delims=" %%i in ('git describe --tags --exact-match 2^>nul') do set GIT_TAG=%%i
if not defined GIT_TAG (
    for /f "delims=" %%i in ('git describe --tags --always 2^>nul') do set GIT_TAG=%%i
)

rem Code version: prefer exact tag; fallback to describe output
for /f "delims=" %%i in ('git describe --tags --always --dirty 2^>nul') do set CODE_VERSION=%%i
for /f "delims=" %%i in ('git show -s --format^=%%s HEAD') do set GIT_COMMIT_COMMENT=%%i
for /f "delims=" %%i in ('git show -s --format^=%%an HEAD') do set GIT_COMMIT_AUTHOR=%%i
for /f "delims=" %%i in ('git rev-list --count HEAD') do set GIT_COMMIT_INDEX=%%i

echo Code Branch         : %GIT_BRANCH%
echo Code Version        : %CODE_VERSION%
echo Code Hash           : %GIT_COMMIT_HASH%
echo Code Short Hash     : %GIT_COMMIT_SHORT_HASH%
echo Code Tag            : %GIT_TAG%
echo Code Commit Time    : %GIT_COMMIT_DATE%
echo Code Commit Author  : %GIT_COMMIT_AUTHOR%
echo Code Commit Comment : %GIT_COMMIT_COMMENT%
echo Code Commit Index   : %GIT_COMMIT_INDEX% (from repository start)

endlocal
