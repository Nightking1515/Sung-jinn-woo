# solo_bot.py
"""
Solo Leveling style Telegram bot - full implementation for commands list.
- Uses sqlite3 for storage (solo_bot.db).
- Requires BOT_TOKEN env var.
- All messages in English.
- Deploy on Render, Replit, or your VPS.
"""

import os
import sqlite3
import random
import math
from datetime import datetime, date, timedelta
from functools import wraps

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
from telegram.ext import (
    Application, CommandHandler, CallbackContext
)

# ---- PostgreSQL connection ----
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    level INT DEFAULT 0,
    rank TEXT DEFAULT 'E',
    won_in_hand BIGINT DEFAULT 0,
    won_in_bank BIGINT DEFAULT 0,
    xp BIGINT DEFAULT 0,
    pvp_wins INT DEFAULT 0,
    pvp_losses INT DEFAULT 0
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS items (
    item_id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    item_name TEXT,
    quantity INT DEFAULT 1
);
""")

conn.commit()
# --------------------------------

# ---------------- CONFIG ----------------

ADMIN_TG_ID = None
...

# --------------- CONFIG ---------------
DB_PATH = "solo_bot.db"
ADMIN_TG_ID = None  # set to your telegram id if you want admin-only commands active
INTEREST_RATE_DAILY = 0.02  # 2% daily interest (interest goes to HAND)
DAILY_TASK_COUNT = 3
import os
BOT_TOKEN = "8050711631:AAEOmQtI1LDg8F5zBST1tIPh0mDtHbIISEs"

if not BOT_TOKEN:
    print("ERROR: Set BOT_TOKEN environment variable before running.")
    # don't exit here; app.run_polling will error if not set

# ------------ DB Helpers & Init ------------
def db_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = db_conn(); c = conn.cursor()
    # users table
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE,
        username TEXT,
        level INTEGER DEFAULT 0,
        rank TEXT DEFAULT 'E',
        hand_won INTEGER DEFAULT 0,
        bank_won INTEGER DEFAULT 0,
        loan_amount INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        pvp_points INTEGER DEFAULT 0,
        strength INTEGER DEFAULT 10,
        agility INTEGER DEFAULT 10,
        intelligence INTEGER DEFAULT 10,
        vitality INTEGER DEFAULT 10,
        sense INTEGER DEFAULT 10,
        title TEXT DEFAULT '',
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );""")
    # inventory
    c.execute("""CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_type TEXT,
        name TEXT,
        quantity INTEGER DEFAULT 1,
        is_equipped INTEGER DEFAULT 0
    );""")
    # daily tasks
    c.execute("""CREATE TABLE IF NOT EXISTS daily_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_text TEXT,
        requirement INTEGER,
        progress INTEGER DEFAULT 0,
        is_completed INTEGER DEFAULT 0,
        reward_won INTEGER DEFAULT 0,
        reward_item TEXT,
        assigned_date DATE
    );""")
    # matches (PvP sessions & logs)
    c.execute("""CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attacker_id INTEGER,
        defender_id INTEGER,
        is_active INTEGER DEFAULT 1,
        turn INTEGER, -- tg_id whose turn it is
        attacker_hp INTEGER,
        defender_hp INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        winner_id INTEGER,
        loser_id INTEGER,
        type TEXT -- 'player' or 'bot'
    );""")
    conn.commit(); conn.close()

# ------------ Utility functions ------------
def register_user_if_missing(tg_id, username):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,))
    if c.fetchone():
        conn.close(); return False
    c.execute("INSERT INTO users (tg_id, username, hand_won) VALUES (?, ?, ?)", (tg_id, username, 0))
    conn.commit(); conn.close()
    return True

def get_user_by_tg(tg_id):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT id,tg_id,username,level,rank,hand_won,bank_won,loan_amount,wins,losses,pvp_points,strength,agility,intelligence,vitality,sense,title FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone(); conn.close()
    if not row: return None
    keys = ["id","tg_id","username","level","rank","hand_won","bank_won","loan_amount","wins","losses","pvp_points","strength","agility","intelligence","vitality","sense","title"]
    return dict(zip(keys,row))

def user_exists(tg_id):
    return get_user_by_tg(tg_id) is not None

def update_user_field(tg_id, field, value):
    conn = db_conn(); c = conn.cursor()
    c.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (value, tg_id))
    conn.commit(); conn.close()

def adjust_money(tg_id, hand_delta=0, bank_delta=0):
    user = get_user_by_tg(tg_id)
    if not user: return None
    new_hand = max(0, user['hand_won'] + hand_delta)
    new_bank = max(0, user['bank_won'] + bank_delta)
    conn = db_conn(); c = conn.cursor()
    c.execute("UPDATE users SET hand_won=?, bank_won=? WHERE tg_id=?", (new_hand, new_bank, tg_id))
    conn.commit(); conn.close()
    return new_hand, new_bank

def award_won(tg_id, amount, reason="Reward"):
    if amount <= 0: return get_user_by_tg(tg_id)['hand_won']
    conn = db_conn(); c = conn.cursor()
    c.execute("UPDATE users SET hand_won = hand_won + ? WHERE tg_id=?", (amount, tg_id))
    conn.commit()
    c.execute("SELECT hand_won FROM users WHERE tg_id=?", (tg_id,)); new_hand = c.fetchone()[0]
    conn.close()
    return new_hand

