import os
import platform
import shutil
import subprocess

import obspython as obs

# ------------------------
# State
# ------------------------
source_name = ""
source_ref = None
prefer_hook_signals = True
retry_count = 0
MAX_RETRIES = 6
play_sound_on_save = False
sound_file_path = ""


# ------------------------
# Core Logic
# ------------------------
def set_replay_buffer(active: bool):
    """Single point of control for replay buffer state."""
    is_active = obs.obs_frontend_replay_buffer_active()
    if active and not is_active:
        obs.obs_frontend_replay_buffer_start()
        obs.script_log(obs.LOG_INFO, "Replay Buffer started")
    elif not active and is_active:
        obs.obs_frontend_replay_buffer_stop()
        obs.script_log(obs.LOG_INFO, "Replay Buffer stopped")


def on_source_active(cd):
    """Unified callback for all 'source is active' signals."""
    obs.script_log(obs.LOG_DEBUG, f"Source '{source_name}' active")
    set_replay_buffer(True)


def on_source_inactive(cd):
    """Unified callback for all 'source is inactive' signals."""
    obs.script_log(obs.LOG_DEBUG, f"Source '{source_name}' inactive")
    set_replay_buffer(False)


# ------------------------
# Connection Management
# ------------------------
def disconnect_source():
    """Clean up current source connection."""
    global source_ref, retry_count

    # Ensure RB is not left running when we drop the monitored source
    set_replay_buffer(False)

    if source_ref:
        sh = obs.obs_source_get_signal_handler(source_ref)
        # Disconnect all signals at once (signal_handler handles duplicates gracefully)
        for sig in ["hooked", "activate", "show"]:
            obs.signal_handler_disconnect(sh, sig, on_source_active)
        for sig in ["unhooked", "deactivate", "hide"]:
            obs.signal_handler_disconnect(sh, sig, on_source_inactive)

        obs.obs_source_release(source_ref)
        source_ref = None
        obs.script_log(obs.LOG_INFO, "Disconnected from source")

    obs.remove_current_callback()  # Cancel any pending retry
    retry_count = 0


def connect_source(name: str):
    """Connect to source and wire signals. Retries if source not ready."""
    global source_ref, retry_count

    disconnect_source()

    if not name:
        return

    src = obs.obs_get_source_by_name(name)
    if not src:
        # Retry with exponential backoff
        if retry_count < MAX_RETRIES:
            delay = min(200 * (2**retry_count), 2000)
            retry_count += 1
            obs.timer_add(lambda: connect_source(name), delay)
            obs.script_log(
                obs.LOG_DEBUG,
                f"Source '{name}' not ready, retry {retry_count}/{MAX_RETRIES}",
            )
        return

    # Success
    retry_count = 0
    source_ref = src
    sh = obs.obs_source_get_signal_handler(src)

    obs.script_log(
        obs.LOG_INFO, f"Connected to '{name}' ({obs.obs_source_get_id(src)})"
    )

    # Wire up signals based on preference
    if prefer_hook_signals:
        obs.signal_handler_connect(sh, "hooked", on_source_active)
        obs.signal_handler_connect(sh, "unhooked", on_source_inactive)

    # Always wire generic signals (provides fallback coverage)
    for sig in ["activate", "show"]:
        obs.signal_handler_connect(sh, sig, on_source_active)
    for sig in ["deactivate", "hide"]:
        obs.signal_handler_connect(sh, sig, on_source_inactive)

    # Initial state: avoid dimension heuristics which can be non-zero when not truly active
    # For hook-capable sources, wait for a real 'hooked' signal if preferred.
    sid = obs.obs_source_get_id(src)
    is_hook_capable = sid in ("game_capture", "window_capture")

    if prefer_hook_signals and is_hook_capable:
        obs.script_log(
            obs.LOG_DEBUG, "Deferring initial start; waiting for hook signal"
        )
    else:
        # Use OBS's notion of active/showing if available; otherwise, defer to signals
        showing_fn = getattr(obs, "obs_source_showing", None)
        active_fn = getattr(obs, "obs_source_active", None)
        is_showing = bool(showing_fn(src)) if callable(showing_fn) else False
        is_active = bool(active_fn(src)) if callable(active_fn) else False
        obs.script_log(
            obs.LOG_DEBUG, f"Initial state: showing={is_showing} active={is_active}"
        )
        set_replay_buffer(is_showing or is_active)


# ------------------------
# OBS Callbacks
# ------------------------
def on_frontend_event(event):
    """Handle OBS frontend events."""
    # Trigger reconnect after startup or collection changes
    if event in (
        getattr(obs, "OBS_FRONTEND_EVENT_FINISHED_LOADING", -1),
        getattr(obs, "OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED", -2),
    ):
        if source_name:
            connect_source(source_name)

    # Play confirmation sound on replay buffer saved
    rb_saved_evt = getattr(obs, "OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED", None)
    if rb_saved_evt is not None and event == rb_saved_evt:
        try_play_save_sound()


