import obspython as obs

# ------------------------
# State
# ------------------------
source_name = ""
source_ref = None
prefer_hook_signals = True
retry_count = 0
MAX_RETRIES = 6


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

    # Sync initial state based on source dimensions
    w, h = obs.obs_source_get_width(src), obs.obs_source_get_height(src)
    obs.script_log(obs.LOG_DEBUG, f"Initial dimensions: {w}x{h}")
    set_replay_buffer(w > 0 and h > 0)


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
    obs.obs_properties_add_button(props, "refresh", "Refresh", on_refresh_clicked)
    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "prefer_hook_signals", True)


def script_update(settings):
    global source_name, prefer_hook_signals

    new_name = obs.obs_data_get_string(settings, "source_name")
    prefer_hook_signals = obs.obs_data_get_bool(settings, "prefer_hook_signals")

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
