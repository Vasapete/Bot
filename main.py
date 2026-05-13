import sys
import subprocess
import pkgutil

REQUIRED = ["aiogram", "aiohttp", "python-dotenv", "psutil"]
MODULE_NAME = {"python-dotenv": "dotenv"}

for pkg in REQUIRED:
    mod = MODULE_NAME.get(pkg, pkg)
    if pkgutil.find_loader(mod.replace("-", "_")) is None:
        print(f"[AUTO-INSTALL] Installing {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

import os
import asyncio
import datetime as dt
from typing import List, Dict, Any, Optional, Set

import aiohttp
import psutil
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    CallbackQuery,
    BufferedInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError, TelegramForbiddenError, TelegramRetryAfter
from aiohttp import ClientTimeout
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import logging

logging.basicConfig(level=logging.INFO)
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN in .env!")

USD_PER_ROBUX = 0.0038
USD_TO_CAD = 1.35
OWNER_ID = 1415037406
DEFAULT_LANG = "en"
REQUIRED_CHANNEL = "@RBLXSnews"
BROADCAST_HISTORY = []

bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

START_TIME = dt.datetime.now(dt.timezone.utc)
TOTAL_COMMANDS = 0
USER_LAST_COMMAND: Dict[int, str] = {}
USER_LAST_ARGS: Dict[int, str] = {}
USER_COMMAND_COUNT: Dict[int, int] = {}
CHAT_IDS: Set[int] = set()
USER_IDS: Set[int] = set()
USER_LANG: Dict[int, str] = {}


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def is_member(bot: Bot, user_id: int, channel: str) -> bool:
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

def parse_ids(raw: str, max_count=20):
    ids: List[int] = []
    for p in raw.replace(",", " ").split():
        if p.isdigit():
            ids.append(int(p))
        if len(ids) >= max_count:
            break
    return ids


def parse_iso8601(s: str) -> dt.datetime:
    s = s.replace("Z", "+00:00")

    if "." in s:
        before_tz, tz = (s.split("+", 1) + [""])[:2] if "+" in s else (s.split("-", 1) + [""])
        if "+" in s:
            sign = "+"
        elif "-" in s[19:]:
            sign = "-"
        else:
            sign = ""
        if sign:
            before_tz, tz = s.split(sign, 1)
        else:
            before_tz, tz = s, ""
        if "." in before_tz:
            left, frac = before_tz.split(".", 1)
            if len(frac) == 1:
                frac = frac + "00"
            elif len(frac) == 2:
                frac = frac + "0"
            before_tz = f"{left}.{frac}"
        s = before_tz + (sign + tz if sign else "")
    return dt.datetime.fromisoformat(s)



def detect_language(code: Optional[str]) -> str:
    if not code:
        return DEFAULT_LANG
    c = code.lower()
    if c.startswith("ru"):
        return "ru"
    return "en"


def get_lang(message: Message) -> str:
    if not message.from_user:
        return DEFAULT_LANG
    uid = message.from_user.id
    if uid in USER_LANG:
        return USER_LANG[uid]
    lang = detect_language(getattr(message.from_user, "language_code", None))
    USER_LANG[uid] = lang
    return lang


def get_lang_cb(cb: CallbackQuery) -> str:
    if not cb.from_user:
        return DEFAULT_LANG
    uid = cb.from_user.id
    if uid in USER_LANG:
        return USER_LANG[uid]
    lang = detect_language(getattr(cb.from_user, "language_code", None))
    USER_LANG[uid] = lang
    return lang


def get_lang_by_user_id(uid: int) -> str:
    return USER_LANG.get(uid, DEFAULT_LANG)


def format_uptime(seconds: float) -> str:
    secs = int(seconds)
    days = secs // 86400
    secs %= 86400
    hours = secs // 3600
    secs %= 3600
    minutes = secs // 60
    secs %= 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def track_command(func):
    import functools
    @functools.wraps(func)  # ← preserves function signature for aiogram
    async def wrapper(message: Message, command: CommandObject = None, **kwargs):
        global TOTAL_COMMANDS

        user = message.from_user
        if not user:
            return

        user_id = user.id

        is_in = await is_member(bot, user_id, REQUIRED_CHANNEL)

        if not is_in:
            channel_clean = REQUIRED_CHANNEL.replace("@", "")
            join_link = f"https://t.me/{channel_clean}"
            return await message.answer(
                f"👋 <b>You must join our Telegram channel to use this bot.</b>\n\n"
                f"🔗 Channel: {REQUIRED_CHANNEL}\n"
                f"<a href=\"{join_link}\">👉 Join Channel</a>"
            )

        cmd_name = func.__name__
        cmd_args = (command.args or "").strip() if command else ""

        USER_LAST_COMMAND[user_id] = cmd_name
        USER_LAST_ARGS[user_id] = cmd_args
        USER_COMMAND_COUNT[user_id] = USER_COMMAND_COUNT.get(user_id, 0) + 1

        TOTAL_COMMANDS += 1
        CHAT_IDS.add(message.chat.id)
        USER_IDS.add(user_id)

        return await func(message, command, **kwargs)  # ← pass kwargs (includes state)

    return wrapper



class RobloxAPI:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = ClientTimeout(total=15)
        self.cookie = os.getenv("ROBLOX_COOKIE", "")

    async def ensure(self):
        if self.session is None or self.session.closed:
            headers = {"User-Agent": "Mozilla/5.0 (RBLXScanBot/1.0)"}
            if self.cookie:                                         
                headers["Cookie"] = f".ROBLOSECURITY={self.cookie}" 
            else:                                                   
                print("⚠️ WARNING: ROBLOX_COOKIE is not set!")     
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=headers
            )
        return self.session
        
    async def req(self, method: str, url: str, **kwargs):
        session = await self.ensure()
    
        for attempt in range(3):
            try:
                async with session.request(method, url, **kwargs) as r:
                    try:
                        data = await r.json()
                    except Exception:
                        data = await r.text()
    
                    if 200 <= r.status < 300:
                        return data
    
                    if r.status == 429:
                        await asyncio.sleep(0.8 + attempt * 0.5)
                        continue
    
                    if r.status == 403:
                        return None
    
                    raise RuntimeError(f"HTTP {r.status}: {data}")
    
            except aiohttp.ClientConnectorDNSError as e:
                logging.warning(f"DNS error on attempt {attempt + 1} for {url}: {e}")
                if attempt == 2:
                    raise RuntimeError(f"DNS resolution failed after 3 attempts: {url}") from e
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
                # Force recreate session on DNS error
                if self.session and not self.session.closed:
                    await self.session.close()
                self.session = None
                session = await self.ensure()
                continue
    
            except aiohttp.ClientError as e:
                if attempt == 2:
                    raise RuntimeError(f"Connection error: {e}") from e
                await asyncio.sleep(1)
                continue
    
        raise RuntimeError(f"HTTP 429 after retries: {url}")


    async def get_user_by_username(self, username: str):
        url = "https://users.roblox.com/v1/usernames/users"
        payload = {"usernames": [username], "excludeBannedUsers": False}
        data = await self.req("POST", url, json=payload)
        if not data or not data.get("data"):
            return None
        return data["data"][0]

    async def get_user_by_id(self, user_id: int):
        return await self.req("GET", f"https://users.roblox.com/v1/users/{user_id}")

    async def get_user_details_by_username(self, username: str):
        base = await self.get_user_by_username(username)
        if not base:
            return None
        return await self.get_user_by_id(base["id"])

    async def get_users_by_ids(self, ids: List[int]):
        if not ids:
            return {}
        data = await self.req(
            "POST",
            "https://users.roblox.com/v1/users",
            json={"userIds": ids},
        )
        out: Dict[int, Any] = {}
        if data and data.get("data"):
            for x in data["data"]:
                out[x["id"]] = x
        return out

    async def search_displayname(self, name: str, limit=10):
        from urllib.parse import urlencode
        url = f"https://users.roblox.com/v1/users/search?{urlencode({'keyword': name, 'limit': limit})}"
        data = await self.req("GET", url)
        return data.get("data", []) if data else []

    async def get_presence(self, ids: List[int]):
        if not ids:
            return None
        return await self.req(
            "POST",
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": ids},
        )

    async def get_user_thumbnail(self, user_id: int, ttype: str) -> Optional[str]:
        base = "https://thumbnails.roblox.com/v1/users"
        if ttype == "avatar":
            path = "avatar"
            size = "720x720"
        elif ttype == "headshot":
            path = "avatar-headshot"
            size = "720x720"
        else:
            path = "avatar-bust"
            size = "352x352"
        url = f"{base}/{path}?userIds={user_id}&size={size}&format=Png&isCircular=false"
        try:
            data = await self.req("GET", url)
        except RuntimeError:
            return None
        if not data or not data.get("data"):
            return None
        return data["data"][0]["imageUrl"]

    async def get_asset_icon(self, aid: int) -> Optional[str]:
        url = (
            "https://thumbnails.roblox.com/v1/assets"
            f"?assetIds={aid}&size=512x512&format=Png&isCircular=false"
        )
        data = await self.req("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0]["imageUrl"]
    
    async def get_asset_info(self, aid: int):
        try:
            data = await self.req(
                "GET",
                f"https://economy.roblox.com/v2/assets/{aid}/details",
            )
            if data:
                return data
        except RuntimeError:
            pass
    
        try:
            data = await self.req(
                "GET",
                f"https://catalog.roblox.com/v1/catalog/items/details",
                json={"items": [{"itemType": "Asset", "id": aid}]},
                method="POST"
            )
            if data and data.get("data"):
                return data["data"][0]
        except RuntimeError:
            pass
    
        return None

    async def get_group_by_id(self, gid: int):
        return await self.req("GET", f"https://groups.roblox.com/v1/groups/{gid}")

    async def search_group_by_name(self, name: str, limit=10):
        from urllib.parse import urlencode
        url = f"https://groups.roblox.com/v1/groups/search?{urlencode({'keyword': name, 'limit': limit})}"
        data = await self.req("GET", url)
        return data.get("data", []) if data else []

    async def get_user_groups(self, uid: int):
        data = await self.req(
            "GET",
            f"https://groups.roblox.com/v1/users/{uid}/groups/roles",
        )
        if not data:
            return []
        return data.get("data", [])

    async def get_friends(self, uid: int):
        data = await self.req(
            "GET",
            f"https://friends.roblox.com/v1/users/{uid}/friends?limit=50",
        )
        return data.get("data", []) if data else []

    async def get_followers(self, uid: int):
        data = await self.req(
            "GET",
            f"https://friends.roblox.com/v1/users/{uid}/followers?limit=50",
        )
        return data.get("data", []) if data else []

    async def get_followings(self, uid: int):
        data = await self.req(
            "GET",
            f"https://friends.roblox.com/v1/users/{uid}/followings?limit=50",
        )
        return data.get("data", []) if data else []

    async def get_collectibles(self, uid: int):
        from urllib.parse import urlencode

        base = f"https://inventory.roblox.com/v1/users/{uid}/assets/collectibles"
        items = []
        cursor = None

        while True:
            params = {"limit": 100, "sortOrder": "Asc"}
            if cursor:
                params["cursor"] = cursor

            try:
                data = await self.req("GET", base + "?" + urlencode(params))
            except RuntimeError as e:
                # Inventory private → do not crash
                if "permissions" in str(e) or "403" in str(e):
                    return None
                raise

            if not data:
                break

            items.extend(data.get("data", []))
            cursor = data.get("nextPageCursor")

            if not cursor:
                break

        return items


    async def get_username_history(self, uid: int, limit: int = 50):
        from urllib.parse import urlencode
        url = f"https://users.roblox.com/v1/users/{uid}/username-history?{urlencode({'limit': limit, 'sortOrder': 'Desc'})}"
        return await self.req("GET", url)

    async def user_owns_asset(self, uid: int, asset_id: int):
        from urllib.parse import urlencode
        url = f"https://inventory.roblox.com/v1/users/{uid}/items/asset/{asset_id}?{urlencode({'limit': 1})}"
        data = await self.req("GET", url)
        if not data:
            return None
        arr = data.get("data", [])
        return len(arr) > 0

    async def get_badge_awarded_date(self, uid: int, badge_id: int):
        from urllib.parse import urlencode
        url = f"https://badges.roblox.com/v1/users/{uid}/badges/awarded-dates?{urlencode({'badgeIds': badge_id})}"
        return await self.req("GET", url)


roblox = RobloxAPI()

ROLI_SESSION: Optional[aiohttp.ClientSession] = None
ROLI_ITEMS_CACHE: Optional[Dict[str, list]] = None


async def roli_ensure():
    global ROLI_SESSION
    if ROLI_SESSION is None or ROLI_SESSION.closed:
        ROLI_SESSION = aiohttp.ClientSession(
            timeout=ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (RBLXScanBot/1.0)"}
        )
    return ROLI_SESSION


async def roli_get(url: str):
    s = await roli_ensure()
    async with s.get(url) as r:
        try:
            data = await r.json(content_type=None)
        except Exception:
            data = None
        if r.status != 200:
            raise RuntimeError(f"Rolimons HTTP {r.status}: {data}")
        return data


async def roli_get_items():
    global ROLI_ITEMS_CACHE
    if ROLI_ITEMS_CACHE is not None:
        return ROLI_ITEMS_CACHE
    data = await roli_get("https://api.rolimons.com/items/v2/itemdetails")
    if not data or "items" not in data:
        ROLI_ITEMS_CACHE = {}
    else:
        ROLI_ITEMS_CACHE = data["items"]
    return ROLI_ITEMS_CACHE


async def compose_limiteds_text(uid: int, lang: str) -> str:
    user = await roblox.get_user_by_id(uid)
    if not user:
        return "Пользователь не найден." if lang == "ru" else "User not found."

    name = user.get("name", str(uid))

    items = await roblox.get_collectibles(uid)

    if items is None:
        if lang == "ru":
            return f"🔒 Инвентарь игрока {esc(name)} закрыт. Нельзя просмотреть лимитки."
        return f"🔒 {esc(name)} has a private inventory. Cannot view limiteds."

    if len(items) == 0:
        if lang == "ru":
            return f"У {esc(name)} нет ограниченных предметов."
        return f"{esc(name)} has no limiteds."

    roli_err = None
    roli_items = {}

    try:
        roli_items = await roli_get_items()
    except Exception as e:
        roli_err = str(e)

    total_rap = 0
    total_value = 0
    lines = []

    for it in items:
        aid = it.get("assetId")
        aname = esc(it.get("name", "Unknown"))
        rap = it.get("recentAveragePrice") or 0
        total_rap += rap

        value = rap
        if roli_items:
            arr = roli_items.get(str(aid))
            if arr:
                rv = arr[3]
                if rv and rv > 0:
                    value = rv

        total_value += value

        lines.append(
            f"• <a href=\"https://www.rolimons.com/item/{aid}\">{aname}</a> — "
            f"RAP: <code>{rap:,}</code> | Value: <code>{value:,}</code>"
        )

    if lang == "ru":
        header = (
            f"💼 <b>Лимитки игрока {esc(name)}</b>\n"
            f"Всего предметов: <code>{len(items)}</code>\n"
            f"Суммарный RAP: <code>{total_rap:,}</code>\n"
            f"Суммарный Value: <code>{total_value:,}</code>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Профиль на Rolimons</a>\n"
        )
        if roli_err:
            header += f"\n⚠️ Rolimons не ответил: <code>{esc(roli_err)}</code>\n"
    else:
        header = (
            f"💼 <b>Limiteds of {esc(name)}</b>\n"
            f"Total items: <code>{len(items)}</code>\n"
            f"Total RAP: <code>{total_rap:,}</code>\n"
            f"Total Value: <code>{total_value:,}</code>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Rolimons profile</a>\n"
        )
        if roli_err:
            header += f"\n⚠️ Rolimons issue: <code>{esc(roli_err)}</code>\n"

    return header + "\n" + "\n".join(lines)


def user_profile_keyboard(uid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Roblox Profile",
                    url=f"https://www.roblox.com/users/{uid}/profile"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Rolimons Profile",
                    url=f"https://www.rolimons.com/player/{uid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💼 Limiteds",
                    callback_data=f"roli_stats:{uid}"
                )
            ],
        ]
    )


