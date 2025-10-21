# Event-driven Replay Buffer Controller for OBS (no polling)
# Starts RB when a chosen source becomes active/hooked; stops when it deactivates/unhooks.
# Fixes startup race with a short retry routine until OBS finishes restoring sources.

import os
import platform
import shutil
import subprocess

import obspython as obs

# ------------------------
# Globals / configuration
# ------------------------
source_name = ""
source_ref = None
wired_signals = []  # [(signal_name, callback)]
prefer_hook_signals = True
_ui_refresh_scheduled = False

# Startup/collection reload resilience
_connect_retry_cb = None
_connect_retry_attempt = 0
_CONNECT_MAX_ATTEMPTS = 6
_CONNECT_MAX_DELAY_MS = 2000

# Sound configuration
sound_file_path = ""


# ------------------------
# Helpers: Replay Buffer
# ------------------------
def _run_on_main_thread(fn):
    """Schedule fn on OBS main/UI thread via one-shot timer."""

    def thunk():
        obs.timer_remove(thunk)
        try:
            fn()
        finally:
            _schedule_ui_refresh()

    obs.timer_add(thunk, 1)  # hop to next main-loop tick


def _set_rb(active: bool):
    """Ensure RB state matches 'active'. Schedules work on main thread."""

    def _apply():
        is_active = obs.obs_frontend_replay_buffer_active()
        if active and not is_active:
            obs.obs_frontend_replay_buffer_start()
            obs.script_log(obs.LOG_INFO, "Replay Buffer: start requested")
        elif not active and is_active:
            obs.obs_frontend_replay_buffer_stop()
            obs.script_log(obs.LOG_INFO, "Replay Buffer: stop requested")

    _run_on_main_thread(_apply)


# ------------------------
# UI refresh nudge (one-shot)
# ------------------------
def _ui_refresh_tick():
    global _ui_refresh_scheduled
    obs.timer_remove(_ui_refresh_tick)
    _ui_refresh_scheduled = False


def _schedule_ui_refresh():
    """One-shot 100ms timer to make OBS UI reflect RB state promptly."""
    global _ui_refresh_scheduled
    if not _ui_refresh_scheduled:
        _ui_refresh_scheduled = True
        obs.timer_add(_ui_refresh_tick, 100)


# ------------------------
# Signal callbacks
# ------------------------
def _make_signal_cb(sig: str, start: bool):
    def _cb(cd):
        obs.script_log(obs.LOG_DEBUG, f"Signal: {sig} for '{source_name}'")
        _set_rb(start)

    return _cb


# ------------------------
# Wiring / unwiring
# ------------------------
def _disconnect_current():
    global source_ref, wired_signals, _connect_retry_cb, _connect_retry_attempt
    if source_ref is not None:
        sh = obs.obs_source_get_signal_handler(source_ref)
        for sig, cb in wired_signals:
            obs.signal_handler_disconnect(sh, sig, cb)
        wired_signals = []
        obs.obs_source_release(source_ref)
        source_ref = None
        obs.script_log(obs.LOG_INFO, "Disconnected from previous source")
    # cancel any pending connect retry
    if _connect_retry_cb is not None:
        obs.timer_remove(_connect_retry_cb)
        _connect_retry_cb = None
        _connect_retry_attempt = 0


def _connect_to_source(name: str):
    """Connect to the given source by name and wire up signals. Retries if source isn't ready yet."""
    global source_ref, wired_signals, _connect_retry_cb, _connect_retry_attempt

    _disconnect_current()

    if not name:
        obs.script_log(obs.LOG_WARNING, "No source selected; nothing to connect.")
        return

    src = obs.obs_get_source_by_name(name)
    if not src:
        # Expected during startup; keep quiet so Script Log doesn't pop.
        obs.script_log(obs.LOG_DEBUG, f"Source '{name}' not found yet; retrying…")
        _schedule_connect_retry()
        return

    # success: clear pending retry state
    if _connect_retry_cb is not None:
        obs.timer_remove(_connect_retry_cb)
        _connect_retry_cb = None
        _connect_retry_attempt = 0

    source_id = obs.obs_source_get_id(src) or ""
    obs.script_log(obs.LOG_INFO, f"Connecting to source: '{name}' (type: {source_id})")

    source_ref = src
    sh = obs.obs_source_get_signal_handler(src)

    # Wire signals with minimal, unified callbacks
    to_wire = []
    if prefer_hook_signals:
        to_wire += [("hooked", True), ("unhooked", False)]
    to_wire += [
        ("activate", True),
        ("deactivate", False),
        ("show", True),
        ("hide", False),
    ]

    for sig, is_start in to_wire:
        cb = _make_signal_cb(sig, is_start)
        obs.signal_handler_connect(sh, sig, cb)
        wired_signals.append((sig, cb))

    _sync_now_with_dimensions(src)


