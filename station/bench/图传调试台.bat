@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0..\.."
python -m station.bench.bench
popd
echo 按任意键关闭此窗口。
pause >nul
