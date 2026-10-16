@echo off
setlocal
if "%~1"=="--help" (
    echo Usage: start.cmd [--setup-only] [--no-browser] [--port 0] [--stop] [--data-dir folder]
    echo First start prepares Python, .venv, dependencies and portable components in this clone.
    exit /b 0
)
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" goto architecture_ready
if /i "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto architecture_ready
echo The developer starter supports Windows x64. Use a matching release on other architectures.
exit /b 1
:architecture_ready
set "project_root=%~dp0"
set "local_dir=%project_root%.local"
set "tools=%local_dir%\tools"
set "uv_dir=%tools%\uv-0.8.22"
for %%D in ("%local_dir%" "%tools%" "%uv_dir%" "%local_dir%\python" "%local_dir%\uv-cache" "%local_dir%\bin") do (
    set "STUDYCENTER_PUBLIC_SETUP_CHECK=%%~D"
    if exist "%%~D" (
        powershell.exe -NoLogo -NoProfile -Command "$item = Get-Item -LiteralPath $env:STUDYCENTER_PUBLIC_SETUP_CHECK -Force; if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { exit 1 }"
        if errorlevel 1 goto directory_error
    ) else (
        mkdir "%%~D"
        if errorlevel 1 goto setup_error
    )
)
set "uv=%uv_dir%\uv.exe"
if exist "%uv%" goto uv_ready
where curl.exe >nul 2>nul
if errorlevel 1 goto tools_error
where tar.exe >nul 2>nul
if errorlevel 1 goto tools_error
echo Preparing the project-local Python tool. First start needs internet.
set "STUDYCENTER_PUBLIC_SETUP_ARCHIVE=%tools%\uv-0.8.22-windows.zip"
curl.exe --fail --location --proto "=https" --tlsv1.2 "https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-pc-windows-msvc.zip" --output "%STUDYCENTER_PUBLIC_SETUP_ARCHIVE%"
if errorlevel 1 goto setup_error
powershell.exe -NoLogo -NoProfile -Command "if ((Get-FileHash -LiteralPath $env:STUDYCENTER_PUBLIC_SETUP_ARCHIVE -Algorithm SHA256).Hash.ToLowerInvariant() -ne '5049375aa2a5162f132b2c1cb992e25d42d47d934cab8c174dbe6f60973dcc12') { Write-Error 'Official uv archive checksum mismatch; no downloaded program was run.'; exit 1 }"
if errorlevel 1 goto setup_error
tar.exe -xf "%STUDYCENTER_PUBLIC_SETUP_ARCHIVE%" -C "%uv_dir%" uv.exe
if errorlevel 1 goto setup_error
if not exist "%uv%" goto setup_error
:uv_ready
set "UV_CACHE_DIR=%local_dir%\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%local_dir%\python"
set "UV_PYTHON_BIN_DIR=%local_dir%\bin"
set "UV_PYTHON_INSTALL_BIN=0"
set "UV_PYTHON_CPYTHON_BUILD=20250918"
set "VIRTUAL_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
cd /d "%project_root%"
"%uv%" --no-config python install 3.12.11
if errorlevel 1 goto setup_error
"%uv%" --no-config python find --system --managed-python 3.12.11 > "%local_dir%\python-path.txt"
if errorlevel 1 goto setup_error
set /p "project_python="<"%local_dir%\python-path.txt"
"%project_python%" -I "%project_root%scripts\setup_dev.py" --uv "%uv%" %*
exit /b %errorlevel%
:directory_error
echo Refusing a linked, reparse or non-directory setup path. No foreign directory was changed.
exit /b 1
:tools_error
echo Existing Windows curl.exe and tar.exe are needed. Use the complete Windows release instead.
exit /b 1
:setup_error
echo Developer setup failed. Check the connection and retry start.cmd. See docs\DEVELOPMENT.md.
exit /b 1