def assign_daily_tasks_for_user_id(user_id):
    conn = db_conn(); c = conn.cursor()
    today = date.today().isoformat()
    # remove today's tasks to ensure fresh
    c.execute("DELETE FROM daily_tasks WHERE user_id=? AND assigned_date=?", (user_id, today))
    pool = [
        ("Win 3 PvP vs Bot", 3, 50, None),
        ("Win 2 PvP vs Player", 2, 100, None),
        ("Deposit 500₩ to Bank", 500, 30, None),
        ("Buy 1 Sword from Shop", 1, 80, None),
        ("Use 2 Revival Items", 2, 120, None),
    ]
    chosen = random.sample(pool, k=DAILY_TASK_COUNT)
    for text, req, reward_won, reward_item in chosen:
        c.execute("""INSERT INTO daily_tasks (user_id,task_text,requirement,progress,is_completed,reward_won,reward_item,assigned_date)
                     VALUES (?,?,?,?,?,?,?,?)""", (user_id, text, req, 0, 0, reward_won, reward_item, today))
    conn.commit(); conn.close()

def get_daily_tasks_for_user_id(user_id):
    conn = db_conn(); c = conn.cursor()
    today = date.today().isoformat()
    c.execute("SELECT id,task_text,requirement,progress,is_completed,reward_won,reward_item FROM daily_tasks WHERE user_id=? AND assigned_date=?", (user_id, today))
    rows = c.fetchall(); conn.close()
    return rows

# ------------ Shop items (simple) ------------
# Shop items data
SHOP_ITEMS = {
    "swords": [
        {"id": 1, "name": "Iron Sword", "price": 200, "damage": 10},
        {"id": 2, "name": "Steel Sword", "price": 500, "damage": 20},
        {"id": 3, "name": "Silver Sword", "price": 800, "damage": 30},
        {"id": 4, "name": "Magic Sword", "price": 1500, "damage": 50},
        {"id": 5, "name": "Flame Sword", "price": 2200, "damage": 70},
        {"id": 6, "name": "Ice Sword", "price": 2500, "damage": 80},
        {"id": 7, "name": "Thunder Sword", "price": 3000, "damage": 100},
        {"id": 8, "name": "Dark Sword", "price": 4000, "damage": 120},
        {"id": 9, "name": "Light Sword", "price": 4200, "damage": 125},
        {"id": 10, "name": "Dragon Slayer", "price": 5000, "damage": 150},
        {"id": 11, "name": "Shadow Blade", "price": 6000, "damage": 180},
        {"id": 12, "name": "Heavenly Sword", "price": 7500, "damage": 200},
        {"id": 13, "name": "Chaos Sword", "price": 10000, "damage": 250},
        {"id": 14, "name": "Demonic Sword", "price": 12000, "damage": 300},
        {"id": 15, "name": "Excalibur", "price": 15000, "damage": 400},
    ],
    "revival": [
        {"id": 16, "name": "Revival Potion", "price": 500, "effect": "Revive with 20% HP"},
        {"id": 17, "name": "Strong Revival Potion", "price": 1200, "effect": "Revive with 50% HP"},
        {"id": 18, "name": "Phoenix Feather", "price": 2500, "effect": "Revive with 100% HP"},
        {"id": 19, "name": "Life Scroll", "price": 3000, "effect": "Revive + 20% XP"},
        {"id": 20, "name": "Divine Elixir", "price": 4000, "effect": "Revive with full stats"},
        {"id": 21, "name": "Resurrection Stone", "price": 5000, "effect": "Revive 2 times"},
        {"id": 22, "name": "Angel Tear", "price": 6500, "effect": "Revive + Shield for 1 turn"},
        {"id": 23, "name": "Holy Water", "price": 7000, "effect": "Revive + Full HP"},
        {"id": 24, "name": "God’s Blessing", "price": 9000, "effect": "Auto Revive once"},
        {"id": 25, "name": "Immortal Charm", "price": 12000, "effect": "Revive + Invincible 1 turn"},
    ],
    "poison": [
        {"id": 26, "name": "Poison Dagger", "price": 700, "damage": 15},
        {"id": 27, "name": "Venom Bottle", "price": 1200, "damage": 25},
        {"id": 28, "name": "Toxin Bomb", "price": 2000, "damage": 40},
        {"id": 29, "name": "Paralysis Poison", "price": 2500, "damage": 50},
        {"id": 30, "name": "Deadly Venom", "price": 3500, "damage": 80},
        {"id": 31, "name": "Corruption Gas", "price": 4000, "damage": 100},
        {"id": 32, "name": "Silent Killer", "price": 5000, "damage": 120},
        {"id": 33, "name": "Toxic Arrow", "price": 6000, "damage": 140},
        {"id": 34, "name": "Necro Venom", "price": 7500, "damage": 180},
        {"id": 35, "name": "Plague Bomb", "price": 9000, "damage": 220},
    ],
    "special": [
        {"id": 36, "name": "Hunter Key", "price": 300, "effect": "Unlock dungeons"},
        {"id": 37, "name": "Magic Shield", "price": 2000, "effect": "Reduce damage 20%"},
        {"id": 38, "name": "Golden Armor", "price": 5000, "effect": "Reduce damage 50%"},
        {"id": 39, "name": "XP Booster", "price": 1500, "effect": "Gain double XP"},
        {"id": 40, "name": "Lucky Charm", "price": 1200, "effect": "Increase drop rate"},
        {"id": 41, "name": "Soul Orb", "price": 2500, "effect": "Extra summon power"},
        {"id": 42, "name": "Dark Crystal", "price": 4000, "effect": "Boost poison attack"},
        {"id": 43, "name": "Sacred Ring", "price": 6000, "effect": "Immune to poison 2 turns"},
        {"id": 44, "name": "Teleport Scroll", "price": 1000, "effect": "Escape from battle"},
        {"id": 45, "name": "Binding Chains", "price": 2000, "effect": "Stun enemy 1 turn"},
        {"id": 46, "name": "Power Elixir", "price": 3000, "effect": "Increase damage 30%"},
        {"id": 47, "name": "Stamina Potion", "price": 1500, "effect": "Restore 100 stamina"},
        {"id": 48, "name": "Hunter Medal", "price": 500, "effect": "Collectible"},
        {"id": 49, "name": "Dimensional Stone", "price": 7000, "effect": "Summon ally"},
        {"id": 50, "name": "Time Relic", "price": 10000, "effect": "Take extra turn"},
    ]
}
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

