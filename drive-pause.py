# Google Drive pause: Google Drive for desktop runs only while Claude Code is not in use. Off unless
# "drive_pause": true is in ~/.claude/claude-plus.local.json; "drive_idle_minutes" (default 30) is the idle delay.
#
#   prompt   UserPromptSubmit hook: notes the activity; a Drive claude-plus started is told to quit at once
#            (by a detached helper, so the prompt never waits); registers the watcher task when it is missing
#   stop     Stop hook: notes the activity (a reply finished; also for headless claude -p runs, whose session
#            file is gone once they end)
#   tick     the watcher, a Windows scheduled task every TICK_MINUTES: starts Drive after the idle delay, quits
#            the Drive it started when Claude is in use (the fallback for sessions without the hook)
#   quit     the detached helper the prompt hook starts
#   task     registers the watcher task when the feature is on, removes it when off (install.py calls it)
#   untask   removes the watcher task (uninstall.py calls it)
#
# Never touches a Drive the user started: only the process claude-plus launched (its pid and creation time) is quit.
# Hooks print nothing: a UserPromptSubmit hook's stdout goes into Claude's context.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, json, os, subprocess, sys, time
import ctypes.wintypes as wt

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
LOCAL = os.path.join(CLAUDE, "claude-plus.local.json")
SESSIONS = os.path.join(CLAUDE, "sessions")
STATE_DIR = os.path.join(CLAUDE, "state", "drive-pause")
STATE = os.path.join(STATE_DIR, "state.json")        # {"pid", "created", "started_for", "at", "note"}
ACTIVITY = os.path.join(STATE_DIR, "activity.json")  # {"at", "what", "session"}: the last prompt or finished reply
LOG = os.path.join(STATE_DIR, "drive-pause.log")
# the hidden Claude Code that dictates in the reply window (agent-panel.pyw VOICE_DIR): it starts with the panel at
# sign-in, which is no use of Claude
VOICE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "claude-plus", "voice-host")

TASK = "claude-plus-fa Google Drive pause"
TICK_MINUTES = 5
IDLE_MINUTES = 30       # drive_idle_minutes when unset or not a positive number
BUSY_CAP = 4 * 3600     # a session busy longer than this is taken as hung, not working
QUIT_WAIT = 20          # seconds Drive gets to quit cleanly before it is stopped
START_WAIT = 15         # seconds to wait for the launched Drive to show up
LOG_MAX, LOG_KEEP = 64 * 1024, 32 * 1024
EXE = "GoogleDriveFS.exe"
DRIVE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6BBAE539-2232-434A-A4E5-9A33560C6283}"
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000              # CREATE_BREAKAWAY_FROM_JOB: outlives the hook's (or the task's) job

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.c_void_p] * 4
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_wchar_p, ctypes.c_void_p]


# ---------- settings, state, log ----------