@dp.message(Command("start"))
@track_command
async def cmd_start(message, command: CommandObject):
    lang = get_lang(message)
    if lang == "ru":
        text = (
            "🎮 <b>RS • RBLXScan</b>\n"
            "Быстрый просмотр данных Roblox: профили, лимитки, Rolimons, группы и другое.\n\n"
            "⚙️ Основные команды:\n"
            "/user <code>имя</code>\n→ Профиль пользователя\n\n"
            "/limiteds <code>имя</code>\n→ Все лимитки с RAP/Value\n\n"
            "/rolimons <code>имя</code>\n→ Статистика Rolimons\n\n"
            "Полный список: /help\n"
            "Язык: /language"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧑‍🔧 Команды", callback_data="help_open")],
                [
                    InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
                    InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
                ],
            ]
        )
    else:
        text = (
            "🎮 <b>RS • RBLXScan</b>\n"
            "Fast Roblox lookup: profiles, limiteds, Rolimons, groups and more.\n\n"
            "⚙️ Core commands:\n"
            "/user <code>username</code>\n→ View user profile\n\n"
            "/limiteds <code>username</code>\n→ All limiteds with RAP/Value\n\n"
            "/rolimons <code>username</code>\n→ Rolimons stats\n\n"
            "Full list: /help\n"
            "Language: /language"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧑‍🔧 Help & Commands", callback_data="help_open")],
                [
                    InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
                    InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
                ],
            ]
        )
    await message.answer(text, reply_markup=kb)


@dp.message(Command("help"))
@track_command
async def cmd_help(message, command: CommandObject):
    lang = get_lang(message)
    if lang == "ru":
        text = (
            "🧑‍🔧 <b>Полный список команд</b>\n\n"
            "/user &lt;Имя&gt;\n→ Показать детали профиля по имени\n\n"
            "/id &lt;UserID&gt;\n→ Показать детали профиля по ID\n\n"
            "/username &lt;Имя&gt;\n→ Проверить, занят ли юзернейм\n\n"
            "/displayname &lt;Имя&gt;\n→ Найти пользователей по display name\n\n"
            "/copyid &lt;Имя&gt;\n→ Быстро получить ID пользователя\n\n"
            "/idtousername &lt;ID1 ID2 ...&gt;\n→ Конвертировать ID → имена\n\n"
            "/banned &lt;ID1 ID2 ...&gt;\n→ Проверить, забанены ли пользователи\n\n"
            "/accountage &lt;Имя&gt;\n→ Возраст аккаунта в днях и годах\n\n"
            "/lastonline &lt;Имя&gt;\n→ Последний онлайн и локация\n\n"
            "/avatar &lt;Имя&gt;\n→ Картинка аватара\n\n"
            "/headshot &lt;Имя&gt;\n→ Headshot аватара\n\n"
            "/bust &lt;Имя&gt;\n→ Поясное изображение (bust)\n\n"
            "/assetid &lt;AssetID&gt;\n→ Инфо о предмете\n\n"
            "/asseticon &lt;AssetID&gt;\n→ Иконка предмета\n\n"
            "/groupid &lt;GroupID&gt;\n→ Инфо о группе по ID\n\n"
            "/group &lt;Имя&gt;\n→ Поиск группы по названию\n\n"
            "/groupicon &lt;GroupID&gt;\n→ Ссылка на группу\n\n"
            "/groups &lt;Имя&gt;\n→ Группы, в которых состоит пользователь\n\n"
            "/friends &lt;Имя&gt;\n→ Список друзей\n\n"
            "/followers &lt;Имя&gt;\n→ Список подписчиков\n\n"
            "/followings &lt;Имя&gt;\n→ На кого подписан пользователь\n\n"
            "/limiteds &lt;Имя&gt;\n→ Все лимитки с RAP/Value\n\n"
            "/rolimons &lt;Имя&gt;\n→ Статистика с Rolimons\n\n"
            "/devex &lt;Robux&gt;\n→ Примерная сумма в USD\n\n"
            "/devexcad &lt;Robux&gt;\n→ Примерная сумма в CAD\n\n"
            "/language\n→ Сменить язык бота (en/ru)\n\n"
            "/names &lt;Имя&gt;\n→ История юзернеймов\n\n"
            "/verified &lt;Имя&gt;\n→ Статус верификации\n\n"
            "/owned &lt;Имя&gt; &lt;AssetID&gt;\n→ Проверить, владеет ли пользователь предметом\n\n"
            "/obtained &lt;Имя&gt; &lt;BadgeID&gt;\n→ Когда пользователь получил бейдж\n\n"
            "/template &lt;AssetID&gt;\n→ Ссылка на исходный asset/текстуру\n\n"
            "/offsales &lt;Имя&gt;\n→ Информация о оффсейлах (ограничено)\n\n"
            "/links\n→ Полезные ссылки (скоро)"
        )
    else:
        text = (
            "🧑‍🔧 <b>Full command list</b>\n\n"
            "/user &lt;Username&gt;\n→ Display details about a Roblox user by username\n\n"
            "/id &lt;UserID&gt;\n→ Display details about a Roblox user by ID\n\n"
            "/username &lt;Username&gt;\n→ Check if a username is available/taken\n\n"
            "/displayname &lt;Name&gt;\n→ Find users by display name\n\n"
            "/copyid &lt;Username&gt;\n→ Quickly get a user's ID\n\n"
            "/idtousername &lt;ID1 ID2 ...&gt;\n→ Convert IDs → usernames\n\n"
            "/banned &lt;ID1 ID2 ...&gt;\n→ Check if users are banned\n\n"
            "/accountage &lt;Username&gt;\n→ Show account age in days/years\n\n"
            "/lastonline &lt;Username&gt;\n→ Show last online and location\n\n"
            "/avatar &lt;Username&gt;\n→ Send avatar render\n\n"
            "/headshot &lt;Username&gt;\n→ Send avatar headshot\n\n"
            "/bust &lt;Username&gt;\n→ Send avatar bust\n\n"
            "/assetid &lt;AssetID&gt;\n→ Show item info\n\n"
            "/asseticon &lt;AssetID&gt;\n→ Show item icon\n\n"
            "/groupid &lt;GroupID&gt;\n→ Group info by ID\n\n"
            "/group &lt;Name&gt;\n→ Search group by name\n\n"
            "/groupicon &lt;GroupID&gt;\n→ Link to group\n\n"
            "/groups &lt;Username&gt;\n→ Show user groups\n\n"
            "/friends &lt;Username&gt;\n→ Show friends list\n\n"
            "/followers &lt;Username&gt;\n→ Show followers\n\n"
            "/followings &lt;Username&gt;\n→ Show followings\n\n"
            "/limiteds &lt;Username&gt;\n→ Scan all RAP/Value items\n\n"
            "/rolimons &lt;Username&gt;\n→ Rolimons RAP/Value and more\n\n"
            "/devex &lt;Robux&gt;\n→ Approximate cash value in USD\n\n"
            "/devexcad &lt;Robux&gt;\n→ Approximate cash value in CAD\n\n"
            "/language\n→ Change bot language (en/ru)\n\n"
            "/names &lt;Username&gt;\n→ Show username history\n\n"
            "/verified &lt;Username&gt;\n→ Show verification status\n\n"
            "/owned &lt;Username&gt; &lt;AssetID&gt;\n→ Check if user owns item\n\n"
            "/obtained &lt;Username&gt; &lt;BadgeID&gt;\n→ When user got a player badge\n\n"
            "/template &lt;AssetID&gt;\n→ Mesh/texture/template URL\n\n"
            "/offsales &lt;Username&gt;\n→ Offsale info (limited by APIs)\n\n"
            "/links\n→ Useful links (soon)"
        )
    await message.answer(text)


