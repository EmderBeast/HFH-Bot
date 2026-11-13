@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title HFH-Bot

set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" goto :deps

rem ---- Python suchen / look for Python (3.10 - 3.13, am liebsten / preferably 3.12)
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if defined PY goto :venv

echo Python 3.10+ wurde nicht gefunden.  /  Python 3.10+ was not found.
rem set /p instead of "choice": choice beeps at every other key (13.11.2026)
set "ANS="
set /p "ANS=Python 3.12 jetzt mit winget installieren? / Install Python 3.12 now with winget? [J/Y/N], Enter: "
if /i "%ANS%"=="J" goto :install
if /i "%ANS%"=="Y" goto :install
goto :end

:install
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo Fertig. Bitte dieses Fenster schliessen und start.bat noch einmal starten.
echo Done. Please close this window and run start.bat again.
goto :end

:venv
echo Richte den Bot ein (nur beim ersten Start) ...  /  Setting up the bot (first start only) ...
%PY% -m venv .venv
if errorlevel 1 goto :venvfail
goto :deps

:venvfail
echo Konnte die Python-Umgebung nicht anlegen.  /  Could not create the Python environment.
goto :end

:deps
rem ---- Pakete nur neu installieren, wenn sich requirements.txt geaendert hat / reinstall only when requirements.txt changed
fc /b requirements.txt ".venv\installed.txt" >nul 2>nul
if not errorlevel 1 goto :run
echo Installiere die Texterkennung, 1 bis 3 Minuten ...  /  Installing the text recognition, 1 to 3 minutes ...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :pipfail
copy /y requirements.txt ".venv\installed.txt" >nul
goto :run

:pipfail
echo Installation fehlgeschlagen. Internet da? Dann start.bat noch einmal starten.
echo Installation failed. Check the internet connection and run start.bat again.
goto :end

:run
"%VPY%" potbot.py

:end
echo.
pause
