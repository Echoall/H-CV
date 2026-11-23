@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
where python.exe >nul 2>nul
if errorlevel 1 (
    echo 电脑找不到 Python。请联系我把它补齐，再打开图传调试台。
    pause >nul
    exit /b 1
)
pushd "%~dp0..\.."
python -m station.bench.bench %*
set "wizard_result=%errorlevel%"
popd
echo 按任意键关闭此窗口。
pause >nul
exit /b %wizard_result%