@dp.message(Command("user"))
@track_command
async def cmd_user(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/user &lt;Имя&gt;\n→ Показать детали профиля по имени\nПример: <code>/user d45wn</code>"
            )
        return await message.answer(
            "/user &lt;Username&gt;\n→ Display details about a Roblox user\nExample: <code>/user d45wn</code>"
        )
    try:
        user = await roblox.get_user_details_by_username(name)
    except Exception as e:
        if lang == "ru":
            return await message.answer(f"Ошибка: <code>{esc(str(e))}</code>")
        return await message.answer(f"Error: <code>{esc(str(e))}</code>")
    if not user:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = user["id"]
    desc = esc((user.get("description") or "").strip()[:600])
    created = parse_iso8601(user["created"])
    created_str = created.strftime("%Y-%m-%d %H:%M UTC")
    friends = await roblox.get_friends(uid)
    followers = await roblox.get_followers(uid)
    followings = await roblox.get_followings(uid)
    friends_count = len(friends)
    followers_count = len(followers)
    followings_count = len(followings)
    premium = None
    inv_public = None
    rap = None
    value = None
    last_online_str = None
    roli_badges_text = ""
    try:
        pdata = await roli_get(f"https://api.rolimons.com/players/v1/playerinfo/{uid}")
        premium = pdata.get("premium")
        inv_public = not pdata.get("playerPrivacyEnabled", False)
        rap = pdata.get("rap")
        value = pdata.get("value")
        last_online_ts = pdata.get("lastOnline")
        if last_online_ts:
            lo = dt.datetime.fromtimestamp(last_online_ts, tz=dt.timezone.utc)
            last_online_str = lo.strftime("%Y-%m-%d %H:%M:%S UTC")
        badges = pdata.get("badges") or {}
        if badges:
            badge_keys = [k.replace("_", " ").title() for k in badges.keys()]
            roli_badges_text = ", ".join(badge_keys)
    except Exception:
        pass
    if lang == "ru":
        text = (
            f"👤 <b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📅 Создан: <code>{created_str}</code>\n"
            f"✅ Roblox Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
            f"⛔️ Забанен: <code>{user.get('isBanned', False)}</code>\n"
        )
        if premium is not None:
            text += f"⭐ Premium: <code>{premium}</code>\n"
        if inv_public is not None:
            text += f"📦 Инвентарь: <code>{'Публичный' if inv_public else 'Скрыт'}</code>\n"
        text += (
            f"👥 Друзья: <code>{friends_count}</code> | "
            f"⭐ Подписчики: <code>{followers_count}</code> | "
            f"➡️ Подписки: <code>{followings_count}</code>\n"
        )
        if rap is not None and value is not None:
            text += (
                f"💰 RAP: <code>{rap:,}</code>\n"
                f"💎 Value: <code>{value:,}</code>\n"
            )
        if last_online_str:
            text += f"⏱️ Последний онлайн: <code>{last_online_str}</code>\n"
        text += (
            f"\n<a href=\"https://www.roblox.com/users/{uid}/profile\">Профиль Roblox</a>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Профиль Rolimons</a>\n"
        )
        if roli_badges_text:
            text += f"\n<b>🏅 Значки Rolimons:</b> {esc(roli_badges_text)}"
        if desc:
            text += f"\n\n<b>📜 Описание:</b>\n{desc}"
    else:
        text = (
            f"👤 <b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📅 Created: <code>{created_str}</code>\n"
            f"✅ Roblox Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
            f"⛔️ Banned: <code>{user.get('isBanned', False)}</code>\n"
        )
        if premium is not None:
            text += f"⭐ Premium: <code>{premium}</code>\n"
        if inv_public is not None:
            text += f"📦 Inventory: <code>{'Public' if inv_public else 'Private'}</code>\n"
        text += (
            f"👥 Friends: <code>{friends_count}</code> | "
            f"⭐ Followers: <code>{followers_count}</code> | "
            f"➡️ Following: <code>{followings_count}</code>\n"
        )
        if rap is not None and value is not None:
            text += (
                f"💰 RAP: <code>{rap:,}</code>\n"
                f"💎 Value: <code>{value:,}</code>\n"
            )
        if last_online_str:
            text += f"⏱️ Last online: <code>{last_online_str}</code>\n"
        text += (
            f"\n<a href=\"https://www.roblox.com/users/{uid}/profile\">Roblox profile</a>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Rolimons profile</a>\n"
        )
        if roli_badges_text:
            text += f"\n<b>🏅 Rolimons badges:</b> {esc(roli_badges_text)}"
        if desc:
            text += f"\n\n<b>📜 Description:</b>\n{desc}"

    kb = user_profile_keyboard(uid)
    FALLBACK_IMG = ("https://media.discordapp.net/attachments/1278854601382039686/1503843004232896622/RS.png?ex=6a04d270&is=6a0380f0&hm=7cf1a833960ce626c8e09d6b0c69798de9c7f14f64e5ad4abda534e2df429681&=&format=webp&quality=lossless")
    thumb = await roblox.get_user_thumbnail(uid, "bust")
    
    if (
        not thumb
        or not isinstance(thumb, str)
        or not thumb.startswith("http")
    ):
        thumb = FALLBACK_IMG
    
    try:
        await message.answer_photo(thumb, caption=text, reply_markup=kb)
    except Exception:
        await message.answer_photo(FALLBACK_IMG, caption=text, reply_markup=kb)



@dp.message(Command("id"))
@track_command
async def cmd_id(message, command: CommandObject):
    lang = get_lang(message)
    parts = message.text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg.isdigit():
        if lang == "ru":
            return await message.answer(
                "/id &lt;UserID&gt;\n→ Показать детали профиля по ID\nПример: <code>/id 790144111</code>"
            )
        return await message.answer(
            "/id &lt;UserID&gt;\n→ Display details about a Roblox user by ID\nExample: <code>/id 790144111</code>"
        )
    uid = int(arg)
    try:
        user = await roblox.get_user_by_id(uid)
    except Exception as e:
        if lang == "ru":
            return await message.answer(f"Ошибка: <code>{esc(str(e))}</code>")
        return await message.answer(f"Error: <code>{esc(str(e))}</code>")
    if not user:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    desc = esc((user.get("description") or "").strip()[:600])
    created = parse_iso8601(user["created"])
    created_str = created.strftime("%Y-%m-%d %H:%M UTC")
    if lang == "ru":
        txt = (
            f"👤 <b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📅 Создан: <code>{created_str}</code>\n"
            f"✅ Roblox Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
            f"⛔️ Забанен: <code>{user.get('isBanned', False)}</code>\n\n"
            f"<a href=\"https://www.roblox.com/users/{uid}/profile\">Профиль Roblox</a>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Профиль Rolimons</a>\n"
        )
        if desc:
            txt += f"\n<b>📜 Описание:</b>\n{desc}"
    else:
        txt = (
            f"👤 <b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📅 Created: <code>{created_str}</code>\n"
            f"✅ Roblox Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
            f"⛔️ Banned: <code>{user.get('isBanned', False)}</code>\n\n"
            f"<a href=\"https://www.roblox.com/users/{uid}/profile\">Roblox profile</a>\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Rolimons profile</a>\n"
        )
        if desc:
            txt += f"\n<b>📜 Description:</b>\n{desc}"
    kb = user_profile_keyboard(uid)
    FALLBACK_IMG = "https://media.discordapp.net/attachments/1278854601382039686/1503843004232896622/RS.png?ex=6a04d270&is=6a0380f0&hm=7cf1a833960ce626c8e09d6b0c69798de9c7f14f64e5ad4abda534e2df429681&=&format=webp&quality=lossless"
    thumb = await roblox.get_user_thumbnail(uid, "bust")
    if (
        not thumb
        or not isinstance(thumb, str)
        or not thumb.startswith("http")
    ):
        thumb = FALLBACK_IMG
    try:
        await message.answer_photo(thumb, caption=txt, reply_markup=kb)  # ← was `text`, now `txt`
    except Exception:
        await message.answer_photo(FALLBACK_IMG, caption=txt, reply_markup=kb)


