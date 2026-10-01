@echo off
cd /d "C:\Users\USER\Downloads\Phyton\Kodi FIles Generator"

:: Find the newest .py file in the folder
for /f "delims=" %%f in ('dir /b /a:-d /o-d *.py') do (
    set "latest=%%f"
    goto run
)

:run
echo Running most recent Python file: %latest%
start "" pythonw "%latest%"
