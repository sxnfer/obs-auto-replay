# Event-driven Replay Buffer Controller for OBS (no polling)
# -------------------------------------------------------
# This script starts the Replay Buffer when a chosen source becomes active
# and stops it when the source deactivates (or unhooks), using OBS signals
# instead of interval polling.
#
# How it works (short):
# - Connects to the selected source's signal handler.
# - Uses capture-hook signals when available (Game/Window Capture),
#   falling back to generic activate/deactivate signals for any source type.
# - Gives the UI a tiny one-shot nudge after start/stop so the state
#   visually updates immediately.
#
# Notes:
# - No periodic timers are used for monitoring. Only a one-shot 100ms UI
#   refresh timer runs after each state change.
# - On (re)configuring the source, we do a one-time width/height check
#   to align the Replay Buffer with the current reality without waiting
#   for a new event.
#
# Author: You + GPT-5 Thinking

import obspython as obs

# ------------------------
# Globals / configuration
# ------------------------
source_name = ""
source_ref = None
wired_signals = []  # list[(signal_name, callback)] so we can disconnect cleanly
prefer_hook_signals = True
_ui_refresh_scheduled = False

# ------------------------
# Helpers: Replay Buffer
# ------------------------

def _run_on_main_thread(fn):
    """Schedule fn on OBS main/UI thread via one-shot timer."""
    def thunk():
        # remove this one-shot and run the function
        obs.timer_remove(thunk)
        try:
            fn()
        finally:
            _schedule_ui_refresh()
    # 1 ms is enough to hop to main loop next tick
    obs.timer_add(thunk, 1)


def _ensure_rb_started():
    def _do_start():
        if not obs.obs_frontend_replay_buffer_active():
            obs.obs_frontend_replay_buffer_start()
            obs.script_log(obs.LOG_INFO, "Replay Buffer: start requested")
    _run_on_main_thread(_do_start)


def _ensure_rb_stopped():
    def _do_stop():
        if obs.obs_frontend_replay_buffer_active():
            obs.obs_frontend_replay_buffer_stop()
            obs.script_log(obs.LOG_INFO, "Replay Buffer: stop requested")
    _run_on_main_thread(_do_stop)


# ------------------------
# UI refresh nudge (one-shot)
# ------------------------

def _ui_refresh_tick():
    global _ui_refresh_scheduled
    # Remove this one-shot tick immediately
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

