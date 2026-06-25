from models.studio_state import (
    BLAZE_LISTENER_STATE,
    BLAZE_EVENT_MAP
)

def connect():
    BLAZE_LISTENER_STATE["connected"] = True
    return BLAZE_LISTENER_STATE

def disconnect():
    BLAZE_LISTENER_STATE["connected"] = False
    return BLAZE_LISTENER_STATE

def status():
    return BLAZE_LISTENER_STATE

def map_event(event_name: str):
    return BLAZE_EVENT_MAP.get(
        str(event_name).lower().strip()
    )

def increment_event(event_name: str):
    BLAZE_LISTENER_STATE["eventsReceived"] += 1
    BLAZE_LISTENER_STATE["lastEvent"] = event_name
