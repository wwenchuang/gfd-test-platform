@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if /i "%~1"=="start" goto valid
if /i "%~1"=="status" goto valid
if /i "%~1"=="configure" goto valid
echo Invalid action. Use start.cmd, status.cmd or configure.cmd.
exit /b 1
:valid
where py.exe >nul 2>nul
if errorlevel 1 goto python
py.exe -3 -c "import sys; assert sys.version_info >= (3, 8)" >nul 2>nul
if errorlevel 1 goto python
py.exe -3 "%~dp0stack.py" %~1
set "RESULT=%ERRORLEVEL%"
goto done
:python
where python.exe >nul 2>nul
if errorlevel 1 goto missing
python.exe -c "import sys; assert sys.version_info >= (3, 8)" >nul 2>nul
if errorlevel 1 goto missing
python.exe "%~dp0stack.py" %~1
set "RESULT=%ERRORLEVEL%"
goto done
:missing
echo 未找到 Python 3.8 或以上版本。请使用运行 Windows Runner 的 Python 环境。
set "RESULT=1"
:done
echo.
if not "%RESULT%"=="0" echo 有项目未完成，请查看上面的中文提示。
pause
exit /b %RESULT%
