Parent: [`memory.md`](../memory.md) — the Google Drive pause: Google Drive for desktop runs only while Claude Code is idle, started after the idle delay and quit at the next prompt; current behaviour.

# claude-plus-fa: Google Drive pause

What is true now; history in [`trials.md`](../trials.md). Why it is built this way: the maintainer's mindmap page `claude-plus-fa.md`, Decisions.

## Scope
Owns: `drive-pause.py`, its hooks, its scheduled task, its settings, state and log, and its limits.
Does NOT own: how sessions are read for the panel ([`memory.md`](../memory.md) § Field Agents: sessions), the other hooks (memory.md § Files and where they install).

## What it does
- Off unless `"drive_pause": true` in `~/.claude/claude-plus.local.json`; `drive_idle_minutes` is the idle delay (default `IDLE_MINUTES` 30; a value that is not a positive number gives 30). Both read on every run, so a change applies at the next prompt or tick.
- Claude is **in use** while a live session is mid-turn (status `busy` or `waiting`, or `waitingFor` set; pid alive; busy for less than `BUSY_CAP` 4 h, after which it counts as hung) or the last activity is younger than the delay. Otherwise Claude is **idle**.
- Last activity = the newest of `activity.json` (written by the hooks: a prompt, a finished reply) and the `statusUpdatedAt` of every live session file in `~/.claude/sessions` (this also covers a turn ended by Esc, which runs no Stop hook, and sessions opened before the install, which lack the hooks). Transcript times are never used (Claude Code writes to idle transcripts). Sessions whose pid is dead and the dictation host (cwd `VOICE_DIR`) are left out.
- Idle and Drive not running: Drive is started as its own sign-in entry does (`GoogleDriveFS.exe --startup_mode`, no window), and its pid and creation time are kept in `state.json` (`pid`, `created`, `started_for` = the last activity it was started for, `at`).
- In use and the Drive claude-plus started still running: it is quit. Started by anyone else (pid or creation time not the kept ones): never touched, logged once.
- One start per idle stretch: no start while `last activity <= started_for`, so a Drive the user quits (or that crashes) stays off until Claude is used and idle again; true also when no activity was ever noted (`started_for` 0).

## Where it runs
- `UserPromptSubmit` hook `drive-pause.py prompt` (timeout 10): writes `activity.json`; when the Drive claude-plus started runs, or the watcher task is not noted, it starts a detached helper (`pythonw drive-pause.py quit <session>`, `CREATE_BREAKAWAY_FROM_JOB`, without it when the job refuses) and returns; about 80 ms, run beside the other prompt hooks. Prints nothing.
- `Stop` hook `drive-pause.py stop`: writes `activity.json` (`what` `reply`), so a `claude -p` run that has ended (its session file gone) still counts.
- The helper (`quit`): registers the watcher task when `task.json` does not name this script, then quits the Drive claude-plus started.
- Watcher: Windows scheduled task `claude-plus-fa Google Drive pause` (`TASK`), every `TICK_MINUTES` 5 from its registration, `pythonw drive-pause.py tick`, interactive (runs only while the user is signed in), no admin, runs on battery, `IgnoreNew`, time limit 5 min. So Drive starts 30 to 35 min after the last activity, and the panel being closed changes nothing.
- `tick` order: off → remove the task, log once; in use → quit ours (the fallback for a prompt whose hook did not run), else note a user's Drive; idle → Drive running (leave it), already started in this stretch (skip), not installed (log once), else start.
- The task is registered by `install.py` (`drive-pause.py task`: registered when on, removed when off), by the prompt helper when the feature is turned on later, and removed by the next tick when it is turned off; `uninstall.py` removes it (`untask`) and the state folder. `task.json` notes the registration so prompts do not call `schtasks`.

## Drive
- Found as Drive's own `launch.bat` finds it: `InstallLocation` of the uninstall key `{6BBAE539-2232-434A-A4E5-9A33560C6283}` (HKLM, both views), else the newest folder under `%ProgramFiles%\Google\Drive File Stream` holding `GoogleDriveFS.exe`. Not installed: nothing happens, logged once.
- Running = any `GoogleDriveFS.exe` in this Windows session (Toolhelp snapshot, about 8 ms).
- Quit (`quit_drive()`): `GoogleDriveFS.exe --quit` from the running exe's own path (Drive's remote control over its IPC pipe; the flag's help text reads "Ask DriveFS to quit."), wait up to `QUIT_WAIT` 20 s for the kept process to end, then `taskkill /F /T /PID` (Drive resumes interrupted uploads at its next start). `--push_changes_and_quit` was not used: it waits for every upload.

## State and log
- `~/.claude/state/drive-pause/`: `state.json`, `activity.json` (`at`, `what` prompt or reply, `session` first 8 characters), `task.json` (`at`, `script`), `drive-pause.log`.
- The log has one line per decision: started (with the idle minutes), quitting (why), quit cleanly or stopped, left alone (a user's Drive), not started again, not installed, off, task registered or failed, errors. A decision repeated every tick is logged once (`note` in `state.json` keeps the last one). Cut to 32 KB past 64 KB.

## Tests
- `python -I tools/test-drive-pause.py` (33): settings, both hooks, every `tick()` branch, the stretch rule, ownership by creation time, `quit_drive()`'s clean and forced paths, the task XML. Processes, Drive, `schtasks` and spawning are fakes: no real process is touched and no task is made.

## Limits
- The clean quit (`--quit`) and the forced stop have not been seen on a real Drive (the tests may not touch it); the first real quit's log line says which one acted.
- Only the process claude-plus launched counts as its own: if Drive restarts itself (an update) the new process is taken as the user's and left running.
- A prompt in a session that has no hooks loaded (opened before the install) quits Drive only at the next tick, up to 5 min later.
- A session busy for more than 4 h counts as hung, so Drive may start during a very long turn.
- Windows only; the task needs Task Scheduler, which a locked-down PC may forbid (logged as "could not register").