def on_hooked(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: hooked from '{source_name}'")
    _ensure_rb_started()


def on_unhooked(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: unhooked from '{source_name}'")
    _ensure_rb_stopped()


def on_activate(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: activate for '{source_name}'")
    _ensure_rb_started()


def on_deactivate(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: deactivate for '{source_name}'")
    _ensure_rb_stopped()


def on_show(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: show for '{source_name}'")
    _ensure_rb_started()


def on_hide(cd):
    obs.script_log(obs.LOG_DEBUG, f"Signal: hide for '{source_name}'")
    _ensure_rb_stopped()


# ------------------------
# Wiring / unwiring signals
# ------------------------

def _disconnect_current():
    global source_ref, wired_signals
    if source_ref is not None:
        sh = obs.obs_source_get_signal_handler(source_ref)
        for sig, cb in wired_signals:
            obs.signal_handler_disconnect(sh, sig, cb)
        wired_signals = []
        obs.obs_source_release(source_ref)
        source_ref = None
        obs.script_log(obs.LOG_INFO, "Disconnected from previous source")


def _connect_to_source(name: str):
    """Connect to the given source by name and wire up the best-available signals."""
    global source_ref, wired_signals

    _disconnect_current()

    if not name:
        obs.script_log(obs.LOG_WARNING, "No source selected; nothing to connect.")
        return

    src = obs.obs_get_source_by_name(name)
    if not src:
        obs.script_log(obs.LOG_WARNING, f"Source '{name}' not found; cannot connect.")
        return

    source_id = obs.obs_source_get_id(src) or ""
    obs.script_log(obs.LOG_INFO, f"Connecting to source: '{name}' (type: {source_id})")

    # Hold a reference while we're wired
    source_ref = src

    sh = obs.obs_source_get_signal_handler(src)

    # Try to use capture hook signals when available (common for Game/Window Capture)
    # If not present, these connects are harmless no-ops until such signals ever fire.
    if prefer_hook_signals:
        obs.signal_handler_connect(sh, "hooked", on_hooked)
        wired_signals.append(("hooked", on_hooked))
        obs.signal_handler_connect(sh, "unhooked", on_unhooked)
        wired_signals.append(("unhooked", on_unhooked))

    # Always fall back to generic source visibility/activation signals
    for sig, cb in (
        ("activate", on_activate),
        ("deactivate", on_deactivate),
        ("show", on_show),
        ("hide", on_hide),
    ):
        obs.signal_handler_connect(sh, sig, cb)
        wired_signals.append((sig, cb))

    # One-time sync with current state: infer from width/height right now
    _sync_now_with_dimensions(src)


def _sync_now_with_dimensions(src):
    """One-shot check of current frame size to align RB state immediately.
    This is not polling; it runs only on (re)connect or settings change.
    """
    try:
        w = obs.obs_source_get_width(src)
        h = obs.obs_source_get_height(src)
    except Exception:
        w = h = 0
    obs.script_log(obs.LOG_DEBUG, f"Initial size for '{obs.obs_source_get_name(src)}': {w}x{h}")

    if w > 0 and h > 0:
        _ensure_rb_started()
    else:
        _ensure_rb_stopped()


# ------------------------
# Frontend events
# ------------------------

def _on_frontend_event(event):
    # Keep UI crisp when RB changes outside our control or as a result of our calls
    if event in (
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED,
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED,
    ):
        _schedule_ui_refresh()

# ------------------------
# OBS Script API
# ------------------------

def script_description():
    return (
        "Event-driven Replay Buffer controller (no polling).\n"
        "Starts RB when the selected source is active/hooked, stops it when it deactivates.\n"
        "Uses capture hook signals when present, with generic signals as fallback."
    )


def script_properties():
    props = obs.obs_properties_create()

    # Source dropdown
    p_list = obs.obs_properties_add_list(
        props,
        "source_name",
        "Source to monitor",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING,
    )
    _populate_sources_list(p_list)

    # Prefer hook signals checkbox
    p_hook = obs.obs_properties_add_bool(
        props,
        "prefer_hook_signals",
        "Prefer capture hook signals (Game/Window Capture)",
    )

    # Refresh button to repopulate the source list
    obs.obs_properties_add_button(props, "refresh_sources", "Refresh source list", _on_refresh_sources)

    return props


def _populate_sources_list(list_prop):
    # Clear and repopulate the dropdown with all user-visible sources
    obs.obs_property_list_clear(list_prop)

    # Enumerate all sources
    sources = obs.obs_enum_sources()
    added = 0
    if sources is not None:
        for src in sources:
            name = obs.obs_source_get_name(src)
            sid = obs.obs_source_get_id(src)
            # Show type in label to help disambiguate similarly named sources
            label = f"{name}  [{sid}]"
            obs.obs_property_list_add_string(list_prop, label, name)
            added += 1
        obs.source_list_release(sources)

    if added == 0:
        obs.obs_property_list_add_string(list_prop, "— No sources found —", "")


def _on_refresh_sources(props, prop):
    # Rebuild the source list when the button is pressed
    p_list = obs.obs_properties_get(props, "source_name")
    if p_list is not None:
        _populate_sources_list(p_list)
    return True


def script_update(settings):
    global source_name, prefer_hook_signals

    new_name = obs.obs_data_get_string(settings, "source_name")
    prefer_hook_signals = obs.obs_data_get_bool(settings, "prefer_hook_signals")

    # Connect (or reconnect) if the name changed
    if new_name != source_name:
        source_name = new_name
        _connect_to_source(source_name)


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "prefer_hook_signals", True)


def script_load(settings):
    # Register for frontend events so we can nudge UI when RB starts/stops
    obs.obs_frontend_add_event_callback(_on_frontend_event)
    pass


def script_unload():
    _disconnect_current()
