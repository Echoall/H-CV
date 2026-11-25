@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m station.bench.bench --field %*
) else (
    python -m station.bench.bench --field %*
)
set "result=%errorlevel%"
popd
pause
exit /b %result%
