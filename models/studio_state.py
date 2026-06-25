from datetime import datetime

STUDIO_STATE = {
    "botOnline": True,
    "recognitionEnabled": True,
    "followersToday": 0,
    "votesToday": 0,
    "subsToday": 0,
    "tipsToday": "$0",
    "tipsTotal": 0,
    "foxcoinsToday": 0,
    "recognitionQueue": 0,
    "bossHp": "100%",
    "currentEvent": "None",
    "activity": [
        {
            "time": datetime.now().strftime("%I:%M:%S %p"),
            "message": "🦊 FoxBot Studio online."
        }
    ]
}

BLAZE_LISTENER_STATE = {
    "connected": False,
    "lastEvent": "None",
    "eventsReceived": 0,
    "mappedEvents": 0
}

RECOGNITION_HISTORY = []

SUPPORT_REWARDS = {
    "follow": 50,
    "vote": 25,
    "sub": 250,
    "gift_sub": 300,
    "tip": 500,
    "raid": 300
}

RECOGNITION_TEMPLATES = {
    "follow": "⭐ Thanks {user} for following the Fox Spirit family! +{reward} FoxCoins",
    "vote": "🗳️ Thanks {user} for voting! +{reward} FoxCoins",
    "sub": "🔥 Huge love to {user} for subscribing! +{reward} FoxCoins",
    "gift_sub": "🎁 {user} gifted a sub! Absolute legend! +{reward} FoxCoins",
    "tip": "💚 {user} tipped {amount}! +{reward} FoxCoins",
    "raid": "🚀 {user} raided the stream! Welcome raiders! +{reward} FoxCoins"
}

BLAZE_EVENT_MAP = {
    "follow": "follow",
    "follower": "follow",
    "vote": "vote",
    "sub": "sub",
    "subscription": "sub",
    "gift_sub": "gift_sub",
    "giftsub": "gift_sub",
    "tip": "tip",
    "donation": "tip",
    "raid": "raid"
}

def studio_log(message: str):
    STUDIO_STATE["activity"].insert(0, {
        "time": datetime.now().strftime("%I:%M:%S %p"),
        "message": message
    })
    STUDIO_STATE["activity"] = STUDIO_STATE["activity"][:25]

def add_foxcoins(amount: int):
    STUDIO_STATE["foxcoinsToday"] += amount
