@echo off
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 goto use_python
where py >nul 2>nul
if not errorlevel 1 goto use_py
echo Python was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during installation.
goto end

:use_python
python iot_device.py %*
goto end

:use_py
py -3 iot_device.py %*

:end
pause