# /shop command
def shop_cmd(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("All Items", callback_data="shop_all")],
        [InlineKeyboardButton("Swords", callback_data="shop_swords")],
        [InlineKeyboardButton("Revival Items", callback_data="shop_revival")],
        [InlineKeyboardButton("Poisons", callback_data="shop_poison")],
    ]

    # top 10 most expensive items (across all categories)
    all_items = []
    for category in SHOP_ITEMS.values():
        all_items.extend(category)
    top_items = sorted(all_items, key=lambda x: x["price"], reverse=True)[:10]

    text = "🏪 **SHOP**\n\n🔥 Top 10 Expensive Items:\n"
    for item in top_items:
        text += f"➡️ {item['name']} — 💰 {item['price']}\n"

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


# shop callback for categories
def shop_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    data = query.data.replace("shop_", "")

    if data == "all":
        items = []
        for category in SHOP_ITEMS.values():
            items.extend(category)
    else:
        items = SHOP_ITEMS.get(data, [])

    if not items:
        query.edit_message_text("❌ No items found in this category.")
        return

    text = f"🏪 **SHOP — {data.capitalize()}**\n\n"
    for item in items:
        price = item.get("price", 0)
        text += f"➡️ {item['name']} — 💰 {price}\n"

    query.edit_message_text(text, parse_mode="Markdown")



def buy_item(tg_id, item_id):
    user = get_user_by_tg(tg_id)
    if not user: return False, "User not found."
    item = next((i for i in SHOP_ITEMS if i['id']==item_id), None)
    if not item: return False, "Item not found."
    if user['hand_won'] < item['price']:
        return False, "Not enough Won in hand."
    adjust_money(tg_id, hand_delta=-item['price'])
    conn = db_conn(); c = conn.cursor()
    c.execute("INSERT INTO inventory (user_id,item_type,name,quantity) VALUES (?,?,?,?)", (user['id'], item['type'], item['name'], 1))
    conn.commit(); conn.close()
    return True, f"Bought {item['name']} for {item['price']}₩."

# ------------ PvP Logic & Matches ------------
def compute_power(user):
    # simple power function: rank weight + level * factor + stats contribution
    rank_base = {
        "E":1,"D":3,"C":6,"B":12,"A":20
    }
    if user['rank'].startswith("Sjp"):
        # Sjp ranks high
        try:
            num = int(user['rank'][3:])
            rank_val = 200 + num
        except:
            rank_val = 200
    elif user['rank'].startswith("S"):
        try:
            num = int(user['rank'][1:])
            rank_val = 12 + num
        except:
            rank_val = 12
    else:
        rank_val = rank_base.get(user['rank'], 1)
    stats = (user['strength'] + user['agility'] + user['vitality'] + user['intelligence'] + user['sense'])/10.0
    power = rank_val + user['level'] * 1.5 + stats
    return power

def start_pvp_request(attacker_tg, defender_tg):
    # create match row as pending; actual battle starts on accept
    conn = db_conn(); c = conn.cursor()
    attacker = get_user_by_tg(attacker_tg); defender = get_user_by_tg(defender_tg)
    if not attacker or not defender:
        conn.close(); return None
    # initial HP values relative to a baseline (you can adjust)
    base_hp = 100 + attacker['level']*10
    base_hp2 = 100 + defender['level']*10
    c.execute("""INSERT INTO matches (attacker_id, defender_id, is_active, turn, attacker_hp, defender_hp, type)
                 VALUES (?, ?, 0, ?, ?, ?, 'player')""", (attacker['id'], defender['id'], defender_tg, base_hp, base_hp2))
    match_id = c.lastrowid
    conn.commit(); conn.close()
    return match_id

def create_active_match(attacker_tg, defender_tg):
    # create active match when both agreed: attacker takes first turn by default
    conn = db_conn(); c = conn.cursor()
    attacker = get_user_by_tg(attacker_tg); defender = get_user_by_tg(defender_tg)
    if not attacker or not defender:
        conn.close(); return None
    hp1 = 100 + attacker['level']*10
    hp2 = 100 + defender['level']*10
    c.execute("""INSERT INTO matches (attacker_id, defender_id, is_active, turn, attacker_hp, defender_hp, type)
                 VALUES (?, ?, 1, ?, ?, ?, 'player')""", (attacker['id'], defender['id'], attacker_tg, hp1, hp2))
    mid = c.lastrowid
    conn.commit(); conn.close()
    return mid

def get_active_match_by_participants(attacker_id, defender_id):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE attacker_id=? AND defender_id=? AND is_active=1", (attacker_id, defender_id))
    row = c.fetchone(); conn.close()
    return row

def end_match(match_id, winner_id, loser_id):
    conn = db_conn(); c = conn.cursor()
    c.execute("UPDATE matches SET is_active=0, winner_id=?, loser_id=? WHERE id=?", (winner_id, loser_id, match_id))
    conn.commit(); conn.close()