def _sync_now_with_dimensions(src):
    """One-shot check to align RB with current state (not polling)."""
    try:
        w = obs.obs_source_get_width(src)
        h = obs.obs_source_get_height(src)
    except Exception:
        w = h = 0
    obs.script_log(
        obs.LOG_DEBUG, f"Initial size for '{obs.obs_source_get_name(src)}': {w}x{h}"
    )
    _set_rb(w > 0 and h > 0)


# ------------------------
# Retry helper (startup/collection changes)
# ------------------------
def _schedule_connect_retry():
    """Exponential backoff to bind to the selected source while OBS restores state."""
    global _connect_retry_cb, _connect_retry_attempt
    if not source_name:
        return
    # cap retries (~3–4 seconds typical; can raise if needed)
    if _connect_retry_attempt >= _CONNECT_MAX_ATTEMPTS:
        return

    _connect_retry_attempt += 1
    delay = min(200 * (2 ** (_connect_retry_attempt - 1)), _CONNECT_MAX_DELAY_MS)

    def fire():
        global _connect_retry_cb
        obs.timer_remove(fire)
        _connect_retry_cb = None
        _connect_to_source(source_name)

    if _connect_retry_cb is not None:
        obs.timer_remove(_connect_retry_cb)
    _connect_retry_cb = fire
    obs.timer_add(fire, delay)


# ------------------------
# Frontend events
# ------------------------
def _on_frontend_event(event):
    # Keep UI crisp when RB changes, whether by us or not
    if event in (
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED,
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED,
    ):
        _schedule_ui_refresh()

    # Play confirmation sound when a replay is saved
    rb_saved_evt = getattr(obs, "OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED", None)
    if rb_saved_evt is not None and event == rb_saved_evt:
        _play_save_sound()

    # After OBS finishes loading / when collections change, try (re)connecting
    if event in (
        getattr(obs, "OBS_FRONTEND_EVENT_FINISHED_LOADING", -1),
        getattr(obs, "OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED", -2),
    ):
        if source_name and source_ref is None:
            _schedule_connect_retry()


# ------------------------
# OBS Script API
# ------------------------
def script_description():
    return (
        "Event-driven Replay Buffer controller (no polling).\n"
        "Starts RB when the selected source is active/hooked, stops when it deactivates.\n"
        "Resilient to OBS startup by retrying until sources are restored."
    )


def _populate_sources_list(list_prop):
    obs.obs_property_list_clear(list_prop)
    sources = obs.obs_enum_sources()
    added = 0
    if sources is not None:
        for src in sources:
            name = obs.obs_source_get_name(src)
            sid = obs.obs_source_get_id(src)
            label = f"{name}  [{sid}]"
            obs.obs_property_list_add_string(list_prop, label, name)
            added += 1
        obs.source_list_release(sources)
    if added == 0:
        obs.obs_property_list_add_string(list_prop, "— No sources found —", "")


def _on_refresh_sources(props, prop):
    p_list = obs.obs_properties_get(props, "source_name")
    if p_list is not None:
        _populate_sources_list(p_list)
    return True


def script_properties():
    props = obs.obs_properties_create()
    p_list = obs.obs_properties_add_list(
        props,
        "source_name",
        "Source to monitor",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING,
    )
    _populate_sources_list(p_list)

    obs.obs_properties_add_bool(
        props,
        "prefer_hook_signals",
        "Prefer capture hook signals (Game/Window Capture)",
    )
    obs.obs_properties_add_path(
        props,
        "sound_file",
        "Sound file (e.g., WAV/MP3)",
        obs.OBS_PATH_FILE,
        "Audio files (*.wav *.mp3 *.ogg *.flac);;All files (*.*)",
        None,
    )
    obs.obs_properties_add_button(props, "test_sound", "Test Sound", _on_test_sound)
    obs.obs_properties_add_button(
        props, "refresh_sources", "Refresh source list", _on_refresh_sources
    )
    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "prefer_hook_signals", True)


