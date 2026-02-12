import random
import uuid
import asyncio
import json
import os
import time
import logging
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import dns.resolver

# Fix for Termux/Android missing /etc/resolv.conf
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

from pymongo import MongoClient
from telegram.request import HTTPXRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputMediaPhoto, InputMediaVideo, ReplyKeyboardMarkup, ReplyKeyboardRemove, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
# Global dictionary to keep player data in RAM
player_cache = {}
BUTTON_COOLDOWNS = {}
RARITY_STYLES = {
    "Common": {"symbol": "🔘", "label": "🔘 Common"},
    "Rare": {"symbol": "🔮", "label": "🔮 Rare"},
    "Legendary": {"symbol": "⚜️", "label": "⚜️ Legendary"}
}

RIDDLES = [
    # --- BASICS ---
    {"hint": "the object to stop a ship 🛑", "correct": "⚓️", "options": ["⚔️", "⚓️", "🧭"]},
    {"hint": "the weapon of a true swordsman 🤺", "correct": "⚔️", "options": ["🏹", "⚔️", "🛡"]},
    {"hint": "what you need to steer the ship ☸️", "correct": "☸️", "options": ["🛶", "⚓️", "☸️"]},
    {"hint": "the Jolly Roger flag 🏴‍☠️", "correct": "🏴‍☠️", "options": ["🚩", "🏳️", "🏴‍☠️"]},
    {"hint": "used to find treasure 🗺️", "correct": "🗺️", "options": ["📜", "🗺️", "🔭"]},
    {"hint": "used to spot land from afar 🔭", "correct": "🔭", "options": ["🔫", "🔭", "🕯️"]},
    
    # --- ONE PIECE LORE ---
    {"hint": "the fruit that gives powers 🍇", "correct": "😈", "options": ["🍎", "😈", "🍌"]},
    {"hint": "the currency of the seas 💰", "correct": "🍇", "options": ["🍇", "💵", "💎"]},
    {"hint": "Luffy's favorite food 🍖", "correct": "🍖", "options": ["🍜", "🍖", "🍙"]},
    {"hint": "Zoro's drink of choice 🍶", "correct": "🍶", "options": ["🥛", "🍶", "🍵"]},
    {"hint": "Nami's favorite fruit 🍊", "correct": "🍊", "options": ["🍊", "🍒", "🍑"]},
    {"hint": "Sanji's weapon (his legs) 🦵", "correct": "🦵", "options": ["👊", "🦵", "🗡️"]},
    {"hint": "Chopper's favorite sweet 🍬", "correct": "🍬", "options": ["🍬", "🍰", "🍫"]},
    {"hint": "Franky's fuel source 🥤", "correct": "🥤", "options": ["⛽", "🥤", "☕"]},
    
    # --- COMBAT & ITEMS ---
    {"hint": "protects you from attacks 🛡️", "correct": "🛡️", "options": ["🛡️", "⚔️", "🧶"]},
    {"hint": "fires explosive balls 💣", "correct": "💣", "options": ["🎱", "💣", "🏺"]},
    {"hint": "a sniper's best friend 🎯", "correct": "🏹", "options": ["🏹", "🎣", "🦯"]},
    {"hint": "the Log Pose compass 🧭", "correct": "🧭", "options": ["⌚", "🧭", "⏲️"]},
    {"hint": "a Marine ship 🛳️", "correct": "🛳️", "options": ["🛳️", "⛵", "🛶"]},
    {"hint": "the treasure chest 📦", "correct": "📦", "options": ["📦", "📪", "🧱"]}
]

ADMIN_IDS = [5242138546 , 7708811819]
# =====================
# LOGGING SETUP
# =====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# =====================
# DATA PERSISTENCE (MONGODB ATLAS)
# =====================

MONGO_URI = os.getenv("MONGO_URI")

# Initialize Client with Speed Optimizations
try:
    if MONGO_URI:
        # maxPoolSize allows multiple database tasks at once
        # retryWrites handles brief mobile signal drops automatically
        mongo_client = MongoClient(
            MONGO_URI, 
            serverSelectionTimeoutMS=5000, 
            maxPoolSize=50, 
            retryWrites=True
        )
        # Verify connection immediately
        mongo_client.admin.command('ping')
        db = mongo_client["pirate_v3"]
        players_collection = db["players"]
        print("✅ Connected to MongoDB Atlas successfully.")
    else:
        print("⚠️ MONGO_URI not found in environment.")
        mongo_client = None
        players_collection = None
except Exception as e:
    print(f"❌ Failed to connect to MongoDB: {e}")
    mongo_client = None
    players_collection = None

def init_db():
    if mongo_client:
        try:
            db = mongo_client["pirate_v3"]
            # Create a UNIQUE INDEX on user_id
            # This is the BIGGEST speed boost: it prevents the bot from 
            # scanning the whole DB every time a command is used.
            db["players"].create_index("user_id", unique=True)
            print("✅ Database index created/verified.")
        except Exception as e:
            print(f"Database connection warning: {e}")

def load_player(user_id):
    uid = str(user_id)
    # 1. Check RAM first (Instant)
    if uid in player_cache:
        return player_cache[uid]
        
    if players_collection is None: 
        return None
        
    try:
        # 2. Get from DB (Slow)
        data = players_collection.find_one({"user_id": uid}, {"_id": 0})
        
        if data:
            # 3. SAFETY NET: Inject fields if missing for old players
            updated = False
            if "is_locked" not in data:
                data["is_locked"] = False
                updated = True
            if "verification_active" not in data:
                data["verification_active"] = False
                updated = True
            
            # If we fixed an old record, save it back to DB immediately
            if updated:
                players_collection.update_one({"user_id": uid}, {"$set": data})
                logging.info(f"Fixed missing security fields for old player {uid}")

            # 4. Store in RAM for ultra-fast access
            player_cache[uid] = data 
            return data
            
        return data
    except Exception as e:
        logging.error(f"Error loading player {user_id}: {e}")
        return None