@dp.message(Command("username"))
@track_command
async def cmd_username(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/username &lt;Имя&gt;\n→ Проверить, занят ли юзернейм\nПример: <code>/username d45wn</code>"
            )
        return await message.answer(
            "/username &lt;Username&gt;\n→ Check if a username is available\nExample: <code>/username d45wn</code>"
        )
    user = await roblox.get_user_by_username(u)
    if user:
        if lang == "ru":
            return await message.answer(
                f"❌ <code>{esc(u)}</code> уже занят пользователем "
                f"<code>{esc(user['name'])}</code> (ID <code>{user['id']}</code>)"
            )
        return await message.answer(
            f"❌ <code>{esc(u)}</code> is taken by "
            f"<code>{esc(user['name'])}</code> (ID <code>{user['id']}</code>)"
        )
    if lang == "ru":
        return await message.answer(f"✅ <code>{esc(u)}</code> выглядит свободным.")
    return await message.answer(f"✅ <code>{esc(u)}</code> seems available.")


@dp.message(Command("displayname"))
@track_command
async def cmd_displayname(message, command: CommandObject):
    lang = get_lang(message)
    d = (command.args or "").strip()
    if not d:
        if lang == "ru":
            return await message.answer(
                "/displayname &lt;Имя&gt;\n→ Поиск по display name\nПример: <code>/displayname Darkss</code>"
            )
        return await message.answer(
            "/displayname &lt;Name&gt;\n→ Search by display name\nExample: <code>/displayname Darkss</code>"
        )
    results = await roblox.search_displayname(d)
    if not results:
        if lang == "ru":
            return await message.answer("Ничего не найдено.")
        return await message.answer("No results.")
    exact = [x for x in results if x["displayName"].lower() == d.lower()]
    lines = []
    if exact:
        if lang == "ru":
            lines.append(f"🔍 <b>Точные совпадения</b> ({len(exact)}):")
        else:
            lines.append(f"🔍 <b>Exact matches</b> ({len(exact)}):")
        for u in exact[:5]:
            lines.append(
                f"• {esc(u['displayName'])} / {esc(u['name'])} "
                f"(<code>{u['id']}</code>)"
            )
    else:
        if lang == "ru":
            lines.append("🔍 <b>Похожие результаты:</b>")
        else:
            lines.append("🔍 <b>Similar results:</b>")
        for u in results[:5]:
            lines.append(
                f"• {esc(u['displayName'])} / {esc(u['name'])} "
                f"(<code>{u['id']}</code>)"
            )
    await message.answer("\n".join(lines))


@dp.message(Command("copyid"))
@track_command
async def cmd_copyid(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/copyid &lt;Имя&gt;\n→ Получить ID пользователя\nПример: <code>/copyid d45wn</code>"
            )
        return await message.answer(
            "/copyid &lt;Username&gt;\n→ Get user ID quickly\nExample: <code>/copyid d45wn</code>"
        )
    u = await roblox.get_user_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    if lang == "ru":
        return await message.answer(
            f"🆔 ID пользователя <code>{esc(u['name'])}</code> = <code>{u['id']}</code>"
        )
    return await message.answer(
        f"🆔 ID of <code>{esc(u['name'])}</code> = <code>{u['id']}</code>"
    )

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    confirming = State()


@dp.message(Command("broadcast"))
@track_command
async def cmd_broadcast(message: Message, command: CommandObject = None, state: FSMContext = None):
    lang = get_lang(message)

    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.answer(
            "Эта команда доступна только владельцу бота." if lang == "ru"
            else "This command is owner-only."
        )

    if state is None:
        return await message.answer("State error. Please try again.")

    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь сообщение которое хочешь разослать.\n"
        "Можно отправить: текст, фото, видео, стикер.\n\n"
        "Отправь /cancel для отмены."
        if lang == "ru" else
        "📢 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast.\n"
        "You can send: text, photo, video, sticker.\n\n"
        "Send /cancel to cancel.",
        parse_mode="HTML"
    )
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(BroadcastStates.waiting_for_message)
async def broadcast_get_message(message: Message, state: FSMContext):
    lang = get_lang(message)

    if message.text and message.text == "/cancel":
        await state.clear()
        return await message.answer(
            "❌ Рассылка отменена." if lang == "ru" else "❌ Broadcast cancelled."
        )

    await state.update_data(
        message_id=message.message_id,
        from_chat_id=message.chat.id
    )

    total = len(USER_IDS)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Отправить" if lang == "ru" else "✅ Send",
                callback_data="broadcast_confirm"
            ),
            InlineKeyboardButton(
                text="❌ Отмена" if lang == "ru" else "❌ Cancel",
                callback_data="broadcast_cancel"
            )
        ]
    ])

    await message.answer(
        f"📢 <b>{'Подтверждение' if lang == 'ru' else 'Confirm Broadcast'}</b>\n\n"
        f"{'Получателей' if lang == 'ru' else 'Recipients'}: <b>{total}</b>\n\n"
        f"{'Отправить это сообщение всем?' if lang == 'ru' else 'Send this message to everyone?'}",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(BroadcastStates.confirming)


@dp.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.clear()
    await callback.message.edit_text("❌ Broadcast cancelled.")


@dp.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != OWNER_ID:
        return

    data = await state.get_data()
    message_id = data.get("message_id")
    from_chat_id = data.get("from_chat_id")
    await state.clear()

    await callback.message.edit_text("📤 Broadcasting... Please wait.")

    success = 0
    failed = 0
    blocked = 0

    # Send only to private chats (positive IDs = users)
    user_list = [uid for uid in USER_IDS if uid > 0]

    for user_id in user_list:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            success += 1
            await asyncio.sleep(0.05)

        except TelegramForbiddenError:
            blocked += 1
            USER_IDS.discard(user_id)  # Remove blocked users

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id
                )
                success += 1
            except Exception:
                failed += 1

        except Exception as e:
            failed += 1
            logging.error(f"Broadcast failed for {user_id}: {e}")

    # Save to broadcast history
    BROADCAST_HISTORY.append({
        "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "success": success,
        "failed": failed + blocked
    })

    # Keep only last 10 broadcasts in memory
    if len(BROADCAST_HISTORY) > 10:
        BROADCAST_HISTORY.pop(0)

    await callback.message.edit_text(
        f"📢 <b>Broadcast Complete!</b>\n\n"
        f"✅ Sent: <b>{success}</b>\n"
        f"🚫 Blocked: <b>{blocked}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total attempted: <b>{len(user_list)}</b>",
        parse_mode="HTML"
    )

class AnnounceStates(StatesGroup):
    waiting_for_message = State()
    confirming = State()


@dp.message(Command("announce"))
@track_command
async def cmd_announce(message: Message, command: CommandObject = None, state: FSMContext = None):
    lang = get_lang(message)

    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.answer(
            "Эта команда доступна только владельцу бота." if lang == "ru"
            else "This command is owner-only."
        )

    if state is None:
        return await message.answer("State error.")

    await message.answer(
        "📰 <b>Объявление / Новость</b>\n\n"
        "Отправь сообщение которое хочешь опубликовать как новость.\n"
        "Оно будет отправлено всем пользователям с префиксом 📰 <b>Новость</b>.\n\n"
        "/cancel — отмена"
        if lang == "ru" else
        "📰 <b>Announcement / News</b>\n\n"
        "Send the message to publish as a news announcement.\n"
        "It will be sent to all users with a 📰 <b>News</b> prefix.\n\n"
        "/cancel — cancel",
        parse_mode="HTML"
    )
    await state.set_state(AnnounceStates.waiting_for_message)


@dp.message(AnnounceStates.waiting_for_message)
async def announce_get_message(message: Message, state: FSMContext):
    lang = get_lang(message)

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer(
            "❌ Объявление отменено." if lang == "ru" else "❌ Announcement cancelled."
        )

    await state.update_data(
        message_id=message.message_id,
        from_chat_id=message.chat.id
    )

    total = len([uid for uid in USER_IDS if uid > 0])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Опубликовать" if lang == "ru" else "✅ Publish",
                callback_data="announce_confirm"
            ),
            InlineKeyboardButton(
                text="❌ Отмена" if lang == "ru" else "❌ Cancel",
                callback_data="announce_cancel"
            )
        ]
    ])

    await message.answer(
        f"📰 <b>{'Подтверждение' if lang == 'ru' else 'Confirm Announcement'}</b>\n\n"
        f"{'Получателей' if lang == 'ru' else 'Recipients'}: <b>{total}</b>\n\n"
        f"{'Опубликовать это объявление?' if lang == 'ru' else 'Publish this announcement to everyone?'}",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(AnnounceStates.confirming)


@dp.callback_query(F.data == "announce_cancel")
async def announce_cancel_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.clear()
    await callback.message.edit_text("❌ Announcement cancelled.")


@dp.callback_query(F.data == "announce_confirm")
async def announce_confirm_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != OWNER_ID:
        return

    data = await state.get_data()
    message_id = data.get("message_id")
    from_chat_id = data.get("from_chat_id")
    await state.clear()

    await callback.message.edit_text("📤 Sending announcement... Please wait.")

    user_list = [uid for uid in USER_IDS if uid > 0]
    success = 0
    failed = 0
    blocked = 0

    # Send header + original message
    for user_id in user_list:
        try:
            # Send news header first
            await bot.send_message(
                chat_id=user_id,
                text="📰 <b>RBLXScan News</b>",
                parse_mode="HTML"
            )
            # Then copy the actual message
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            success += 1
            await asyncio.sleep(0.07)  # ~14 messages/second, safe for Telegram

        except TelegramForbiddenError:
            blocked += 1
            USER_IDS.discard(user_id)

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="📰 <b>RBLXScan News</b>",
                    parse_mode="HTML"
                )
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id
                )
                success += 1
            except Exception:
                failed += 1

        except Exception as e:
            failed += 1
            logging.error(f"Announce failed for {user_id}: {e}")

    BROADCAST_HISTORY.append({
        "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "type": "announcement",
        "success": success,
        "failed": failed + blocked
    })
    if len(BROADCAST_HISTORY) > 10:
        BROADCAST_HISTORY.pop(0)

    await callback.message.edit_text(
        f"📰 <b>Announcement Sent!</b>\n\n"
        f"✅ Delivered: <b>{success}</b>\n"
        f"🚫 Blocked: <b>{blocked}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total: <b>{len(user_list)}</b>",
        parse_mode="HTML"
    )

