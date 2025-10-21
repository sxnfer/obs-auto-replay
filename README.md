# Event‑Driven Replay Buffer for OBS (Python)

A small OBS Python script that starts the Replay Buffer only when a chosen source is truly detected and stops it as soon as that source disconnects or becomes inactive. It is fully event‑driven (no polling) and hook‑aware for Game/Window Capture.

File: `rb_event_driven.py`

---

## What’s New
- **True detection‑based start:** Removed the old width/height heuristic that could start RB on load. Starts only on real signals or OBS active/showing state for non‑hook sources.
- **Hook‑aware behavior:** With “Prefer capture hooks” enabled, Game/Window Capture waits for `hooked` before starting, and stops on `unhooked`.
- **Stop on disconnect:** When the monitored source is disconnected or changed, RB is explicitly stopped.
- **Clean reconnection:** Rewires on OBS load or scene‑collection changes with short, bounded retries.
- **Save confirmation sound:** Optional sound plays when a clip is saved via Replay Buffer.

---

## How It Works
- **Signals first:** Subscribes to the selected source’s signals to drive RB state.
- **Hook signals:** `hooked` and `unhooked` for `game_capture` and `window_capture` when preferred.
- **Generic signals:** `activate`, `deactivate`, `show`, `hide` as a universal fallback.
- **Initial state policy:**
  - If hook‑capable and hook preference is on, it defers until a real `hooked` event.
  - Otherwise, it uses OBS’s `obs_source_showing`/`obs_source_active` (when available) for a safe initial sync.
- **No polling:** The script does not use timers to sample state, only to retry connection during startup.

---

## Requirements
- **OBS Studio** with Python scripting (Tools → Scripts → Python tab).
- **Replay Buffer configured** (Settings → Output → Replay Buffer) with save path and duration.

---

## Installation
- **Add script:** Tools → Scripts → Python tab → `+` → choose `rb_event_driven.py`.
- **Select source:** In the script UI, choose the source to monitor.
- **Optional:** Enable “Prefer capture hooks (Game/Window)”.

---

## Configuration
- **`Monitor Source`:** Pick the source that should control the Replay Buffer.
- **`Prefer capture hooks (Game/Window)`:** When enabled (default), uses `hooked`/`unhooked` for capture types; generic signals remain connected for coverage.
- **`Play sound when clip saves`:** When enabled, the script plays a short sound on successful Replay Buffer save (useful confirmation).
- **`Sound file (e.g., WAV/MP3)`:** Choose an audio file to play. Cross‑platform playback attempts: on Windows uses `winsound` for WAV, then `ffplay` (if available) or a PowerShell MediaPlayer fallback for MP3/other formats; on macOS uses `afplay`; on Linux tries `paplay`/`aplay`/`ffplay`.
- **`Test Sound`:** Click to immediately play the selected sound (falls back to a simple beep if no/invalid file).
- **`Refresh`:** Repopulates the source list without reopening the dialog.

---

## Expected Behavior
- **Start on detect:** RB starts when the monitored source emits `hooked`/`activate`/`show` or OBS reports it as active/showing (for non‑hook sources).
- **Stop on loss:** RB stops on `unhooked`/`deactivate`/`hide` or when the monitored source is disconnected/changed.
- **No premature start:** RB does not start just because a source has non‑zero dimensions.
- **Rewire on changes:** On OBS load or scene‑collection changes, the script reconnects and resumes listening.
- **Clip save confirmation:** On `Replay Buffer Saved` event, a sound plays if enabled and a valid file is selected.

---

## Supported Sources
- **Game/Window Capture:** Best experience with hook signals (`hooked`/`unhooked`).
- **Other sources (Display, Media, Image, etc.):** Controlled via `activate`/`deactivate`/`show`/`hide` and initial `active/showing` state when available.

---

## Troubleshooting
- **RB doesn’t start:** Confirm Replay Buffer is configured in Settings → Output → Replay Buffer. Check script logs via Help → Log Files → View Current Log.
- **Source never hooks:** For Game/Window Capture, open the target window/game. If it still doesn’t hook, try disabling “Prefer capture hooks” and rely on generic signals.
- **UI lag:** If the Controls dock looks stale, briefly toggle docks/tabs; the script manages RB state even if the button UI lags.
- **Multiple scenes / Studio Mode:** Script reacts to the selected source irrespective of Program. If you need Program‑only gating, open an issue.
- **No sound on save:** Ensure a valid file path is set. On Linux, install one of `paplay`, `aplay`, or `ffplay`. On macOS, `afplay` is built‑in. Windows uses `winsound`.

---

## Notes and Limitations
- **No polling:** All changes are event‑driven; timers are only used for short retries on startup.
- **Source names:** The script binds by source name; renaming requires re‑selection from the list.
- **Hook availability:** Not all sources emit `hooked`/`unhooked`; generic signals provide coverage.

---

## Quick Start
- **Pick a capture source** (e.g., Game Capture) and keep “Prefer capture hooks” enabled.
- **Open the game/window:** RB should start when the source hooks, and stop when it unhooks or is hidden.
- **Try a non‑hook source:** Disable hook preference if necessary and verify start/stop on show/hide.
- **Confirm audio:** Enable “Play sound when clip saves”, choose a file, and click “Test Sound”. Then save a replay to confirm you hear it.

---

## Contributing
- **Principles:** No polling, minimal work in callbacks, clean connect/disconnect, clear logging (INFO for transitions, DEBUG for detail).
- **Issues/PRs:** Welcome for improvements such as Program‑only gating, debounce, or multi‑source logic.
