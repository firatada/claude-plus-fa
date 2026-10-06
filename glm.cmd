@echo off
rem Claude Code against z.ai GLM, sharing ~/.claude (sessions, CLAUDE.md, skills, hooks).
rem The key lives in the claudex profile's .env. Part of claude-plus.
setlocal
set "PROFILE_DIR=%USERPROFILE%\.claudex\profiles\glm"
for /f "usebackq tokens=1,* delims==" %%a in ("%PROFILE_DIR%\.env") do if "%%a"=="CLAUDEX_KEY" set "CLAUDEX_KEY=%%~b"
set "ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic"
set "ANTHROPIC_AUTH_TOKEN=%CLAUDEX_KEY%"
set "ANTHROPIC_API_KEY="
set "ANTHROPIC_MODEL=glm-4.7-flash"
set "ANTHROPIC_SMALL_FAST_MODEL=glm-4.5-flash"
claude %*