def populate_sources(list_prop):
    """Fill dropdown with available sources."""
    obs.obs_property_list_clear(list_prop)
    sources = obs.obs_enum_sources()

    if sources:
        for src in sources:
            name = obs.obs_source_get_name(src)
            sid = obs.obs_source_get_id(src)
            obs.obs_property_list_add_string(list_prop, f"{name} [{sid}]", name)
        obs.source_list_release(sources)
    else:
        obs.obs_property_list_add_string(list_prop, "— No sources —", "")


def on_refresh_clicked(props, prop):
    """Refresh button callback."""
    populate_sources(obs.obs_properties_get(props, "source_name"))
    return True


# ------------------------
# OBS Script Interface
# ------------------------
def script_description():
    return (
        "Event-driven Replay Buffer controller.\n"
        "Automatically starts/stops replay buffer based on source activity."
    )


def script_properties():
    props = obs.obs_properties_create()
    p_list = obs.obs_properties_add_list(
        props,
        "source_name",
        "Monitor Source",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING,
    )
    populate_sources(p_list)
    obs.obs_properties_add_bool(
        props, "prefer_hook_signals", "Prefer capture hooks (Game/Window)"
    )
    obs.obs_properties_add_bool(
        props, "play_sound_on_save", "Play sound when clip saves"
    )
    obs.obs_properties_add_path(
        props,
        "sound_file",
        "Sound file (e.g., WAV/MP3)",
        obs.OBS_PATH_FILE,
        "Audio files (*.wav *.mp3 *.ogg *.flac);;All files (*.*)",
        None,
    )
    obs.obs_properties_add_button(
        props, "test_sound", "Test Sound", on_test_sound_clicked
    )
    obs.obs_properties_add_button(props, "refresh", "Refresh", on_refresh_clicked)
    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "prefer_hook_signals", True)
    obs.obs_data_set_default_bool(settings, "play_sound_on_save", False)


def script_update(settings):
    global source_name, prefer_hook_signals, play_sound_on_save, sound_file_path

    new_name = obs.obs_data_get_string(settings, "source_name")
    prefer_hook_signals = obs.obs_data_get_bool(settings, "prefer_hook_signals")
    play_sound_on_save = obs.obs_data_get_bool(settings, "play_sound_on_save")
    sound_file_path = obs.obs_data_get_string(settings, "sound_file") or ""

    if new_name != source_name:
        source_name = new_name
        connect_source(source_name)


def script_load(settings):
    obs.obs_frontend_add_event_callback(on_frontend_event)
    # Trigger initial connection attempt
    if source_name:
        connect_source(source_name)


def script_unload():
    disconnect_source()


# ------------------------
# Sound Playback
# ------------------------
def play_sound_file(path: str, allow_beep: bool = True, context: str = ""):
    """Cross-platform, non-blocking playback of a local audio file.

    Falls back to a console beep if no player is available and allow_beep=True.
    """
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        if allow_beep:
            print("\a", end="")
            msg = f"{context + ' - ' if context else ''}beep (no/invalid file)"
            obs.script_log(obs.LOG_DEBUG, msg)
        else:
            obs.script_log(
                obs.LOG_WARNING,
                f"{context + ' - ' if context else ''}sound file missing: {path}",
            )
        return

    system = platform.system().lower()
    try:
        if system == "windows":
            try:
                import winsound  # type: ignore

                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                obs.script_log(obs.LOG_INFO, f"{context} played (winsound)")
                return
            except Exception as e:  # Fallback to PowerShell
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[console]::beep(1000,100)",
                ]
                subprocess.Popen(cmd)
                obs.script_log(
                    obs.LOG_DEBUG, f"{context} winsound failed, PowerShell beep: {e}"
                )
                return

        if system == "darwin":  # macOS
            if shutil.which("afplay"):
                subprocess.Popen(["afplay", path])
                obs.script_log(obs.LOG_INFO, f"{context} played (afplay)")
                return

        # Linux or unknown: try paplay/aplay/ffplay
        for player in ("paplay", "aplay", "ffplay"):
            exe = shutil.which(player)
            if not exe:
                continue
            if player == "ffplay":
                subprocess.Popen(
                    [exe, "-nodisp", "-autoexit", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    [exe, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            obs.script_log(obs.LOG_INFO, f"{context} played ({player})")
            return

        # As a last resort: console beep
        if allow_beep:
            print("\a", end="")
            obs.script_log(obs.LOG_DEBUG, f"{context} console beep fallback")
    except Exception as e:
        obs.script_log(obs.LOG_WARNING, f"{context} failed to play sound: {e}")


def try_play_save_sound():
    """Attempt to play the configured sound asynchronously for replay save."""
    if not play_sound_on_save:
        return
    path = (sound_file_path or "").strip()
    if not path:
        obs.script_log(obs.LOG_WARNING, "Clip saved - sound disabled (no file set)")
        return
    play_sound_file(path, allow_beep=True, context="Replay save sound")


def on_test_sound_clicked(props, prop):
    """UI button callback to test the selected sound file."""
    path = (sound_file_path or "").strip()
    if not path:
        obs.script_log(obs.LOG_WARNING, "Test Sound - no file set; playing beep")
    play_sound_file(path, allow_beep=True, context="Test sound")
    return True