@dp.message(Command("idtousername"))
@track_command
async def cmd_idtousername(message, command: CommandObject):
    lang = get_lang(message)
    ids = parse_ids((command.args or ""), 50)
    if not ids:
        if lang == "ru":
            return await message.answer(
                "/idtousername &lt;ID1 ID2 ...&gt;\n→ Конвертировать ID в имена\nПример: <code>/idtousername 1 2 3</code>"
            )
        return await message.answer(
            "/idtousername &lt;ID1 ID2 ...&gt;\n→ Convert IDs to usernames\nExample: <code>/idtousername 1 2 3</code>"
        )
    info = await roblox.get_users_by_ids(ids)
    if lang == "ru":
        lines = ["🔁 <b>ID → Имя пользователя</b>"]
    else:
        lines = ["🔁 <b>ID → Username</b>"]
    for i in ids:
        u = info.get(i)
        if u:
            lines.append(
                f"{i} → {esc(u['name'])} / {esc(u['displayName'])}"
            )
        else:
            if lang == "ru":
                lines.append(f"{i} → не найден")
            else:
                lines.append(f"{i} → not found")
    await message.answer("\n".join(lines))


@dp.message(Command("banned"))
@track_command
async def cmd_banned(message, command: CommandObject):
    lang = get_lang(message)
    ids = parse_ids((command.args or ""), 20)
    if not ids:
        if lang == "ru":
            return await message.answer(
                "/banned &lt;ID1 ID2 ...&gt;\n→ Проверить статус бана\nПример: <code>/banned 1 2 3</code>"
            )
        return await message.answer(
            "/banned &lt;ID1 ID2 ...&gt;\n→ Check banned status\nExample: <code>/banned 1 2 3</code>"
        )
    if lang == "ru":
        lines = ["⛔️ <b>Статус бана:</b>"]
    else:
        lines = ["⛔️ <b>Banned status:</b>"]
    for i in ids:
        u = await roblox.get_user_by_id(i)
        if u:
            lines.append(f"{i}: banned = <code>{u.get('isBanned', False)}</code>")
        else:
            if lang == "ru":
                lines.append(f"{i}: не найден")
            else:
                lines.append(f"{i}: not found")
    await message.answer("\n".join(lines))


@dp.message(Command("accountage"))
@track_command
async def cmd_accountage(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/accountage &lt;Имя&gt;\n→ Показать возраст аккаунта\nПример: <code>/accountage d45wn</code>"
            )
        return await message.answer(
            "/accountage &lt;Username&gt;\n→ Show account age\nExample: <code>/accountage d45wn</code>"
        )
    u = await roblox.get_user_details_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    created = parse_iso8601(u["created"])
    now = dt.datetime.now(dt.timezone.utc)
    days = (now - created).days
    if lang == "ru":
        await message.answer(
            f"📅 <b>{esc(u['name'])}</b>\n"
            f"Создан: <code>{created.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
            f"Возраст: <code>{days}</code> дней (~<code>{days/365:.2f}</code> лет)"
        )
    else:
        await message.answer(
            f"📅 <b>{esc(u['name'])}</b>\n"
            f"Created: <code>{created.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
            f"Age: <code>{days}</code> days (~<code>{days/365:.2f}</code> years)"
        )


@dp.message(Command("lastonline"))
@track_command
async def cmd_lastonline(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/lastonline &lt;Имя&gt;\n→ Последний онлайн\nПример: <code>/lastonline d45wn</code>"
            )
        return await message.answer(
            "/lastonline &lt;Username&gt;\n→ Show last online time\nExample: <code>/lastonline d45wn</code>"
        )
    u = await roblox.get_user_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    pr = await roblox.get_presence([u["id"]])
    p = (pr or {}).get("userPresences", [{}])[0]
    last = p.get("lastOnline")
    loc = p.get("lastLocation") or "Unknown"
    if last:
        last = parse_iso8601(last).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        last = "Unknown"
    if lang == "ru":
        await message.answer(
            f"⏱️ <b>{esc(u['name'])}</b>\n"
            f"Локация: <code>{esc(loc)}</code>\n"
            f"Последний онлайн: <b>{last}</b>"
        )
    else:
        await message.answer(
            f"⏱️ <b>{esc(u['name'])}</b>\n"
            f"Location: <code>{esc(loc)}</code>\n"
            f"Last Online: <b>{last}</b>"
        )


