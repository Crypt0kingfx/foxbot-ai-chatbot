# -*- coding: utf-8 -*-
"""
FoxBot Rewards 2.2
Safe module used by /api/foxbot/admin-command.
"""

import json
import re
import time
from pathlib import Path

ADMIN_USERNAME = "crypt0k1ng96"

REWARDS = [
    {"id":"hug","emoji":"🤗💛","cost":10,"category":"cheap","name":"Fox Hug","aliases":["hug","foxhug"]},
    {"id":"hype","emoji":"🔥⚡","cost":25,"category":"cheap","name":"Hype Blast","aliases":["hype"]},
    {"id":"flex","emoji":"💪😤","cost":50,"category":"cheap","name":"Flex Moment","aliases":["flex"]},
    {"id":"hydrate","emoji":"💧🧊","cost":50,"category":"cheap","name":"Hydration Check","aliases":["hydrate","water"]},
    {"id":"stretch","emoji":"🧘✨","cost":75,"category":"cheap","name":"Stretch Break","aliases":["stretch"]},
    {"id":"foxfact","emoji":"🦊📜","cost":75,"category":"cheap","name":"Fox Fact","aliases":["foxfact","fact"]},
    {"id":"clipit","emoji":"🎬✂️","cost":100,"category":"cheap","name":"Clip This Moment","aliases":["clipit","clip"]},
    {"id":"lurklove","emoji":"👀💜","cost":100,"category":"cheap","name":"Lurker Love","aliases":["lurklove","lurk"]},

    {"id":"shoutout","emoji":"📣🌟","cost":500,"category":"social","name":"Stream Shoutout","aliases":["shoutout","so"]},
    {"id":"socialsplug","emoji":"🔗🚀","cost":650,"category":"social","name":"Socials Plug","aliases":["socialsplug","socials","plug"]},
    {"id":"nickname","emoji":"🏷️😂","cost":800,"category":"social","name":"Temporary Nickname","aliases":["nickname","nick"]},
    {"id":"poll","emoji":"🗳️🧠","cost":900,"category":"social","name":"Community Poll","aliases":["poll","vote"]},
    {"id":"mvp","emoji":"👑🔥","cost":1000,"category":"social","name":"MVP Spotlight","aliases":["mvp"]},
    {"id":"og","emoji":"🛡️🦊","cost":1000,"category":"social","name":"OG Spirit Shoutout","aliases":["og"]},
    {"id":"raidcaptain","emoji":"🚩⚔️","cost":1200,"category":"social","name":"Raid Captain","aliases":["raidcaptain","raid"]},
    {"id":"vipwall","emoji":"⭐🏆","cost":1500,"category":"social","name":"VIP Wall Mention","aliases":["vipwall","vip"]},

    {"id":"loadout","emoji":"🎯🔫","cost":750,"category":"control","name":"Choose My Loadout","aliases":["loadout"]},
    {"id":"dropzone","emoji":"🪂📍","cost":850,"category":"control","name":"Choose Drop Zone","aliases":["dropzone","drop"]},
    {"id":"challenge","emoji":"⚡🎲","cost":1000,"category":"control","name":"Streamer Challenge","aliases":["challenge"]},
    {"id":"gamemode","emoji":"🕹️🎮","cost":1250,"category":"control","name":"Choose Game Mode","aliases":["gamemode","mode"]},
    {"id":"soundalert","emoji":"🔊🤣","cost":1250,"category":"control","name":"Sound Alert Moment","aliases":["soundalert","sound"]},
    {"id":"choosegame","emoji":"🎮👀","cost":3000,"category":"control","name":"Choose The Game","aliases":["choosegame","game"]},

    {"id":"goldenfox","emoji":"🦊🏆✨","cost":2500,"category":"premium","name":"Golden Fox Callout","aliases":["goldenfox"]},
    {"id":"treasurecall","emoji":"💰🗺️","cost":3000,"category":"premium","name":"Treasure Drop Call","aliases":["treasurecall","treasure"]},
    {"id":"bossboost","emoji":"⚔️👹","cost":3500,"category":"premium","name":"Boss Battle Boost","aliases":["bossboost","boss"]},
    {"id":"doublepoints","emoji":"✖️2️⃣✨","cost":4000,"category":"premium","name":"Double Points Minute","aliases":["doublepoints","double","2x"]},
    {"id":"giveawayboost","emoji":"🎟️🎁","cost":4500,"category":"premium","name":"Giveaway Entry Boost","aliases":["giveawayboost","ticket"]},
    {"id":"mysterybox","emoji":"🎁❓","cost":5000,"category":"premium","name":"Mystery Box","aliases":["mysterybox","box"]},

    {"id":"sponsor","emoji":"💎📢","cost":10000,"category":"elite","name":"Sponsor Spotlight","aliases":["sponsor"]},
    {"id":"producer","emoji":"🎥👑","cost":15000,"category":"elite","name":"Stream Producer Credit","aliases":["producer"]},
    {"id":"streamsegment","emoji":"🎤🎬","cost":20000,"category":"elite","name":"Viewer Stream Segment","aliases":["streamsegment","segment"]},
    {"id":"vipnight","emoji":"🌙🌟","cost":25000,"category":"elite","name":"VIP Community Night Vote","aliases":["vipnight"]},
    {"id":"foxlegend","emoji":"🏆🦊🔥","cost":50000,"category":"elite","name":"Fox Legend Status","aliases":["foxlegend","legend"]},
]