def save_player(user_id, player_data):
    uid = str(user_id)
    # Update RAM first (Instant)
    player_cache[uid] = player_data
    
    if players_collection is None: return
    try:
        # Update DB in the background
        players_collection.update_one(
            {"user_id": uid},
            {"$set": player_data},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error saving player {user_id}: {e}")


init_db()

# =====================
# CONSTANTS & STATS
# =====================

WHEEL_VIDEO = "BAACAgUAAyEFAATl0UgqAAIeZWmFStczJlfo5LnlJVRzeWuUtoSLAAJtIAACMx4pVKMk-1-9BG_3OAQ"
YAMATO_ULT_VIDEO = "BAACAgUAAxkBAAIr1WmAKRpP7UEXoxx58xwDRtc65mzSAALQGQACW_z5V441GyK7BGOqOAQ"
KID_ULT_VIDEO = "BAACAgUAAxkBAAIuemmCBZpyHB8s96nGwTuTrPhrqeDlAAKYIwACukcJVL9MKAIltY9HOAQ"
SUMMON_ANIMATION = "BAACAgUAAyEFAATl0UgqAAIWWGmDReT_fHUD07PqTFA7r1TrK_PUAAIJIQACAeUZVObA9U5rAk3jOAQ"

# New Animation File IDs
KID_SUMMON_ANIM = "BAACAgUAAxkBAAIxV2mKEHo_X_pEYR0aQr-NHEMP7q38AAKaIAACryVRVFV9W5Uh5TDHOgQ"
YAMATO_SUMMON_ANIM = "BAACAgUAAyEFAATl0UgqAAImqWmKD-7KRrw-sso1fflrKeMwybCIAAKfIAACryVRVLi7jhi0hkHkOgQ"

# New Explore Ultimate Images
KID_EXPLORE_ULT = "AgACAgUAAxkBAAIxXWmKEK3AQvUWEetFepCwwCKkFTyrAAKnDmsbukcJVEAQZ7R1nCaCAQADAgADeQADOgQ"
YAMATO_EXPLORE_ULT = "AgACAgUAAxkBAAIxW2mKEKa3uIN7-5Yta1jxPFtZrOFbAALBDWsbW_z5VwQFGVqA2BWRAQADAgADeAADOgQ"

INVENTORY_IMAGE = "AgACAgUAAxkBAAIuz2mDX1GCg9NPgBG0UPXp6zuwsuUjAAIMEGsbCfwYVE0skEm56DPBAQADAgADeQADOAQ"
STORE_IMG = "AgACAgUAAxkBAAIvsmmEYjJo8rnkBkUIxxFbfnBr0I0bAAKmEGsbMx4pVDtFcs56hQohAQADAgADeQADOAQ"

DARK_CHEST_IMG = "AgACAgUAAyEFAATl0UgqAAIaemmEQz9KfL_beaSK3YHjzdZrIDppAAJqEGsbMx4pVH2Pdkr37vjJAQADAgADeAADOAQ"
GOLD_CHEST_IMG = "AgACAgUAAyEFAATl0UgqAAIaf2mEQ0NHcjbHdZb1dWsqhZJ4sXKwAAJuEGsbMx4pVM8P8sb8dtXNAQADAgADeAADOAQ"
FROST_CHEST_IMG = "AgACAgUAAyEFAATl0UgqAAIaimmEQ0YhFqdVd5Zq3re9x0bD6F5CAAJxEGsbMx4pVPkAASeeheyLwgEAAwIAA3gAAzgE"

ULT_IMAGES = {
    "Alvida": "AgACAgUAAxkBAAIwvmmFaNMraidlErRzZgi1TWMPVr2sAAK_DWsbwLX4VyXUp5J3sHNxAQADAgADeQADOAQ",
    "Chopper": "AgACAgUAAxkBAAIwwGmFaRNHwyApKmXmKY1Ag0SJhhTHAAIIFGsbRLwpVOiLpE244XGLAQADAgADeQADOAQ",
    "Arlong": "AgACAgUAAxkBAAIwwmmFakbAtQgSLy8nfirVa28hqJ2zAAISFGsbRLwpVFeRRVKsvFY4AQADAgADeQADOAQ",
    "Nami": "AgACAgUAAxkBAAIwxGmFakwzyAlUYx9Z2jtKSFhuPa-_AAIUFGsbRLwpVLd3FmJH0uDDAQADAgADeQADOAQ",
    "Helmeppo": "AgACAgUAAxkBAAIwxmmFal1aHInnNIhlV7DV1ztYJhkwAAIVFGsbRLwpVJFzZewcCLqcAQADAgADeQADOAQ",
    "Buggy": "AgACAgUAAxkBAAIwyGmFaorEl41lmrnu_yiHEZj6UevDAAIWFGsbRLwpVLlNPNKDVSb9AQADAgADeQADOAQ",
    "Usopp": "AgACAgUAAxkBAAIwymmFaqLzk4NzAAEvHJtBBhij5O_FzgACGBRrG0S8KVR8F-_",
    "Koby": "AgACAgUAAxkBAAIsAmmAp65JZUNHpXF2Fv3FjxeT1MhPAAILDWsbY4oBVKYcdcYZUyUxAQADAgADeAADOAQ"
}

DEVIL_FRUITS = {
    "Sand Sand Fruit": {
        "text": "Sand sand fruit \n\nRarity:⭐️\n\nDevil fruit info: This fruit allow user to manipulate, control and create sand at will. \n\n     Fruits stats\nDefense:32-38\nDamage: 15-25\nCritical chance:25%\nAccuracy:94%\nRank requirement: 7",
        "img": "AgACAgUAAyEFAATl0UgqAAIdCGmEyAP0N7D46htpr52YyW4gk59hAAIuEmsbMx4pVO7ttlPyKjeNAQADAgADeQADOAQ",
        "atk_buff": 20, "def_buff": 35, "hp_buff": 0, "cost": 15000, "lvl": 7
    },
    "Shadow Shadow Fruit": {
        "text": "Shadow shadow fruit \n\nRarity:⭐️\n\nDevil fruit info: This fruit allows users to manipulate, manifest, and steal shadow and turn into corpse. \n\n     Fruits stats\nDamage: 30-32\nDefense: 15-20\nCritical chance: 20%\nAccuracy: 91%\nRank requirement: 4",
        "img": "AgACAgUAAyEFAATl0UgqAAIdFWmE7Liugh6UWYR8q5tA_sHCPNsdAAIvEmsbMx4pVEpP7t_9_F97AQADAgADeAADOAQ",
        "atk_buff": 31, "def_buff": 17, "hp_buff": 0, "cost": 15000, "lvl": 4
    },
    "Barrier Barrier Fruit": {
        "text": "Barrier barrier fruit \n\nRarity:⭐️\n\nDevil fruit info: This fruit allows consumer to turn any weapon or surrounding into strong barrier. \n\n     Fruits stats\nDefense: 25-35\nAttack:5-10\nCritical chance: 15%\nAccuracy: 98%\nRank requirement:1",
        "img": "AgACAgUAAyEFAATl0UgqAAIeemmE7IhCyP3Xv5z0MWur0XyybbDSAAJxEmsbMx4pVCqTaJaSZ0K8AQADAgADeQADOAQ",
        "atk_buff": 7, "def_buff": 30, "hp_buff": 0, "cost": 10000, "lvl": 1
    },
    "Munch Munch Fruit": {
        "text": "Munch munch fruit \n\nRarity:⭐️\n\nDevil fruit info: This fruit allows consume any substance and incorporate into their body. \n\n     Fruits stats\nHP: 30-40\nDamage: 15-20\nCritical chance: 12%\nAccuracy: 95%\nRank requirement: 1",
        "img": "AgACAgUAAyEFAATl0UgqAAIemWmE90tMDiO7XXEHktwrz9tFu_V9AALLEmsbMx4pVCKf_jfVPhlrAQADAgADeQADOAQ",
        "atk_buff": 17, "def_buff": 0, "hp_buff": 35, "cost": 10000, "lvl": 1
    },
    "Gum Gum Fruit": {
        "text": "Gum gum fruit\n\nRarity:⭐️\n\nDevil fruit info: This fruit turns consumer's body into rubber, allowing them to stretch.\n\n     Fruits stats\nDamage: 25-30\nDefense: 10-20\nCritical chance: 10%\nAccuracy: 90%\nRank Requirement: 1",
        "img": "AgACAgUAAyEFAATl0UgqAAIecmmE7I5fGowjyIYU7_df7Dwxf1UyAAJkEmsbMx4pVHs0wiHNY7NzAQADAgADeQADOAQ",
        "atk_buff": 27, "def_buff": 15, "hp_buff": 0, "cost": 10000, "lvl": 1
    }
}

WEAPONS = {
    "Dual Katana": {
        "rarity": "⭐️", "atk_range": "35-40", "atk_val": 45, "crit": "10%", "acc": "98%", "spec": "Dual slash", "lvl": 1, "cost": 10000,
        "img": "AgACAgUAAyEFAATl0UgqAAIZt2mEOqRXXmoQl-ulHSOIrVWLQjzoAAJUFmsbMx4hVLLn3TFeaW8CAQADAgADeAADOAQ"
    },
    "Triple Katana": {
        "rarity": "⭐️", "atk_range": "45-50", "atk_val": 55, "crit": "10%", "acc": "98%", "spec": "Triple Tornado", "lvl": 1, "cost": 20000,
        "img": "AgACAgUAAyEFAATl0UgqAAIZuWmEOqlKixV19PQIvi96-GuoPHIKAAJWFmsbMx4hVHbg-8QGvEdRAQADAgADeAADOAQ"
    },
    "Shark Saw": {
        "rarity": "⭐️", "atk_range": "50-55", "atk_val": 60, "crit": "15%", "acc": "98%", "spec": "Shark resonance", "lvl": 1, "cost": 25000,
        "img": "AgACAgUAAyEFAATl0UgqAAIZu2mEOq10xpnpB49zbKaEg-j40GqKAAJXFmsbMx4hVFAid74FSZU-AQADAgADeAADOAQ"
    },
    "Green Blade": {
        "rarity": "⭐️", "atk_range": "60-70", "atk_val": 75, "crit": "25%", "acc": "98%", "spec": "Green slash", "lvl": 5, "cost": 45000,
        "img": "AgACAgUAAyEFAATl0UgqAAIZw2mEOrMJjjEtehNfXfiSBoJxMCBiAAJYFmsbMx4hVIch9ahbbQQMAQADAgADeQADOAQ"
    },
    "Magma Dagger": {
        "rarity": "⭐️", "atk_range": "80-90", "atk_val": 100, "crit": "25%", "acc": "95%", "spec": "Magma Force", "lvl": 15, "cost": 65000,
        "img": "AgACAgUAAyEFAATl0UgqAAIZ7WmEOry82XEfPl1oUYc0_KU4djqAAAJZFmsbMx4hVGmQ1rgUOcjsAQADAgADeQADOAQ"
    },
    "Azure Needle": {
        "rarity": "⭐️⭐️", "atk_range": "100-110", "atk_val": 125, "crit": "30%", "acc": "95%", "spec": "Azure Counter", "lvl": 30, "cost": 70000,
        "img": "AgACAgUAAyEFAATl0UgqAAIaAAFphDzjTunksxUIOZYWj3FlV9BURwACWhZrGzMeIVQ0u2UqGs_0JgEAAwIAA3kAAzgE"
    },
    "Forest Blade": {
        "rarity": "⭐️⭐️", "atk_range": "130-150", "atk_val": 160, "crit": "35%", "acc": "95%", "spec": "Forest god slash", "lvl": 30, "cost": 85000,
        "img": "AgACAgUAAyEFAATl0UgqAAIaFWmEPQJkm-U2-AlHDexL0Ke8vLYpAAJcFmsbMx4hVCdm7R62bN3iAQADAgADeQADOAQ"
    }
}

BOSS_MISSIONS = {
    15: {"name": "Arlong", "img": "AgACAgUAAxkBAAIs9mmAsORj03tw4HZ2sKKGwEms-wu7AAJyDGsb19YJVFX3zXQ6I9cxAQADAgADeAADOAQ", "mission_num": 1},
    30: {"name": "Piccolo", "img": "AgACAgUAAxkBAAIs-WmAsQgNDT_G4xg1HsGZuxkcdFnNAAJzDGsb19YJVIQu9AK0gu4dAQADAgADeQADOAQ", "mission_num": 2},
    50: {"name": "Rui", "img": "AgACAgUAAxkBAAIs_GmAsU-6zEng4yccNa3jO4gvmZREAAJ1DGsb19YJVH82a_F3nMnQAQADAgADeQADOAQ", "mission_num": 3},
    100: {"name": "Crocodile", "img": "AgACAgUAAxkBAAIs_2mAsYd_L-nQWCy3hg5LJtYpljZeAAJ2DGsb19YJVGU6G-b2-XALAQADAgADeQADOAQ", "mission_num": 4},
    150: {"name": "Itachi Uchiha", "img": "AgACAgUAAxkBAAItAmmAsafdewloIC8XlfjrJH9pe9aOAAJ3DGsb19YJVCgdljzoYSuWAQADAgADeQADOAQ", "mission_num": 5},
    175: {"name": "Feitan Portan", "img": "AgACAgUAAxkBAAItBWmAsdjhWowAAeqczS1z10GvXLwhcwACeAxrG9fWCVSbcVihfLwy4AEAAwIAA3gAAzgE", "mission_num": 6},
    200: {"name": "Cell", "img": "AgACAgUAAxkBAAItCGmAsgr3LLepNQABIZVvHeGIOvWmpwACeQxrG9fWCVQmfj_9ateLEQEAAwIAA3kAAzgE", "mission_num": 7},
    250: {"name": "Stark", "img": "AgACAgUAAxkBAAItC2mAskg-PoSoVlUo5Qgc-8uvjQhRAAJ6DGsb19YJVKsAAWIESVmIKgEAAwIAA3kAAzgE", "mission_num": 8},
    300: {"name": "Broly", "img": "AgACAgUAAxkBAAItDmmAssckahqYZhiN0uynWs_seJv_AAJ8DGsb19YJVF95gLcEjQYsAQADAgADeQADOAQ", "mission_num": 9},
    350: {"name": "Frieza", "img": "AgACAgUAAxkBAAIsEWmAswleMjJ_SfE8fNFVUqY0CJS3AAJ-DGsb19YJVKnYLmtuOcZkAQADAgADeQADOAQ", "mission_num": 10},
    375: {"name": "Daki", "img": "AgACAgUAAxkBAAItFWmAuR96mNKa6nWKcjtHBfKubbjLAAKiDGsb19YJVPFkdMsNCLzYAQADAgADeAADOAQ", "mission_num": 11},
    400: {"name": "Gyutaro", "img": "AgACAgUAAxkBAAItGGmAuTMCpNCQAx0vb9ZgIueO97wIAAKjDGsb19YJVLihQcGB8TQeAQADAgADeQADOAQ", "mission_num": 12},
    450: {"name": "Dabi", "img": "AgACAgUAAxkBAAItG2mAuTzySUl34WPt97W9FClXI1P4AAKkDGsb19YJVMKVj4qnzg_FAQADAgADeQADOAQ", "mission_num": 13},
    475: {"name": "Blackbeard", "img": "AgACAgUAAxkBAAIs-WmAsQgNDT_G4xg1HsGZuxkcdFnNAAJzDGsb19YJVIQu9AK0gu4dAQADAgADe4dAQADAgADeQADOAQ", "mission_num": 14},
    500: {"name": "Kakashi Hatake", "img": "AgACAgUAAxkBAAItIWmAuVXkpvD5uainre7pr8SjFhS5AAKmDGsb19YJVGAO_rS_wuDOAQADAgADeQADOAQ", "mission_num": 15},
    550: {"name": "Geto", "img": "AgACAgUAAxkBAAItJGmAuV5MgGjeuvA9WtkZp4EfXn6dAAKnDGsb19YJVKd9qK8QNZTHAQADAgADeQADOAQ", "mission_num": 16},
    600: {"name": "Frieren", "img": "AgACAgUAAxkBAAItJ2mAuWkzkL-DCBH3BzmVXJivvfRqAAKoDGsb19YJVGys0QQbelRsAQADAgADeAADOAQ", "mission_num": 17},
    650: {"name": "Black Goku", "img": "AgACAgUAAxkBAAItKmmAuXJYD_h4faJP09TW1job5zPRAAKpDGsb19YJVIv-zCCKuXwjAQADAgADeAADOAQ", "mission_num": 18},
    700: {"name": "Mahito", "img": "AgACAgUAAxkBAAItLWmAuXh1H9IdpBzSD9n10UuOoI5lAAKqDGsb19YJVLhG2SytKJIWAQADAgADeAADOAQ", "mission_num": 19},
    750: {"name": "Yuji Itadori", "img": "AgACAgUAAxkBAAItMGmAuYECU_BK5Gt18HAyz0Jm7WdRAAKrDGsb19YJVJsz3N7U80ABAQADAgADeQADOAQ", "mission_num": 20}
}

EFFECT_DESCRIPTIONS = {
    "Alvida": "Increases defense by 10%.",
    "Chopper": "deals 70 damage and increases every teammate and himself health❤️ by 50 hp.",
    "Arlong": "Deals 85 damage. Increase his attack ⚔by 15% for 2 moves.",
    "Koby": "Deals 70 Damage and increases his chance to dodge next move by 30%.",
    "Usopp": "Deals 75 damage and reduced enemy Defense 🛡by 5%. And heals himself hy 25 Hp for 2 moves .",
    "Buggy": "Deals 80 damage increase all teamates attack⚔ by 5%.",
    "Helmeppo": "Deals 70 Damage and increases his chance to dodge next move by 50%. Increases his teamates speed⚡️by 10%.",
    "Nami": "Deals 70 Damage and stuns💤 enemy for 1 round.",
    "Yamato": "Deals 130 damage. Increases her chances of dodge by 50%. For 2 rounds her attack⚔ increases by 10%. Defense 🛡increases by 15%.",
    "Eustass Kid": "Deals 145 Damage. For 2 rounds Kid increases his attack by 25%. Speed increased by 10%."
}

MOVES = {
    "Kanabo smash": {"dmg": 50}, "Slip Slip punch": {"dmg": 55}, "Sube sube no mi": {"dmg": 60, "effect": "def_buff_10"},
    "Heavy gong": {"dmg": 45}, "Kung fu point": {"dmg": 45}, "Kokutei Roseo Metal": {"dmg": 70, "effect": "team_heal_50"},
    "Shark teeth": {"dmg": 65}, "Shark on dart": {"dmg": 70}, "Kiribachi": {"dmg": 85, "effect": "atk_buff_15_2"},
    "Kamisoro": {"dmg": 40}, "Tempest Kick": {"dmg": 50}, "Honesty impact": {"dmg": 70, "effect": "dodge_30"},
    "Skull Bomb grass": {"dmg": 40}, "Impact wolf": {"dmg": 45}, "Usopp hammer": {"dmg": 75, "effect": "usopp_ult"},
    "Chop Chop canon": {"dmg": 60}, "Chop Chop buzzsaw": {"dmg": 65}, "Bara Bara festival": {"dmg": 80, "effect": "team_atk_5"},
    "Sword swing": {"dmg": 50}, "Dual Kukri": {"dmg": 55}, "Firey morale": {"dmg": 70, "effect": "helmeppo_ult"},
    "Thunderbolt Tempo": {"dmg": 50}, "Swing Arm": {"dmg": 40}, "Zeus breeze tempo": {"dmg": 70, "effect": "stun_1"},
    "Namuji Hyoga": {"dmg": 80}, "Namuji glacier fang": {"dmg": 75}, "Thunder Bagua": {"dmg": 130, "effect": "yamato_ult"},
    "Riperu": {"dmg": 70}, "Punk Gibson": {"dmg": 80}, "Damned Punk": {"dmg": 145, "effect": "kid_ult"},
    "Strike": {"dmg": 30}, "Bash": {"dmg": 35}, "Special Beam": {"dmg": 45}, "Quick Slash": {"dmg": 35}, "Heavy Blow": {"dmg": 40},
    "Dual slash": {"dmg": 45}, "Triple Tornado": {"dmg": 55}, "Shark resonance": {"dmg": 60}, "Green slash": {"dmg": 75},
    "Magma Force": {"dmg": 100}, "Azure Counter": {"dmg": 125}, "Forest god slash": {"dmg": 160}
}

CHARACTERS = {
    "Alvida": {"rarity": "Common", "class": "Tank🛡", "hp": 600, "atk_min": 22, "atk_max": 22, "def": 30, "spe": 30, "moves": ["Kanabo smash"], "ult": "Sube sube no mi"},
    "Chopper": {"rarity": "Rare", "class": "Healer🧚‍♂", "hp": 700, "atk_min": 30, "atk_max": 35, "def": 40, "spe": 25, "moves": ["Heavy gong"], "ult": "Kokutei Roseo Metal"},
    "Arlong": {"rarity": "Rare", "class": "Damage dealer⚔", "hp": 660, "atk_min": 40, "atk_max": 45, "def": 30, "spe": 35, "moves": ["Shark teeth"], "ult": "Kiribachi"},
    "Koby": {"rarity": "Common", "class": "Assassin 🥷", "hp": 550, "atk_min": 25, "atk_max": 25, "def": 20, "spe": 35, "moves": ["Kamisoro"], "ult": "Honesty impact"},
    "Usopp": {"rarity": "Rare", "class": "Healer 🧚‍♂", "hp": 650, "atk_min": 35, "atk_max": 40, "def": 40, "spe": 30, "moves": ["Skull Bomb grass"], "ult": "Usopp hammer"},
    "Buggy": {"rarity": "Rare", "class": "Damage dealer ⚔", "hp": 620, "atk_min": 40, "atk_max": 45, "def": 25, "spe": 35, "moves": ["Chop Chop canon"], "ult": "Bara Bara festival"},
    "Helmeppo": {"rarity": "Rare", "class": "Assassin 🥷", "hp": 680, "atk_min": 35, "atk_max": 35, "def": 30, "spe": 45, "moves": ["Sword swing"], "ult": "Firey morale"},
    "Nami": {"rarity": "Rare", "class": "Support💪", "hp": 600, "atk_min": 25, "atk_max": 30, "def": 35, "spe": 25, "moves": ["Thunderbolt Tempo"], "ult": "Zeus breeze tempo"},
    "Yamato": {"rarity": "Legendary", "class": "Assassin", "hp": 900, "atk_min": 50, "atk_max": 60, "def": 60, "spe": 50, "moves": ["Namuji Hyoga"], "ult": "Thunder Bagua"},
    "Eustass Kid": {"rarity": "Legendary", "class": "Damage dealer⚔", "hp": 850, "atk_min": 60, "atk_max": 70, "def": 65, "spe": 40, "moves": ["Riperu"], "ult": "Damned Punk"}
}

EXPLORE_DATA = {
    "King": "AgACAgUAAxkBAAIr6mmAp2EYS4XrDKMXRRsowyQ3gfWuAALTDmsbW_wBVCZuP_JtZpU6AQADAgADeAADOAQ",
    "Rob Lucci": "AgACAgUAAxkBAAIr7GmAp2dGkUu9U2zDjJENaIbEhEXdAALYDmsbW_wBVDCtsBlLQUE9AQADAgADeAADOAQ",
    "Black Maria": "AgACAgUAAxkBAAIr7mmAp2yhp6b-TPl_kZoC9Sx_ip7JAALaDmsbW_wBVJBjLXZqyYS4AQADAgADeAADOAQ",
    "Arlong NPC": "AgACAgUAAxkBAAIr8GmAp3FoGJG0zdvL9Fs4qGd-iprHAALbDmsbW_wBVPQ_PZ-g2wjwAQADAgADeAADOAQ",
    "Douglas Bullet": "AgACAgUAAxkBAAIr8mmAp3bMU6vUWJxSI0r6q4nm8r3hAALdDmsbW_wBVM3rxFBI3zKVAQADAgADeAADOAQ",
    "Don krieg": "AgACAgUAAxkBAAIr9GmAp3xMoMASkWzhbhPCtp3T7aaZAALhDmsbW_wBVDa02bAREgkPAQADAgADeAADOAQ",
    "Kuro": "AgACAgUAAxkBAAIr9mmAp4DcGKfugO7-_tM2NBwCiEN6AAKEDmsbCU4BVCEiRHdTM7RgAQADAgADeQADOAQ",
    "Kalifa": "AgACAgUAAxkBAAIr-GmAp4Ubq2XZfqzGQV2qfdqPb8OiAAKFDmsbCU4BVLrZYr2v1n_1AQADAgADeAADOAQ",
    "Ulti": "AgACAgUAAxkBAAIr-mmAp4q7nE5gPuA2i4K6UQo1qbAbAAKGDmsbCU4BVPQqFYrKr717AQADAgADeAADOAQ",
    "NPC Pirate": "AgACAgUAAxkBAAIr_GmAp5tSMZpbfYPU3VoGqodY398MAAJZDWsbW_z5V9-rjIYJ8FDzAQADAgADeQADOAQ",
    "Monet": "AgACAgUAAxkBAAIr_mmAp6NagP0JJ_AsUdJoVdGkDvLkAAIJDWsbY4oBVNAe98Ggvic5AQADAgADeAADOAQ",
    "Doflamingo": "AgACAgUAAxkBAAIsAAFpgKeoyYY2fgwMtvIm2DqtunrdKgACCg1rG2OKAVRNYd_PuO7I6AEAAwIAA3gAAzgE",
    "Smoker": "AgACAgUAAxkBAAIsAmmAp65JZUNHpXF2Fv3FjxeT1MhPAAILDWsbY4oBVKYcdcYZUyUxAQADAgADeAADOAQ",
    "Enel": "AgACAgUAAxkBAAIsBGmAp7NGBf0jobEyAnpSmhPfL3VvAAIMDWsbY4oBVGBVP8GMQgESAQADAgADeAADOAQ",
    "Buggy Clown": "AgACAgUAAxkBAAIsBmmAp7kDSA-CX8RZx1HhbPi0r6jtAAINDWsbY4oBVOiWq2rQ4TFNAQADAgADeAADOAQ",
    "Crocodile": "AgACAgUAAxkBAAIsCGmAp8Cgyg_O5D4s-_S8a19pbu3EAAIZDWsbY4oBVBP3GgiVf8lBAQADAgADeAADOAQ",
    "Pell": "AgACAgUAAxkBAAIsCmmAp8RfZlOnqGJxdJWHlx664LWMAALDDWsb0oUBVAnJaPV02zFcAQADAgADeAADOAQ",
    "Perona": "AgACAgUAAxkBAAIsDGmAp8mypupnW3pKXlMnRrCWyN3hAALIDWsb0oUBVLW6hccH2dyxAQADAgADeAADOAQ",
    "Brook": "AgACAgUAAxkBAAIsDmmAp89uGTJnc65Jkf5e9ro2svqYAALKDWsb0oUBVFeK4PzUUNmTAQADAgADeAADOAQ",
    "Portgas D Ace": "AgACAgUAAxkBAAIsEGmAp9NYcj5JLk75ww3138FuwtKdAALLDWsb0oUBVC4rPNaY3hSNAQADAgADeAADOAQ",
    "Killer": "AgACAgUAAxkBAAIsEmmAp9jVH7jyYecJIx09flxCGinlAALMDWsb0oUBVIn_3xMoF3-6AQADAgADeAADOAQ",
    "Nico Robin": "AgACAgUAAxkBAAIsFGmAp9529v6Di1chuw4_9cfU-EkiAALNDWsb0oUBVKHQ34AWzcxxAQADAgADeQADOAQ",
    "Chopper NPC": "AgACAgUAAxkBAAIsFmmAp-Ps0-kNAAG6wMynROiPP7Kz1wAC0Q1rG9KFAVTfJ8Q_AVxyAQEAAwIAA3gAAzgE",
    "Nami NPC": "AgACAgUAAxkBAAIsGGmAp-hyS4PhRQlgGnMqTk--c_vLAALODWsb0oUBVNeTsJ6uHBbvAQADAgADeQADOAQ",
    "Sabo": "AgACAgUAAxkBAAIsGmmAp-0Scu1YFauEeVHHCLRRt1C4AALSDWsb0oUBVE2FpBZskDHEAQADAgADeAADOAQ",
    "Rosinante": "AgACAgUAAxkBAAIsHGmAp_NvBL1yg_LIAAHjE2B1Y1GNrAAC0w1rG9KFAVRj_yXuH6ZzHgEAAwIAA3gAAzgE",
    "Trafalgar Law": "AgACAgUAAxkBAAIsHmmAp_cnj4ldcMUQb5P_YJUr0sw6AALUDWsb0oUBVBGlM1j0jRV3AQADAgADeAADOAQ",
    "Doll": "AgACAgUAAxkBAAIsIGmAqAEPwN4oGsKGAUvnBrkU-YCvAALVDWsb0oUBVFo-Q7a6rnUPAQADAgADeQADOAQ",
    "Katakuri": "AgACAgUAAxkBAAIsImmAqAVnfjSguZUhUjTwFiEeykTKAALXDWsb0oUBVPuwnTNl2Lw8AQADAgADeAADOAQ",
    "Franky": "AgACAgUAAxkBAAIsJGmAqApwyGDjojhoBFwb59zn2u3gAALYDWsb0oUBVMQkXqlz5vEiAQADAgADeAADOAQ",
    "Senor Pink": "AgACAgUAAxkBAAIsJmmAqA8LC6RISSpb5joQLGN3ivGoAALZDWsb0oUBVFQWgjNjVFn8AQADAgADeAADOAQ",
    "S-Hawk": "AgACAgUAAxkBAAIsKGmAqBQgv3Pi-0vDP1qeW-Q0bE5_AALaDWsb0oUBVPY1sSKf2NPEAQADAgADeAADOAQ",
    "S-Snake": "AgACAgUAAxkBAAIsKmmAqBmqHPO5HNEGsG7F34tcRqARAALbDWsb0oUBVMJs_uiK4tb-AQADAgADeAADOAQ",
    "Pica": "AgACAgUAAxkBAAIsLGmAqB5gB0FNagtLz6K8mUFCYIxdAALfDWsb0oUBVFkBi-8jSNjSAQADAgADeAADOAQ",
    "Jinbe": "AgACAgUAAxkBAAIsLmmAqCPNoAJ9BITlc4BCFS2aFRjSAALgDWsb0oUBVLx6jvmtf0SDAQADAgADeAADOAQ",
    "Nefertari Cobra": "AgACAgUAAxkBAAIsMGmAqClnnxJ6HBQLowHbytwJKhRxAALhDWsb0oUBVIo-1t6SqGnNAQADAgADeAADOAQ",
    "Usopp NPC": "AgACAgUAAxkBAAIsMmmAqC4KFJ5hJ0O8mdk6nsE1vHrOAALjDWsb0oUBVO7B5sGyU4nuAQADAgADeAADOAQ",
    "Daz Bones": "AgACAgUAAxkBAAIsNGmAqDNUHlUfRw1sOZdjCrQBobBsAALkDWsb0oUBVNpgENZvy-4EAQADAgADeAADOAQ",
    "Pedro": "AgACAgUAAxkBAAIsNmmAqDhxEyD3lHufXU64YuZj_o5qAALlDWsb0oUBVDMZN23hyW_QAQADAgADeQADOAQ",
    "Sasaki": "AgACAgUAAxkBAAIsOGmAqDw6P3NWTizNj4jrd8O1YXKcAALnDWsb0oUBVC5iYxJKoamJAQADAgADeAADOAQ",
    "Dellinger": "AgACAgUAAxkBAAIsOmmAqEItemsDUkwiboWcvtMTHwbrTB-AALoDWsb0oUBVMjxgygfT7OSAQADAgADeAADOAQ",
    "Wiper": "AgACAgUAAxkBAAIsPmmAqE9pCvTsRZYTEQNKH84ix3eFAALpDWsb0oUBVHyd9E7yshXsAQADAgADeAADOAQ",
    "Vinsmoke Judge": "AgACAgUAAxkBAAIsQGmAqFb4YMOn4SMWwt-7QWUSqLTJAALqDWsb0oUBVDjzzbqqKxPUAQADAgADeAADOAQ",
    "Kyros": "AgACAgUAAxkBAAIsQmmAqFt3qSkIYPLgUz5V4eQYQAFrAALrDWsb0oUBVHVHMfwF5eYIAQADAgADeAADOAQ",
    "Shiki": "AgACAgUAAxkBAAIsRGmAqGArETkXJBXmPN5zGzx_cs_8AALsDWsb0oUBVHoEYRuKBPOjAQADAgADeAADOAQ",
    "Saint Charlos": "AgACAgUAAxkBAAIsRmmAqGU67kcFgFevliEdrlk9p1XbAALtDWsb0oUBVONdflaMUG2EAQADAgADeAADOAQ",
    "Akainu": "AgACAgUAAxkBAAIsSGmAqHVcbU6TsRT9ZQJ0AdDGAAHPUQAC7w1rG9KFAVRh7peu8gRcCwEAAwIAA3gAAzgE",
    "Apoo": "AgACAgUAAxkBAAIsSmmAqHtJaLc8iYKWyCraXO3ENfROAALwDWsb0oUBVG7jsMjhVDOpAQADAgADeAADOAQ",
    "Boa Hancock": "AgACAgUAAxkBAAIsTGmAqIDcR7y0YE4XM8sAAcVudfgepQAC8Q1rG9KFAVRcM1V6s4E-wgEAAwIAA3gAAzgE",
    "Sugar": "AgACAgUAAxkBAAIsTmmAqIRCQ-YtpDEFm79e8TYoZOq5AALyDWsb0oUBVI2a6eWzpFQiAQADAgADeAADOAQ",
    "Gecko Moria": "AgACAgUAAxkBAAIsUGmAqIqV-24xs8tOeP05aOsE80UHAALzDWsb0oUBVOq8YeCJMa48AQADAgADeAADOAQ",
    "Magellan": "AgACAgUAAxkBAAIsUmmAqI9rzBPliOMSBB3R_E1USh8gAAL0DWsb0oUBVOY9-PIYyp5fAQADAgADeAADOAQ",
    "Koby NPC": "AgACAgUAAxkBAAIsVGmAqJVXMyfs9T20Mxz2jxodNUmTAAL1DWsb0oUBVJoLYOQVzqhNAQADAgADeAADOAQ",
    "Bartholomew Kuma": "AgACAgUAAxkBAAIsVmmAqJp3eBuAona1ASfMCE9SGs5hAAL2DWsb0oUBVN4wVyP6muPLAQADAgADeAADOAQ",
    "Bonney": "AgACAgUAAxkBAAIsWGmAqJ_yk-bvV5J-wWu4DVLLpcXSAAL3DWsb0oUBVC4NxOQyuavxAQADAgADeQADOAQ",
    "Stussy": "AgACAgUAAxkBAAIsWmmAqKX0a4UAAfXc7Fr8VgABdL4b4iAAAvoNaxvShQFUqHnTd24J--0BAAMCAAN5AAM4BA",
    "Lilith": "AgACAgUAAxkBAAIsXGmAqKq11oExlC3h0eoPidbt9PVwAAL8DWsb0oUBVCm1B2-tXE5vAQADAgADeAADOAQ",
    "Nico Olivia": "AgACAgUAAxkBAAIsXmmAqK_CWqp7go5HAAGCOSkiW9q5YAAC_Q1rG9KFAVQIRRg3q0WmkQEAAwIAA3gAAzgE",
    "Caesar Clown": "AgACAgUAAxkBAAIsYGmAqLVVWbEeE1vdt8LlwUrNPIZPAAL-DWsb0oUBVNyKLKFDq2PjAQADAgADeAADOAQ",
    "Jack": "AgACAgUAAxkBAAIsYmmAqMGHno-NAwi8hg9jIjyjW6VZAAL_DWsb0oUBVLeDCEvCFd3SAQADAgADeAADOAQ",
    "Vergo": "AgACAgUAAxkBAAIsZGmAqMZwXhduDyYSwNMZeaylDnHQAAICDmsb0oUBVOcAAZIvcrhHMAEAAwIAA3gAAzgE",
    "Van Augur": "AgACAgUAAxkBAAIsZmmAqM02g4dx-CrtzpXM2oIoPyVlAAIDDmsb0oUBVHBl15mNK7N9AQADAgADeAADOAQ",
    "Helmeppo NPC": "AgACAgUAAxkBAAIsaGmAqNgXfXKfeXfJ1J4sUIzn2lmrAAIEDmsb0oUBVO-gt3HoPNs8AQADAgADeQADOAQ",
    "Emet": "AgACAgUAAxkBAAIsammAqNxVgxkjE1dorKSY4Jxcl7dtAAIGDmsb0oUBVBc9pkn3eeqGAQADAgADeAADOAQ",
    "Hiyori Kozuki": "AgACAgUAAxkBAAIsbGmAqORWYIYM6geIR_ZrY6ti1LzWAAIQDmsb0oUBVIelBAWHq4OlAQADAgADeAADOAQ",
    "Paragus": "AgACAgUAAxkBAAIsbmmAqPPMfsqqRxioGNqX-YltVysbAAJ-Dmsb0oUBVASnsjVwKkW1AQADAgADeAADOAQ",
    "King Vegeta": "AgACAgUAAxkBAAIscGmAqPifpo3L5EjHh4hfNHzQuA-XAAJ_Dmsb0oUBVIdw2lC3kap0AQADAgADeAADOAQ",
    "Android 16": "AgACAgUAAxkBAAIscmmAqP1aDvg0A583aNKkdDVI5wqyAAKADmsb0oUBVBg9cnU6EgcrAQADAgADeAADOAQ",
    "Nappa": "AgACAgUAAxkBAAIsdGmAqQELDMA5AiKK9311da4BAkMGAAKDDmsb0oUBVEN9BzwqV7WNAQADAgADeAADOAQ",
    "Raditz": "AgACAgUAAxkBAAIsdmmAqQfvXHv0LqUCUIFADr74miV-AAKGDmsb0oUBVK-nRknGbJIgAQADAgADeAADOAQ",
    "Android 19": "AgACAgUAAxkBAAIseGmAqQyyYjhzzIOzErfvB7LotoMZAAKHDmsb0oUBVBtPDZGHk4AaAQADAgADeQADOAQ",
    "Zarbon": "AgACAgUAAxkBAAIsemmAqRi60eHtcRmAOYSExMyV_6YGAAKJDmsb0oUBVHS2DrS-RiNnAQADAgADeAADOAQ",
    "Yamcha": "AgACAgUAAxkBAAIr2mmAWGUiHmGXiJ12ZUievoQ9yNPwAAKLDmsb0oUBVK37xzJaQ8hvAQADAgADeAADOAQ",
    "Rangiku": "AgACAgUAAxkBAAIsfmmAqSgI8GwQYqg896bwyxX4dYFmAAI3DWsb0oUJVPw1vQ_dECRgAQADAgADeQADOAQ",
    "Nelliel": "AgACAgUAAxkBAAIsgGmAqS3E8km8teHNAdxrHZo2ZDQGAAI9DWsb0oUJVKR4vaUKcbJCAQADAgADeQADOAQ",
    "Rukia": "AgACAgUAAxkBAAIsgmmAqTSObQM3bjnpP7torTOJd_jDAAJFDWsb0oUJVHUD_z5xRVB8AQADAgADeAADOAQ",
    "Renji Abarai": "AgACAgUAAxkBAAIshGmAqTm6esmlOi6l-fiddwABsXE04QACRg1rG9KFCVShjZ_Ta34hZgEAAwIAA3kAAzgE",
    "Riruka": "AgACAgUAAxkBAAIshmmAqT-WPnKRkDFNf2KsC5EcoQjJAAJHDWsb0oUJVAwki4zC66QaAQADAgADeQADOAQ",
    "Yachiru": "AgACAgUAAxkBAAIsiGmAqUQi2P1sqX4FyKdmHvWjXpd_AAJJDWsb0oUJVHq3osYmTNmnAQADAgADeAADOAQ",
    "Kotetsu": "AgACAgUAAxkBAAIsimmAqUnLYWFc_yU6ySpQsfnKgV8JAAJODWsb0oUJVJ3Jafm6NIC9AQADAgADeAADOAQ",
    "Yasutora Sado": "AgACAgUAAxkBAAIsjGmAqU6vNG1QRa01eKhjgSdvORHAAAJWDWsb0oUJVNCISbFeTlAHAQADAgADeAADOAQ",
    "Shuhei hisagi": "AgACAgUAAxkBAAIsjmmAqVOdHdhUFpQeyM-Pl6zf4SO4AAJbDWsb0oUJVPhaCIrlsM1wAQADAgADeAADOAQ",
    "Ikkaku": "AgACAgUAAxkBAAIskGmAqVqv73yKeokjw-vYUisxflH3AAJcDWsb0oUJVDDrg9oe6lsuAQADAgADeAADOAQ",
    "Yumichika": "AgACAgUAAxkBAAIsk2mAqV9SK7h_XEF5U6ZAacdgSTZBAAJdDWsb0oUJVK6PUMu_Yw-SAQADAgADeAADOAQ",
    "Tetsuzaemon": "AgACAgUAAxkBAAIslWmAqWXYyg5qnlEzVags9lpfNYBAAAJnDWsb0oUJVENjHANZjQjSAQADAgADeAADOAQ",
    "Orihime Inoue": "AgACAgUAAxkBAAIsl2mAqZClDh0PEJkVeFSdqMJxm6_6AAJsDWsb0oUJVD0U1bcJxNyVAQADAgADeQADOAQ",
    "Tsukishima": "AgACAgUAAxkBAAIsmWmAqZWFsjFyvMS3cXADHoWb6EvUAAJtDWsb0oUJVNQPFqRytClUAQADAgADeAADOAQ",
    "Gremmy": "AgACAgUAAxkBAAIsm2mAqZtIYKGh4jt_vpUJeAELK5ijAAJ3DWsb0oUJVJIe_dmPcqjhAQADAgADeAADOAQ",
    "Fana": "AgACAgUAAxkBAAIsnWmAqaWxXFthfa4oMI8qZKwEyuHfAAJ4DWsb0oUJVAiBJ-gbDPLcAQADAgADeAADOAQ",
    "Vanessa": "AgACAgUAAxkBAAIsn2mAqaouXwuXBnPhCuc6qVwWHZ27AAKDDWsb0oUJVI_mhSQzIhbQAQADAgADeAADOAQ",
    "Gaja": "AgACAgUAAxkBAAIsoWmAqa9BTgGiThO85uPAxLnAabYaAAKEDWsb0oUJVNV3_MBDJ1v4AQADAgADeAADOAQ",
    "Mimosa": "AgACAgUAAxkBAAIso2mAqbMwd98TjDo8MaWh8cCvrcBUAAKHDWsb0oUJVM0eZVUpzN5ZAQADAgADeAADOAQ",
    "Zora Ideale": "AgACAgUAAxkBAAIspWmAqbiSY51AuJfgyVmTWDL4Z-FTAAKJDWsb0oUJVK0sUgTzdgILAQADAgADeAADOAQ",
    "Nero": "AgACAgUAAxkBAAIsp2mAqb5CQ4XYl_N370PObdpxt_vyAAKKDWsb0oUJVKKWgdbnPtPSAQADAgADeAADOAQ",
    "Noelle Silva": "AgACAgUAAxkBAAIsqWmAqcN8bX2GCKON-MZ5ugzuCWlKAAKLDWsb0oUJVLfXiv245rAUAQADAgADeAADOAQ",
    "Luck Voltia": "AgACAgUAAxkBAAIsq2mAqcwbEya_qaPyQMpn07qxqLBXAAKPDWsb0oUJVPgpdV8X_Yn6AQADAgADeAADOAQ",
    "Finral": "AgACAgUAAxkBAAIsrWmAqdMSWgRRYKTttpW4VucjscOBAAKRDWsb0oUJVI0x03d3hrpLAQADAgADeAADOAQ",
    "Magma": "AgACAgUAAxkBAAIsr2mAqdginB1NqalbZVvS3Hhzu3bfAAKSDWsb0oUJVHxEZK53uVlTAQADAgADeAADOAQ",
    "Langris": "AgACAgUAAxkBAAIssWmAqd1fD9imYS9QEpE58Yb9gd59AAKVDWsb0oUJVEaBK4J9mtOOAQADAgADeAADOAQ"
}

IMAGE_URLS = {
    "Yamato": "AgACAgUAAxkBAAIrbGl_XYB0TK4J67UGqAJ7K72GVRWhAAK2DGsb94QBVJk8dxXA-7hyAQADAgADeQADOAQ",
    "Eustass Kid": "AgACAgUAAxkBAAIud2mCA58ss_N8uDbjp-yOnkC6JAj8AAKBD2sbCfwQVBM3mUmj7RHHAQADAgADeQADOAQ",
    "Buggy": "AgACAgUAAxkBAAIrb2l_XZt7xmqcfFrmkBnJXtZp5j4dAAK4DGsb94QBVBinfq8obshLAQADAgADeQADOAQ",
    "Arlong": "AgACAgUAAxkBAAIrcml_XaQs5vPwGs0vezSGgvxz9s4zAAK5DGsb94QBVPxeNDcE4NrPAQADAgADeAADOAQ",
    "Koby": "AgACAgUAAxkBAAIrdWl_XasSiKHzywg5b3G7kIhHtvtoAAK6DGsb94QBVIpHVTFkNstNAQADAgADeQADOAQ",
    "Alvida": "AgACAgUAAxkBAAIreGl_XbU_P1NbZt7B84BKciNBrXRRAAK7DGsb94QBVMTiyOREkM4WAQADAgADeQADOAQ",
    "Chopper": "AgACAgUAAxkBAAIre2l_Xb0Y2RI0E44l0Nr0GXoGAh6cAAK9DGsb94QBVGen5Paut_nn2AQADAgADeQADOAQ",
    "Usopp": "AgACAgUAAxkBAAIrfml_XcfQ6mWgLwebz_Ns4jfR-XeHAAK-DGsb94QBVDGoTiUCadGIAQADAgADeQADOAQ",
    "Helmeppo": "AgACAgUAAxkBAAIrgWl_XdSazwtqkNQQ5jOoWeeJ9hrqAAK_DGsb94QBVLh6NPV1y_YZAQADAgADeQADOAQ",
    "Nami": "AgACAgUAAxkBAAIp-2l-txM84hKLMqVz6oT9z-wpc_o9AAKhDWsb94T5V_JkNM5QQs5BAQADAgADeQADOAQ",
    "Default": "AgACAgUAAxkBAAIBXWl3kMo8CaQ8taCni8_uV3ikQiN4AAJZDWsbLpy4V86gS3f_7AWhAQADAgADeAADOAQ"
}

battles = {}
pending_explores = {}

# =====================
# LEVELING UTILS
# =====================

def get_required_char_exp(level):
    if 1 <= level <= 5: return 500
    if 6 <= level <= 10: return 1000
    if 11 <= level <= 15: return 2000
    if 16 <= level <= 20: return 2500
    return 3000

def get_required_player_exp(level):
    if level >= 100: return 999999999
    if 1 <= level <= 5: return 200
    if 6 <= level <= 10: return 500
    if 11 <= level <= 20: return 1500
    if 21 <= level <= 30: return 2000
    if 31 <= level <= 70: return 3000
    if 71 <= level <= 100: return 6000
    return 10000

def check_player_levelup(p):
    lvl = p.get('level', 1)
    exp = p.get('exp', 0)
    req = get_required_player_exp(lvl)
    levels_gained = 0

    # Ensure we don't exceed level 100
    while exp >= req and lvl < 100:
        exp -= req
        lvl += 1
        levels_gained += 1
        req = get_required_player_exp(lvl)
        
        # Apply your specific rewards per level gained
        p['clovers'] = p.get('clovers', 0) + 10
        p['berries'] = p.get('berries', 0) + 500
        p['bounty'] = p.get('bounty', 0) + 40

    p['level'] = lvl
    p['exp'] = exp
    return levels_gained


def check_char_levelup(char):
    lvl = char.get('level', 1)
    exp = char.get('exp', 0)
    req = get_required_char_exp(lvl)
    while exp >= req:
        exp -= req
        lvl += 1
        req = get_required_char_exp(lvl)
    char['level'] = lvl
    char['exp'] = exp

def get_scaled_stats(char_obj, player_fruit=None):
    name = char_obj['name']
    base = CHARACTERS.get(name, CHARACTERS["Usopp"])
    lvl = char_obj.get('level', 1)
    bonus_multiplier = lvl - 1

    stats = {
        "hp": base['hp'] + (15 * bonus_multiplier),
        "atk_min": base['atk_min'] + (10 * bonus_multiplier),
        "atk_max": base['atk_max'] + (10 * bonus_multiplier),
        "def": base['def'] + (8 * bonus_multiplier),
        "spe": base['spe'] + (12 * bonus_multiplier)
    }

    if player_fruit and player_fruit in DEVIL_FRUITS:
        fruit = DEVIL_FRUITS[player_fruit]
        stats['atk_min'] += fruit['atk_buff']
        stats['atk_max'] += fruit['atk_buff']
        stats['def'] += fruit['def_buff']
        stats['hp'] += fruit['hp_buff']

    return stats

# =====================
# CORE UTILS
# =====================
async def is_spamming(user_id, cooldown_seconds=3):
    p = get_player(user_id)
    current_time = time.time()
    last_time = BUTTON_COOLDOWNS.get(user_id, 0)
    
    if current_time - last_time < cooldown_seconds:
        return True, int(cooldown_seconds - (current_time - last_time))
    
    BUTTON_COOLDOWNS[user_id] = current_time
    return False, 0

async def trigger_security_check(user_id, context):
    p = get_player(user_id)
    riddle = random.choice(RIDDLES)
    
    # 1. Update State First
    p['verification_active'] = True
    save_player(user_id, p) # Save immediately so they can't dodge by restarting app
    
    # Randomize button order
    options = riddle['options'].copy()
    random.shuffle(options)
    
    keyboard = []
    for opt in options:
        # Data format: verify:is_correct:user_id
        is_correct = "1" if opt == riddle['correct'] else "0"
        keyboard.append(InlineKeyboardButton(opt, callback_data=f"v:{is_correct}:{user_id}"))

    # FIXED: Use single * for bold in standard Markdown
    text = (
        f"⚠️ *MARINE SECURITY CHECK!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Identify *{riddle['hint']}* within 30 seconds!\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    
    try:
        msg = await context.bot.send_message(
            chat_id=user_id, 
            text=text, 
            reply_markup=InlineKeyboardMarkup([keyboard]), 
            parse_mode="Markdown"
        )
        
        # Auto-lock after 15 seconds if still active
        context.job_queue.run_once(
            security_timeout, 
            30, 
            data={'user_id': user_id, 'msg_id': msg.message_id}
        )
        
    except Exception as e:
        # If user blocked bot, reset their verify status so they don't get stuck
        p['verification_active'] = False
        save_player(user_id, p)
        logging.warning(f"Could not send security check to {user_id}: {e}")


async def security_timeout(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    uid = job_data['user_id']
    msg_id = job_data['msg_id']
    
    p = get_player(uid) # RAM Fetch
    
    # If they still have verification_active, it means they didn't click anything
    if p and p.get('verification_active'):
        p['verification_active'] = False
        p['is_locked'] = True
        save_player(uid, p) # Push to RAM and DB

        try:
            # Update the message so they know they are locked
            await context.bot.edit_message_text(
                chat_id=uid,
                message_id=msg_id,
                text="🚫 **ACCOUNT LOCKED (TIMEOUT)**\nYou failed to respond to the Marine Security Check. Contact admin."
            )
            
            # Notify your Log Group
            await context.bot.send_message(
                chat_id="-1003855697962",
                text=f"🚨 **BOT DETECTION (TIMEOUT)**\n👤: `{p.get('name')}`\n🆔: `{uid}`\n👉 `/unlock {uid}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Timeout logic failed for {uid}: {e}")

async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Security Check: Only allow Admins
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Access Denied: Admins only.")
        return

    # 2. Argument Check
    if not context.args:
        await update.message.reply_text("⚠️ Usage:\n`/unlock <id>` (Unlock one)\n`/unlock all` (Unlock EVERYONE)")
        return

    command_arg = context.args[0].lower()

    # ==========================
    # OPTION A: UNLOCK EVERYONE
    # ==========================
    if command_arg == "all":
        await update.message.reply_text("🔄 **Unlocking ALL players...** (This may take a moment)")
        
        try:
            # 🟢 FIXED: Using the correct name 'players_collection'
            result = players_collection.update_many(
                {"is_locked": True},
                {"$set": {"is_locked": False, "verification_active": False, "last_interaction": 0}}
            )
            
            # 2. Update RAM (Catches online players)
            ram_count = 0
            for uid in player_cache:
                if player_cache[uid].get('is_locked'):
                    player_cache[uid]['is_locked'] = False
                    player_cache[uid]['verification_active'] = False
                    player_cache[uid]['last_interaction'] = 0
                    ram_count += 1
            
            # 3. Report
            msg = (
                f"✅ **GLOBAL UNLOCK COMPLETE**\n\n"
                f"📂 Database Updated: {result.modified_count} players\n"
                f"🧠 RAM Updated: {ram_count} active sessions\n"
                f"🔓 Everyone is free to sail!"
            )
            await update.message.reply_text(msg)
            
            # Log to Admin Group
            try:
                await context.bot.send_message(
                    chat_id="-1003855697962", 
                    text=f"🚨 **GLOBAL UNLOCK** initiated by {update.effective_user.first_name}!"
                )
            except:
                pass 
            
        except Exception as e:
            await update.message.reply_text(f"❌ Database Error: {e}")
        return

    # ==========================
    # OPTION B: UNLOCK SPECIFIC IDs
    # ==========================
    results = []
    for target_id in context.args:
        try:
            clean_id = str(target_id).replace(",", "").strip()
            p = load_player(clean_id)
            
            if not p:
                results.append(f"⚠️ `{clean_id}`: Not found")
                continue

            # Unlock logic
            p['is_locked'] = False
            p['verification_active'] = False
            p['last_interaction'] = 0 
            save_player(clean_id, p)
            
            results.append(f"✅ `{p['name']}`: Unlocked")
            
            # Attempt DM
            try:
                await context.bot.send_message(chat_id=clean_id, text="🔓 **Account Unlocked!**\nThe Marine Security lock has been lifted.")
            except:
                pass 

        except Exception as e:
            results.append(f"❌ `{target_id}`: Error")

    if results:
        await update.message.reply_text("\n".join(results), parse_mode="Markdown")

def get_player(user_id, username=None):
    uid = str(user_id)
    
    # 1. load_player now checks RAM first, making this instant
    p = load_player(uid)
    
    if not p:
        # Create new player template
        p = {
            "user_id": uid, "name": username or "Pirate", "team": [], "characters": [],
            "berries": 10000, "clovers": 0, "bounty": 0, "exp": 0, "level": 1,
            "starter_summoned": False, "wins": 0, "losses": 0, "explore_wins": 0, "kill_count": 0,
            "fruits": [], "equipped_fruit": None, "tokens": 0, "weapons": [],
            "explore_count": 0, "start_date": datetime.now().strftime("%Y-%m-%d"),
            "referred_by": None, "referrals": 0
        }
        # Only save to DB for brand new registrations
        save_player(uid, p)
    else:
        # 2. Fill in missing keys (Migration logic) without hitting the DB
        defaults = {
            "user_id": uid, "team": [], "characters": [], "berries": 0, "clovers": 0, "bounty": 0,
            "exp": 0, "level": 1, "wins": 0, "losses": 0, "explore_wins": 0, "kill_count": 0,
            "fruits": [], "equipped_fruit": None, "tokens": 0, "weapons": [],
            "explore_count": 0, "start_date": datetime.now().strftime("%Y-%m-%d"),
            "referred_by": None, "referrals": 0
        }
        modified = False
        for k, v in defaults.items():
            if k not in p: 
                p[k] = v
                modified = True
        
        if not p.get("name") or p["name"] == "Pirate":
            if username: 
                p["name"] = username
                modified = True
        
        # 3. Handle Admin Stats in RAM
        if int(user_id) in ADMIN_IDS:
            p["berries"] = max(p.get("berries", 0), 99999999)
            p["clovers"] = max(p.get("clovers", 0), 99999999)
            p["level"] = 100
            modified = True

        # Only save if we actually added missing default keys
        if modified:
            save_player(uid, p)

    # 4. Return the RAM-cached object immediately
    return p

def get_stats_text(char_obj_or_name, player_fruit=None):
    if isinstance(char_obj_or_name, str):
        name = char_obj_or_name
        lvl = 1
        weapon = None
    else:
        name = char_obj_or_name['name']
        lvl = char_obj_or_name.get('level', 1)
        weapon = char_obj_or_name.get('equipped_weapon')

    c = CHARACTERS.get(name)
    if not c: return "Character not found."

    # Pull the full label from your RARITY_STYLES dictionary
    rarity_info = RARITY_STYLES.get(c['rarity'], {"label": c['rarity']})
    rarity_display = rarity_info['label']

    stats = get_scaled_stats({"name": name, "level": lvl}, player_fruit)
    ult_name = c['ult']
    ult_damage = MOVES[ult_name]['dmg']
    ult_desc = EFFECT_DESCRIPTIONS.get(name, "No additional effect.")

    # UI Construction
    text = (
        f"《Name》: {name}\n"
        f"《Rarity》: {rarity_display}\n"
        f"《 Class》: {c['class']}\n"
        f"《Level》: {lvl}\n\n"
        f"      《STATS》\n"
        f"《HP: {stats['hp']}\n"
        f"《ATK: {stats['atk_min']}-{stats['atk_max']}\n"
        f"《SPE: {stats['spe']}\n"
        f"《 DEF: {stats['def']}\n\n"
        f"■ 《BASIC》: {c['moves'][0]}: Damage {MOVES[c['moves'][0]]['dmg']}\n"
        f"♤《 ULTIMATE》: {ult_name}: Damage {ult_damage}. {ult_desc}"
    )
    
    if weapon:
        w_data = WEAPONS[weapon]
        text += f"\n⚔️ 《WEAPON》: {weapon}: {w_data['spec']} (Dmg: {w_data['atk_val']})"
    return text


def generate_char_instance(name, level=1, player_fruit=None, equipped_weapon=None):
    c = CHARACTERS.get(name, {
        "hp": 300, "atk_min": 15, "atk_max": 25, "def": 15, "spe": 20,
        "moves": ["Strike", "Bash"], "ult": "Special Beam"
    })

    stats = get_scaled_stats({"name": name, "level": level}, player_fruit)
    moves = list(c.get('moves', ["Strike", "Bash"]))
    if equipped_weapon and equipped_weapon in WEAPONS:
        moves.append(WEAPONS[equipped_weapon]['spec'])

    return {
        "id": str(uuid.uuid4())[:8], "name": name, "level": level, "exp": 0,
        "hp": stats['hp'], "max_hp": stats['hp'], "atk_min": stats['atk_min'],
        "atk_max": stats['atk_max'], "def": stats['def'], "spe": stats['spe'],
        "moves": moves, "ult": c.get('ult', "Special Beam"),
        "stunned": False, "ult_used": False, "dodge_chance": 0, "equipped_weapon": equipped_weapon
    }
# =====================
# EXPLORE LOGIC
# =====================

async def explore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Added DM Check
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command can only be used in private messages (DM).")
        return

    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    p = get_player(update.effective_user.id)
    uid = str(p['user_id'])
    p['last_interaction'] = time.time()
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    # UPDATED: 2-minute cooldown logic for pending battles
    if uid in pending_explores:
        pending_data = pending_explores[uid]
        # Check if it's the old format (string) or new dict format for backward compatibility
        last_time = pending_data.get('time', 0) if isinstance(pending_data, dict) else 0

        if time.time() - last_time < 120: # 120 seconds = 2 minutes
            remaining = int(120 - (time.time() - last_time))
            await update.message.reply_text(f"⚠️ You have an unfinished battle! You can escape and explore again in {remaining} seconds.")
            return
        else:
            # 2 minutes passed, allow new explore (reset pending)
            del pending_explores[uid]

    p['explore_count'] += 1

    # 1 to 2 Clovers per explore
    clover_gain = random.randint(1, 2)
    p['clovers'] += clover_gain
    save_player(uid, p)

    roll = random.random()
    if roll < 0.005:
        c_luck = random.randint(15, 25)
        c_berry = random.randint(4000, 6000)
        c_tokens = random.randint(4, 5)
        p['clovers'] += c_luck
        p['berries'] += c_berry
        p['tokens'] += c_tokens
        save_player(p['user_id'], p)
        text = f"While exploring, You found a Frost Chest\n\nIt contains\n{c_luck} 🍀\n{c_berry} 🍇\n{c_tokens} Level up token🧩"
        await update.message.reply_photo(FROST_CHEST_IMG, caption=text)
        return
    elif roll < 0.015:
        c_luck = random.randint(5, 10)
        c_berry = random.randint(2000, 4000)
        c_tokens = random.randint(1, 2)
        p['clovers'] += c_luck
        p['berries'] += c_berry
        p['tokens'] += c_tokens
        save_player(p['user_id'], p)
        text = f"While exploring, You found a Golden Chest\n\nIt contains\n{c_luck} 🍀\n{c_berry} 🍇\n{c_tokens} Level up token🧩"
        await update.message.reply_photo(GOLD_CHEST_IMG, caption=text)
        return
    elif roll < 0.065:
        c_luck = random.randint(1, 5)
        c_berry = 1500
        p['clovers'] += c_luck
        p['berries'] += c_berry
        save_player(p['user_id'], p)
        text = f"While exploring, You found a Dark Chest.\n\nIt contains\n{c_luck} 🍀\n{c_berry} 🍇"
        await update.message.reply_photo(DARK_CHEST_IMG, caption=text)
        return

    wins = p.get('explore_wins', 0)
    if wins in BOSS_MISSIONS:
        boss = BOSS_MISSIONS[wins]
        char_name = boss['name']
        img_id = boss['img']
        text = (
            f"🚨 **MISSION BOSS ENCOUNTER** 🚨\n\n"
            f"You've defeated {wins} challengers! The boss **{char_name}** has appeared to block your path!\n\n"
            f"Prepare for a legendary battle!"
        )
    else:
        char_name = random.choice(list(EXPLORE_DATA.keys()))
        img_id = EXPLORE_DATA[char_name]
        text = (
            f"🧭 **EXPLORATION** 🧭\n\n"
            f"You encountered **{char_name}** while sailing the Grand Line!\n"
            f"Do you wish to engage in battle?"
        )

    # UPDATED: Store timestamp"
    pending_explores[uid] = {'name': char_name, 'time': time.time()}

    kb = [
        [InlineKeyboardButton(f"Fight {char_name} ⚔", callback_data=f"efight_{char_name}")],
        [InlineKeyboardButton("📜 Missions", callback_data="show_missions")]
    ]

    # 🛑 CRITICAL FIX: Wrap this in try/except to prevent crashes
    try:
        await update.message.reply_photo(
            img_id, 
            caption=text, 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode="Markdown"
        )
    except Exception as e:
        # If the image ID is broken (like Blackbeard), this runs instead
        logging.error(f"Image failed for {char_name}: {e}")
        await update.message.reply_text(
            f"⚠️ **IMAGE ERROR** ⚠️\n(The image for {char_name} is broken, but you can still fight!)\n\n{text}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

# =====================
# STARTER, REFERRAL & NAV
# =====================

# UPDATED: Added target_user_id to ensure strict checking
async def show_starter_page(update, name, target_user_id):
    text = get_stats_text(name)
    img = IMAGE_URLS.get(name, IMAGE_URLS["Default"])
    order = ["Usopp", "Nami", "Helmeppo"]
    if name not in order: name = "Usopp"
    idx = order.index(name)

    # Embed target_user_id in callback data
    btns = [[InlineKeyboardButton("Choose this Pirate", callback_data=f"choose_{name}_{target_user_id}")]]
    nav = []
    if idx > 0: nav.append(InlineKeyboardButton("⬅ Previous", callback_data=f"start_{order[idx-1]}_{target_user_id}"))
    if idx < len(order) - 1: nav.append(InlineKeyboardButton("Next ➡", callback_data=f"start_{order[idx+1]}_{target_user_id}"))
    btns.append(nav)

    markup = InlineKeyboardMarkup(btns)
    try:
        if update.callback_query:
            await update.callback_query.edit_message_media(InputMediaPhoto(img, caption=text), reply_markup=markup)
        else:
            await update.message.reply_photo(img, caption=text, reply_markup=markup)
    except Exception: pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or update.effective_user.first_name or "Pirate"
    
    # 1. Instant RAM Lookup
    p = load_player(user_id) 
    is_new = False

    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    if not p:
        is_new = True
        p = {
            "user_id": user_id, "name": username, "team": [], "characters": [],
            "berries": 10000, "clovers": 0, "bounty": 0, "exp": 0, "level": 1,
            "starter_summoned": False, "wins": 0, "losses": 0, "explore_wins": 0, "kill_count": 0,
            "fruits": [], "equipped_fruit": None, "tokens": 0, "weapons": [],
            "explore_count": 0, "start_date": datetime.now().strftime("%Y-%m-%d"),"is_locked": False,
            "verification_active": False, "referred_by": None, "referrals": 0
        }

    # 2. Optimized Referral Logic
    if is_new and context.args:
        try:
            referrer_id = str(context.args[0])
            if referrer_id != user_id:
                referrer = load_player(referrer_id)
                if referrer:
                    p['referred_by'] = referrer_id
                    p['berries'] += 5000
                    p['clovers'] += 50
                    
                    referrer['berries'] += 10000
                    referrer['clovers'] += 100
                    referrer['referrals'] = referrer.get('referrals', 0) + 1

                    # Save referrer immediately to RAM
                    save_player(referrer_id, referrer)

                    await update.message.reply_text(f"🤝 Referred by {referrer['name']}! Bonus: 5,000 🍇 + 50 🍀")
                    
                    # UPDATED: Notification including Clovers
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id, 
                            text=f"🤝 **{p['name']}** joined!\n🍇 `+10,000` Berries\n🍀 `+100` Clovers",
                            parse_mode="Markdown"
                        )
                    except: pass
        except Exception as e:
            logging.error(f"Referral logic error: {e}")

    # 3. Final Save
    save_player(user_id, p)

    if p.get("starter_summoned"):
        await update.message.reply_text(f"Welcome back Captain {p['name']}!")
        return

    await show_starter_page(update, "Usopp", user_id)

async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={user_id}"
    
    p = get_player(user_id)
    ref_count = p.get('referrals', 0)

    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    text = (
        f"╭━━━━━━━━━━━━━━━╮\n"
        f"✦    🤝 REFERRAL 🤝     ✦\n"
        f"╰━━━━━━━━━━━━━━━╯\n\n"
        f"Share your link to grow your fleet!\n\n"
        f"🔗 **YOUR LINK:**\n`{link}`\n\n"
        f"🎁 **REWARDS**\n"
        f"• You get: 10,000 🍇 + 100 🍀\n"
        f"• Friend gets: 5,000 🍇 + 50 🍀\n\n"
        f"📊 **TOTAL RECRUITS:** `{ref_count}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")



# =====================
# STORE & BUY SYSTEM
# =====================

async def store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Added DM Check
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command can only be used in private messages (DM).")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    text = "⚓️ **PIRATE STORE** ⚓️\n\nWelcome to the black market. Select a category to browse items."
    kb = [
        [InlineKeyboardButton("Weapons ⚔️", callback_data="store_weapons"), InlineKeyboardButton("Fruits 🍎", callback_data="store_fruits")],
        [InlineKeyboardButton("Close", callback_data="wheel_cancel")]
    ]
    await update.message.reply_photo(STORE_IMG, caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def handle_store_callback(query, category):
    if category == "weapons":
        text = "⚔️ **WEAPONS FOR SALE** ⚔️\n\n"
        for name, d in WEAPONS.items():
            text += f"• **{name}**: 🍇{d['cost']:,} (Rank {d['lvl']}+)\n"
        text += "\nUse `/buy Item Name` to purchase."
    else:
        text = "🍎 **DEVIL FRUITS FOR SALE** 🍎\n\n"
        for name, d in DEVIL_FRUITS.items():
            text += f"• **{name}**: 🍇{d['cost']:,} (Rank {d['lvl']}+)\n"
        text += "\nUse `/buy Item Name` to purchase."

    kb = [[InlineKeyboardButton("Back to Store", callback_data="back_to_store")]]
    await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/buy Item Name`")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return



    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    input_item = " ".join(context.args).lower().strip()
    p = get_player(update.effective_user.id)

    target_name = None
    item_type = None

    for w_name in WEAPONS:
        if w_name.lower() == input_item:
            target_name = w_name
            item_type = "weapon"
            break
    if not target_name:
        for f_name in DEVIL_FRUITS:
            if f_name.lower() == input_item:
                target_name = f_name
                item_type = "fruit"
                break

    if not target_name:
        await update.message.reply_text("Item not found in store.")
        return

    item_data = WEAPONS[target_name] if item_type == "weapon" else DEVIL_FRUITS[target_name]
    req_lvl = item_data['lvl']

    if p.get('level', 1) < req_lvl:
        await update.message.reply_text(f"❌ You need Player Rank {req_lvl} to purchase {target_name}!")
        return

    if item_type == "weapon":
        w = WEAPONS[target_name]
        text = (f"➥Name: {target_name}\n➥Rarity: {w['rarity']}\n➥Attack: {w['atk_range']}\n"
                f"➥Critical chance: {w['crit']}\n➥Accuracy: {w['acc']}\n"
                f"➥Special attack: {w['spec']}\n➥Rank requirement: {w['lvl']}\n\n➥ Cost: {w['cost']}🍇")
        img = w['img']
    else:
        f = DEVIL_FRUITS[target_name]
        text = f['text'] + f"\n\n➥ Cost: {f['cost']}🍇"
        img = f['img']

    kb = [[InlineKeyboardButton("Confirm Purchase ✅", callback_data=f"confbuy|{item_type}|{target_name}")]]
    await update.message.reply_photo(img, caption=text, reply_markup=InlineKeyboardMarkup(kb))

async def use_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/use Item Name`")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    input_name = " ".join(context.args).lower().strip()
    p = get_player(update.effective_user.id)

    if "level-up token" in input_name or "level up token" in input_name:
        if p.get('tokens', 0) <= 0:
            await update.message.reply_text("You don't have any Level-up tokens!")
            return
        kb = []
        for i, char in enumerate(p.get('characters', [])):
            kb.append([InlineKeyboardButton(f"{char['name']} (Lv.{char['level']})", callback_data=f"usetoken|{i}")])
        if not kb:
            await update.message.reply_text("You have no pirates to level up.")
            return
        await update.message.reply_text("Select a pirate to level up using 1 token:", reply_markup=InlineKeyboardMarkup(kb))
        return

    target_fruit = None
    for f_name in p.get('fruits', []):
        if f_name.lower() == input_name:
            target_fruit = f_name
            break

    if target_fruit:
        p['fruits'].remove(target_fruit)
        p['equipped_fruit'] = target_fruit
        save_player(p['user_id'], p)
        await update.message.reply_text(f"✨ {target_fruit} consumed! This devil fruit's abilities have been added to your whole team.")
        return

    target_weapon = None
    for w_name in p.get('weapons', []):
        if w_name.lower() == input_name:
            target_weapon = w_name
            break

    if target_weapon:
        kb = []
        for i, char in enumerate(p.get('characters', [])):
            # Show if already equipped
            eq = " (Equipped)" if char.get('equipped_weapon') == target_weapon else ""
            kb.append([InlineKeyboardButton(f"{char['name']} (Lv.{char['level']}){eq}", callback_data=f"wepattach|{target_weapon}|{i}")])
        await update.message.reply_text(f"Select a character to equip **{target_weapon}** (Weapon will be consumed):", reply_markup=InlineKeyboardMarkup(kb))
        return

    await update.message.reply_text("You don't own this item or it's not usable.")

# =====================
# TEAM MANAGEMENT
# =====================

async def myteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Added DM Check
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command can only be used in private messages (DM).")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    p = get_player(update.effective_user.id)
    team_names = ", ".join([c['name'] for c in p.get('team', [])]) or "None"
    txt = f"⚓️ YOUR TEAM ⚓️\n\nActive: {team_names}\n\nSelect up to 3 pirates for battle."
    kb = [[InlineKeyboardButton("Set Team ⚔", callback_data="manage_team")]]
    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def manage_team(query, p):
    chars = p.get("characters", [])
    
    if not chars:
        await query.answer("You have no pirates! Use /wheel first.", show_alert=True)
        return
    kb = []
    for i, c in enumerate(chars):
        status = "✅" if any(tc['id'] == c['id'] for tc in p.get('team', [])) else "❌"
        kb.append([InlineKeyboardButton(f"{c['name']} (Lv.{c['level']}) {status}", callback_data=f"toggle_{i}")])
    kb.append([InlineKeyboardButton("💾 Save Team", callback_data="save_team")])

    text = "Select up to 3 characters:"
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    except:
        try:
            await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(kb))
        except:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# =====================
# BATTLE LOGIC
# =====================

async def battle_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    bid = job.data['bid']
    if bid in battles:
        b = battles[bid]
        if b['last_move_time'] == job.data['last_time']:
            quitter_p = b['turn_owner']
            winner_p = "p2" if quitter_p == "p1" else "p1"
            winner_name = b[f'{winner_p}_name']
            quitter_name = b[f'{quitter_p}_name']

            try:
                await context.bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.data['msg_id'],
                    text=f"⏰ **TIMEOUT!**\n\n**{quitter_name}** took too long to move! **{winner_name}** wins by default!",
                    parse_mode="Markdown"
                )
            except: pass
            if bid in battles: del battles[bid]

async def battle_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to someone to challenge them!")
        return

    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    p1_id = str(update.effective_user.id)
    p2_id = str(update.message.reply_to_message.from_user.id)
    if p1_id == p2_id: return

    # Check Registration for Opponent
    if not load_player(p2_id):
        await update.message.reply_text("⚠️ Your opponent hasn't started their journey yet!")
        return

    # Check if either player is already in battle
    for b in battles.values():
        if p1_id in [str(b['p1_id']), str(b.get('p2_id'))] or p2_id in [str(b['p1_id']), str(b.get('p2_id'))]:
            await update.message.reply_text("One of the players is already in a battle!")
            return

    p1, p2 = get_player(p1_id), get_player(p2_id)
    if not p1.get('team') or not p2.get('team'):
        await update.message.reply_text("Both players must have a team set via /myteam!")
        return
    kb = [[InlineKeyboardButton("Accept Battle ⚔", callback_data=f"accept_{p1_id}_{p2_id}")]]
    await update.message.reply_text(f"Hey {p2['name']}, {p1['name']} challenged you!", reply_markup=InlineKeyboardMarkup(kb))

def get_bar(h, m):
    if m <= 0: return "▒" * 10
    ratio = max(0, min(1, h/m))
    filled = int(ratio * 10)
    return "█" * filled + "▒" * (10 - filled)

async def run_battle_turn(query, battle_id, move_name=None, context=None):
    b = battles.get(battle_id)
    if not b: return

    b['last_move_time'] = time.time()
    p1_char = b['p1_team'][b['p1_idx']]
    p2_char = b['p2_team'][b['p2_idx']]

    # Determine roles
    if b['turn_owner'] == "p1":
        attacker, defender, att_p, def_p, att_team = p1_char, p2_char, "p1", "p2", b['p1_team']
    else:
        attacker, defender, att_p, def_p, att_team = p2_char, p1_char, "p2", "p1", b['p2_team']

    # 1. STUN LOGIC
    if attacker.get('stunned'):
        attacker['stunned'] = False
        log = f"💫 **{attacker['name']}** is stunned and skipped their turn!"
        b['turn_owner'] = def_p
        await show_move_selection(query, battle_id, log, context)
        if b.get('is_npc') and b['turn_owner'] == "p2":
            await asyncio.sleep(0.5) 
            await run_battle_turn(query, battle_id, move_name=None, context=context)
        return

    # 2. NPC AI LOGIC (Snappy 0.5s response)
    if b.get('is_npc') and b['turn_owner'] == "p2":
        # Uses only moves[0] to prevent IndexError from old multi-move system
        basic_move = attacker['moves'][0] 
        if not attacker.get('ult_used') and random.random() < 0.3:
            move_name = attacker['ult']
        else:
            move_name = basic_move

    if not move_name:
        await show_move_selection(query, battle_id, context=context)
        return

    # 3. DODGE LOGIC
    if random.random() < (attacker.get('dodge_chance', 0) / 100):
        log = f"💨 **{defender['name']}** dodged the attack!"
        attacker['dodge_chance'] = 0
    else:
        # 4. DAMAGE CALCULATION
        move_data = MOVES.get(move_name, MOVES["Strike"])
        is_ult = (move_name == attacker['ult'])
        
        if is_ult:
            attacker['ult_used'] = True
            if attacker['name'] == "Yamato":
                img = YAMATO_EXPLORE_ULT if b.get('is_npc') else YAMATO_ULT_VIDEO
                try: await query.message.reply_photo(photo=img, caption="⚡️ **THUNDER BAGUA!**")
                except: pass
            elif attacker['name'] == "Eustass Kid":
                img = KID_EXPLORE_ULT if b.get('is_npc') else KID_ULT_VIDEO
                try: await query.message.reply_photo(photo=img, caption="⚡️ **DAMNED PUNK!**")
                except: pass

        # Damage Formula using scaled stats
        damage = max(5, (random.randint(attacker.get('atk_min', 20), attacker.get('atk_max', 30)) + move_data['dmg'] + 120) - defender.get('def', 10))
        defender['hp'] -= damage
        log = f"🔥 **{attacker['name']}** uses **{move_name}**!\n💥 Deals **{damage}** DMG!"

        # 5. MOVE EFFECTS
        effect = move_data.get('effect')
        if effect:
            if effect == "def_buff_10": attacker['def'] += 10
            elif effect == "team_heal_50":
                for char in att_team: char['hp'] = min(char['max_hp'], char['hp'] + 50)
            elif effect == "dodge_30": attacker['dodge_chance'] = 30
            elif effect == "stun_1": defender['stunned'] = True

    # 6. DEATH & REWARDS LOGIC (Ultra-Fast Cache Updates)
    if defender['hp'] <= 0:
        defender['hp'] = 0
        b[f'{def_p}_idx'] += 1
        log += f"\n\n💀 **{defender['name']}** HAS FALLEN!"

        if b[f'{def_p}_idx'] >= len(b[f'{def_p}_team']):
            winner_name = b['p1_name'] if def_p == "p2" else b['p2_name']
            loser_name = b['p2_name'] if def_p == "p2" else b['p1_name']
            rank_up_section = ""

            if b.get('is_npc'):
                uid = str(b['p1_id'])
                if uid in pending_explores: del pending_explores[uid]
                p = get_player(uid) # Instant RAM lookup
                
                # LOOT TABLE: Clovers restored
                wins_at = p.get('explore_wins', 0)
                if wins_at in BOSS_MISSIONS:
                    exp_gain, berry_gain, clover_gain, bounty_gain = random.randint(200,300), random.randint(200,250), random.randint(5,10), random.randint(100,200)
                else:
                    exp_gain, berry_gain, clover_gain, bounty_gain = random.randint(50,100), random.randint(50,100), random.randint(1,3), random.randint(20,30)
                
                p['explore_wins'] += 1
                p['exp'] += exp_gain; p['berries'] += berry_gain; p['clovers'] += clover_gain; p['bounty'] += bounty_gain
                
                # Update character-specific exp
                for team_char in b['p1_team']:
                    for main_char in p.get('characters', []):
                        if main_char['name'] == team_char['name']:
                            main_char['exp'] = main_char.get('exp', 0) + exp_gain
                            check_char_levelup(main_char)

                # LEVEL UP REWARDS (Separated Display)
                lvls = check_player_levelup(p)
                if lvls > 0: 
                    rank_up_section = (
                        f"\n\n🎊 **RANK UP!** You reached **Level {p['level']}**!\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🎁 **LEVEL UP REWARDS**:\n"
                        f"🍇 Berries: `+{lvls * 500}`\n"
                        f"🍀 Clovers: `+{lvls * 10}`\n"
                        f"฿ Bounty: `+{lvls * 40}`"
                    )
                
                save_player(uid, p) # RAM update + Background DB sync

                final_ui = (
                    f"◈☰☰☰⚔️ ＢＡＴＴＬＥ ＲＥＳＵＬＴ ⚔️☰☰☰◈\n\n"
                    f"🏆 **{winner_name}** defeated **{loser_name}**!\n\n"
                    f"📦 **LOOT DROPPED**:\n"
                    f"🌟 EXP: `+{exp_gain}`\n"
                    f"🍇 Berries: `+{berry_gain}`\n"
                    f"🍀 Clovers: `+{clover_gain}`\n"
                    f"฿ Bounty: `+{bounty_gain}`"
                    f"{rank_up_section}"
                )
            else:
                wp_id = b['p1_id'] if def_p == "p2" else b['p2_id']
                wp = get_player(wp_id); wp['wins'] += 1; save_player(wp_id, wp)
                final_ui = f"🏆 **{winner_name}** triumphed in PvP!"

            if battle_id in battles: del battles[battle_id]

            # FIX: Use edit_message_caption to prevent 'no text to edit' crash on images
            try:
                await query.edit_message_caption(caption=final_ui, parse_mode="Markdown")
            except Exception:
                try: await query.edit_message_text(final_ui, parse_mode="Markdown")
                except: await query.message.reply_text(final_ui)
            return
            
    # 7. TURN ROTATION
    b['turn_owner'] = def_p
    await show_move_selection(query, battle_id, log, context)
    
    if b.get('is_npc') and b['turn_owner'] == "p2":
        await asyncio.sleep(0.5) 
        await run_battle_turn(query, battle_id, move_name=None, context=context)


async def show_move_selection(query, battle_id, log="", context=None):
    b = battles.get(battle_id)
    if not b: return
    p1_char = b['p1_team'][b['p1_idx']]; p2_char = b['p2_team'][b['p2_idx']]
    attacker = b[b['turn_owner'] + '_team'][b[b['turn_owner'] + '_idx']]

    # Simplified to only take the first move
    basic_move = attacker['moves'][0]
    # Check if a weapon special move exists (it would be at index 2 or 1 depending on list length)
    spec_move = attacker['moves'][2] if len(attacker['moves']) > 2 else (attacker['moves'][1] if len(attacker['moves']) > 1 else None)

    ult_name = attacker['ult']
    ult_desc = EFFECT_DESCRIPTIONS.get(attacker['name'], "Standard massive damage.")

    status = (
        f"⚔️ **ARENA** ⚔️\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 **{b['p1_name'].upper()} - {p1_char['name']}**: {p1_char['hp']}/{p1_char['max_hp']}\n"
        f"`{get_bar(p1_char['hp'], p1_char['max_hp'])}`\n\n"
        f"👤 **{b['p2_name'].upper()} - {p2_char['name']}**: {p2_char['hp']}/{p2_char['max_hp']}\n"
        f"`{get_bar(p2_char['hp'], p2_char['max_hp'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚡️ **{attacker['name'].upper()}'S TURN** ⚡️\n"
        f"👊 **BASIC**: {basic_move}\n"
    )
    
    if spec_move:
        status += f"⚔️ **SPECIAL**: {spec_move}\n"

    status += (
        f"🌟 **ULTIMATE**: {ult_name}\n"
        f"└─ *{ult_desc}*\n"
        f"━━━━━━━━━━━━━━━━━━\n{log if log else 'Waiting for your move...'}\n"
        f"━━━━━━━━━━━━━━━━━━\n⌛️ TURN: **{b[b['turn_owner'] + '_name']}**"
    )

    # Simplified Keyboard: One row for Basic, one for Special (if exists), one for Ult
    kb = [
        [InlineKeyboardButton(f"👊 {basic_move}", callback_data=f"bmove|{battle_id}|{basic_move}")]
    ]
    
    if spec_move:
        kb.append([InlineKeyboardButton(f"⚔️ {spec_move}", callback_data=f"bmove|{battle_id}|{spec_move}")])

    kb.append([InlineKeyboardButton(f"🌟 ULTIMATE: {ult_name} 🌟" if not attacker.get('ult_used') else "🚫 ULTIMATE DEPLETED", 
                                  callback_data=f"bmove|{battle_id}|{ult_name}" if not attacker.get('ult_used') else "none")])
    
    kb.append([InlineKeyboardButton("🏃 Run", callback_data=f"brun_{battle_id}"), 
               InlineKeyboardButton("🏳 Forfeit", callback_data=f"bforfeit_{battle_id}")])

    try:
        msg = await query.edit_message_text(status, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    except:
        try: msg = await query.edit_message_caption(caption=status, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except: return

    if context and hasattr(context, 'job_queue') and context.job_queue and not b.get('is_npc'):
        context.job_queue.run_once(battle_timeout_check, 120, data={'bid': battle_id, 'last_time': b['last_move_time'], 'msg_id': msg.message_id}, chat_id=query.message.chat_id)

# =====================
# CALLBACK MASTER
# =====================

async def main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = query.data  # Define this FIRST so it can be used in checks
    
    # 1. Load Player & Check Registration
    p = load_player(uid)
    if not p and not data.startswith("choose_") and not data.startswith("v:"):
        await query.answer("⚠️ Start your journey first! Use /start.", show_alert=True)
        return

    # 2. Universal Spam Protection (2s as you set)
    spamming, wait_time = await is_spamming(uid, 2)
    if spamming:
        await query.answer(f"⏳ Slow down! Wait {wait_time}s...", show_alert=False)
        return
    if p: p['last_interaction'] = time.time()
    # 3. Global Security Lock
    # Stop locked users from doing anything EXCEPT the verification check
    if p and p.get('is_locked') and not data.startswith("v:"):
        await query.answer("🚫 Account Locked! Contact Admin.", show_alert=True)
        return

    # 4. Marine Security Verification Logic
    if data.startswith("v:"):
        _, is_correct, target_uid = data.split(":")
        
        if uid != target_uid:
            await query.answer("❌ This check isn't for you!", show_alert=True)
            return

        if not p or not p.get('verification_active'): 
            await query.answer("⌛ This check has expired.")
            await query.message.delete()
            return

        if is_correct == "1":
            p['verification_active'] = False
            save_player(uid, p)
            await query.edit_message_text("✅ **Verification Passed!**\nContinue your journey.")
        else:
            p['is_locked'] = True
            p['verification_active'] = False
            save_player(uid, p)
            await query.edit_message_text("🚫 **ACCOUNT LOCKED.**\nContact owner to prove your identity.")
            
            await context.bot.send_message(
                chat_id="-1003855697962",
                text=f"🚨 **BOT ALERT**\n👤: `{p.get('name')}`\n🆔: `{uid}`\n❌: Failed Emoji\n👉 `/unlock {uid}`",
                parse_mode="Markdown"
            )
        return

    if data == "none":
        await query.answer("Ultimate can only be used once!")
        return

    if data == "go_shop":
        await query.answer("Visit the /store to purchase this item!", show_alert=True)
        return

    if data.startswith("start_"):
        parts = data.split("_")
        # Ensure only the original user can interact
        if len(parts) > 2:
            target_id = parts[2]
            if str(uid) != str(target_id):
                await query.answer("This menu is not for you!", show_alert=True)
                return
            await show_starter_page(update, parts[1], target_id)

    elif data.startswith("choose_"):
        parts = data.split("_")
        # Ensure only the original user can interact
        if len(parts) > 2:
            target_id = parts[2]
            if str(uid) != str(target_id):
                await query.answer("This menu is not for you!", show_alert=True)
                return

        if p.get("starter_summoned"): return
        name = parts[1]
        p.setdefault("characters", []).append(generate_char_instance(name))
        p["starter_summoned"] = True
        save_player(uid, p)
        await query.message.edit_caption(caption=f"✅ You chose **{name}**!")
    elif data == "manage_team": await manage_team(query, p)
    elif data.startswith("toggle_"):
        idx = int(data.split("_")[1])
        if idx < len(p["characters"]):
            char = p["characters"][idx]
            if any(tc['id'] == char['id'] for tc in p.get('team', [])):
                p['team'] = [tc for tc in p.get('team', []) if tc['id'] != char['id']]
            elif len(p.get('team', [])) < 3:
                if "team" not in p: p["team"] = []
                p['team'].append(char)
            save_player(uid, p); await manage_team(query, p)
    elif data == "save_team":
        await query.message.delete()
        await query.message.chat.send_message(f"Team saved! ({len(p.get('team', []))} pirates)")
    elif data == "show_missions":
        wins = p.get('explore_wins', 0)
        upcoming = [w for w in BOSS_MISSIONS.keys() if w > wins]
        m_text = f"📜 **MISSION**\n\nTarget: Defeat {min(upcoming) if upcoming else 'Max'} enemies.\nProgress: {wins}"
        await query.answer(m_text, show_alert=True)
    elif data.startswith("efight_"):
        npc_name = data.split("_", 1)[1]
        if not p.get('team'):
            await query.answer("Set your team first using /myteam!", show_alert=True); return
        bid = f"explore_{uid}"
        battles[bid] = {
            "p1_id": uid, "p2_id": "NPC",
            "p1_team": [generate_char_instance(c['name'], c.get('level', 1), p.get('equipped_fruit'), c.get('equipped_weapon')) for c in p['team']],
            "p2_team": [generate_char_instance(npc_name)], "p1_idx": 0, "p2_idx": 0,
            "p1_name": p['name'], "p2_name": npc_name, "turn_owner": "p1", "is_npc": True,
            "run_votes": set(), "last_move_time": time.time()
        }
        await run_battle_turn(query, bid, move_name=None, context=context)
    elif data.startswith("accept_"):
        parts = data.split("_"); p1_id, p2_id = parts[1], parts[2]
        if uid != p2_id:
            await query.answer("This challenge isn't for you!", show_alert=True); return
        p1, p2 = get_player(p1_id), get_player(p2_id); bid = f"{p1_id}_{p2_id}"
        starter = "p1" if p1['team'][0].get('spe', 0) >= p2['team'][0].get('spe', 0) else "p2"
        battles[bid] = {
            "p1_id": p1_id, "p2_id": p2_id,
            "p1_team": [generate_char_instance(c['name'], c.get('level', 1), p1.get('equipped_fruit'), c.get('equipped_weapon')) for c in p1['team']],
            "p2_team": [generate_char_instance(c['name'], c.get('level', 1), p2.get('equipped_fruit'), p2.get('equipped_weapon')) for c in p2['team']],
            "p1_idx": 0, "p2_idx": 0, "p1_name": p1['name'], "p2_name": p2['name'],
            "turn_owner": starter, "run_votes": set(), "last_move_time": time.time()
        }
        await run_battle_turn(query, bid, move_name=None, context=context)
    elif data.startswith("bmove|"):
        try: await query.answer()
        except: pass
        parts = data.split("|"); bid, move_name = parts[1], parts[2]; b = battles.get(bid)
        if not b: return
        current_turn_id = str(b['p1_id']) if b['turn_owner'] == "p1" else str(b.get('p2_id', "NPC"))
        if uid != current_turn_id and current_turn_id != "NPC":
            await query.answer("It's not your turn!", show_alert=True); return
        await run_battle_turn(query, bid, move_name, context=context)
    elif data.startswith("brun_"):
        bid = data.replace("brun_", ""); b = battles.get(bid)
        if not b or uid not in [str(b['p1_id']), str(b.get('p2_id'))]: return
        if b.get('is_npc'):
            try: await query.edit_message_caption(caption="🤝 You escaped safely.");
            except: await query.edit_message_text("🤝 You escaped safely.")
            if bid in battles: del battles[bid]
            # UPDATED: If escape, keep explore cooldown (conceptually battle finished by running)
            # Or if you want running to allow immediate explore, remove from pending:
            if uid in pending_explores: del pending_explores[uid]
            return
        b['run_votes'].add(uid)
        if len(b['run_votes']) >= 2:
            try: await query.edit_message_text("🤝 Both players decided to stop.");
            except: await query.edit_message_caption(caption="🤝 Both players decided to stop.");
            if bid in battles: del battles[bid]
        else: await query.answer("Waiting for the other player...", show_alert=True)
    elif data.startswith("bforfeit_"):
        bid = data.replace("bforfeit_", ""); b = battles.get(bid)
        if not b or uid not in [str(b['p1_id']), str(b.get('p2_id'))]: return
        name = b['p1_name'] if uid == str(b['p1_id']) else b['p2_name']
        try: await query.edit_message_text(f"🏳 {name} ran away!");
        except: await query.edit_message_caption(caption=f"🏳 {name} ran away!");
        if bid in battles: del battles[bid]
        if b.get('is_npc') and uid in pending_explores: del pending_explores[uid]
    elif data == "store_weapons": await handle_store_callback(query, "weapons")
    elif data == "store_fruits": await handle_store_callback(query, "fruits")
    elif data == "back_to_store":
        kb = [[InlineKeyboardButton("Weapons ⚔️", callback_data="store_weapons"), InlineKeyboardButton("Fruits 🍎", callback_data="store_fruits")], [InlineKeyboardButton("Close", callback_data="wheel_cancel")]]
        await query.edit_message_caption(caption="⚓️ **PIRATE STORE** ⚓️\n\nWelcome back. Choose a category.", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("confbuy|"):
        _, itype, iname = data.split("|")
        if itype == "weapon":
            cost = WEAPONS[iname]['cost']
            if p['berries'] >= cost:
                p['berries'] -= cost
                p.setdefault('weapons', []).append(iname)
                await query.answer(f"Bought {iname}!", show_alert=True)
            else: await query.answer("Not enough berries!", show_alert=True)
        else:
            cost = DEVIL_FRUITS[iname]['cost']
            if p['berries'] >= cost:
                p['berries'] -= cost
                p.setdefault('fruits', []).append(iname)
                await query.answer(f"Bought {iname}!", show_alert=True)
            else: await query.answer("Not enough berries!", show_alert=True)
        save_player(uid, p)
    elif data == "inv_weapons":
        txt = "⚔️ **YOUR WEAPONS** ⚔️\n\n"
        for w in p.get('weapons', []):
            txt += f"• {w}\n"
        kb = [[InlineKeyboardButton("Back", callback_data="back_inv")]]
        await query.edit_message_caption(caption=txt or "No weapons in treasury.", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "inv_fruits":
        txt = "🍎 **YOUR DEVIL FRUITS** 🍎\n\n"
        for f in p.get('fruits', []):
            txt += f"• {f}\n"
        kb = [[InlineKeyboardButton("Back", callback_data="back_inv")]]
        await query.edit_message_caption(caption=txt or "No fruits in treasury.", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "back_inv":
        await inventory_cmd(query, context, is_cb=True)
    elif data.startswith("wepattach|"):
        parts = data.split("|"); w_name, c_idx = parts[1], int(parts[2])
        if w_name in p.get('weapons', []):
            p['weapons'].remove(w_name)
            p['characters'][c_idx]['equipped_weapon'] = w_name
            save_player(uid, p); await query.edit_message_text(f"✅ Character **{p['characters'][c_idx]['name']}** now wields **{w_name}**! (Weapon consumed from inventory)")
        else:
            await query.answer("You don't own this weapon anymore!", show_alert=True)
    elif data.startswith("usetoken|"):
        c_idx = int(data.split("|")[1])
        if p.get('tokens', 0) > 0:
            p['tokens'] -= 1
            p['characters'][c_idx]['level'] = p['characters'][c_idx].get('level', 1) + 1
            save_player(uid, p)
            await query.edit_message_text(f"✨ Success! **{p['characters'][c_idx]['name']}** has reached Level {p['characters'][c_idx]['level']}!")
        else:
            await query.answer("No tokens left!", show_alert=True)
    elif data == "char_wheel": await wheel_options(query, "Character")
    elif data == "res_wheel": await wheel_options(query, "Resource")
    elif data.startswith("wheel_1"):
        await handle_wheel(query, p, 1, data.split("_")[2])
    elif data.startswith("wheel_5"):
        await handle_wheel(query, p, 5, data.split("_")[2])
    elif data == "wheel_cancel": await query.message.delete()
    elif data == "wheel_prob":
        await query.answer("📊 PROBABILITIES\nYamato: 2%\nKid: 2%\nFruits: 5%\nOthers: Balanced", show_alert=True)
    elif data.startswith("equip_"):
        fname = data.split("_", 1)[1]
        if fname in p.get('fruits', []):
            p['fruits'].remove(fname)
            p['equipped_fruit'] = fname
            save_player(uid, p)
            await query.answer(f"Equipped {fname}! (Consumed)", show_alert=True)
            await query.message.edit_caption(caption=f"✨ Entire team buffed by {fname}!")
        else:
            await query.answer("Fruit no longer in inventory!", show_alert=True)

# =====================
# WHEEL LOGIC
# =====================

async def wheel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    desc = "🎡 **PIRATE WHEELS** 🎡\n\nChoose the wheel you want to spin!"
    kb = [[InlineKeyboardButton("Character Wheel 👤", callback_data="char_wheel")], [InlineKeyboardButton("Resource Wheel 💎", callback_data="res_wheel")]]
    await update.message.reply_video(WHEEL_VIDEO, caption=desc, reply_markup=InlineKeyboardMarkup(kb))

async def wheel_options(query, type_name):
    if type_name == "Character":
        cost1, cost5 = "150 🍀", "500 🍀"
        data_c1, data_c5 = "wheel_1_Character", "wheel_5_Character"
    else:
        cost1, cost5 = "100 🍀", "400 🍀"
        data_c1, data_c5 = "wheel_1_Resource", "wheel_5_Resource"

    desc = f"🎡 {type_name.upper()} WHEEL 🎡\n\n1x Pull: {cost1}\n5x Pull: {cost5}"
    kb = [[InlineKeyboardButton("1x Pull", callback_data=data_c1), InlineKeyboardButton("5x Pull", callback_data=data_c5)], [InlineKeyboardButton("Back", callback_data="wheel_cancel"), InlineKeyboardButton("Probability", callback_data="wheel_prob")]]
    await query.edit_message_media(InputMediaVideo(WHEEL_VIDEO, caption=desc), reply_markup=InlineKeyboardMarkup(kb))

async def handle_wheel(query, p, count, wheel_type):
    uid = str(p['user_id'])
    if wheel_type == "Character":
        cost = 150 if count == 1 else 500
    else:
        cost = 100 if count == 1 else 400

    if p.get("clovers", 0) < cost:
        await query.answer("Not enough 🍀 Clovers!", show_alert=True)
        return

    p["clovers"] -= cost
    save_player(uid, p) # Instant RAM update

    results = []
    special_anim = None

    if wheel_type == "Character":
        for _ in range(count):
            roll = random.random()
            if roll < 0.02:
                res = "Yamato"
                special_anim = YAMATO_SUMMON_ANIM
            elif roll < 0.04:
                res = "Eustass Kid"
                special_anim = KID_SUMMON_ANIM
            else:
                others = [c for c in CHARACTERS.keys() if c not in ["Yamato", "Eustass Kid"]]
                res = random.choice(others)

            char_data = CHARACTERS[res]
            # Use symbol only from your RARITY_STYLES
            rarity = char_data.get('rarity', 'Common')
            symbol = RARITY_STYLES.get(rarity, {}).get("symbol", "🔘")

            existing = next((c for c in p["characters"] if c["name"] == res), None)
            if existing:
                existing["level"] = existing.get("level", 1) + 1
                results.append(f"• {res} {symbol} (Lv.{existing['level']})")
            else:
                p["characters"].append(generate_char_instance(res))
                results.append(f"• {res} {symbol} (New!)")
    else:
        for _ in range(count):
            roll = random.random()
            if roll < 0.05:
                fruit_name = random.choice(list(DEVIL_FRUITS.keys()))
                p.setdefault("fruits", []).append(fruit_name)
                results.append(f"🍎 {fruit_name} (NEW!)")
            elif roll < 0.15:
                clovers = random.randint(10, 50)
                p['clovers'] += clovers
                results.append(f"🍀 {clovers} Clovers")
            else:
                berries = random.randint(5000, 15000)
                p['berries'] += berries
                results.append(f"🍇 {berries} Berries")

    save_player(uid, p) # Background cloud save
    res_text = f"🎰 **{wheel_type.upper()} RESULTS**:\n\n" + "\n".join(results)

    final_anim = special_anim if special_anim else SUMMON_ANIMATION
    try:
        await query.edit_message_media(InputMediaVideo(final_anim, caption=res_text, parse_mode="Markdown"), reply_markup=None)
    except Exception:
        # Fallback for images or if video fails
        await query.message.reply_text(res_text, parse_mode="Markdown")


# =====================
# INSPECT & FRUIT
# =====================

async def inspect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/inspect [Name]`")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    name = " ".join(context.args).title()
    p = get_player(update.effective_user.id)

    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return

    if name in WEAPONS:
        w = WEAPONS[name]
        text = (f"➥Name: {name}\n➥Rarity: {w['rarity']}\n➥Attack: {w['atk_range']}\n"
                f"➥Critical chance: {w['crit']}\n➥Accuracy: {w['acc']}\n"
                f"➥Special attack: {w['spec']}\n➥Rank requirement: {w['lvl']}\n\n➥ Cost: {w['cost']}🍇")
        img = w['img']
        kb = [[InlineKeyboardButton("In Stock (Check /store) 🛒", callback_data="go_shop")]]
        await update.message.reply_photo(img, caption=text, reply_markup=InlineKeyboardMarkup(kb))
    elif name in DEVIL_FRUITS or any(k in name for k in ["Sand", "Shadow", "Barrier", "Munch", "Gum"]):
        search_name = name
        if "Sand" in name: search_name = "Sand Sand Fruit"
        elif "Shadow" in name: search_name = "Shadow Shadow Fruit"
        elif "Barrier" in name: search_name = "Barrier Barrier Fruit"
        elif "Munch" in name: search_name = "Munch Munch Fruit"
        elif "Gum" in name: search_name = "Gum Gum Fruit"

        if search_name not in DEVIL_FRUITS:
            await update.message.reply_text("Devil fruit not found.")
            return

        f = DEVIL_FRUITS[search_name]; kb = []
        if search_name in p.get("fruits", []):
            kb.append([InlineKeyboardButton("Equip (Consume) ⚡️", callback_data=f"equip_{search_name}")])
        else:
            kb.append([InlineKeyboardButton("In Stock (Check /store) 🛒", callback_data="go_shop")])

        await update.message.reply_photo(f['img'], caption=f['text'], reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text("Item not found.")

# =====================
# PROFILE & INV
# =====================

async def myprofile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    user_id = update.effective_user.id; p = get_player(user_id, update.effective_user.first_name)
    lvl = p.get('level', 1); exp = p.get('exp', 0); req = get_required_player_exp(lvl)
    wins = p.get('wins', 0); losses = p.get('losses', 0); total = wins + losses
    win_ratio = (wins / total * 100) if total > 0 else 0
    start_date = p.get('start_date', 'Unknown')
    prof = f"⦿ Name: {p.get('name')}\n⦿ ID: {user_id}\n⦿ Level: {lvl}\n🌟 EXP: {exp}/{req}\n⦿ Bounty฿: {p.get('bounty', 0):,}\n📅 Journey Started: {start_date}\n▰▱▱▱▱▱▱▱▱▱\n[ Ｓ Ｔ Ａ Ｔ Ｓ ]\n➜ 🏆 Victory: {wins}\n➜ 🏳️ Defeat: {losses}\n➜ 📊 Win Ratio: {win_ratio:.1f}%\n➜ ⚔️ Total Wins on explore: {p.get('explore_wins', 0)}"
    try:
        photos = await context.bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0: await update.message.reply_photo(photos.photos[0][-1].file_id, caption=f"🏴‍☠️ **BOUNTY POSTER** 🏴‍☠️\n\n{prof}", parse_mode="Markdown")
        else: await update.message.reply_text(prof)
    except: await update.message.reply_text(prof)

async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, is_cb=False):
    uid = update.effective_user.id if not is_cb else update.callback_query.from_user.id
    
    # Check Registration
    if not load_player(uid):
        if is_cb:
            await update.callback_query.answer("⚠️ You must start your journey first!", show_alert=True)
        else:
            await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    p = get_player(uid)
    lvl = p.get('level', 1); exp = p.get('exp', 0); req = get_required_player_exp(lvl)
    inv = f"╭━━━━━━━━━━━━━━━╮\n✦    📦 INVENTORY 📦     ✦\n╰━━━━━━━━━━━━━━━╯\n\nɴᴀᴍᴇ 📛: {p['name']}\nDevil fruit🪻: {p.get('equipped_fruit') or 'None'}\n━━━━━━━━━━━━━━━━━━━\nBerry🍇: {p.get('berries', 0):,}\nClover🍀: {p.get('clovers', 0):,}\n━━━━━━━━━━━━━━━━━━━\n\n━━━━━━━━━━━━━━━━━━━\nʟᴇᴠᴇʟ ⭐️: {lvl}\nᴇxᴘ 📈: {exp}/{req}\nʟᴠʟ ᴜᴘ ᴛᴏᴋᴇɴ 🧩: {p.get('tokens', 0)}\nᴋɪʟʟ ᴄᴏᴜɴᴛ 🩸: {p.get('kill_count', 0)}\n"
    kb = [[InlineKeyboardButton("Weapons ⚔️", callback_data="inv_weapons"), InlineKeyboardButton("Fruits 🍎", callback_data="inv_fruits")]]

    if is_cb:
        await update.callback_query.edit_message_caption(caption=inv, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_photo(INVENTORY_IMAGE, caption=inv, reply_markup=InlineKeyboardMarkup(kb))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return
    uid = str(update.effective_user.id)
    p = load_player(uid)
    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    p = get_player(update.effective_user.id)
    if not context.args: return
    name = " ".join(context.args).title()
    char_obj = next((c for c in p.get('characters', []) if c['name'] == name), None)
    if not char_obj and name in CHARACTERS:
        await update.message.reply_photo(IMAGE_URLS.get(name, IMAGE_URLS["Default"]), caption=get_stats_text(name, p.get('equipped_fruit')))
    elif char_obj:
        await update.message.reply_photo(IMAGE_URLS.get(name, IMAGE_URLS["Default"]), caption=get_stats_text(char_obj, p.get('equipped_fruit')))

async def mycollection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Instant RAM Lookup
    p = get_player(user_id)
    if not p:
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    if p and p.get('is_locked'):
    # This works for both messages and button clicks!
        await update.effective_message.reply_text("❌ Your account is locked. Contact admin.")
        return


    txt = "📜 **YOUR PIRATE FLEET** 📜\n━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not p.get('characters'):
        txt += "_No pirates recruited yet._"
    else:
        for c in p['characters']:
            name = c['name']
            lvl = c.get('level', 1)
            
            # Get rarity from master data and map to symbol
            char_master = CHARACTERS.get(name, {})
            rarity_type = char_master.get('rarity', 'Common')
            symbol = RARITY_STYLES.get(rarity_type, {}).get("symbol", "🔘")
            
            wep = f" | ⚔️ {c['equipped_weapon']}" if c.get('equipped_weapon') else ""
            
            # Format: Name Symbol (Lv.X)
            txt += f"• **{name}** {symbol} (Lv.{lvl}){wep}\n"

    await update.message.reply_text(txt, parse_mode="Markdown")


async def sendberry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    if not update.message.reply_to_message or not context.args: return
    try:
        amount = int(context.args[0]); sender_id = update.effective_user.id; receiver_id = update.message.reply_to_message.from_user.id; sender = get_player(sender_id)
        if sender.get('berries', 0) < amount or amount <= 0: return
        receiver = get_player(receiver_id, update.message.reply_to_message.from_user.first_name); sender['berries'] -= amount; receiver['berries'] += amount
        save_player(sender_id, sender); save_player(receiver_id, receiver); await update.message.reply_text(f"✅ Sent 🍇{amount:,} to {receiver['name']}")
    except: pass

async def sendclovers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Registration
    if not load_player(update.effective_user.id):
        await update.message.reply_text("⚠️ You must start your journey first! Use /start.")
        return

    if not update.message.reply_to_message or not context.args: return
    try:
        amount = int(context.args[0]); sender_id = update.effective_user.id; receiver_id = update.message.reply_to_message.from_user.id; sender = get_player(sender_id)
        if sender.get('clovers', 0) < amount or amount <= 0: return
        receiver = get_player(receiver_id, update.message.reply_to_message.from_user.first_name); sender['clovers'] -= amount; receiver['clovers'] += amount
        save_player(sender_id, sender); save_player(receiver_id, receiver); await update.message.reply_text(f"✅ Sent 🍀{amount:,} to {receiver['name']}")
    except: pass

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only work in Private Chat (DM)
    if update.effective_chat.type != constants.ChatType.PRIVATE:
        return # Ignore if in a group

    user_id = update.effective_user.id
    if not get_player(user_id):
        await update.message.reply_text("⚠️ Start your journey first with /start.")
        return

    keyboard = [['Explore 🧭'], ['Close ❌']]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🎮 **MENU OPENED** (DM Only)",
        reply_markup=markup,
        parse_mode="Markdown"
    )

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only work in Private Chat (DM)
    if update.effective_chat.type != constants.ChatType.PRIVATE:
        return

    await update.message.reply_text(
        "🔒 **MENU CLOSED**",
        reply_markup=ReplyKeyboardRemove()
    )

async def handle_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only process button text in Private Chat (DM)
    if update.effective_chat.type != constants.ChatType.PRIVATE:
        return

    text = update.message.text
    
    if text == "Explore 🧭":
        # Call your existing explore_cmd function directly
        return await explore_cmd(update, context)
        
    elif text == "Close ❌":
        return await close_cmd(update, context)

async def unstuck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    p = load_player(uid)
    
    if not p:
        return

    # 1. Security Check
    if p.get('is_locked'):
        await update.message.reply_text("❌ You are currently locked by Marine Security. Contact Admin.")
        return

    # 2. State Reset
    p['last_interaction'] = 0 
    p['verification_active'] = False
    
    # 3. THE FIX: Clear the exploration/battle lock
    if uid in pending_explores:
        del pending_explores[uid]
    
    # Optional: Clear active battle session if it exists
    for bid in list(battles.keys()):
        if uid in bid:
            del battles[bid]
    
    # Save changes
    save_player(uid, p)
    
    await update.message.reply_text("🛠 **System Reset!** Your session and battle timers have been unstuck.")
    
    # Admin Log
    await context.bot.send_message(
        chat_id="-1003855697962",
        text=f"🛠 **UNSTUCK:** User `{p.get('name', 'Unknown')}` (`{uid}`) reset their state.",
        parse_mode="Markdown"
    )


async def auto_detector_job(context: ContextTypes.DEFAULT_TYPE):
    current_time = time.time()
    
    # Iterate through a copy of items to avoid runtime errors if dict changes
    for uid, p in list(player_cache.items()):
        last_act = p.get('last_interaction', 0)
        
        # Check: Active in last 5 mins (300s) AND not already locked/verifying
        if (current_time - last_act < 300) and not p.get('is_locked') and not p.get('verification_active'):
            try:
                await trigger_security_check(uid, context)
                # Small sleep to prevent hitting Telegram Flood Limits
                await asyncio.sleep(0.1) 
            except Exception as e:
                logging.error(f"Security check failed for {uid}: {e}")

async def get_file_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else (update.message.video.file_id if update.message.video else None)
    if fid: await update.message.reply_text(f"File ID: `{fid}`", parse_mode="Markdown")

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Start Journey"), 
        BotCommand("wheel", "Spin Wheel"), 
        BotCommand("explore", "Explore Grand Line"),
        BotCommand("myteam", "Manage Team"), 
        BotCommand("battle", "Challenge Player"), 
        BotCommand("stats", "Character Stats"),
        BotCommand("open", "Open Menu"), 
        BotCommand("close", "Close Menu"),
        BotCommand("mycollection", "View Crew"), 
        BotCommand("inventory", "Treasury"),
        BotCommand("myprofile", "Player Profile"), 
        BotCommand("unlock", "Unlock Player"),
        BotCommand("unstuck", "Reset Stuck Session"), # FIXED: Corrected spelling
        BotCommand("sendberry", "Gift Berries"), 
        BotCommand("sendclovers", "Gift Clovers"), 
        BotCommand("inspect", "Fruit/Weapon Info"),
        BotCommand("store", "Open Store"), 
        BotCommand("buy", "Buy Items"), 
        BotCommand("use", "Use Items"),
        BotCommand("referral", "Invite Friends")
    ])

# =====================
# BOT EXECUTION
# =====================
TOKEN = os.getenv("BOT_TOKEN")

if __name__ == "__main__":

    if not TOKEN:
        print("❌ Error: BOT_TOKEN is missing!")
        exit(1)
    if not MONGO_URI:
        print("❌ Error: MONGO_URI is missing!")
        exit(1)

    # Building the application with JobQueue enabled
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # Registering Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("open", open_cmd))
    application.add_handler(CommandHandler("close", close_cmd))
    application.add_handler(CommandHandler("wheel", wheel_cmd))
    application.add_handler(CommandHandler("explore", explore_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("unstuck", unstuck_cmd)) # FIXED: Function name
    application.add_handler(CommandHandler("unlock", unlock_cmd))   # ADDED: Admin Unlock
    application.add_handler(CommandHandler("inspect", inspect_cmd))
    application.add_handler(CommandHandler("mycollection", mycollection))
    application.add_handler(CommandHandler("inventory", inventory_cmd))
    application.add_handler(CommandHandler("myprofile", myprofile_cmd))
    application.add_handler(CommandHandler("sendberry", sendberry_cmd))
    application.add_handler(CommandHandler("sendclovers", sendclovers_cmd))
    application.add_handler(CommandHandler("myteam", myteam))
    application.add_handler(CommandHandler("battle", battle_request))
    application.add_handler(CommandHandler("store", store_cmd))
    application.add_handler(CommandHandler("buy", buy_cmd))
    application.add_handler(CommandHandler("use", use_cmd))
    application.add_handler(CommandHandler("referral", referral_cmd))
    
    # Generic Message Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_click))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, get_file_ids))
    application.add_handler(CallbackQueryHandler(main_callback))

    # START SECURITY SCHEDULER (Every 15 Minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(auto_detector_job, interval=900, first=10)

    print("🏴‍☠️ Pirate Bot is sailing with Marine Security Active!...")
    application.run_polling()

