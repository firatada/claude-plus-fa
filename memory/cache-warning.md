Parent: [`memory.md`](../memory.md) — the cache warning: a red ! on an open idle session's card before its prompt cache expires, its sounds, its setting; current behaviour.

# claude-plus-fa: cache warning

What is true now; the measurement behind it in [`trials.md`](../trials.md) (2026-11-26, the prompt cache's lifetime). Why 45/55 and why on by default: the maintainer's mindmap page `claude-plus-fa.md`, Decisions.

## Scope
Owns: `cache_of()`, `cache_stage()`, `cache_view()`, `cache_rings()`, `cache_warning_on()` and `Panel.check_cache()` in `agent-panel.pyw`; the mark `.cw` and `cacheSaid()` in `agent-panel.html`; `mascot/cache-soon.wav`, `mascot/cache-last.wav` and `tools/cache-sound.py`; the `cache_warning` setting. Not the card's other parts (`memory.md` § Field Agents: look), the alert's knock (§ Field Agents: alert), nor the keep-alive, its kept mark and `cache_base()` ([`keepalive.md`](./keepalive.md)).

## What it reads
- `cache_of(path, size)`: from the transcript's end (`CACHE_LOOK` 2 MB), top-level `assistant` lines of a `claude-*` model only (GLM and sidechain lines skipped): `tokens` = the newest one's `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`; `model` = the newest one's model (for the keep-alive); `ttl` = 3600 from the newest one with `usage.cache_creation.ephemeral_1h_input_tokens` > 0, 300 with `ephemeral_5m_input_tokens` > 0, else None. About 1 ms a file.
- Read in `parse()` (kept in `cache.json` as `cache`; an entry without it is parsed again) and in `update_live()` when an open session's transcript changed, beside `last_at()`.
- Idle start = `cache_base()`: the session's `at` (`last_at()`: newest top-level prompt, reply or tool result), or its last refreshing keep-alive in the same stretch if later ([`keepalive.md`](./keepalive.md)); never the file's time.

## Stages (`cache_stage()`)
- Only an open session (`status` set) that is not working (`busy` without `waitingFor`); a session waiting for an answer counts.
- No warning when `ttl` is unknown or not longer than `CACHE_LEAD[0]` (a 5-minute cache, e.g. some API-key setups).
- `CACHE_LEAD` = (15 min, 5 min): stage 1 from `ttl - 15 min` idle, 2 from `ttl - 5 min`, 3 from `ttl` (expired); on the 1 h cache 45, 55, 60 min.
- A prompt moves `at`, a keep-alive moves `cache_base()`: either way the stage drops to 0 and the stretch starts again.
- With the keep-alive on, stage 1 is neither shown nor sounded while a keep-alive is still to come for that session (it is due at 50 min, before stage 2).

## What the page gets and shows
- `state()` sends `cache`: `cache_view()` = `{stage, ends (epoch ms the cache expires), tokens, kept}` or null (null with both this setting and the keep-alive off; with this one off and the keep-alive on, only the kept mark). The pushed state changes only on a stage change; the page counts minutes itself.
- Mark `.cw`: a 16 px circle with `!`, astride the card's right edge at `top: 29px; right: -8px` (beside the title, fixed when the card opens), outlined in `--bg`; on a top tab 14 px at the top right corner. Absolutely placed, so nothing on the card moves when it appears.
- Stage 1: `--red`, blinks (`cwblink`, opacity to 0.2) every 1.6 s. Stage 2: blinks every 0.6 s (under three flashes a second) with a 2 px `--red-tint` halo. Stage 3: still, `--panel` fill with a 1.5 px `--muted` ring and a muted `!`.
- Reduced motion: no blink (the global `animation: none`); stage 2 keeps its halo, so 1 and 2 differ by shape.
- Tooltip (`data-tip`, the panel's shared tooltip): `cacheSaid()`, e.g. "Cache expires in 12 min. After that, the next prompt here pays again for up to 245k tokens of context." / "Cache expired 7 min ago. The next prompt here pays again for ...". Refreshed by `ticking()` each second. The card's `aria-label` ends with the short form ("cache expires in 12 min").
- The mark is the one element inside a card with its own tooltip: the pointer on it closes or never opens the opened card (one thing at a time over a card).

## Sounds (`check_cache()` after every live pass, rule in `cache_rings()`)
- `cache-soon.wav` (soft falling chime, 0.7 s) when a session reaches stage 1, `cache-last.wav` (three quick beeps, 0.6 s) at stage 2; `winsound` async, no window, no focus taken.
- Once per stage per idle stretch (`self.cache_rung`: id -> (`cache_base()`, highest stage)); several sessions crossing in one pass make one sound, the more urgent; none at expiry; a session first seen at stage 2 sounds only the second.
- The first pass after the panel starts only records, so a restart or reinstall sounds nothing.
- Silent when the tray's **Alert Sound** is off; nothing at all when `cache_warning` is false.

## Setting
- `cache_warning` in `claude-plus.local.json`: on unless `false`; read on every push and pass. Row *Warn Before a Session's Cache Expires* (switch), first in the settings view; no extra row for the sound.

## Limits
- The cache's real end can differ by a few minutes from `at + ttl`: measured misses from 55 min and hits up to 63 min (`trials.md`). Stage 2 is the last call, not a promise.
- Idle counts from the last line's time; the cache was last used when that reply's request started, so a long reply makes the true idle a little longer.
- Claude Code drops old tool results when it finds the cache cold, so the next prompt usually writes less than the whole context; the tooltip says "up to".
- Sessions inside WSL distros are read the same way; untested there.
