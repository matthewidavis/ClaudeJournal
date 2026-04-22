@echo off
REM Stop the running ClaudeJournal serve process (headless).
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0stop-claudejournal.ps1"