@dp.message(Command("avatar"))
@track_command
async def cmd_avatar(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()

    if not name:
        if lang == "ru":
            return await message.answer(
                "/avatar &lt;Имя&gt;\n→ Аватар пользователя\nПример: <code>/avatar d45wn</code>"
            )
        return await message.answer(
            "/avatar &lt;Username&gt;\n→ Send avatar render\nExample: <code>/avatar d45wn</code>"
        )

    u = await roblox.get_user_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")

    url = await roblox.get_user_thumbnail(u["id"], "avatar")
    if not url:
        if lang == "ru":
            return await message.answer("Не удалось получить аватар.")
        return await message.answer("No avatar thumbnail available.")

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    if lang == "ru":
                        return await message.answer(f"Не удалось загрузить аватар (HTTP {resp.status}).")
                    return await message.answer(f"Failed to download avatar (HTTP {resp.status}).")
                image_data = await resp.read()

        photo = BufferedInputFile(image_data, filename="avatar.png")

        if lang == "ru":
            await message.answer_photo(
                photo,
                caption=f"🧍 Аватар <b>{esc(u['name'])}</b>",
                parse_mode="HTML"
            )
        else:
            await message.answer_photo(
                photo,
                caption=f"🧍 Avatar of <b>{esc(u['name'])}</b>",
                parse_mode="HTML"
            )

    except aiohttp.ClientError:
        if lang == "ru":
            return await message.answer("Ошибка загрузки аватара. Попробуйте позже.")
        return await message.answer("Failed to download avatar. Please try again later.")

    except TelegramNetworkError:
        if lang == "ru":
            return await message.answer("Telegram не успел отправить фото. Попробуйте ещё раз.")
        return await message.answer("Telegram timed out sending the photo. Please try again.")

    except Exception as e:
        if lang == "ru":
            return await message.answer(f"Неизвестная ошибка: {type(e).__name__}")
        return await message.answer(f"Unexpected error: {type(e).__name__}")


@dp.message(Command("headshot"))
@track_command
async def cmd_headshot(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/headshot &lt;Имя&gt;\n→ Headshot аватара\nПример: <code>/headshot d45wn</code>"
            )
        return await message.answer(
            "/headshot &lt;Username&gt;\n→ Send avatar headshot\nExample: <code>/headshot d45wn</code>"
        )
    u = await roblox.get_user_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    url = await roblox.get_user_thumbnail(u["id"], "headshot")
    if not url:
        if lang == "ru":
            return await message.answer("Не удалось получить headshot.")
        return await message.answer("No headshot thumbnail available.")
    if lang == "ru":
        await message.answer_photo(url, caption=f"🙂 Headshot <b>{esc(u['name'])}</b>")
    else:
        await message.answer_photo(url, caption=f"🙂 Headshot of <b>{esc(u['name'])}</b>")


@dp.message(Command("bust"))
@track_command
async def cmd_bust(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/bust &lt;Имя&gt;\n→ Поясное изображение аватара\nПример: <code>/bust d45wn</code>"
            )
        return await message.answer(
            "/bust &lt;Username&gt;\n→ Send avatar bust\nExample: <code>/bust d45wn</code>"
        )
    u = await roblox.get_user_by_username(name)
    if not u:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    url = await roblox.get_user_thumbnail(u["id"], "bust")
    if not url:
        if lang == "ru":
            return await message.answer("Не удалось получить bust.")
        return await message.answer("No bust thumbnail available.")
    if lang == "ru":
        await message.answer_photo(url, caption=f"🧍‍♂️ Bust <b>{esc(u['name'])}</b>")
    else:
        await message.answer_photo(url, caption=f"🧍‍♂️ Bust of <b>{esc(u['name'])}</b>")


@dp.message(Command("assetid"))
@track_command
async def cmd_assetid(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/assetid &lt;AssetID&gt;\n→ Информация о предмете\nПример: <code>/assetid 1029025</code>"
            )
        return await message.answer(
            "/assetid &lt;AssetID&gt;\n→ Show item info\nExample: <code>/assetid 1029025</code>"
        )
    aid = int(raw)

    try:
        info = await roblox.get_asset_info(aid)
    except Exception as e:
        if lang == "ru":
            return await message.answer(f"❌ Ошибка при получении данных: <code>{esc(str(e))}</code>")
        return await message.answer(f"❌ Error fetching asset data: <code>{esc(str(e))}</code>")

    if not info:
        if lang == "ru":
            return await message.answer("Предмет не найден.")
        return await message.answer("Asset not found.")
    
    name = info.get("Name") or info.get("name") or "?"
    description = info.get("Description") or info.get("description") or ""
    price = info.get("PriceInRobux") or info.get("priceInRobux") or "N/A"
    
    # Creator field differs between APIs
    creator = info.get("Creator") or {}
    creator_name = creator.get("Name") or creator.get("name") or info.get("creatorName") or "?"
    creator_id = creator.get("Id") or creator.get("targetId") or info.get("creatorTargetId") or "?"
    
    is_limited = info.get("IsLimited") or ("Limited" in (info.get("itemRestrictions") or []))
    is_limited_u = info.get("IsLimitedUnique") or ("LimitedUnique" in (info.get("itemRestrictions") or []))
    
    desc = esc(str(description)[:600])

    if lang == "ru":
        text = (
            f"🎩 <b>{esc(str(name))}</b>\n"
            f"🆔 ID: <code>{aid}</code>\n"
            f"👤 Создатель: <code>{esc(str(creator_name))}</code> "
            f"(<code>{creator_id}</code>)\n"
            f"💰 Цена: <code>{price}</code>\n"
            f"♻️ Limited: <code>{is_limited}</code>\n"
            f"♻️ LimitedU: <code>{is_limited_u}</code>\n"
            f"<a href=\"https://www.roblox.com/catalog/{aid}\">Открыть в каталоге</a>"
        )
        if desc:
            text += f"\n\n<b>📜 Описание:</b>\n{desc}"
    else:
        text = (
            f"🎩 <b>{esc(str(name))}</b>\n"
            f"🆔 ID: <code>{aid}</code>\n"
            f"👤 Creator: <code>{esc(str(creator_name))}</code> "
            f"(<code>{creator_id}</code>)\n"
            f"💰 Price: <code>{price}</code>\n"
            f"♻️ Limited: <code>{is_limited}</code>\n"
            f"♻️ LimitedU: <code>{is_limited_u}</code>\n"
            f"<a href=\"https://www.roblox.com/catalog/{aid}\">Open in catalog</a>"
        )
        if desc:
            text += f"\n\n<b>📜 Description:</b>\n{desc}"

    icon = await roblox.get_asset_icon(aid)
    if icon:
        return await message.answer_photo(icon, caption=text)
    return await message.answer(text)

@dp.message(Command("asseticon"))
@track_command
async def cmd_asseticon(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/asseticon &lt;AssetID&gt;\n→ Иконка предмета\nПример: <code>/asseticon 1029025</code>"
            )
        return await message.answer(
            "/asseticon &lt;AssetID&gt;\n→ Show item icon\nExample: <code>/asseticon 1029025</code>"
        )
    aid = int(raw)
    icon = await roblox.get_asset_icon(aid)
    if not icon:
        if lang == "ru":
            return await message.answer("Иконка недоступна.")
        return await message.answer("No icon.")
    if lang == "ru":
        await message.answer_photo(icon, caption=f"🎴 Предмет <b>{aid}</b>")
    else:
        await message.answer_photo(icon, caption=f"🎴 Asset <b>{aid}</b>")


@dp.message(Command("groupid"))
@track_command
async def cmd_groupid(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/groupid &lt;GroupID&gt;\n→ Инфо о группе по ID\nПример: <code>/groupid 35700808</code>"
            )
        return await message.answer(
            "/groupid &lt;GroupID&gt;\n→ Group info by ID\nExample: <code>/groupid 35700808</code>"
        )
    gid = int(raw)
    g = await roblox.get_group_by_id(gid)
    if not g:
        if lang == "ru":
            return await message.answer("Группа не найдена.")
        return await message.answer("Group not found.")
    desc = esc((g.get("description") or "")[:600])
    owner = g.get("owner") or {}
    if lang == "ru":
        txt = (
            f"👥 <b>{esc(g.get('name', '?'))}</b>\n"
            f"🆔 ID: <code>{gid}</code>\n"
            f"👑 Владелец: <code>{owner.get('userId', 'Unknown')}</code>\n"
            f"👥 Участников: <code>{g.get('memberCount', '?')}</code>\n"
            f"<a href=\"https://www.roblox.com/groups/{gid}\">Открыть группу</a>"
        )
        if desc:
            txt += f"\n\n<b>📜 Описание:</b>\n{desc}"
    else:
        txt = (
            f"👥 <b>{esc(g.get('name', '?'))}</b>\n"
            f"🆔 ID: <code>{gid}</code>\n"
            f"👑 Owner: <code>{owner.get('userId', 'Unknown')}</code>\n"
            f"👥 Members: <code>{g.get('memberCount', '?')}</code>\n"
            f"<a href=\"https://www.roblox.com/groups/{gid}\">Open group</a>"
        )
        if desc:
            txt += f"\n\n<b>📜 Description:</b>\n{desc}"
    await message.answer(txt)


@dp.message(Command("group"))
@track_command
async def cmd_group(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/group &lt;Название&gt;\n→ Поиск группы по названию\nПример: <code>/group Darkss Group</code>"
            )
        return await message.answer(
            "/group &lt;Name&gt;\n→ Search group by name\nExample: <code>/group Darkss Group</code>"
        )
    results = await roblox.search_group_by_name(name)
    if not results:
        if lang == "ru":
            return await message.answer("Группы не найдены.")
        return await message.answer("No groups found.")
    g = results[0]
    gid = g["id"]
    full = await roblox.get_group_by_id(gid)
    desc = esc((full.get("description") or "")[:600])
    if lang == "ru":
        txt = (
            f"👥 <b>{esc(full['name'])}</b>\n"
            f"🆔 ID: <code>{gid}</code>\n"
            f"👥 Участников: <code>{full.get('memberCount')}</code>\n"
            f"<a href=\"https://www.roblox.com/groups/{gid}\">Открыть группу</a>"
        )
        if desc:
            txt += f"\n\n<b>📜 Описание:</b>\n{desc}"
    else:
        txt = (
            f"👥 <b>{esc(full['name'])}</b>\n"
            f"🆔 ID: <code>{gid}</code>\n"
            f"👥 Members: <code>{full.get('memberCount')}</code>\n"
            f"<a href=\"https://www.roblox.com/groups/{gid}\">Open group</a>"
        )
        if desc:
            txt += f"\n\n<b>📜 Description:</b>\n{desc}"
    await message.answer(txt)


@dp.message(Command("groupicon"))
@track_command
async def cmd_groupicon(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/groupicon &lt;GroupID&gt;\n→ Открыть страницу группы\nПример: <code>/groupicon 35700808</code>"
            )
        return await message.answer(
            "/groupicon &lt;GroupID&gt;\n→ Open group page\nExample: <code>/groupicon 35700808</code>"
        )
    gid = int(raw)
    if lang == "ru":
        await message.answer(
            f"Иконка группы через API недоступна.\n\n"
            f"Группа: https://www.roblox.com/groups/{gid}"
        )
    else:
        await message.answer(
            f"Roblox group icons are not exposed via this API.\n\n"
            f"Group: https://www.roblox.com/groups/{gid}"
        )


@dp.message(Command("groups"))
@track_command
async def cmd_groups(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/groups &lt;Имя&gt;\n→ Показать группы пользователя\nПример: <code>/groups d45wn</code>"
            )
        return await message.answer(
            "/groups &lt;Username&gt;\n→ Show user groups\nExample: <code>/groups d45wn</code>"
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    data = await roblox.get_user_groups(base["id"])
    if not data:
        if lang == "ru":
            return await message.answer("Нет групп или профиль скрыт.")
        return await message.answer("No groups or profile is private.")
    if lang == "ru":
        lines = [f"👥 <b>Группы пользователя {esc(base['name'])}:</b>"]
    else:
        lines = [f"👥 <b>Groups of {esc(base['name'])}:</b>"]
    for g in data[:20]:
        group = g.get("group", {})
        role = g.get("role", {})
        lines.append(
            f"• {esc(group.get('name', '?'))} "
            f"(<code>{group.get('id')}</code>) — role: <code>{esc(role.get('name', '?'))}</code>"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("friends"))
@track_command
async def cmd_friends(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/friends &lt;Имя&gt;\n→ Показать друзей\nПример: <code>/friends d45wn</code>"
            )
        return await message.answer(
            "/friends &lt;Username&gt;\n→ Show user's friends\nExample: <code>/friends d45wn</code>"
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    data = await roblox.get_friends(base["id"])
    if not data:
        if lang == "ru":
            return await message.answer("Нет друзей.")
        return await message.answer("No friends.")
    if lang == "ru":
        lines = [f"👥 <b>Друзья {esc(base['name'])}:</b>"]
    else:
        lines = [f"👥 <b>Friends of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")
    await message.answer("\n".join(lines))


@dp.message(Command("followers"))
@track_command
async def cmd_followers(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/followers &lt;Имя&gt;\n→ Показать подписчиков\nПример: <code>/followers d45wn</code>"
            )
        return await message.answer(
            "/followers &lt;Username&gt;\n→ Show user's followers\nExample: <code>/followers d45wn</code>"
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    data = await roblox.get_followers(base["id"])
    if not data:
        if lang == "ru":
            return await message.answer("Нет подписчиков.")
        return await message.answer("No followers.")
    if lang == "ru":
        lines = [f"⭐️ <b>Подписчики {esc(base['name'])}:</b>"]
    else:
        lines = [f"⭐️ <b>Followers of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")
    await message.answer("\n".join(lines))


@dp.message(Command("followings"))
@track_command
async def cmd_followings(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/followings &lt;Имя&gt;\n→ Показать, на кого подписан пользователь\nПример: <code>/followings d45wn</code>"
            )
        return await message.answer(
            "/followings &lt;Username&gt;\n→ Show who user follows\nExample: <code>/followings d45wn</code>"
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    data = await roblox.get_followings(base["id"])
    if not data:
        if lang == "ru":
            return await message.answer("Нет подписок.")
        return await message.answer("No followings.")
    if lang == "ru":
        lines = [f"➡️ <b>Подписки {esc(base['name'])}:</b>"]
    else:
        lines = [f"➡️ <b>Followings of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")
    await message.answer("\n".join(lines))


@dp.message(Command("limiteds"))
@track_command
async def cmd_limiteds(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/limiteds &lt;Имя&gt;\n→ Просканировать все лимитки (RAP/Value)\n"
                "Пример: <code>/limiteds d45wn</code>\n"
                "Покажет список всех limited-предметов пользователя."
            )
        return await message.answer(
            "/limiteds &lt;Username&gt;\n→ Scan all RAP/Value items\n"
            "Example: <code>/limiteds d45wn</code>\n"
            "Shows a full list of user's limiteds."
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    text = await compose_limiteds_text(base["id"], lang)
    await message.answer(text)


@dp.message(Command("rolimons"))
@track_command
async def cmd_rolimons(message, command: CommandObject):
    lang = get_lang(message)
    u = (command.args or "").strip()
    if not u:
        if lang == "ru":
            return await message.answer(
                "/rolimons &lt;Имя&gt;\n→ RAP/Value и прочее с Rolimons\nПример: <code>/rolimons d45wn</code>"
            )
        return await message.answer(
            "/rolimons &lt;Username&gt;\n→ RAP/Value and more from Rolimons\nExample: <code>/rolimons d45wn</code>"
        )
    base = await roblox.get_user_by_username(u)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = base["id"]
    try:
        data = await roli_get(f"https://api.rolimons.com/players/v1/playerinfo/{uid}")
    except Exception as e:
        if lang == "ru":
            return await message.answer(
                f"Rolimons ошибка: <code>{esc(str(e))}</code>\n\n"
                f"Профиль: https://www.rolimons.com/player/{uid}"
            )
        return await message.answer(
            f"Rolimons error: <code>{esc(str(e))}</code>\n\n"
            f"You can still open profile:\n"
            f"https://www.rolimons.com/player/{uid}"
        )
    p = data
    rap = p.get("rap")
    value = p.get("value")
    inv_public = not p.get("playerPrivacyEnabled", False)
    premium = p.get("premium", False)
    last_online_ts = p.get("lastOnline")
    if last_online_ts:
        last_online = dt.datetime.fromtimestamp(last_online_ts, tz=dt.timezone.utc)
        last_online_str = last_online.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        last_online_str = "Unknown"
    if lang == "ru":
        text = (
            f"📊 <b>Rolimons статистика для {esc(base['name'])}</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 RAP: <code>{rap:,}</code>\n"
            f"💎 Value: <code>{value:,}</code>\n"
            f"⭐ Premium: <code>{premium}</code>\n"
            f"📦 Инвентарь публичный: <code>{inv_public}</code>\n"
            f"⏱️ Последний онлайн: <code>{last_online_str}</code>\n\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Открыть на Rolimons</a>"
        )
    else:
        text = (
            f"📊 <b>Rolimons stats for {esc(base['name'])}</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 RAP: <code>{rap:,}</code>\n"
            f"💎 Value: <code>{value:,}</code>\n"
            f"⭐ Premium: <code>{premium}</code>\n"
            f"📦 Inventory public: <code>{inv_public}</code>\n"
            f"⏱️ Last online: <code>{last_online_str}</code>\n\n"
            f"<a href=\"https://www.rolimons.com/player/{uid}\">Open on Rolimons</a>"
        )
    await message.answer(text)


@dp.message(Command("devex"))
@track_command
async def cmd_devex(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/devex &lt;Robux&gt;\n→ Приблизительная сумма в USD\nПример: <code>/devex 100000</code>"
            )
        return await message.answer(
            "/devex &lt;Robux&gt;\n→ Approximate cash value in USD\nExample: <code>/devex 100000</code>"
        )
    r = int(raw)
    usd = r * USD_PER_ROBUX
    if lang == "ru":
        await message.answer(
            f"💵 <code>{r:,}</code> R$ ≈ <b>${usd:,.2f}</b> USD (примерно)"
        )
    else:
        await message.answer(
            f"💵 <code>{r:,}</code> R$ ≈ <b>${usd:,.2f}</b> USD (approx.)"
        )


@dp.message(Command("devexcad"))
@track_command
async def cmd_devexcad(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/devexcad &lt;Robux&gt;\n→ Приблизительная сумма в CAD\nПример: <code>/devexcad 100000</code>"
            )
        return await message.answer(
            "/devexcad &lt;Robux&gt;\n→ Approximate cash value in CAD\nExample: <code>/devexcad 100000</code>"
        )
    r = int(raw)
    cad = (r * USD_PER_ROBUX) * USD_TO_CAD
    if lang == "ru":
        await message.answer(
            f"💵 <code>{r:,}</code> R$ ≈ <b>${cad:,.2f}</b> CAD (примерно)"
        )
    else:
        await message.answer(
            f"💵 <code>{r:,}</code> R$ ≈ <b>${cad:,.2f}</b> CAD (approx.)"
        )


@dp.message(Command("language"))
@track_command
async def cmd_language(message, command: CommandObject):
    uid = message.from_user.id

    parts = message.text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    lang_current = get_lang(message)

    if arg in ("en", "ru"):
        USER_LANG[uid] = arg
        await message.answer(
            "Язык бота установлен на: 🇷🇺 Русский." if arg == "ru"
            else "Bot language set to: 🇬🇧 English."
        )
        return

    text = (
        "🌐 <b>Language settings</b>\n\n"
        "• 🇬🇧 English — /language en\n"
        "• 🇷🇺 Russian — /language ru"
        if lang_current == "en"
        else
        "🌐 <b>Смена языка</b>\n\n"
        "• 🇬🇧 English — /language en\n"
        "• 🇷🇺 Русский — /language ru"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
            ]
        ]
    )

    await message.answer(text, reply_markup=kb)


@dp.message(Command("names"))
@track_command
async def cmd_names(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/names &lt;Имя&gt;\n→ История юзернеймов\nПример: <code>/names d45wn</code>"
            )
        return await message.answer(
            "/names &lt;Username&gt;\n→ Show username history\nExample: <code>/names d45wn</code>"
        )
    base = await roblox.get_user_by_username(name)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = base["id"]
    try:
        data = await roblox.get_username_history(uid)
    except Exception as e:
        if lang == "ru":
            return await message.answer(f"Ошибка при запросе истории: <code>{esc(str(e))}</code>")
        return await message.answer(f"Error fetching history: <code>{esc(str(e))}</code>")
    if not data or not data.get("data"):
        if lang == "ru":
            return await message.answer("История имён пустая или скрыта.")
        return await message.answer("No username history or it is hidden.")
    if lang == "ru":
        lines = [f"📜 <b>История имён {esc(base['name'])}:</b>"]
    else:
        lines = [f"📜 <b>Username history of {esc(base['name'])}:</b>"]
    for entry in data["data"]:
        uname = esc(entry.get("name", ""))
        created = entry.get("created")
        if created:
            created_str = parse_iso8601(created).strftime("%Y-%m-%d")
            lines.append(f"• {uname} — {created_str}")
        else:
            lines.append(f"• {uname}")
    await message.answer("\n".join(lines))


@dp.message(Command("verified"))
@track_command
async def cmd_verified(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/verified &lt;Имя&gt;\n→ Статус верификации\nПример: <code>/verified d45wn</code>"
            )
        return await message.answer(
            "/verified &lt;Username&gt;\n→ Show verification status\nExample: <code>/verified d45wn</code>"
        )
    base = await roblox.get_user_details_by_username(name)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = base["id"]
    roblox_verified = base.get("hasVerifiedBadge", False)
    roli_verified = None
    try:
        pdata = await roli_get(f"https://api.rolimons.com/players/v1/playerinfo/{uid}")
        roli_verified = pdata.get("playerVerified")
    except Exception:
        pass
    if lang == "ru":
        text = (
            f"✅ <b>Верификация {esc(base['name'])}</b>\n\n"
            f"Roblox Verified Badge: <code>{roblox_verified}</code>\n"
        )
        if roli_verified is not None:
            text += f"Rolimons Verified: <code>{roli_verified}</code>\n"
        text += f"\nПрофиль: https://www.roblox.com/users/{uid}/profile"
    else:
        text = (
            f"✅ <b>Verification status for {esc(base['name'])}</b>\n\n"
            f"Roblox Verified Badge: <code>{roblox_verified}</code>\n"
        )
        if roli_verified is not None:
            text += f"Rolimons Verified: <code>{roli_verified}</code>\n"
        text += f"\nProfile: https://www.roblox.com/users/{uid}/profile"
    await message.answer(text)


@dp.message(Command("owned"))
@track_command
async def cmd_owned(message, command: CommandObject):
    lang = get_lang(message)
    args = (command.args or "").split()
    if len(args) < 2 or not args[1].isdigit():
        if lang == "ru":
            return await message.answer(
                "/owned &lt;Имя&gt; &lt;AssetID&gt;\n→ Проверить, владеет ли пользователь предметом\n"
                "Пример: <code>/owned d45wn 1029025</code>"
            )
        return await message.answer(
            "/owned &lt;Username&gt; &lt;AssetID&gt;\n→ Check if user owns an item\n"
            "Example: <code>/owned d45wn 1029025</code>"
        )
    username = args[0]
    asset_id = int(args[1])
    base = await roblox.get_user_by_username(username)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = base["id"]
    try:
        owns = await roblox.user_owns_asset(uid, asset_id)
    except RuntimeError as e:
        msg = str(e)
        if "403" in msg:
            if lang == "ru":
                return await message.answer("Инвентарь скрыт или недоступен для проверки.")
            return await message.answer("Inventory is private or cannot be checked.")
        if lang == "ru":
            return await message.answer(f"Ошибка при проверке владения: <code>{esc(msg)}</code>")
        return await message.answer(f"Error checking ownership: <code>{esc(msg)}</code>")
    if owns is None:
        if lang == "ru":
            return await message.answer("Не удалось проверить владение предметом.")
        return await message.answer("Could not verify ownership.")
    if owns:
        if lang == "ru":
            await message.answer(
                f"✅ <b>{esc(base['name'])}</b> владеет предметом <code>{asset_id}</code>.\n"
                f"https://www.roblox.com/catalog/{asset_id}"
            )
        else:
            await message.answer(
                f"✅ <b>{esc(base['name'])}</b> owns asset <code>{asset_id}</code>.\n"
                f"https://www.roblox.com/catalog/{asset_id}"
            )
    else:
        if lang == "ru":
            await message.answer(
                f"❌ <b>{esc(base['name'])}</b> не владеет предметом <code>{asset_id}</code>."
            )
        else:
            await message.answer(
                f"❌ <b>{esc(base['name'])}</b> does not own asset <code>{asset_id}</code>."
            )


@dp.message(Command("obtained"))
@track_command
async def cmd_obtained(message, command: CommandObject):
    lang = get_lang(message)
    args = (command.args or "").split()
    if len(args) < 2 or not args[1].isdigit():
        if lang == "ru":
            return await message.answer(
                "/obtained &lt;Имя&gt; &lt;BadgeID&gt;\n→ Когда пользователь получил игровой бейдж\n"
                "Пример: <code>/obtained d45wn 1234567890</code>"
            )
        return await message.answer(
            "/obtained &lt;Username&gt; &lt;BadgeID&gt;\n→ When user obtained a player badge\n"
            "Example: <code>/obtained d45wn 1234567890</code>"
        )
    username = args[0]
    badge_id = int(args[1])
    base = await roblox.get_user_by_username(username)
    if not base:
        if lang == "ru":
            return await message.answer("Пользователь не найден.")
        return await message.answer("User not found.")
    uid = base["id"]
    try:
        data = await roblox.get_badge_awarded_date(uid, badge_id)
    except Exception as e:
        if lang == "ru":
            return await message.answer(f"Ошибка при запросе бейджа: <code>{esc(str(e))}</code>")
        return await message.answer(f"Error fetching badge: <code>{esc(str(e))}</code>")
    if not data or not data.get("data"):
        if lang == "ru":
            return await message.answer("Информация о получении бейджа не найдена.")
        return await message.answer("No award data found for this badge.")
    entry = data["data"][0]
    awarded = entry.get("awardedDate")
    if not awarded:
        if lang == "ru":
            return await message.answer("Пользователь не получил этот бейдж.")
        return await message.answer("User has not obtained this badge.")
    dt_award = parse_iso8601(awarded)
    dt_str = dt_award.strftime("%Y-%m-%d %H:%M:%S UTC")
    if lang == "ru":
        await message.answer(
            f"🏅 <b>{esc(base['name'])}</b> получил бейдж <code>{badge_id}</code>:\n"
            f"<code>{dt_str}</code>"
        )
    else:
        await message.answer(
            f"🏅 <b>{esc(base['name'])}</b> obtained badge <code>{badge_id}</code> on:\n"
            f"<code>{dt_str}</code>"
        )


@dp.message(Command("template"))
@track_command
async def cmd_template(message, command: CommandObject):
    lang = get_lang(message)
    raw = (command.args or "").strip()
    if not raw.isdigit():
        if lang == "ru":
            return await message.answer(
                "/template &lt;AssetID&gt;\n→ Ссылка на ресурс/текстуру/mesh\nПример: <code>/template 1029025</code>"
            )
        return await message.answer(
            "/template &lt;AssetID&gt;\n→ Asset/texture/mesh URL\nExample: <code>/template 1029025</code>"
        )
    aid = int(raw)
    asset_url = f"https://www.roblox.com/asset/?id={aid}"
    delivery_url = f"https://assetdelivery.roblox.com/v1/asset/?id={aid}"
    if lang == "ru":
        text = (
            f"🧩 <b>Template / Asset для ID {aid}</b>\n\n"
            f"Стандартный asset URL:\n<code>{asset_url}</code>\n\n"
            f"AssetDelivery URL:\n<code>{delivery_url}</code>\n\n"
            f"Открой в браузере или вставь в Studio как rbxassetid."
        )
    else:
        text = (
            f"🧩 <b>Template / Asset for ID {aid}</b>\n\n"
            f"Standard asset URL:\n<code>{asset_url}</code>\n\n"
            f"AssetDelivery URL:\n<code>{delivery_url}</code>\n\n"
            f"Open in browser or use in Studio as rbxassetid."
        )
    await message.answer(text)


@dp.message(Command("offsales"))
@track_command
async def cmd_offsales(message, command: CommandObject):
    lang = get_lang(message)
    name = (command.args or "").strip()
    if not name:
        if lang == "ru":
            return await message.answer(
                "/offsales &lt;Имя&gt;\n→ Оффсейл-предметы (ограничено API)\nПример: <code>/offsales d45wn</code>"
            )
        return await message.answer(
            "/offsales &lt;Username&gt;\n→ Offsale catalog items (API-limited)\nExample: <code>/offsales d45wn</code>"
        )
    if lang == "ru":
        await message.answer(
            "❌ Полноценный список оффсейл-предметов сейчас нельзя получить через публичные Roblox API.\n"
            "Как только появится стабильный источник данных, эта команда будет обновлена."
        )
    else:
        await message.answer(
            "❌ A full offsale item scan is not currently possible via public Roblox APIs.\n"
            "Once a stable data source is available, this command will be upgraded."
        )


@dp.message(Command("links"))
@track_command
async def cmd_links(message, command: CommandObject):
    lang = get_lang(message)
    if lang == "ru":
        await message.answer("🔗 Скоро здесь появятся ссылки на канал и другие ресурсы RBLXScan.")
    else:
        await message.answer("🔗 Soon: links to the main channel and other RBLXScan resources will appear here.")


@dp.message(Command("botstats"))
@track_command
async def cmd_botstats(message, command):
    lang = get_lang(message)

    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.answer(
            "Эта команда доступна только владельцу бота." if lang == "ru"
            else "This command is owner-only."
        )

    now = dt.datetime.now(dt.timezone.utc)
    uptime_sec = (now - START_TIME).total_seconds()
    uptime_str = format_uptime(uptime_sec)

    total_chats = len(CHAT_IDS)
    total_users = len(USER_IDS)
    cmds = TOTAL_COMMANDS
    per_hour = cmds / (uptime_sec / 3600) if uptime_sec > 0 else cmds

    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / (1024 * 1024)

    restart_str = START_TIME.strftime("%Y-%m-%d %H:%M:%S UTC")

    last_cmd_text = ""
    if USER_LAST_COMMAND:
        last_uid = list(USER_LAST_COMMAND.keys())[-1]
        last_cmd = USER_LAST_COMMAND[last_uid]
        last_args = USER_LAST_ARGS.get(last_uid, "")
        last_cmd_text = f"{last_cmd} {last_args}".strip()
    else:
        last_cmd_text = "None"

    if USER_COMMAND_COUNT:
        sorted_top = sorted(
            USER_COMMAND_COUNT.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        top_users_text = "\n".join(
            [f"• <code>{uid}</code>: {count} commands" for uid, count in sorted_top]
        )
    else:
        top_users_text = "None"

    # Group chats count
    active_groups = len([c for c in CHAT_IDS if c < 0])  # negative IDs = groups
    private_chats = len([c for c in CHAT_IDS if c > 0])  # positive IDs = private

    # Last broadcast info
    if BROADCAST_HISTORY:
        last_bc = BROADCAST_HISTORY[-1]
        bc_type = last_bc.get("type", "broadcast")
        bc_icon = "📰" if bc_type == "announcement" else "📢"
        broadcast_text = (
            f"{bc_icon} {last_bc['date']}\n"
            f"   ✅ {last_bc['success']} | ❌ {last_bc['failed']}"
        )
    else:
        broadcast_text = "Never" if lang != "ru" else "Никогда"

    if lang == "ru":
        text = (
            "📈 <b>Статистика бота</b>\n\n"
            "👥 <b>Пользователи</b>\n"
            f"├ Всего пользователей: <code>{total_users}</code>\n"
            f"├ Личных чатов: <code>{private_chats}</code>\n"
            f"└ Групповых чатов: <code>{active_groups}</code>\n\n"
            "⚙️ <b>Активность</b>\n"
            f"├ Всего команд: <code>{cmds}</code>\n"
            f"├ Команд в час: <code>{per_hour:.2f}</code>\n"
            f"├ Аптайм: <code>{uptime_str}</code>\n"
            f"├ Память: <code>{mem_mb:.2f} MB</code>\n"
            f"└ Последний рестарт: <code>{restart_str}</code>\n\n"
            f"🕹️ <b>Последняя команда:</b> <code>{last_cmd_text}</code>\n\n"
            f"📢 <b>Последняя рассылка:</b>\n└ {broadcast_text}\n\n"
            f"🏆 <b>Топ пользователей:</b>\n{top_users_text}"
        )
    else:
        text = (
            "📈 <b>Bot Statistics</b>\n\n"
            "👥 <b>Users</b>\n"
            f"├ Total users: <code>{total_users}</code>\n"
            f"├ Private chats: <code>{private_chats}</code>\n"
            f"└ Group chats: <code>{active_groups}</code>\n\n"
            "⚙️ <b>Activity</b>\n"
            f"├ Total commands: <code>{cmds}</code>\n"
            f"├ Commands/hour: <code>{per_hour:.2f}</code>\n"
            f"├ Uptime: <code>{uptime_str}</code>\n"
            f"├ Memory: <code>{mem_mb:.2f} MB</code>\n"
            f"└ Last restart: <code>{restart_str}</code>\n\n"
            f"🕹️ <b>Last command:</b> <code>{last_cmd_text}</code>\n\n"
            f"📢 <b>Last broadcast:</b>\n└ {broadcast_text}\n\n"
            f"🏆 <b>Top users:</b>\n{top_users_text}"
        )

    await message.answer(text, parse_mode="HTML")

@dp.callback_query(F.data == "help_open")
async def cb_help_open(cb: CallbackQuery):
    lang = get_lang_cb(cb)
    if lang == "ru":
        txt = (
            "🧑‍🔧 Быстрый гайд:\n\n"
            "/user <code>имя</code> — профиль\n"
            "/limiteds <code>имя</code> — лимитки с RAP/Value\n"
            "/rolimons <code>имя</code> — статистика Rolimons\n"
            "/assetid <code>asset_id</code> — инфо о предмете\n"
            "/devex <code>robux</code> — примерная сумма в $"
        )
    else:
        txt = (
            "🧑‍🔧 Quick usage:\n\n"
            "/user <code>username</code> — full profile\n"
            "/limiteds <code>username</code> — all limiteds with RAP & value\n"
            "/rolimons <code>username</code> — Rolimons stats\n"
            "/assetid <code>asset_id</code> — item info\n"
            "/devex <code>robux</code> — approx cash value"
        )
    await cb.message.answer(txt)
    await cb.answer()


@dp.callback_query(F.data.startswith("roli_stats:"))
async def cb_roli_stats(cb: CallbackQuery):
    lang = get_lang_cb(cb)
    try:
        uid = int(cb.data.split(":", 1)[1])
    except Exception:
        return await cb.answer("Invalid data", show_alert=True)
    text = await compose_limiteds_text(uid, lang)
    await cb.message.answer(text)
    await cb.answer()


@dp.callback_query(F.data.startswith("set_lang:"))
async def cb_set_lang(cb: CallbackQuery):
    code = cb.data.split(":", 1)[1]
    if code not in ("en", "ru"):
        return await cb.answer("Invalid language", show_alert=True)
    if cb.from_user:
        USER_LANG[cb.from_user.id] = code
    if code == "ru":
        await cb.message.answer("Язык бота установлен на: 🇷🇺 Русский.")
    else:
        await cb.message.answer("Bot language set to: 🇬🇧 English.")
    await cb.answer()

# Track when bot is added to or removed from groups
@dp.my_chat_member()
async def on_bot_chat_member_update(event):
    try:
        # Bot added to group
        if (
            event.new_chat_member.user.id == bot.id
            and event.new_chat_member.status in ("member", "administrator")
            and event.old_chat_member.status in ("left", "kicked", "restricted")
        ):
            CHAT_IDS.add(event.chat.id)
            logging.info(f"Bot added to group: {event.chat.id} ({event.chat.title})")

        # Bot removed from group
        elif (
            event.new_chat_member.user.id == bot.id
            and event.new_chat_member.status in ("left", "kicked")
        ):
            CHAT_IDS.discard(event.chat.id)
            logging.info(f"Bot removed from group: {event.chat.id}")

    except Exception as e:
        logging.error(f"Error in chat member update: {e}")
        
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_messages(message: Message):
    pass


@dp.message()
async def handle_unknown(message: Message):
    pass
    
async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Webhook deleted, starting polling...")
    except Exception as e:
        logging.warning(f"Could not delete webhook: {e}")
        
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start / short help"),
            BotCommand(command="help", description="Full command list"),
            BotCommand(command="language", description="Change bot language"),

            BotCommand(command="user", description="Lookup user by username"),
            BotCommand(command="id", description="Lookup user by ID"),
            BotCommand(command="username", description="Check username availability"),
            BotCommand(command="displayname", description="Search by display name"),
            BotCommand(command="copyid", description="Copy user ID"),
            BotCommand(command="idtousername", description="IDs to usernames"),
            BotCommand(command="banned", description="Check if user is banned"),
            BotCommand(command="accountage", description="Show account age"),
            BotCommand(command="lastonline", description="Show last online"),

            BotCommand(command="avatar", description="Avatar render"),
            BotCommand(command="headshot", description="Headshot render"),
            BotCommand(command="bust", description="Bust render"),

            BotCommand(command="assetid", description="Asset info by ID"),
            BotCommand(command="asseticon", description="Asset icon"),
            BotCommand(command="template", description="Asset template URL"),

            BotCommand(command="groupid", description="Group by ID"),
            BotCommand(command="group", description="Search group by name"),
            BotCommand(command="groupicon", description="Open group link"),
            BotCommand(command="groups", description="Show user groups"),

            BotCommand(command="friends", description="Show user's friends"),
            BotCommand(command="followers", description="Show user's followers"),
            BotCommand(command="followings", description="Show user's followings"),

            BotCommand(command="limiteds", description="Show user limiteds"),
            BotCommand(command="rolimons", description="Rolimons stats"),
            BotCommand(command="devex", description="Robux → USD"),
            BotCommand(command="devexcad", description="Robux → CAD"),

            BotCommand(command="names", description="Username history"),
            BotCommand(command="verified", description="Verification status"),
            BotCommand(command="owned", description="Check item ownership"),
            BotCommand(command="obtained", description="When badge was obtained"),
            BotCommand(command="offsales", description="Offsale info"),
            BotCommand(command="links", description="Links"),
        ]
    )
    print("Bot running...")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        polling_timeout=30,
        handle_as_tasks=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