# --------------- BOT HANDLERS ---------------
def only_for_registered(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg_id = update.effective_user.id
        if not user_exists(tg_id):
            await update.message.reply_text("Please /start first to register.")
            return
        return await func(update, context)
    return wrapper

# /start
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.full_name
    is_new = register_user_if_missing(tg_id, username)
    if is_new:
        # starter won and daily tasks
        adjust_money(tg_id, hand_delta=200)
        user = get_user_by_tg(tg_id)
        assign_daily_tasks_for_user_id(user['id'])
        await update.message.reply_text(f"Welcome {username}! You are registered. Starter 200₩ has been added to your HAND. Use /profile to view your stats.")
    else:
        await update.message.reply_text("You are already registered. Use /profile or /status to check your data.")

# /profile (self or reply)
@only_for_registered
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # if message is a reply, show target's profile
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        target_user = get_user_by_tg(target.id)
        if not target_user:
            await update.message.reply_text("That user is not registered.")
            return
        user = target_user
    else:
        user = get_user_by_tg(update.effective_user.id)
    text = (f"Profile: {user['username']}\n"
            f"Level: {user['level']}  Rank: {user['rank']}\n"
            f"Wins: {user['wins']}  Losses: {user['losses']}\n"
            f"Hand: {user['hand_won']}₩  Bank: {user['bank_won']}₩\n")
    # count items
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM inventory WHERE user_id=?", (user['id'],))
    item_count = c.fetchone()[0]; conn.close()
    text += f"Items: {item_count}\nTitle: {user.get('title','')}\n"
    await update.message.reply_text(text)

# /status
@only_for_registered
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_by_tg(update.effective_user.id)
    # compute next rank requirement (simple rule): require pvp_points threshold
    current_points = user['pvp_points']
    next_threshold = (user['level'] + 1) * 50  # example
    text = (f"Status for {user['username']}:\n"
            f"Strength: {user['strength']}\nAgility: {user['agility']}\nIntelligence: {user['intelligence']}\n"
            f"Vitality: {user['vitality']}\nSense: {user['sense']}\n"
            f"PvP Points: {current_points}\nPoints needed for next level/rank progress: {max(0, next_threshold - current_points)}")
    await update.message.reply_text(text)

# /rank
@only_for_registered
async def rank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        tuser = get_user_by_tg(target.id)
        if not tuser:
            await update.message.reply_text("Target user not registered.")
            return
        await update.message.reply_text(f"{tuser['username']}'s Rank: {tuser['rank']}")
    else:
        u = get_user_by_tg(update.effective_user.id)
        await update.message.reply_text(f"Your Rank: {u['rank']}")

# /level
@only_for_registered
async def level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        tuser = get_user_by_tg(target.id)
        if not tuser:
            await update.message.reply_text("Target user not registered.")
            return
        await update.message.reply_text(f"{tuser['username']}'s Level: {tuser['level']}")
    else:
        u = get_user_by_tg(update.effective_user.id)
        await update.message.reply_text(f"Your Level: {u['level']}")

# /pvp (reply to user to challenge)
@only_for_registered
async def pvp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user message with /pvp to challenge them.")
        return
    attacker = update.effective_user
    defender = update.message.reply_to_message.from_user
    if attacker.id == defender.id:
        await update.message.reply_text("You cannot challenge yourself.")
        return
    if not user_exists(defender.id):
        await update.message.reply_text("The target is not registered in the bot.")
        return
    # send accept/decline inline keyboard to defender
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Accept", callback_data=f"pvp_accept:{attacker.id}"),
                                InlineKeyboardButton("Decline", callback_data=f"pvp_decline:{attacker.id}")]])
    await update.message.reply_text(f"You challenged @{defender.username or defender.full_name}. Waiting for response...", reply_markup=None)
    try:
        await context.bot.send_message(chat_id=defender.id, text=f"You have been challenged to a PvP by @{attacker.username or attacker.full_name}. Accept?", reply_markup=kb)
    except Exception:
        await update.message.reply_text("Could not send challenge to the target (maybe their privacy settings).")

# Callback for accept/decline
async def pvp_accept_decline_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    # format: pvp_accept:<attacker_tg> or pvp_decline:<attacker_tg>
    parts = data.split(":")
    if len(parts) != 2:
        await query.message.reply_text("Invalid callback.")
        return
    action, attacker_tg = parts[0], int(parts[1])
    defender_tg = query.from_user.id
    attacker_user = get_user_by_tg(attacker_tg); defender_user = get_user_by_tg(defender_tg)
    if action == "pvp_decline":
        try:
            await context.bot.send_message(chat_id=attacker_tg, text=f"Your PvP challenge to @{defender_user['username']} was declined.")
        except:
            pass
        await query.message.reply_text("You declined the challenge.")
        return
    # accept: create active match and notify both
    match_id = create_active_match(attacker_tg, defender_tg)
    if not match_id:
        await query.message.reply_text("Could not start match (error).")
        return
    await query.message.reply_text("You accepted the PvP challenge! Battle started. Attacker moves first.")
    # Notify attacker
    try:
        await context.bot.send_message(chat_id=attacker_tg, text=f"@{defender_user['username']} accepted. PvP started. Your turn.")
    except:
        pass
    # Send fight UI as inline buttons to attacker (they will press when ready)
    await send_battle_ui(context, match_id)

