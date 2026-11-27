Parent: [`memory.md`](../memory.md) — the keep-alive: an open idle session's prompt cache refreshed by a headless forked resume before it expires, its setting, state, log and limits; current behaviour.

# claude-plus-fa: keep-alive

What is true now; the measurements behind it in [`trials.md`](../trials.md) (2026-11-27, the keep-alive, which way to send it). Why a headless fork and not the terminal, and why these numbers: the maintainer's mindmap page `claude-plus-fa.md`, Decisions.

## Scope
Owns: `KEEPALIVE_*`, `KEEPING`, `stretch_rec()`, `cache_base()`, `keepalive_fits()`, `keepalive_due()`, `keepalive_on()`, `keepalive_argv()`, `keepalive_outcome()`, `keepalive_forks()`, `keepalive_load()`, `keepalive_save()`, `keepalive_log()`, `Panel.check_keepalive()` and `Panel.keep_alive()` in `agent-panel.pyw`; the kept mark (`.cw.kept`, `keptSaid()`) in `agent-panel.html`; the `keepalive` setting; `keepalive.json` and `keepalive.log`; `tools/keepalive-trial.py` and `tools/test-keepalive.py`. Not the warning's own stages and sounds ([`cache-warning.md`](./cache-warning.md)), though they count from `cache_base()`.

## What it does
- An open session left idle gets a keep-alive `KEEPALIVE_LEAD` (10 min) before its prompt cache expires: 50 min idle on the 1 h cache, then 50 min after each keep-alive, `KEEPALIVE_MAX` 3 per idle stretch (the cache then lasts to about 3 h 30 min); a real prompt or reply (a newer `at`) starts a new stretch with 3 again.
- Which sessions (`keepalive_fits()`): terminal open with `status` `idle` (not working, not `shell`, not waiting for an approval or a question), not on GLM or Codex (`self.returns`), not inside WSL, not headless, the cache's `ttl` longer than `CACHE_LEAD[0]` (a 5-minute cache never), the newest Claude reply's context (`cache_of()` `tokens`) over `KEEPALIVE_MIN_TOKENS` 100k, fewer than 3 sent and none failed in this stretch; the session's folder must exist. Due (`keepalive_due()`) from `ttl - KEEPALIVE_LEAD` after `cache_base()` until the cache has expired (after that a keep-alive would write the whole context, as the next prompt would).
- `check_keepalive()` after every live pass: the first due session gets `keep_alive()` on a thread; one at a time (`keep_busy`).
- No condition on the session's prompt box: nothing is typed into the window, so a half-written prompt stays as it is.

## The keep-alive itself
- `keepalive_argv()`: `claude -p --resume <id> --fork-session --session-id <new id> --no-session-persistence --output-format json --settings {"disableAllHooks": true} --model <the newest Claude reply's model> "<KEEPALIVE_TEXT>"`, in the session's folder, `clean_env()`, `CREATE_NO_WINDOW`, stopped after `KEEPALIVE_WAIT` 180 s. No `--dangerously-skip-permissions`: the fork may not run tools unasked.
- `KEEPALIVE_TEXT`: `Keep-alive from claude-plus: reply only `ok`, do nothing else.` The hooks know it by its first words (`Keep-alive from claude-plus:`).
- Why it refreshes the same cache: Claude Code records the system prompt on a conversation's first request and sends that record as is on every resume (`--system-prompt-snapshot`, on by default), and a fork resends the session's messages unchanged, so the request's prefix is byte-identical to the interactive session's and reads its cache entry, which a read keeps alive for another hour. Measured: kept alive at 50 min, the session's own next prompt at 75 min read all 119k and wrote 101 tokens; a control session at 75 min read 32k and wrote 88k.
- Refreshed (`keepalive_outcome()`): exit 0, no `is_error`, `cache_read_input_tokens` at least half the context, one turn. Otherwise the stretch has failed: no more keep-alives until the next prompt, and the warning's stage 1 shows at once. Every attempt counts toward the 3, failed ones too (a run that cannot even start included), so nothing is retried in a loop.
- Measured cost (Opus 5.5, about 119k context): about 6 to 10 s, read the whole context, wrote 1k to 3k tokens (the last reply and the keep-alive), reply `ok`, 0.035 to 0.043 USD at list price (Claude Code's `total_cost_usd` on a first fork; on later forks of the same session it is the session's running total, so only `usage` is read). In input-price units a keep-alive is 0.1 x 119k + 2 x 2k = about 16k against 2 x 119k = 238k for a miss: about 1/15.

