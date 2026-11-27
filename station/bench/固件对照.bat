@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m station.bench.firmware %*
) else (
    python -m station.bench.firmware %*
)
set "result=%errorlevel%"
popd
pause
exit /b %result%