async def send_battle_ui(context: ContextTypes.DEFAULT_TYPE, match_id: int):
    # fetch match
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT id,attacker_id,defender_id,is_active,turn,attacker_hp,defender_hp,type FROM matches WHERE id=?", (match_id,))
    row = c.fetchone(); conn.close()
    if not row: return
    _, attacker_id, defender_id, is_active, turn, a_hp, d_hp, mtype = row
    # convert user ids to tg ids
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT tg_id,username FROM users WHERE id=?", (attacker_id,)); att = c.fetchone()
    c.execute("SELECT tg_id,username FROM users WHERE id=?", (defender_id,)); dev = c.fetchone()
    conn.close()
    if not att or not dev: return
    att_tg, att_name = att; dev_tg, dev_name = dev
    # build message for current turn holder
    # fetch match again to get turn tg id (we stored turn as tg id)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT turn, attacker_hp, defender_hp FROM matches WHERE id=?", (match_id,))
    match = c.fetchone(); conn.close()
    if not match: return
    turn_tg, a_hp, d_hp = match
    # build inline buttons: Fight / Defence / Use Item / Revival
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Fight", callback_data=f"battle_action:fight:{match_id}"),
        InlineKeyboardButton("Defend", callback_data=f"battle_action:defend:{match_id}")
    ],[
        InlineKeyboardButton("Use Item", callback_data=f"battle_action:item:{match_id}"),
        InlineKeyboardButton("Revival", callback_data=f"battle_action:revival:{match_id}")
    ]])
    # send message to turn owner
    try:
        await context.bot.send_message(chat_id=turn_tg, text=f"Your turn in PvP (Match #{match_id}). Attacker HP: {a_hp} | Defender HP: {d_hp}\nChoose an action:", reply_markup=kb)
    except Exception:
        # if cannot message, try notify both
        try:
            await context.bot.send_message(chat_id=att_tg, text=f"PvP Match #{match_id} update. Turn: {turn_tg}")
            await context.bot.send_message(chat_id=dev_tg, text=f"PvP Match #{match_id} update. Turn: {turn_tg}")
        except:
            pass

# Handle battle action callbacks
async def battle_action_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.message.reply_text("Invalid action.")
        return
    _, action, match_id = parts[0], parts[1], int(parts[2])
    user_tg = query.from_user.id
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT id,attacker_id,defender_id,is_active,turn,attacker_hp,defender_hp FROM matches WHERE id=?", (match_id,))
    row = c.fetchone()
    if not row:
        await query.message.reply_text("Match not found.")
        conn.close(); return
    mid, attacker_id, defender_id, is_active, turn_tg, a_hp, d_hp = row
    # check turn
    if user_tg != turn_tg:
        await query.message.reply_text("Not your turn.")
        conn.close(); return
    # get user records
    c.execute("SELECT tg_id,level,strength FROM users WHERE id=?", (attacker_id,)); att = c.fetchone()
    c.execute("SELECT tg_id,level,strength FROM users WHERE id=?", (defender_id,)); dev = c.fetchone()
    if not att or not dev:
        await query.message.reply_text("User data missing.")
        conn.close(); return
    att_tg, att_lvl, att_str = att
    dev_tg, dev_lvl, dev_str = dev
    # determine which side the current turn user is (attacker or defender)
    if user_tg == att_tg:
        actor = "attacker"
        target = "defender"
    elif user_tg == dev_tg:
        actor = "defender"
        target = "attacker"
    else:
        await query.message.reply_text("You are not part of this match.")
        conn.close(); return
    # compute base damage and apply action
    # simple damage formula:
    def compute_damage(level, strength):
        return int(level * 2 + strength * random.uniform(0.8, 1.2))
    damage = 0
    defend_reduction = 0
    if action == "fight":
        if actor == "attacker":
            damage = compute_damage(att_lvl, att_str)
            d_hp -= damage
        else:
            damage = compute_damage(dev_lvl, dev_str)
            a_hp -= damage
        await query.message.reply_text(f"Attack dealt {damage} damage.")
    elif action == "defend":
        # mark defend by setting a small reduction for next incoming attack: we simulate by reducing next damage by 50%
        # We'll store that as negative hp buff? Simpler: reduce the opponent's computed damage now by 50%
        if actor == "attacker":
            damage = compute_damage(att_lvl, att_str)
            reduced = int(damage * 0.5)
            d_hp -= reduced
            await query.message.reply_text(f"You defended and then countered: {reduced} damage dealt (reduced opponent effectiveness).")
        else:
            damage = compute_damage(dev_lvl, dev_str)
            reduced = int(damage * 0.5)
            a_hp -= reduced
            await query.message.reply_text(f"You defended and then countered: {reduced} damage dealt (reduced opponent effectiveness).")
    elif action == "item":
        # Use first consumable if exists - heal or extra damage
        # check user's inventory
        c.execute("SELECT id,item_type,name,quantity FROM inventory WHERE user_id=? AND quantity>0", (attacker_id if actor=="attacker" else defender_id,))
        inv = c.fetchone()
        if not inv:
            await query.message.reply_text("No items in inventory.")
        else:
            iid, itype, iname, qty = inv
            if itype == "consumable":
                # treat as heal for simplicity
                heal = 40
                if actor == "attacker":
                    a_hp = min(a_hp + heal, 100 + att_lvl*10)
                else:
                    d_hp = min(d_hp + heal, 100 + dev_lvl*10)
                c.execute("UPDATE inventory SET quantity = quantity - 1 WHERE id=?", (iid,))
                await query.message.reply_text(f"Used {iname}. Restored {heal} HP.")
            elif itype == "sword":
                # deal big damage
                damage = 80
                if actor == "attacker":
                    d_hp -= damage
                else:
                    a_hp -= damage
                await query.message.reply_text(f"Used {iname}. Dealt {damage} damage.")
            else:
                await query.message.reply_text("Used item, but effect is minimal.")
    elif action == "revival":
        # Use revival item if actor hp<=0? allow using to restore if match ended? We'll allow one revive if inventory has revival
        c.execute("SELECT id,quantity FROM inventory WHERE user_id=? AND item_type='revival' AND quantity>0", ((attacker_id if actor=="attacker" else defender_id),))
        rv = c.fetchone()
        if not rv:
            await query.message.reply_text("No revival items available.")
        else:
            rid, rqty = rv
            # revive to 50% HP
            if actor == "attacker":
                a_hp = max(a_hp, int((100 + att_lvl*10) * 0.5))
            else:
                d_hp = max(d_hp, int((100 + dev_lvl*10) * 0.5))
            c.execute("UPDATE inventory SET quantity = quantity - 1 WHERE id=?", (rid,))
            await query.message.reply_text("Revival item used. HP restored.")
    else:
        await query.message.reply_text("Unknown action.")
    # update match hp and switch turn
    # ensure hp not below negative
    a_hp = max(-9999, a_hp); d_hp = max(-9999, d_hp)
    # who wins?
    winner = None
    loser = None
    if a_hp <= 0:
        winner = dev_tg; loser = att_tg
    elif d_hp <= 0:
        winner = att_tg; loser = dev_tg
    # update DB
    # save updated hp and next turn (switch to other player's tg)
    next_turn = att_tg if user_tg != att_tg else dev_tg
    c.execute("UPDATE matches SET attacker_hp=?, defender_hp=?, turn=? WHERE id=?", (a_hp, d_hp, next_turn, match_id))
    conn.commit()
    conn.close()
    # notify both players of HP
    try:
        await context.bot.send_message(chat_id=att_tg, text=f"Match {match_id} update — Attacker HP: {a_hp}, Defender HP: {d_hp}")
        await context.bot.send_message(chat_id=dev_tg, text=f"Match {match_id} update — Attacker HP: {a_hp}, Defender HP: {d_hp}")
    except:
        pass
    # if ended, finalize rewards
    if winner:
        # record wins/losses, award won & pvp points
        winner_user = get_user_by_tg(winner); loser_user = get_user_by_tg(loser)
        # simple reward calc
        reward_won = 50 + (winner_user['level'] - loser_user['level'])*5
        reward_won = max(20, reward_won)
        reward_points = 10
        award_won(winner, reward_won)
        # update wins/losses and pvp_points
        conn = db_conn(); c = conn.cursor()
        c.execute("UPDATE users SET wins = wins + 1, pvp_points = pvp_points + ? WHERE tg_id=?", (reward_points, winner))
        c.execute("UPDATE users SET losses = losses + 1 WHERE tg_id=?", (loser,))
        conn.commit(); conn.close()
        # mark match ended
        end_match(match_id, winner_user['id'], loser_user['id'])
        await context.bot.send_message(chat_id=winner, text=f"Victory! You won {reward_won}₩ and {reward_points} PvP points.")
        await context.bot.send_message(chat_id=loser, text="You lost this PvP. Better luck next time.")
        return
    # else continue - prompt next turn owner
    await send_battle_ui(context, match_id)

