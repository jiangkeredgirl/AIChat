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

for /f "delims=" %%i in ('git rev-parse --abbrev-ref HEAD') do set CODE_BRANCH=%%i
for /f "delims=" %%i in ('git rev-parse HEAD') do set CODE_COMMIT_HASH=%%i
for /f "delims=" %%i in ('git rev-parse --short HEAD') do set CODE_COMMIT_SHORT_HASH=%%i
for /f "delims=" %%i in ('git show -s --format^=%%ci HEAD') do set CODE_COMMIT_DATE=%%i

rem Prefer exact tag on HEAD; fallback to nearest describe value
for /f "delims=" %%i in ('git describe --tags --exact-match 2^>nul') do set CODE_TAG=%%i
if not defined CODE_TAG (
    for /f "delims=" %%i in ('git describe --tags --always 2^>nul') do set CODE_TAG=%%i
)

rem Code version: prefer exact tag; fallback to describe output
for /f "delims=" %%i in ('git describe --tags --always --dirty 2^>nul') do set CODE_VERSION=%%i
for /f "delims=" %%i in ('git show -s --format^=%%s HEAD') do set CODE_COMMIT_COMMENT=%%i
for /f "delims=" %%i in ('git show -s --format^=%%an HEAD') do set CODE_COMMIT_AUTHOR=%%i
for /f "delims=" %%i in ('git rev-list --count HEAD') do set CODE_COMMIT_INDEX=%%i

echo Code Branch         : %CODE_BRANCH%
echo Code Version        : %CODE_VERSION%
echo Code Short Hash     : %CODE_COMMIT_SHORT_HASH%
echo Code Hash           : %CODE_COMMIT_HASH%
echo Code Tag            : %CODE_TAG%
echo Code Commit Time    : %CODE_COMMIT_DATE%
echo Code Commit Author  : %CODE_COMMIT_AUTHOR%
echo Code Commit Comment : %CODE_COMMIT_COMMENT%
echo Code Commit Index   : %CODE_COMMIT_INDEX% (from repository start)


set OUT_FILE=%~1
if "%OUT_FILE%"=="" set "OUT_FILE=./git_info.h"
  if not "%OUT_FILE%"=="" (
    > "%OUT_FILE%" echo #pragma once
	>>"%OUT_FILE%" echo #define COMPANY "AIXAM"
	>>"%OUT_FILE%" echo #define PRODUCT "venipuncture"
    >>"%OUT_FILE%" echo #define CODE_BRANCH "%CODE_BRANCH%"
    >>"%OUT_FILE%" echo #define CODE_VERSION "%CODE_VERSION%"
    >>"%OUT_FILE%" echo #define CODE_HASH "%CODE_COMMIT_HASH%"
    >>"%OUT_FILE%" echo #define CODE_SHORT_HASH "%CODE_COMMIT_SHORT_HASH%"
    >>"%OUT_FILE%" echo #define CODE_TAG "%CODE_TAG%"
    >>"%OUT_FILE%" echo #define CODE_COMMIT_TIME "%CODE_COMMIT_DATE%"
    >>"%OUT_FILE%" echo #define CODE_COMMIT_AUTHOR "%CODE_COMMIT_AUTHOR%"
    >>"%OUT_FILE%" echo #define CODE_COMMIT_COMMENT "%CODE_COMMIT_COMMENT%"
    >>"%OUT_FILE%" echo #define CODE_COMMIT_INDEX "%CODE_COMMIT_INDEX%"
  )
 

set "RC_FILE=./Version.rc"

(
echo #include ^<windows.h^>
echo VS_VERSION_INFO VERSIONINFO
echo FILEVERSION     1,0,0,0
echo PRODUCTVERSION  1,0,0,0
echo FILEFLAGSMASK   0x3fL
echo FILEFLAGS       0x0L
echo FILEOS          VOS__WINDOWS32
echo FILETYPE        VFT_APP
echo FILESUBTYPE     VFT2_UNKNOWN
echo BEGIN
echo     BLOCK "StringFileInfo"
echo     BEGIN
echo         BLOCK "080404B0"
echo         BEGIN
echo             VALUE "CompanyName",      "AIXAM\0"
::echo             VALUE "FileDescription",  "My App\0"
::echo             VALUE "FileVersion",      "%CODE_VERSION%\0"
::echo             VALUE "ProductVersion",  "%CODE_VERSION%\0"
echo             VALUE "ProductName",      "venipuncture\0"
echo             VALUE "LegalCopyright",   "Copyright (C) 2026\0"
echo             :: 下面这些会出现在“详细信息”的“备注/自定义字段”
echo             VALUE "CODE_BRANCH",      "%CODE_BRANCH%\0"
::echo             VALUE "CODE_HASH",        "%CODE_COMMIT_HASH%\0"
echo             VALUE "CODE_VERSION", "%CODE_VERSION%\0"
::echo             VALUE "CODE_TAG",         "%CODE_TAG%\0"
echo             VALUE "CODE_COMMIT_TIME", "%CODE_COMMIT_DATE%\0"
::echo             VALUE "CODE_COMMIT_AUTHOR", "%CODE_COMMIT_AUTHOR%\0"
echo             VALUE "CODE_COMMIT_INDEX", "%CODE_COMMIT_INDEX%\0"
echo         END
echo     END
echo     BLOCK "VarFileInfo"
echo     BEGIN
echo         VALUE "Translation", 0x0804, 0x04B0
echo     END
echo END
) > "%RC_FILE%"

echo Generated: %OUT_FILE%, %RC_FILE% 
  
endlocal