def script_update(settings):
    global source_name, prefer_hook_signals, sound_file_path
    new_name = obs.obs_data_get_string(settings, "source_name")
    prefer_hook_signals = obs.obs_data_get_bool(settings, "prefer_hook_signals")
    sound_file_path = obs.obs_data_get_string(settings, "sound_file") or ""

    if new_name != source_name:
        source_name = new_name
        _connect_to_source(source_name)
    else:
        # On startup, settings may be loaded before sources exist
        if source_name and source_ref is None:
            _schedule_connect_retry()


def script_load(settings):
    obs.obs_frontend_add_event_callback(_on_frontend_event)
    # OBS may still be restoring the scene collection when the script initializes
    _schedule_connect_retry()


def script_unload():
    _disconnect_current()


# ------------------------
# Sound playback
# ------------------------
def _play_sound_file(path: str):
    """Cross-platform, non-blocking playback of a local audio file.

    If path is empty or missing, do nothing silently (default: no sound).
    """
    p = (path or "").strip()
    if not p:
        return
    if not os.path.isfile(p):
        obs.script_log(obs.LOG_WARNING, f"Sound file not found: {p}")
        return

    system = platform.system().lower()
    try:
        if system == "windows":
            try:
                import winsound  # type: ignore

                # winsound supports WAV only; use it for .wav paths
                if p.lower().endswith(".wav"):
                    winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
            except Exception:
                pass  # fall through to generic players

        if system == "darwin":  # macOS
            if shutil.which("afplay"):
                subprocess.Popen(["afplay", p])
                return

        # Linux/Windows/unknown: try common CLI players
        for player in ("paplay", "aplay", "ffplay"):
            exe = shutil.which(player)
            if not exe:
                continue
            if player == "ffplay":
                # On Windows, avoid flashing a console window
                _kwargs = {}
                if system == "windows":
                    _kwargs["creationflags"] = getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    )
                subprocess.Popen(
                    [exe, "-nodisp", "-autoexit", p],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_kwargs,
                )
            else:
                subprocess.Popen(
                    [exe, p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            return

        # Final Windows fallback for non-WAV (e.g., MP3): use PowerShell MediaPlayer
        if system == "windows":
            ps = shutil.which("pwsh") or shutil.which("powershell")
            if ps:
                try:
                    # Use PresentationCore MediaPlayer to play most common audio types.
                    from pathlib import Path

                    uri = Path(p).resolve().as_uri()
                    ps_cmd = (
                        "Add-Type -AssemblyName PresentationCore; "
                        "$m = New-Object System.Windows.Media.MediaPlayer; "
                        "$null = Register-ObjectEvent -InputObject $m -EventName MediaEnded -Action { $global:__obs_done = $true }; "
                        "$null = Register-ObjectEvent -InputObject $m -EventName MediaFailed -Action { $global:__obs_failed = $true }; "
                        "$null = Register-ObjectEvent -InputObject $m -EventName MediaOpened -Action { $global:__obs_opened = $true }; "
                        f'$m.Open([Uri]"{uri}"); '
                        "$m.Volume = 1.0; $m.Play(); "
                        "$sw = [System.Diagnostics.Stopwatch]::StartNew(); "
                        "while(-not $global:__obs_done -and -not $global:__obs_failed -and $sw.Elapsed.TotalSeconds -lt 30){ Start-Sleep -Milliseconds 100 }"
                    )
                    # Build args based on host (Windows PowerShell vs PowerShell Core)
                    if ps.lower().endswith("powershell.exe"):
                        args = [
                            ps,
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-STA",
                            "-Command",
                            ps_cmd,
                        ]
                    else:
                        args = [ps, "-NoProfile", "-NonInteractive", "-Command", ps_cmd]
                    # Hide console window on Windows
                    kwargs = {
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    }
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    subprocess.Popen(args, **kwargs)
                    return
                except Exception:
                    pass
    except Exception as e:
        obs.script_log(obs.LOG_WARNING, f"Failed to play sound: {e}")


def _play_save_sound():
    """Play configured sound when a replay is saved (if any)."""
    if not sound_file_path:
        return  # default: no sound
    _play_sound_file(sound_file_path)


def _on_test_sound(props, prop):
    """UI callback to test the selected sound."""
    if not sound_file_path:
        obs.script_log(obs.LOG_WARNING, "Test Sound: no file selected")
    _play_sound_file(sound_file_path)
    return True
