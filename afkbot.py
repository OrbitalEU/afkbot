import discord
from discord.ext import tasks, commands
from discord import app_commands
import time
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

# ========================= CONFIG =========================
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1236613214779736064
GENERAL_CHANNEL_ID = 1236613215534841930
AFK_CHANNEL_ID = 1471282612373946491
ANNOUNCE_TEXT_CHANNEL_ID = 123456789012345678

TIME_LIMIT = 900  # 15 Minuten
TIMEZONE = ZoneInfo("Europe/Berlin")
DB_PATH = "afk_stats.db"

# === WHITELIST - Diese User werden NIE in den AFK-Channel gemoved ===
WHITELISTED_USERS = {
    412347257233604609,
    1049373036039639041
}
# =========================================================

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
inactive_users = {}   # nur für nicht-whitelisted User


# ======================= DATABASE ========================
def month_key(dt):
    return dt.strftime("%Y-%m")


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            user_id INTEGER PRIMARY KEY,
            joined_at INTEGER NOT NULL,
            month TEXT NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS monthly_totals (
            month TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            seconds INTEGER NOT NULL,
            PRIMARY KEY (month, user_id)
        )
        """)
        con.commit()


def add_time(mkey, user_id, secs):
    if secs <= 0:
        return
    with db() as con:
        cur = con.cursor()
        cur.execute("""
        INSERT INTO monthly_totals(month, user_id, seconds)
        VALUES(?,?,?)
        ON CONFLICT(month, user_id) DO UPDATE SET seconds = seconds + excluded.seconds
        """, (mkey, user_id, secs))
        con.commit()


def start_session(user_id, joined_at, mkey):
    with db() as con:
        cur = con.cursor()
        cur.execute("""
        INSERT INTO active_sessions(user_id, joined_at, month)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET joined_at=excluded.joined_at, month=excluded.month
        """, (user_id, joined_at, mkey))
        con.commit()


def end_session(user_id, left_at):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT joined_at, month FROM active_sessions WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return
        joined_at, mkey = row
        cur.execute("DELETE FROM active_sessions WHERE user_id=?", (user_id,))
        con.commit()
    secs = max(0, left_at - joined_at)
    add_time(mkey, user_id, secs)


# ======================= EVENTS ==========================
@bot.event
async def on_ready():
    init_db()
    print(f"✅ Bot online: {bot.user}")
    now = int(time.time())
    current_month = month_key(datetime.now(TIMEZONE))

    # Initialisiere AFK Channel
    afk_voice = bot.get_channel(AFK_CHANNEL_ID)
    if afk_voice:
        for member in afk_voice.members:
            start_session(member.id, now, current_month)

    # Initialisiere inaktive User (nur Nicht-Whitelisted)
    general_channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if general_channel:
        for member in general_channel.members:
            vs = member.voice
            if vs and (vs.self_mute or vs.self_deaf) and member.id not in WHITELISTED_USERS:
                inactive_users[member.id] = now

    if not check_inactive.is_running():
        check_inactive.start()
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Slash Commands synchronisiert")


@bot.event
async def on_voice_state_update(member, before, after):
    now = int(time.time())
    current_month = month_key(datetime.now(TIMEZONE))
    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None

    # AFK Channel Logik (unverändert)
    if after_id == AFK_CHANNEL_ID and before_id != AFK_CHANNEL_ID:
        start_session(member.id, now, current_month)
    if before_id == AFK_CHANNEL_ID and after_id != AFK_CHANNEL_ID:
        end_session(member.id, now)

    # Inaktivität nur für nicht-whitelisted User tracken
    if after.channel and after.channel.id == GENERAL_CHANNEL_ID:
        if member.id in WHITELISTED_USERS:
            inactive_users.pop(member.id, None)  # Sicherstellen, dass sie nicht drin sind
            return

        if after.self_mute or after.self_deaf:
            if member.id not in inactive_users:
                inactive_users[member.id] = now
        else:
            inactive_users.pop(member.id, None)
    else:
        inactive_users.pop(member.id, None)


# ======================= TASKS ===========================
@tasks.loop(seconds=30)
async def check_inactive():
    guild = bot.guilds[0]
    general_channel = guild.get_channel(GENERAL_CHANNEL_ID)
    afk_channel = guild.get_channel(AFK_CHANNEL_ID)
    if not general_channel or not afk_channel:
        return

    now = int(time.time())

    for member in list(general_channel.members):
        if member.id in WHITELISTED_USERS:
            continue

        if member.id in inactive_users and (now - inactive_users[member.id] >= TIME_LIMIT):
            try:
                await member.move_to(afk_channel, reason="AFK Timeout")
                print(f"⏰ {member} wurde in den AFK-Channel verschoben")
            except Exception as e:
                print(f"Move fehlgeschlagen: {e}")
            finally:
                inactive_users.pop(member.id, None)


# ======================= SLASH COMMANDS ==================
# (deine Commands bleiben unverändert)
@tree.command(
    name="leaderboard",
    description="Zeigt die Top 10 AFK-Hunde (dieser Monat)",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(all_time="Alle Zeit anzeigen (statt nur diesen Monat)")
async