def settings():
    """(on, idle seconds) from claude-plus.local.json, read on every run so a change applies at once."""
    try:
        with open(LOCAL, encoding="utf-8") as f:
            o = json.load(f)
    except (OSError, ValueError):
        return False, IDLE_MINUTES * 60
    if not isinstance(o, dict):
        return False, IDLE_MINUTES * 60
    minutes = o.get("drive_idle_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        minutes = IDLE_MINUTES
    return o.get("drive_pause") is True, minutes * 60


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            o = json.load(f)
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def write(path, obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def log(line):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            with open(LOG, "rb") as f:
                f.seek(-LOG_KEEP, 2)
                tail = f.read()
            with open(LOG, "wb") as f:
                f.write(tail[tail.find(b"\n") + 1:])
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


def note(state, key, line):
    """A decision the watcher repeats every tick is logged once, when it first applies."""
    if state.get("note") != key:
        log(line)
        state["note"] = key
        write(STATE, state)


# ---------- processes ----------

class ENTRY(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def drive_procs():
    """{pid: parent pid} of every GoogleDriveFS.exe in this Windows session (another signed-in user's Drive is not
    ours to count)."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    mine, out, e = wt.DWORD(), {}, ENTRY()
    kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(mine))
    e.dwSize = ctypes.sizeof(ENTRY)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == EXE.lower():
            sid = wt.DWORD()
            if kernel32.ProcessIdToSessionId(e.th32ProcessID, ctypes.byref(sid)) and sid.value == mine.value:
                out[e.th32ProcessID] = e.th32ParentProcessID
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return out


def created(pid):
    """The process's creation time (100 ns ticks), or None when it is gone: with the pid it names one process,
    since Windows reuses pids."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wt.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        c, x, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x), ctypes.byref(k), ctypes.byref(u)):
            return None
        return (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        kernel32.CloseHandle(h)


def image(pid):
    """The full path of a running process's exe, or None."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else None
    finally:
        kernel32.CloseHandle(h)


def alive(pid):
    return created(pid) is not None


def drive_running():
    return bool(drive_procs())


def ours(state):
    """True while the Drive claude-plus launched is still running: same pid, same creation time."""
    pid = state.get("pid")
    return bool(pid) and state.get("created") is not None and created(pid) == state["created"]


def find_exe():
    """GoogleDriveFS.exe as Drive's own launch.bat finds it: the uninstall entry's InstallLocation, else the newest
    version folder under Program Files that holds it. None when Drive for desktop is not installed."""
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DRIVE_KEY, 0, winreg.KEY_READ | view) as k:
                path = winreg.QueryValueEx(k, "InstallLocation")[0]
            if path.lower().endswith(EXE.lower()) and os.path.isfile(path):
                return path
        except OSError:
            pass
    base = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Google", "Drive File Stream")
    try:
        found = [os.path.join(base, d, EXE) for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, EXE))]
    except OSError:
        return None
    return max(found, key=os.path.getctime) if found else None


def spawn(args, cwd=None):
    """A process that outlives the hook or task that starts it; without breakaway when the job forbids it."""
    kw = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    try:
        return subprocess.Popen(args, creationflags=DETACHED | BREAKAWAY, **kw)
    except OSError:
        return subprocess.Popen(args, creationflags=DETACHED, **kw)


def start_drive(exe):
    """Drive started the way its own sign-in entry does (--startup_mode: no window); returns (pid, created) of the
    launched process, or None when it did not come up."""
    p = spawn([exe, "--startup_mode"], cwd=os.path.dirname(exe))
    end = time.time() + START_WAIT
    while time.time() < end:
        c = created(p.pid)
        if c is not None:
            return p.pid, c
        time.sleep(0.5)
    return None


def quit_drive(state):
    """The Drive claude-plus started is asked to quit through its own remote control (GoogleDriveFS.exe --quit, the
    flag its command-line help calls "Ask DriveFS to quit."), then stopped with its child processes if it is still
    there after QUIT_WAIT. Returns "quit" or "stopped" (or "gone" when it had already ended)."""
    pid = state["pid"]
    exe = image(pid) or find_exe()
    if not ours(state):
        return "gone"
    if exe:
        try:
            subprocess.run([exe, "--quit"], creationflags=NO_WINDOW, timeout=QUIT_WAIT, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
    end = time.time() + QUIT_WAIT
    while time.time() < end:
        if not ours(state):
            return "quit"
        time.sleep(0.5)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], creationflags=NO_WINDOW,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "stopped"


# ---------- is Claude in use ----------

def sessions():
    """Live Claude Code sessions: (status, statusUpdatedAt in seconds) from ~/.claude/sessions, pid alive."""
    out = []
    try:
        names = os.listdir(SESSIONS)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        o = read(os.path.join(SESSIONS, n))
        if not o.get("pid") or not alive(o["pid"]):
            continue
        try:
            if o.get("cwd") and os.path.normcase(os.path.normpath(o["cwd"])) == \
                    os.path.normcase(os.path.normpath(VOICE_DIR)):
                continue
        except (TypeError, ValueError):
            pass
        since = o.get("statusUpdatedAt")
        out.append({"status": o.get("status") or "idle", "waiting": bool(o.get("waitingFor")),
                    "since": since / 1000.0 if isinstance(since, (int, float)) else None})
    return out


def usage(now):
    """(working, last activity): working is a live session mid-turn (busy or waiting for an answer) for less than
    BUSY_CAP; last activity is the newest prompt or finished reply the hooks noted, or status change of a live
    session (which also covers turns interrupted with Esc and sessions opened before the hooks were installed)."""
    working, last = False, read(ACTIVITY).get("at") or 0
    for s in sessions():
        if s["since"]:
            last = max(last, s["since"])
        if (s["status"] in ("busy", "waiting") or s["waiting"]) and (s["since"] is None or now - s["since"] < BUSY_CAP):
            working = True
    return working, last


# ---------- the watcher task ----------

def pythonw():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.isfile(w) else sys.executable


def task_xml():
    from xml.sax.saxutils import escape
    start = time.strftime("%Y-%m-%dT%H:%M:%S")
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>claude-plus-fa: starts Google Drive when Claude Code has been idle, quits it when Claude is used again (drive_pause in claude-plus.local.json).</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger><StartBoundary>%s</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT%dM</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>%s</Command><Arguments>"%s" tick</Arguments></Exec></Actions>
</Task>
""" % (start, TICK_MINUTES, escape(pythonw()), escape(os.path.abspath(__file__)))


def register_task():
    """The watcher task, made or replaced; True when Task Scheduler took it. The marker file spares every prompt
    a call to schtasks."""
    os.makedirs(STATE_DIR, exist_ok=True)
    xml = os.path.join(STATE_DIR, "task.xml")
    with open(xml, "w", encoding="utf-16") as f:
        f.write(task_xml())
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", xml, "/F"], creationflags=NO_WINDOW,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    os.remove(xml)
    if r.returncode == 0:
        write(os.path.join(STATE_DIR, "task.json"), {"at": time.time(), "script": os.path.abspath(__file__)})
        return True
    log("could not register the watcher task: %s" % (r.stderr or r.stdout).strip()[-200:])
    return False


def remove_task():
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], creationflags=NO_WINDOW, capture_output=True,
                       stdin=subprocess.DEVNULL)
    try:
        os.remove(os.path.join(STATE_DIR, "task.json"))
    except OSError:
        pass
    return r.returncode == 0


def task_noted():
    return read(os.path.join(STATE_DIR, "task.json")).get("script") == os.path.abspath(__file__)


# ---------- the modes ----------

def hook_input():
    try:
        return json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return {}


def on_prompt(data):
    """A prompt: Claude is in use from now. Only what must not wait happens here; the quit runs in a helper."""
    on, _ = settings()
    if not on:
        return
    sid = (data.get("session_id") or "")[:8]
    write(ACTIVITY, {"at": time.time(), "what": "prompt", "session": sid})
    state = read(STATE)
    helper = ours(state) or not task_noted()
    if helper:
        spawn([pythonw(), os.path.abspath(__file__), "quit", sid])
    elif drive_running():
        note(state, "user-drive", "prompt (session %s): Drive is running, started by the user, not by "
                                  "claude-plus: left alone" % sid)


def on_stop(data):
    on, _ = settings()
    if on:
        write(ACTIVITY, {"at": time.time(), "what": "reply", "session": (data.get("session_id") or "")[:8]})


def quit_ours(state, why):
    log("quitting the Drive claude-plus started (pid %s): %s" % (state["pid"], why))
    how = quit_drive(state)
    log({"quit": "Drive quit cleanly", "stopped": "Drive did not quit within %d s: stopped" % QUIT_WAIT,
         "gone": "Drive had already ended"}[how])
    state.update(pid=None, created=None, note="quit")
    write(STATE, state)


def on_quit(sid):
    """The prompt hook's helper: registers the watcher if needed, then quits the Drive claude-plus started."""
    if settings()[0] and not task_noted():
        if register_task():
            log("watcher task registered (every %d min)" % TICK_MINUTES)
    state = read(STATE)
    if ours(state):
        quit_ours(state, "prompt in session %s" % sid)


def tick(now=None):
    """The watcher's one pass. The order matters: off first, then in use, then idle."""
    on, idle = settings()
    state = read(STATE)
    if not on:
        remove_task()
        note(state, "off", "drive_pause is off: watcher task removed%s" %
             (", Drive left as it is" if ours(state) else ""))
        return
    now = now or time.time()
    working, last = usage(now)
    if working or now - last < idle:
        if ours(state):
            quit_ours(state, "Claude is in use (%s)" % ("a session is working" if working else
                                                         "activity %d min ago" % ((now - last) // 60)))
        elif drive_running():
            note(state, "user-drive", "Claude is in use: Drive is running, started by the user, not by claude-plus: "
                                      "left alone")
        return
    if drive_running():
        if not ours(state):
            note(state, "user-drive", "idle: Drive is already running, started by the user: left alone")
        return
    if "started_for" in state and last <= state["started_for"]:
        # this idle stretch already had its start: Drive was quit by the user (or crashed) since. The next prompt or
        # finished reply opens a new stretch.
        note(state, "not-again", "idle: Drive is not running, but claude-plus started it once in this idle stretch "
                                 "and it was quit since: not started again until Claude is used and idle again")
        return
    exe = find_exe()
    if not exe:
        note(state, "no-drive", "Google Drive for desktop is not installed: nothing to do")
        return
    got = start_drive(exe)
    if not got:
        note(state, "start-failed", "could not start Drive (%s)" % exe)
        return
    state.update(pid=got[0], created=got[1], started_for=last, at=now, note="started")
    write(STATE, state)
    log("started Drive (pid %d): Claude idle for %d min" % (got[0], (now - last) // 60))


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    try:
        if mode == "prompt":
            on_prompt(hook_input())
        elif mode == "stop":
            on_stop(hook_input())
        elif mode == "tick":
            tick()
        elif mode == "quit":
            on_quit(argv[2] if len(argv) > 2 else "?")
        elif mode == "task":
            if settings()[0]:
                ok = register_task()
                print("ok      Google Drive pause on: watcher task registered" if ok else
                      "WARNING Google Drive pause on, but its watcher task could not be registered (see %s)" % LOG)
                if not find_exe():
                    print("note    Google Drive for desktop is not installed, so the Google Drive pause does nothing")
            else:
                remove_task()
                print("ok      Google Drive pause off (turn it on with \"drive_pause\": true in %s)" % LOCAL)
        elif mode == "untask":
            remove_task()
    except Exception as e:  # never block a prompt or a reply over a convenience
        log("error in %s: %r" % (mode, e))


if __name__ == "__main__":
    main(sys.argv)
    sys.exit(0)
