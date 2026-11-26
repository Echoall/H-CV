@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m station.bench.bench --hubei-m1 %*
) else (
    python -m station.bench.bench --hubei-m1 %*
)
set "result=%errorlevel%"
popd
pause
exit /b %result%
