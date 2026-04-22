@echo off
REM Headless launcher for ClaudeJournal. Runs the nightly pipeline, then
REM starts the web server as a hidden background process. Logs go to db\.
REM Use stop-claudejournal.bat to stop.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-claudejournal.ps1"