# /endbettle (end current active battle initiated by user)
@only_for_registered
async def endbettle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    # end any active matches where this user is participant
    conn = db_conn(); c = conn.cursor()
    c.execute("""SELECT id,attacker_id,defender_id FROM matches WHERE is_active=1""")
    rows = c.fetchall()
    ended = 0
    for mid, att_id, def_id in rows:
        c2 = db_conn(); cc = c2.cursor()
        cc.execute("SELECT tg_id FROM users WHERE id=?", (att_id,)); arow = cc.fetchone()
        cc.execute("SELECT tg_id FROM users WHERE id=?", (def_id,)); drow = cc.fetchone()
        c2.close()
        if arow and drow and (arow[0]==tg_id or drow[0]==tg_id):
            end_match(mid, None, None)
            ended += 1
    conn.close()
    await update.message.reply_text(f"Ended {ended} active battle(s) you were in (if any).")

# /pvpbot (PvP vs bot)
@only_for_registered
async def pvpbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = get_user_by_tg(tg_id)
    # simulate fight with slightly stronger bot
    power_user = compute_power(user)
    power_bot = power_user * 1.02
    prob_win = power_user / (power_user + power_bot)
    if random.random() < prob_win:
        # win
        points = max(1, int(10 + user['level']/2))
        won = max(10, int(20 + points*2))
        award_won(tg_id, won)
        conn = db_conn(); c = conn.cursor(); c.execute("UPDATE users SET pvp_points = pvp_points + ?, wins = wins + 1 WHERE tg_id=?", (points, tg_id)); conn.commit(); conn.close()
        await update.message.reply_text(f"You defeated the Training Bot! +{points} PvP points and +{won}₩ added to your HAND.")
    else:
        # lose small consolation
        award_won(tg_id, 5)
        conn = db_conn(); c = conn.cursor(); c.execute("UPDATE users SET losses = losses + 1 WHERE tg_id=?", (tg_id,)); conn.commit(); conn.close()
        await update.message.reply_text("You lost to the Training Bot. Consolation: +5₩ added to your HAND.")

# /won
@only_for_registered
async def won_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    await update.message.reply_text(f"In Hand: {u['hand_won']}₩\nIn Bank: {u['bank_won']}₩\nLoan Owed: {u['loan_amount']}₩")

# /wongive <amount> (reply to user)
@only_for_registered
async def wongive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user message with /wongive <amount> to give them Won.")
        return
    try:
        amt = int(context.args[0])
    except:
        await update.message.reply_text("Usage: /wongive <amount> (reply to target)")
        return
    sender = update.effective_user.id
    target = update.message.reply_to_message.from_user.id
    if not user_exists(target):
        await update.message.reply_text("Target not registered.")
        return
    sender_user = get_user_by_tg(sender)
    if sender_user['hand_won'] < amt:
        await update.message.reply_text("Not enough Won in your hand.")
        return
    adjust_money(sender, hand_delta=-amt, bank_delta=0)
    adjust_money(target, hand_delta=amt, bank_delta=0)
    await update.message.reply_text(f"You gave {amt}₩ to the user.")

