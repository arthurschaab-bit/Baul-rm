@echo off
setlocal
set "REPO_DIR=%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%REPO_DIR%run_pipeline.py" %*
) else (
  python "%REPO_DIR%run_pipeline.py" %*
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Baul-rm wurde mit Fehlercode %RC% beendet.
)
exit /b %RC%
