# Event-driven Replay Buffer Controller for OBS (no polling)
# Starts RB when a chosen source becomes active/hooked; stops when it deactivates/unhooks.
# Fixes startup race with a short retry routine until OBS finishes restoring sources.

import obspython as obs

# ------------------------
# Globals / configuration
# ------------------------
source_name = ""
source_ref = None
wired_signals = []     # [(signal_name, callback)]
prefer_hook_signals = True
_ui_refresh_scheduled = False

# Startup/collection reload resilience
_connect_retry_cb = None
_connect_retry_attempt = 0

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

    # Prefer capture hook signals (Game/Window Capture), harmless on others
    if prefer_hook_signals:
        obs.signal_handler_connect(sh, "hooked", on_hooked);   wired_signals.append(("hooked", on_hooked))
        obs.signal_handler_connect(sh, "unhooked", on_unhooked); wired_signals.append(("unhooked", on_unhooked))

    # Generic visibility/activation signals (fallback + extra coverage)
    for sig, cb in (("activate", on_activate), ("deactivate", on_deactivate), ("show", on_show), ("hide", on_hide)):
        obs.signal_handler_connect(sh, sig, cb); wired_signals.append((sig, cb))

    _sync_now_with_dimensions(src)

def _sync_now_with_dimensions(src):
    """One-shot check to align RB with current state (not polling)."""
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
# Retry helper (startup/collection changes)
# ------------------------
def _schedule_connect_retry():
    """Exponential backoff to bind to the selected source while OBS restores state."""
    global _connect_retry_cb, _connect_retry_attempt
    if not source_name:
        return
    # cap retries (~3–4 seconds typical; can raise if needed)
    max_attempts = 6
    if _connect_retry_attempt >= max_attempts:
        return

    _connect_retry_attempt += 1
    delay = min(200 * (2 ** (_connect_retry_attempt - 1)), 2000)

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
    if event in (obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED,
                 obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED):
        _schedule_ui_refresh()

    # After OBS finishes loading / when collections change, try (re)connecting
    if event in (getattr(obs, "OBS_FRONTEND_EVENT_FINISHED_LOADING", -1),
                 getattr(obs, "OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED", -2)):
        if source_name and source_ref is None:
            _schedule_connect_retry()

# ------------------------
# OBS Script API
# ------------------------
def script_description():
    return ("Event-driven Replay Buffer controller (no polling).\n"
            "Starts RB when the selected source is active/hooked, stops when it deactivates.\n"
            "Resilient to OBS startup by retrying until sources are restored.")

def _populate_sources_list(list_prop):
    obs.obs_property_list_clear(list_prop)
    sources = obs.obs_enum_sources()
    added = 0
    if sources is not None:
        for src in sources:
            name = obs.obs_source_get_name(src)
            sid  = obs.obs_source_get_id(src)
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
        props, "source_name", "Source to monitor",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    _populate_sources_list(p_list)

    obs.obs_properties_add_bool(props, "prefer_hook_signals",
        "Prefer capture hook signals (Game/Window Capture)")
    obs.obs_properties_add_button(props, "refresh_sources",
        "Refresh source list", _on_refresh_sources)
    return props

def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "prefer_hook_signals", True)

def script_update(settings):
    global source_name, prefer_hook_signals
    new_name = obs.obs_data_get_string(settings, "source_name")
    prefer_hook_signals = obs.obs_data_get_bool(settings, "prefer_hook_signals")

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
