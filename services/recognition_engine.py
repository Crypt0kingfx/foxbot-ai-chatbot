from datetime import datetime

from models.studio_state import (
    STUDIO_STATE,
    RECOGNITION_HISTORY,
    SUPPORT_REWARDS,
    RECOGNITION_TEMPLATES,
    studio_log,
    add_foxcoins
)

def studio_recognition_response(event_type: str, user: str = "TestUser", amount: str = "$5"):
    reward = SUPPORT_REWARDS.get(event_type, 0)
    template = RECOGNITION_TEMPLATES.get(event_type, "🦊 Thanks {user}!")

    message = template.format(
        user=user,
        reward=reward,
        amount=amount
    )

    add_foxcoins(reward)

    if event_type == "follow":
        STUDIO_STATE["followersToday"] += 1
    elif event_type == "vote":
        STUDIO_STATE["votesToday"] += 1
    elif event_type == "sub":
        STUDIO_STATE["subsToday"] += 1
    elif event_type == "tip":
        numeric_tip = 5
        STUDIO_STATE["tipsTotal"] += numeric_tip
        STUDIO_STATE["tipsToday"] = f"${STUDIO_STATE['tipsTotal']}"

    entry = {
        "time": datetime.now().strftime("%I:%M:%S %p"),
        "event": event_type,
        "user": user,
        "reward": reward,
        "message": message
    }

    RECOGNITION_HISTORY.insert(0, entry)
    RECOGNITION_HISTORY[:] = RECOGNITION_HISTORY[:50]

    studio_log(message)

    return entry