# /bank main menu - instructive
@only_for_registered
async def bank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("Bank Menu:\n"
            "Deposit: /deposit <amount> (Hand -> Bank)\n"
            "Withdraw: /withdraw <amount> (Bank -> Hand)\n"
            "Loan: /loan <amount>\n"
            "Repay: /repay <amount>\n"
            f"Interest rate: {int(INTEREST_RATE_DAILY*100)}% per day (interest will be added to your HAND).")
    await update.message.reply_text(text)

@only_for_registered
async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /deposit <amount>")
        return
    try:
        amt = int(context.args[0])
    except:
        await update.message.reply_text("Amount must be a number.")
        return
    tg = update.effective_user.id
    user = get_user_by_tg(tg)
    if user['hand_won'] < amt:
        await update.message.reply_text("Not enough Won in hand.")
        return
    adjust_money(tg, hand_delta=-amt, bank_delta=amt)
    await update.message.reply_text(f"Deposited {amt}₩ to bank. Bank balance: {get_user_by_tg(tg)['bank_won']}₩")

@only_for_registered
async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /withdraw <amount>")
        return
    try:
        amt = int(context.args[0])
    except:
        await update.message.reply_text("Amount must be a number.")
        return
    tg = update.effective_user.id
    user = get_user_by_tg(tg)
    if user['bank_won'] < amt:
        await update.message.reply_text("Not enough Won in bank.")
        return
    adjust_money(tg, hand_delta=amt, bank_delta=-amt)
    await update.message.reply_text(f"Withdrew {amt}₩ to hand. Hand balance: {get_user_by_tg(tg)['hand_won']}₩")

@only_for_registered
async def loan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /loan <amount>")
        return
    try:
        amt = int(context.args[0])
    except:
        await update.message.reply_text("Amount must be a number.")
        return
    tg = update.effective_user.id
    u = get_user_by_tg(tg)
    max_loan = u['bank_won']*2 + 5000
    if amt > max_loan:
        await update.message.reply_text(f"Loan denied. Max allowed: {max_loan}₩")
        return
    # add with simple 10% fee to owed
    new_owed = u['loan_amount'] + amt + int(amt*0.10)
    conn = db_conn(); c = conn.cursor()
    c.execute("UPDATE users SET loan_amount=?, hand_won = hand_won + ? WHERE tg_id=?", (new_owed, amt, tg))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Loan granted: {amt}₩ (total owed with fee: {new_owed}₩).")

@only_for_registered
async def repay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /repay <amount>")
        return
    try:
        amt = int(context.args[0])
    except:
        await update.message.reply_text("Amount must be a number.")
        return
    tg = update.effective_user.id
    u = get_user_by_tg(tg)
    if u['hand_won'] < amt:
        await update.message.reply_text("Not enough Won in hand.")
        return
    repay = min(amt, u['loan_amount'])
    adjust_money(tg, hand_delta=-repay, bank_delta=0)
    conn = db_conn(); c = conn.cursor()
    c.execute("UPDATE users SET loan_amount = loan_amount - ? WHERE tg_id=?", (repay, tg))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Repaid {repay}₩. Remaining owed: {get_user_by_tg(tg)['loan_amount']}₩")

@only_for_registered
async def myloan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    await update.message.reply_text(f"Active Loan: {u['loan_amount']}₩")

# /shop and /buy
@only_for_registered
async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Shop Items:\n"
    for it in SHOP_ITEMS:
        text += f"{it['id']}. {it['name']} - {it['price']}₩ ({it['type']})\n"
    text += "\nBuy with /buy <item_id>"
    await update.message.reply_text(text)

@only_for_registered
async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /buy <item_id>")
        return
    try:
        iid = int(context.args[0])
    except:
        await update.message.reply_text("Item id must be a number.")
        return
    ok, msg = buy_item(update.effective_user.id, iid)
    await update.message.reply_text(msg)

# /inventory, /swards, /revivalitem
@only_for_registered
async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT item_type,name,quantity FROM inventory WHERE user_id=?", (u['id'],))
    rows = c.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("Inventory empty.")
        return
    text = "Inventory:\n"
    for itype, name, qty in rows:
        text += f"{name} ({itype}) x{qty}\n"
    await update.message.reply_text(text)

@only_for_registered
async def swards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT name,quantity FROM inventory WHERE user_id=? AND item_type='sword'", (u['id'],))
    rows = c.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("No swords in inventory.")
        return
    text = "Swords:\n" + "\n".join([f"{n} x{q}" for n,q in rows])
    await update.message.reply_text(text)

@only_for_registered
async def revivalitem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT name,quantity FROM inventory WHERE user_id=? AND item_type='revival'", (u['id'],))
    rows = c.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("No revival items.")
        return
    text = "Revival Items:\n" + "\n".join([f"{n} x{q}" for n,q in rows])
    await update.message.reply_text(text)

# /dailytask and /taskreward
@only_for_registered
async def dailytask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    tasks = get_daily_tasks_for_user_id(u['id'])
    if not tasks:
        assign_daily_tasks_for_user_id(u['id'])
        tasks = get_daily_tasks_for_user_id(u['id'])
    text = "Today's Tasks:\n"
    for r in tasks:
        tid, text_t, req, prog, completed, reward_won, reward_item = r
        text += f"ID {tid}: {text_t} ({prog}/{req}) Completed: {bool(completed)}\n"
    await update.message.reply_text(text)

