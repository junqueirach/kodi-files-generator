@echo off
rem Kodi Files Generator launcher: starts kodi_generator.py from this folder
cd /d "%~dp0"
start "" pythonw kodi_generator.py