def _queue_path():
    Path("data").mkdir(parents=True, exist_ok=True)
    return Path("data") / "foxbot_rewards22_redemptions.json"

def _read_queue():
    path = _queue_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def _write_queue(queue):
    _queue_path().write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")

def _result_text(result):
    if isinstance(result, dict):
        return str(result.get("response") or result.get("reply") or result.get("message") or result)
    return str(result or "")

def _find_reward(name):
    key = str(name or "").strip().lower().replace(" ", "")
    for reward in REWARDS:
        keys = [reward["id"], reward["name"].lower().replace(" ", "")]
        keys += [str(a).lower().replace(" ", "") for a in reward.get("aliases", [])]
        if key in keys:
            return reward
    return None

def _shop(page="main"):
    page = str(page or "main").strip().lower()
    aliases = {
        "starter":"cheap",
        "basic":"cheap",
        "recognition":"social",
        "stream":"control",
        "high":"premium",
        "legend":"elite",
        "full":"all"
    }
    page = aliases.get(page, page)

    labels = {
        "cheap":"🪙 Starter Fun Rewards — Prices in FoxCoins",
        "social":"🌟 Recognition Rewards — Prices in FoxCoins",
        "control":"🎮 Control The Stream — Prices in FoxCoins",
        "premium":"💎 Premium Chaos Rewards — Prices in FoxCoins",
        "elite":"🏆 Elite Fox Legend Rewards — Prices in FoxCoins",
        "all":"🌈 All FoxBot Rewards — Prices in FoxCoins",
    }

    if page in {"main", "menu"}:
        return "🦊✨ FoxBot Rewards 2.2 ✨🦊 | 🪙 !shop cheap | 🌟 !shop social | 🎮 !shop control | 💎 !shop premium | 🏆 !shop elite | 🌈 !shop all | 🎁 Redeem: !redeem name"

    if page == "all":
        return "🌈 All FoxBot Rewards — Prices in FoxCoins: cheap: hug, hype, flex, hydrate, stretch, foxfact, clipit, lurklove | social: shoutout, socialsplug, nickname, poll, mvp, og, raidcaptain, vipwall | control: loadout, dropzone, challenge, gamemode, soundalert, choosegame | premium: goldenfox, treasurecall, bossboost, doublepoints, giveawayboost, mysterybox | elite: sponsor, producer, streamsegment, vipnight, foxlegend | 🎁 Use !redeem name"

    items = [r for r in REWARDS if r["category"] == page]
    if not items:
        return "🦊❓ Shop page not found. Try !shop cheap, social, control, premium, elite, or all."

    return labels[page] + ": " + " | ".join([f"{r['emoji']} {r['id']} {r['cost']}" for r in items]) + " | 🎁 Redeem: !redeem name"

def _get_balance(username, chat_func):
    try:
        result = chat_func(message="!balance", username=username)
        text = _result_text(result)
        patterns = [
            r"([0-9][0-9,]*)\s*(?:FoxCoins|foxcoins|coins|points|FC)",
            r"balance[^0-9]*([0-9][0-9,]*)",
            r"has[^0-9]*([0-9][0-9,]*)",
        ]
        found = []
        for pat in patterns:
            found += re.findall(pat, text, flags=re.I)
        if found:
            return int(found[-1].replace(",", "")), text
        return None, text
    except Exception as e:
        return None, f"balance check failed: {e}"

def _give_points(username, amount, chat_func):
    try:
        result = chat_func(message=f"!givepoints {username} {int(amount)}", username=ADMIN_USERNAME)
        return True, _result_text(result)
    except Exception as e:
        return False, str(e)