@only_for_registered
async def taskreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT id,reward_won,reward_item,is_completed FROM daily_tasks WHERE user_id=? AND is_completed=1", (u['id'],))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text("No completed tasks to claim.")
        conn.close(); return
    total = 0
    for tid, reward_won, reward_item, is_completed in rows:
        if reward_won:
            award_won(u['tg_id'], reward_won)
            total += reward_won
        if reward_item:
            c.execute("INSERT INTO inventory (user_id,item_type,name,quantity) VALUES (?,?,?,?)", (u['id'], 'special', reward_item, 1))
        c.execute("DELETE FROM daily_tasks WHERE id=?", (tid,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Claimed rewards: {total}₩ and any items.")

# Leaderboards
@only_for_registered
async def tophunters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # top by rank (we'll order by level & pvp_points)
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT username,level,rank,pvp_points FROM users ORDER BY level DESC, pvp_points DESC LIMIT 10")
    rows = c.fetchall(); conn.close()
    text = "Top Hunters:\n"
    for i, r in enumerate(rows, start=1):
        text += f"{i}. {r[0]} — Level {r[1]} Rank {r[2]} PvP {r[3]}\n"
    await update.message.reply_text(text)

@only_for_registered
async def globleleader_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT username,level,hand_won+bank_won AS total_won FROM users ORDER BY level DESC, total_won DESC LIMIT 10")
    rows = c.fetchall(); conn.close()
    text = "Global Leaders (Level & Wealth):\n"
    for i,r in enumerate(rows, start=1):
        text += f"{i}. {r[0]} — Level {r[1]} Total Won {r[2]}₩\n"
    await update.message.reply_text(text)

@only_for_registered
async def localleader_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # local leaderboard requires mapping users to groups; for now show top by level as placeholder
    await update.message.reply_text("Local leaderboard: feature works when users are mapped to groups. For now use /tophunters and /globleleader.")

# /title (show user's title)
@only_for_registered
async def title_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_by_tg(update.effective_user.id)
    await update.message.reply_text(f"Your Title: {u.get('title','No title')}")

# /help /guide /owner
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("/start /profile /status /rank /level /pvp (reply to user) /pvpbot /endbettle\n"
            "/won /wongive (reply) /bank /deposit /withdraw /loan /repay /myloan\n"
            "/shop /buy /inventory /swards /revivalitem\n"
            "/dailytask /taskreward /tophunters /globleleader /localleader\n"
            "/title /help /guide /owner")
    await update.message.reply_text(text)

async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("Guide: Register with /start. Use /profile to see profile.\n"
            "Challenge via replying to a user's message with /pvp.\n"
            "Use /bank to deposit/withdraw and view loan options.\n"
            "Daily tasks: /dailytask and claim with /taskreward.\n"
            "Use /shop and /buy to get items.")
    await update.message.reply_text(text)

async def owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Contact owner: @Nightking1515")

# Admin givewon (optional)
async def givewon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_TG_ID is None:
        await update.message.reply_text("Admin give disabled on this instance.")
        return
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("You are not admin.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /givewon <tg_id> <amount>")
        return
    try:
        target = int(context.args[0]); amt = int(context.args[1])
    except:
        await update.message.reply_text("Arguments must be integers.")
        return
    award_won(target, amt)
    await update.message.reply_text(f"Gave {amt}₩ to {target}.")

# ---------- Interest payout scheduler ----------
from apscheduler.schedulers.background import BackgroundScheduler

def interest_payout(application: Application):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT tg_id, bank_won FROM users WHERE bank_won>0")
    rows = c.fetchall()
    for tg_id, bank_won in rows:
        interest = int(bank_won * INTEREST_RATE_DAILY)
        if interest > 0:
            award_won(tg_id, interest)
            try:
                application.bot.send_message(chat_id=tg_id, text=f"Bank interest: +{interest}₩ has been added to your HAND.")
            except:
                pass
    conn.close()

# ------------ Startup ------------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # command handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("rank", rank_cmd))
    app.add_handler(CommandHandler("level", level_cmd))
    app.add_handler(CommandHandler("pvp", pvp_cmd))
    app.add_handler(CallbackQueryHandler(pvp_accept_decline_cb, pattern=r"^pvp_(accept|decline):"))
    app.add_handler(CallbackQueryHandler(battle_action_cb, pattern=r"^battle_action:"))
    app.add_handler(CommandHandler("endbettle", endbettle_cmd))
    app.add_handler(CommandHandler("pvpbot", pvpbot_cmd))

    app.add_handler(CommandHandler("won", won_cmd))
    app.add_handler(CommandHandler("wongive", wongive_cmd))
    app.add_handler(CommandHandler("bank", bank_cmd))
    app.add_handler(CommandHandler("deposit", deposit_cmd))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("loan", loan_cmd))
    app.add_handler(CommandHandler("repay", repay_cmd))
    app.add_handler(CommandHandler("myloan", myloan_cmd))

    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("inventory", inventory_cmd))
    app.add_handler(CommandHandler("swards", swards_cmd))
    app.add_handler(CommandHandler("revivalitem", revivalitem_cmd))

    app.add_handler(CommandHandler("dailytask", dailytask_cmd))
    app.add_handler(CommandHandler("taskreward", taskreward_cmd))

    app.add_handler(CommandHandler("tophunters", tophunters_cmd))
    app.add_handler(CommandHandler("globleleader", globleleader_cmd))
    app.add_handler(CommandHandler("localleader", localleader_cmd))
    app.add_handler(CommandHandler("title", title_cmd))

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("guide", guide_cmd))
    app.add_handler(CommandHandler("owner", owner_cmd))
    app.add_handler(CommandHandler("givewon", givewon_cmd))
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))

  # scheduler for interest (runs daily)
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: interest_job(conn), 'interval', minutes=1)
    scheduler.start()

    print("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()