## What it leaves untouched
- The original transcript (byte for byte), the session's own `~/.claude/sessions/<pid>.json`, its window and prompt box; the fork writes no transcript.
- The fork's id is chosen before it starts and kept in `KEEPING` and in `keepalive.json` `forks` while it runs: `running()` leaves it out (its short-lived `~/.claude/sessions/<pid>.json`, `entrypoint` `sdk-cli`, would otherwise start a scan), and `drive-pause.py` leaves it out of the live sessions and its Stop hook.
- Hooks are off for the fork. As a second guard, `prompt-recall.py` and `drive-pause.py prompt` skip a prompt that starts with the keep-alive's words; `drive-pause.py stop` skips a listed fork.
- Last activity, card order and age (`last_at()` reads the untouched transcript), headless detection (no transcript), the context guard and the status line (they never run) are unaffected.

## The cache warning with keep-alives
- `cache_base()`: when the cache was last used, the session's `at` or its last refreshing keep-alive in the same stretch, whichever is later. `cache_stage()`, `cache_view()` and `cache_rings()` count from it, so a keep-alive clears the mark and re-arms the sounds as a prompt does.
- While a keep-alive is still to come (`keepalive_fits()` with the setting on), stage 1 (the 45-minute mark and its chime) is neither shown nor sounded; stage 2 at 55 min still is, meaning the keep-alive did not happen.
- `cache_view()` adds `kept` (`n`, `of`, `last` epoch ms, `ok`, `left`); sent with the cache warning off too, then without stages.
- Kept mark: at stage 0 after a refreshing keep-alive, the `.cw` place shows a still `--green` ring with `↻` (`.cw.kept`) on `--panel`. Tooltip (`cacheSaid()` → `keptSaid()`): "Kept alive at 14:32 (1 of 3, 2 left). It expires in 48 min unless the next keep-alive runs."; on a warning mark the same sentence follows the warning's; a failed one: "The keep-alive at 14:32 did not refresh it; no more until your next prompt." Card `aria-label`: "cache kept alive at 14:32".

## Setting, state, log
- `keepalive` in `claude-plus.local.json`: off unless `true`; read on every pass and push. Row *Keep Idle Sessions' Cache Alive* (switch), second in the settings view.
- `~/.claude/state/agent-panel/keepalive.json`: `sessions` {id: `stretch` (the `at` it belongs to), `sent`, `last` (start of the last attempt), `refreshed` (start of the last refreshing one), `failed` (why)}, records older than two days dropped; `forks` {id: `session`, `at`} while one runs. Read at start, so a restart neither forgets the count nor sends again.
- `keepalive.log` (same folder, cut to 32 KB past 64 KB): one line each, e.g. `3e1895bd keep-alive 1/3, context 120k: refreshed (read 120k, wrote 3k, reply 'ok', 9.7 s)` or `... not refreshed, missed the cache: read 32k of 119k (6.4 s); no more in this idle stretch`.

## Tests
- `python -I tools/test-keepalive.py` (22, fake clock and fake `claude`): conditions, the count, the reset, the failure stop, the warning's stages, view and sounds with keep-alives, the command, the run's record, log and fork listing, and each consumer (`running()`, `drive-pause.py` sessions, prompt and Stop hooks, `prompt-recall.py`).
- `python tools/keepalive-trial.py [--quick | --wait1 MIN --wait2 MIN]` (not `-I`: it needs `pywinpty` and `pyte` from the user packages): two throwaway interactive sessions in hidden pseudo-consoles (no window) under `%TEMP%`, one kept alive by real forks, one control; usage read from transcripts and the forks' JSON. Costs real tokens.

## Limits
- Needs the panel running and the setting on. Sessions inside WSL are left out (the fork would have to run in the distro).
- A session whose request no longer matches its recorded prefix misses: one miss costs about one cache write and ends that stretch's keep-alives (logged). Not seen yet: a session whose `/effort` or thinking setting was changed after it started (the fork asks with the defaults), or a `CLAUDE.md` edited between the session's last turn and the keep-alive. A date change between them was seen and still hit (a keep-alive at 00:37 for a turn at 23:47).
- The real cache's end can differ by a few minutes from `at + ttl` (cache warning, Limits); at 10 min ahead this has not mattered.
- Each keep-alive uses a little of the 5-hour and weekly limits (about 1/15 of what the expiry would cost).
