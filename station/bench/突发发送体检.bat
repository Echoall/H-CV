@echo off
setlocal
chcp 65001 >nul
call "%~dp0图传调试台.bat" --link-check --burst
exit /b %errorlevel%