def _redeem(username, reward_name, chat_func):
    username = str(username or "viewer").strip().lstrip("@")
    reward = _find_reward(reward_name)

    if not reward:
        return "🦊❓ Reward not found. Try !shop cheap, social, control, premium, or elite."

    cost = int(reward["cost"])
    balance, balance_text = _get_balance(username, chat_func)

    deducted = False
    deduct_text = ""

    if balance is not None:
        if balance < cost:
            return f"🚫 @{username}, {reward['name']} costs {cost} FoxCoins, but you only have {balance}. Keep hunting 🦊✨"

        deducted, deduct_text = _give_points(username, -cost, chat_func)
        if not deducted:
            return f"⚠️ @{username}, you have enough FoxCoins, but FoxBot could not deduct them yet. Ask the streamer to approve manually."

    queue = _read_queue()
    item = {
        "id": f"R{int(time.time())}{len(queue)+1}",
        "username": username,
        "reward_id": reward["id"],
        "reward_name": reward["name"],
        "emoji": reward["emoji"],
        "cost": cost,
        "category": reward["category"],
        "status": "pending",
        "deducted": deducted,
        "balance_before": balance,
        "balance_status": "verified" if balance is not None else "manual_review",
        "deduct_result": deduct_text,
        "created_at": int(time.time()),
    }
    queue.append(item)
    _write_queue(queue)

    if balance is None:
        return f"{reward['emoji']} @{username} requested {reward['name']} for {cost} FoxCoins! 📋 Added to pending queue for manual balance approval. ID: {item['id']}"

    return f"{reward['emoji']} REDEEMED! @{username} spent {cost} FoxCoins on {reward['name']}! ✨ Added to pending reward queue. ID: {item['id']}"

def _queue_text():
    queue = _read_queue()
    pending = [q for q in queue if q.get("status") in {"pending", "approved"}]
    if not pending:
        return "🦊✨ Reward queue is empty. Chat can redeem with !redeem name."

    latest = pending[-5:]
    parts = []
    for i, item in enumerate(latest, start=1):
        parts.append(f"#{i} {item.get('emoji','🎁')} @{item.get('username')} - {item.get('reward_name')} [{item.get('status')}] {item.get('id')}")

    return "🎁 Pending Reward Queue: " + " | ".join(parts) + " | Admin: !rewardapprove latest, !rewardcomplete latest, !rewarddeny latest"

def _find_queue_item(target):
    queue = _read_queue()
    target = str(target or "latest").strip()
    candidates = [q for q in queue if q.get("status") in {"pending", "approved"}]

    if not candidates:
        return queue, None, None

    if target.lower() in {"", "latest", "last"}:
        item = candidates[-1]
        return queue, item, queue.index(item)

    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(candidates):
            item = candidates[idx]
            return queue, item, queue.index(item)

    for item in queue:
        item_id = str(item.get("id", ""))
        if item_id == target or item_id.lower().endswith(target.lower()):
            return queue, item, queue.index(item)

    return queue, None, None

def _admin_action(action, target, chat_func):
    queue, item, idx = _find_queue_item(target)
    if item is None:
        return "🦊❓ Reward item not found. Use !rewardqueue."

    if action == "approve":
        item["status"] = "approved"
        item["approved_at"] = int(time.time())
        queue[idx] = item
        _write_queue(queue)
        return f"✅ Approved reward {item.get('id')}: {item.get('emoji')} @{item.get('username')} - {item.get('reward_name')}"

    if action == "complete":
        item["status"] = "completed"
        item["completed_at"] = int(time.time())
        queue[idx] = item
        _write_queue(queue)
        return f"🎉 Completed reward {item.get('id')}: {item.get('emoji')} @{item.get('username')} - {item.get('reward_name')}"

    if action == "deny":
        refunded = False
        refund_text = ""
        if item.get("deducted"):
            refunded, refund_text = _give_points(item.get("username"), int(item.get("cost", 0)), chat_func)

        item["status"] = "denied"
        item["denied_at"] = int(time.time())
        item["refunded"] = refunded
        item["refund_result"] = refund_text
        queue[idx] = item
        _write_queue(queue)

        if refunded:
            return f"🚫 Denied reward {item.get('id')} and refunded {item.get('cost')} FoxCoins to @{item.get('username')}."
        return f"🚫 Denied reward {item.get('id')} for @{item.get('username')}."

    return "🦊 Unknown reward action."

def handle_rewards_command(username, message, chat_func):
    msg = str(message or "").strip()
    lower = msg.lower()

    if lower in {"!shop", "!rewards", "!rewardshop"} or lower.startswith("!shop ") or lower.startswith("!rewards "):
        parts = msg.split()
        page = parts[1] if len(parts) > 1 else "main"
        return _shop(page)

    if lower.startswith("!redeem"):
        reward_name = msg.split(" ", 1)[1].strip() if " " in msg else ""
        return _redeem(username, reward_name, chat_func)

    if lower in {"!rewardqueue", "!redemptions", "!queue"}:
        return _queue_text()

    if lower.startswith("!rewardapprove"):
        target = msg.split(" ", 1)[1].strip() if " " in msg else "latest"
        return _admin_action("approve", target, chat_func)

    if lower.startswith("!rewardcomplete"):
        target = msg.split(" ", 1)[1].strip() if " " in msg else "latest"
        return _admin_action("complete", target, chat_func)

    if lower.startswith("!rewarddeny"):
        target = msg.split(" ", 1)[1].strip() if " " in msg else "latest"
        return _admin_action("deny", target, chat_func)

    return None
