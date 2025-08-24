# Event‑Driven Replay Buffer Controller for OBS (Python, no polling)

A lightweight OBS script that **starts the Replay Buffer when a chosen source becomes active** and **stops it when that source deactivates**—all without interval polling. It uses OBS **signal handlers** (events) for near‑zero CPU overhead and snappy UI updates.

> File: `rb_event_driven.py`

---

## Why event‑driven?
Traditional polling scripts wake up every N milliseconds to check source state, which wastes CPU and can still react late. This script listens to OBS **signals** such as `hooked`/`unhooked` (for Game/Window Capture) and generic `activate`/`deactivate`/`show`/`hide` (for all sources). The script runs only when something meaningful happens.

---

## Features
- **Zero polling** — reacts purely to OBS signals.
- **Game/Window Capture aware** — uses `hooked`/`unhooked` when available.
- **Works with any source type** — falls back to generic visibility/activation signals.
- **Instant UI feedback** — safely marshals actions to the UI thread and nudges the UI, so the **Replay Buffer button updates immediately**.
- **One‑time initial sync** — aligns RB state to the current source size on (re)configuration.
- **Clean lifecycle** — connects/disconnects handlers and releases OBS references correctly.

---

## Requirements
- OBS Studio with **Python scripting** enabled (Tools → Scripts → *Python* tab present).
- A configured **Replay Buffer** (Settings → Output → Replay Buffer): set a save path and duration.

> Tip: OBS bundles a specific Python version; make sure the installed Python scripting runtime matches your OBS build.

---

## Installation
1. Save the script as `rb_event_driven.py` somewhere on your machine.
2. In OBS: **Tools → Scripts**.
3. Select the **Python** tab, click **+**, and choose `rb_event_driven.py`.
4. The script’s UI will appear in the right‑hand pane.

---

## Configuration
- **Source to monitor**: pick the source that should control the Replay Buffer (e.g., a *Game Capture* or *Window Capture*).
- **Prefer capture hook signals**: when enabled (default), the script listens for `hooked`/`unhooked` on capture sources for maximum accuracy; generic signals remain connected as a fallback.
- **Refresh source list**: repopulates the dropdown if you’ve added/renamed sources while the dialog is open.

**Behavior**
- When the chosen source **hooks/activates/shows**, the script **starts** the Replay Buffer.
- When it **unhooks/deactivates/hides**, the script **stops** the Replay Buffer.
- On first setup or when you change the source, the script performs a **one‑time width/height check** to immediately match the current state—still no ongoing polling.

---

## How it works (under the hood)
- Subscribes to the selected source’s **signal handler**:
  - Capture‑specific: `hooked`, `unhooked` (Game/Window Capture).
  - Generic: `activate`, `deactivate`, `show`, `hide` (any source).
- Signal callbacks invoke **start/stop** via a small dispatcher that runs on OBS’s **main/UI thread** for safety.
- A short **one‑shot UI nudge** ensures the Controls dock visually updates immediately after a transition.
- Listens for `REPLAY_BUFFER_STARTED/STOPPED` **frontend events** to keep UI state crisp even when RB changes outside the script.

---

## Troubleshooting
- **RB doesn’t start**: Verify Replay Buffer is enabled and configured in **Settings → Output → Replay Buffer**. Check the script’s log messages in **Help → Log Files → View Current Log** or the Scripts dialog.
- **Button state lags or needs a hover**: This version marshals calls to the UI thread and nudges the UI; if you still see lag, confirm the Control dock is visible and not hidden by a custom layout.
- **Capture type doesn’t emit `hooked`/`unhooked`**: That’s expected for some sources. The script still responds to `activate`/`deactivate`/`show`/`hide`.
- **Multiple scenes / Studio Mode**: By default, the script reacts to the selected source’s signals regardless of whether it’s on **Program**. See *Roadmap* for Program‑only gating.

---

## Roadmap / Ideas
- **Program‑only gating** (Studio‑mode aware): start only when the source is visible on Program.
- **Debounce transient losses** (loading screens, alt‑tab) to avoid start/stop thrash.
- **Multi‑source selection**: start if *any* of several sources are active; stop when *none* are.
- **Save‑on‑loss**: automatically save the current replay on unhook before stopping RB.
- **Automation toggle hotkey**: quickly suspend/resume automation during rehearsals.
- **Scene/profile resilience**: rewire and resync on scene collection/profile changes.

---

## Contributing
Issues and PRs are welcome. Please keep the core philosophy intact:
- No polling.
- Minimal allocations in callbacks.
- Clean connect/disconnect on settings change and unload.
- Clear, level‑appropriate logging (INFO for transitions, DEBUG for detail).

---

## License
Add a license file to your repository (for example, MIT or Apache‑2.0) and reference it here.

---

## Credits
- Script author: You.
- Event‑driven refactor and documentation: collaborative work with GPT‑5 Thinking.

