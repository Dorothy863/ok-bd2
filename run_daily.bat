@echo off
chcp 65001 >nul
cd /d "%~dp0"
uv run python scripts\run_daily.py
set EXIT_CODE=%ERRORLEVEL%
echo.
if %EXIT_CODE% neq 0 (
  echo 一键完成日常执行失败，退出码：%EXIT_CODE%
) else (
  echo 一键完成日常执行结束。
)
pause
exit /b %EXIT_CODE%
