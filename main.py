# =========================================================
# AUTO-INSTALL REQUIRED PACKAGES (for Python 3.13)
# =========================================================
import sys
import subprocess
import pkgutil

REQUIRED = ["aiogram", "aiohttp", "python-dotenv"]

MODULE_NAME = {
    "python-dotenv": "dotenv",
}

for pkg in REQUIRED:
    mod = MODULE_NAME.get(pkg, pkg)
    if pkgutil.find_loader(mod) is None:
        print(f"[AUTO-INSTALL] Installing {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

# =========================================================
# IMPORTS
# =========================================================
import os
import asyncio
import datetime as dt
from typing import List, Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from aiogram.client.default import DefaultBotProperties
from aiohttp import ClientTimeout

# =========================================================
# CONFIG
# =========================================================

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN in .env!")

USD_PER_ROBUX = 0.0038
USD_TO_CAD = 1.35

bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# =========================================================
# UTILITIES
# =========================================================

def esc(t: str) -> str:
    """Escape &, <, > for HTML."""
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_ids(raw: str, max_count=20):
    ids: List[int] = []
    for p in raw.replace(",", " ").split():
        if p.isdigit():
            ids.append(int(p))
        if len(ids) >= max_count:
            break
    return ids


def parse_iso8601(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# =========================================================
# HTTP SESSIONS
# =========================================================

class RobloxAPI:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = ClientTimeout(total=15)

    async def ensure(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 (RobloxBot/1.0)"}
            )
        return self.session

    async def req(self, method: str, url: str, **kwargs):
        s = await self.ensure()
        async with s.request(method, url, **kwargs) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = None
            if r.status == 404:
                return None
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}: {data}")
            return data

    # ------------------ USERS ------------------
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

    # ------------------ PRESENCE ------------------
    async def get_presence(self, ids: List[int]):
        if not ids:
            return None
        return await self.req(
            "POST",
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": ids},
        )

    # ------------------ THUMBNAILS ------------------
    async def get_user_thumbnail(self, user_id: int, ttype: str) -> Optional[str]:
        base = "https://thumbnails.roblox.com/v1/users"
        if ttype == "avatar":
            path = "avatar"
            size = "720x720"
        elif ttype == "headshot":
            path = "avatar-headshot"
            size = "720x720"
        else:  # bust → some sizes are invalid, use safe one
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

    # ------------------ ASSETS ------------------
    async def get_asset_info(self, aid: int):
        return await self.req(
            "GET",
            f"https://api.roblox.com/marketplace/productinfo?assetId={aid}",
        )

    # ------------------ GROUPS ------------------
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
        # FIX: API returns {"data":[...]}
        if not data:
            return []
        return data.get("data", [])

    # ------------------ FRIENDS / FOLLOWS ------------------
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

    # ------------------ LIMITEDS ------------------
    async def get_collectibles(self, uid: int):
        from urllib.parse import urlencode
        base = f"https://inventory.roblox.com/v1/users/{uid}/assets/collectibles"
        items = []
        cursor = None

        while True:
            params = {"limit": 100, "sortOrder": "Asc"}
            if cursor:
                params["cursor"] = cursor
            data = await self.req("GET", base + "?" + urlencode(params))
            if not data:
                break
            items.extend(data.get("data", []))
            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        return items


roblox = RobloxAPI()

# ------------------ ROLIMONS ------------------

ROLI_SESSION: Optional[aiohttp.ClientSession] = None
ROLI_ITEMS_CACHE: Optional[Dict[str, list]] = None


async def roli_ensure():
    global ROLI_SESSION
    if ROLI_SESSION is None or ROLI_SESSION.closed:
        ROLI_SESSION = aiohttp.ClientSession(
            timeout=ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (RolimonsBot/1.0)"}
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


# =========================================================
# COMMAND HANDLERS
# =========================================================

# --------------- /start & /help ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "🎮 <b>Roblox Lookup Bot</b>\n\n"
        "Use me to inspect profiles, limiteds, groups, assets and values.\n\n"
        "📂 <b>Basic commands:</b>\n"
        "• /user <code>username</code>\n"
        "• /id <code>user_id</code>\n"
        "• /limiteds <code>username</code>\n"
        "• /rolimons <code>username</code>\n"
        "• /devex <code>robux</code>\n\n"
        "Use /help for full command list."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧑‍🔧 Help & Commands", callback_data="help_open")],
        ]
    )
    await message.answer(text, reply_markup=kb)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "🧑‍🔧 <b>Full command list</b>\n\n"
        "<b>Users:</b>\n"
        "• /user <code>username</code>\n"
        "• /id <code>user_id</code>\n"
        "• /username <code>name</code>\n"
        "• /displayname <code>name</code>\n"
        "• /copyid <code>username</code>\n"
        "• /idtousername <code>id1 id2 ...</code>\n"
        "• /banned <code>id1 id2 ...</code>\n"
        "• /accountage <code>username</code>\n"
        "• /lastonline <code>username</code>\n\n"
        "<b>Avatar:</b>\n"
        "• /avatar <code>username</code>\n"
        "• /headshot <code>username</code>\n"
        "• /bust <code>username</code>\n\n"
        "<b>Assets:</b>\n"
        "• /assetid <code>asset_id</code>\n"
        "• /asseticon <code>asset_id</code>\n\n"
        "<b>Groups:</b>\n"
        "• /groupid <code>group_id</code>\n"
        "• /group <code>name</code>\n"
        "• /groupicon <code>group_id</code>\n"
        "• /groups <code>username</code>\n\n"
        "<b>Social:</b>\n"
        "• /friends <code>username</code>\n"
        "• /followers <code>username</code>\n"
        "• /followings <code>username</code>\n\n"
        "<b>Limiteds & Value:</b>\n"
        "• /limiteds <code>username</code>\n"
        "• /rolimons <code>username</code>\n"
        "• /devex <code>robux</code>\n"
        "• /devexcad <code>robux</code>\n"
    )
    await message.answer(text)


# --------------- USER COMMANDS ----------------

def user_profile_keyboard(uid: int, username: str):
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
                    text="📊 Rolimons Site",
                    url=f"https://www.rolimons.com/player/{uid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Rolimons Stats",
                    callback_data=f"roli_stats:{uid}"
                )
            ],
        ]
    )


@dp.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer("Usage: <code>/user username</code>")

    try:
        user = await roblox.get_user_details_by_username(name)
    except Exception as e:
        return await message.answer(f"Error: <code>{esc(str(e))}</code>")

    if not user:
        return await message.answer("User not found.")

    desc = esc((user.get("description") or "").strip()[:600])
    created = parse_iso8601(user["created"])
    created_str = created.strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"👤 <b>{esc(user['name'])}</b> "
        f"(<i>{esc(user['displayName'])}</i>)\n"
        f"🆔 ID: <code>{user['id']}</code>\n"
        f"📅 Created: <code>{created_str}</code>\n"
        f"✅ Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"⛔ Banned: <code>{user.get('isBanned', False)}</code>\n\n"
        f"<a href=\"https://www.roblox.com/users/{user['id']}/profile\">Roblox profile</a>\n"
        f"<a href=\"https://www.rolimons.com/player/{user['id']}\">Rolimons profile</a>\n"
    )
    if desc:
        text += f"\n<b>📜 Description:</b>\n{desc}"

    thumb = await roblox.get_user_thumbnail(user["id"], "headshot")
    kb = user_profile_keyboard(user["id"], user["name"])

    if thumb:
        return await message.answer_photo(thumb, caption=text, reply_markup=kb)
    return await message.answer(text, reply_markup=kb)


@dp.message(Command("id"))
async def cmd_id(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        return await message.answer("Usage: <code>/id user_id</code>")

    uid = int(arg)
    try:
        user = await roblox.get_user_by_id(uid)
    except Exception as e:
        return await message.answer(f"Error: <code>{esc(str(e))}</code>")

    if not user:
        return await message.answer("User not found.")

    desc = esc((user.get("description") or "").strip()[:600])
    created = parse_iso8601(user["created"])
    created_str = created.strftime("%Y-%m-%d %H:%M UTC")

    txt = (
        f"👤 <b>{esc(user['name'])}</b> "
        f"(<i>{esc(user['displayName'])}</i>)\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📅 Created: <code>{created_str}</code>\n"
        f"✅ Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"⛔ Banned: <code>{user.get('isBanned', False)}</code>\n\n"
        f"<a href=\"https://www.roblox.com/users/{uid}/profile\">Roblox profile</a>\n"
        f"<a href=\"https://www.rolimons.com/player/{uid}\">Rolimons profile</a>\n"
    )
    if desc:
        txt += f"\n<b>📜 Description:</b>\n{desc}"

    thumb = await roblox.get_user_thumbnail(uid, "headshot")
    kb = user_profile_keyboard(uid, user["name"])

    if thumb:
        return await message.answer_photo(thumb, caption=txt, reply_markup=kb)
    return await message.answer(txt, reply_markup=kb)


@dp.message(Command("username"))
async def cmd_username(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/username name</code>")

    user = await roblox.get_user_by_username(u)
    if user:
        return await message.answer(
            f"❌ <code>{esc(u)}</code> is taken by "
            f"<code>{esc(user['name'])}</code> (ID <code>{user['id']}</code>)"
        )
    await message.answer(f"✅ <code>{esc(u)}</code> seems available.")


@dp.message(Command("displayname"))
async def cmd_displayname(message: Message, command: CommandObject):
    d = (command.args or "").strip()
    if not d:
        return await message.answer("Usage: <code>/displayname name</code>")

    results = await roblox.search_displayname(d)
    if not results:
        return await message.answer("No results.")

    exact = [x for x in results if x["displayName"].lower() == d.lower()]
    lines = []

    if exact:
        lines.append(f"🔍 <b>Exact matches</b> ({len(exact)}):")
        for u in exact[:5]:
            lines.append(
                f"• {esc(u['displayName'])} / {esc(u['name'])} "
                f"(<code>{u['id']}</code>)"
            )
    else:
        lines.append("🔍 <b>Similar results:</b>")
        for u in results[:5]:
            lines.append(
                f"• {esc(u['displayName'])} / {esc(u['name'])} "
                f"(<code>{u['id']}</code>)"
            )

    await message.answer("\n".join(lines))


@dp.message(Command("copyid"))
async def cmd_copyid(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer("Usage: <code>/copyid username</code>")

    u = await roblox.get_user_by_username(name)
    if not u:
        return await message.answer("User not found.")
    await message.answer(
        f"🆔 ID of <code>{esc(u['name'])}</code> = <code>{u['id']}</code>"
    )


@dp.message(Command("idtousername"))
async def cmd_idtousername(message: Message, command: CommandObject):
    ids = parse_ids((command.args or ""), 50)
    if not ids:
        return await message.answer(
            "Usage: <code>/idtousername id1 id2 ...</code>"
        )

    info = await roblox.get_users_by_ids(ids)
    lines = ["🔁 <b>ID → Username</b>"]

    for i in ids:
        u = info.get(i)
        if u:
            lines.append(
                f"{i} → {esc(u['name'])} / {esc(u['displayName'])}"
            )
        else:
            lines.append(f"{i} → not found")

    await message.answer("\n".join(lines))


@dp.message(Command("banned"))
async def cmd_banned(message: Message, command: CommandObject):
    ids = parse_ids((command.args or ""), 20)
    if not ids:
        return await message.answer(
            "Usage: <code>/banned id1 id2 ...</code>"
        )

    lines = ["⛔ <b>Banned status:</b>"]
    for i in ids:
        u = await roblox.get_user_by_id(i)
        if u:
            lines.append(f"{i}: banned = <code>{u.get('isBanned', False)}</code>")
        else:
            lines.append(f"{i}: not found")
    await message.answer("\n".join(lines))


@dp.message(Command("accountage"))
async def cmd_accountage(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer(
            "Usage: <code>/accountage username</code>"
        )

    u = await roblox.get_user_details_by_username(name)
    if not u:
        return await message.answer("User not found.")

    created = parse_iso8601(u["created"])
    now = dt.datetime.now(dt.timezone.utc)
    days = (now - created).days

    await message.answer(
        f"📅 <b>{esc(u['name'])}</b>\n"
        f"Created: <code>{created.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
        f"Age: <code>{days}</code> days (~<code>{days/365:.2f}</code> years)"
    )


@dp.message(Command("lastonline"))
async def cmd_lastonline(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer(
            "Usage: <code>/lastonline username</code>"
        )

    u = await roblox.get_user_by_username(name)
    if not u:
        return await message.answer("User not found.")

    pr = await roblox.get_presence([u["id"]])
    p = (pr or {}).get("userPresences", [{}])[0]

    last = p.get("lastOnline")
    loc = p.get("lastLocation") or "Unknown"

    if last:
        last = parse_iso8601(last).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        last = "Unknown"

    await message.answer(
        f"⏱ <b>{esc(u['name'])}</b>\n"
        f"Location: <code>{esc(loc)}</code>\n"
        f"Last Online: <b>{last}</b>"
    )


# --------------- AVATAR COMMANDS --------------

@dp.message(Command("avatar"))
async def cmd_avatar(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer(
            "Usage: <code>/avatar username</code>"
        )

    u = await roblox.get_user_by_username(name)
    if not u:
        return await message.answer("User not found.")

    url = await roblox.get_user_thumbnail(u["id"], "avatar")
    if not url:
        return await message.answer("No avatar thumbnail available.")

    await message.answer_photo(url, caption=f"🧍 Avatar of <b>{esc(u['name'])}</b>")


@dp.message(Command("headshot"))
async def cmd_headshot(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer(
            "Usage: <code>/headshot username</code>"
        )

    u = await roblox.get_user_by_username(name)
    if not u:
        return await message.answer("User not found.")

    url = await roblox.get_user_thumbnail(u["id"], "headshot")
    if not url:
        return await message.answer("No headshot thumbnail available.")

    await message.answer_photo(url, caption=f"🙂 Headshot of <b>{esc(u['name'])}</b>")


@dp.message(Command("bust"))
async def cmd_bust(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer(
            "Usage: <code>/bust username</code>"
        )

    u = await roblox.get_user_by_username(name)
    if not u:
        return await message.answer("User not found.")

    url = await roblox.get_user_thumbnail(u["id"], "bust")
    if not url:
        return await message.answer("No bust thumbnail available.")

    await message.answer_photo(url, caption=f"🧍‍♂️ Bust of <b>{esc(u['name'])}</b>")


# --------------- ASSETS COMMANDS --------------

@dp.message(Command("assetid"))
async def cmd_assetid(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/assetid id</code>")

    aid = int(raw)
    info = await roblox.get_asset_info(aid)
    if not info:
        return await message.answer("Asset not found.")

    desc = esc((info.get("Description") or "")[:600])
    c = info.get("Creator") or {}

    text = (
        f"🎩 <b>{esc(info.get('Name', '?'))}</b>\n"
        f"🆔 ID: <code>{aid}</code>\n"
        f"👤 Creator: <code>{esc(c.get('Name', '?'))}</code> "
        f"(<code>{c.get('Id', '?')}</code>)\n"
        f"💰 Price: <code>{info.get('PriceInRobux', 'N/A')}</code>\n"
        f"♻️ Limited: <code>{info.get('IsLimited')}</code>\n"
        f"♻️ LimitedU: <code>{info.get('IsLimitedUnique')}</code>\n"
        f"<a href=\"https://www.roblox.com/catalog/{aid}\">Open in catalog</a>"
    )
    if desc:
        text += f"\n\n<b>📜 Description:</b>\n{desc}"

    icon = await roblox.get_asset_icon(aid)
    if icon:
        return await message.answer_photo(icon, caption=text)
    return await message.answer(text)


@dp.message(Command("asseticon"))
async def cmd_asseticon(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/asseticon id</code>")

    aid = int(raw)
    icon = await roblox.get_asset_icon(aid)
    if not icon:
        return await message.answer("No icon.")

    await message.answer_photo(icon, caption=f"🎴 Asset <b>{aid}</b>")


# --------------- GROUP COMMANDS --------------

@dp.message(Command("groupid"))
async def cmd_groupid(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/groupid id</code>")

    gid = int(raw)
    g = await roblox.get_group_by_id(gid)
    if not g:
        return await message.answer("Group not found.")

    desc = esc((g.get("description") or "")[:600])
    owner = g.get("owner") or {}

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
async def cmd_group(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.answer("Usage: <code>/group name</code>")

    results = await roblox.search_group_by_name(name)
    if not results:
        return await message.answer("No groups found.")

    g = results[0]
    gid = g["id"]
    full = await roblox.get_group_by_id(gid)

    desc = esc((full.get("description") or "")[:600])
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
async def cmd_groupicon(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/groupicon id</code>")

    gid = int(raw)
    # Roblox has separate group icon endpoint; but we can still show text only.
    await message.answer(
        f"Roblox group icons are not available via this endpoint yet.\n\n"
        f"Open group: https://www.roblox.com/groups/{gid}"
    )


@dp.message(Command("groups"))
async def cmd_groups(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/groups username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    data = await roblox.get_user_groups(base["id"])
    if not data:
        return await message.answer("No groups / private.")

    lines = [f"👥 <b>Groups of {esc(base['name'])}:</b>"]
    for g in data[:20]:
        group = g.get("group", {})
        role = g.get("role", {})
        lines.append(
            f"• {esc(group.get('name', '?'))} "
            f"(<code>{group.get('id')}</code>) — role: <code>{esc(role.get('name', '?'))}</code>"
        )

    await message.answer("\n".join(lines))


# --------------- FRIENDS / FOLLOWERS ----------

@dp.message(Command("friends"))
async def cmd_friends(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/friends username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    data = await roblox.get_friends(base["id"])
    if not data:
        return await message.answer("No friends.")

    lines = [f"👥 <b>Friends of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")

    await message.answer("\n".join(lines))


@dp.message(Command("followers"))
async def cmd_followers(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/followers username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    data = await roblox.get_followers(base["id"])
    if not data:
        return await message.answer("No followers.")

    lines = [f"⭐ <b>Followers of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")

    await message.answer("\n".join(lines))


@dp.message(Command("followings"))
async def cmd_followings(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/followings username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    data = await roblox.get_followings(base["id"])
    if not data:
        return await message.answer("No followings.")

    lines = [f"➡️ <b>Followings of {esc(base['name'])}:</b>"]
    for f in data[:25]:
        name = esc(f.get("name", "Unknown"))
        fid = f.get("id")
        lines.append(f"• {name} (<code>{fid}</code>)")

    await message.answer("\n".join(lines))


# --------------- LIMITEDS & ROLIMONS ----------

@dp.message(Command("limiteds"))
async def cmd_limiteds(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/limiteds username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    items = await roblox.get_collectibles(base["id"])
    if not items:
        return await message.answer("No collectibles or inventory is private.")

    # Try to load Rolimons items for value mapping
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

        value = None
        if roli_items:
            arr = roli_items.get(str(aid))
            if arr:
                roli_val = arr[3]
                if roli_val and roli_val > 0:
                    value = roli_val
        if value is None:
            value = rap
        total_value += value

        # single line item with hyperlink
        lines.append(
            f"• <a href=\"https://www.rolimons.com/item/{aid}\">{aname}</a> — "
            f"RAP: <code>{rap:,}</code> | Value: <code>{value:,}</code>"
        )

    header = (
        f"💼 <b>Limiteds of {esc(base['name'])}</b>\n"
        f"Total items: <code>{len(items)}</code>\n"
        f"Total RAP: <code>{total_rap:,}</code>\n"
        f"Total Value: <code>{total_value:,}</code>\n"
        f"<a href=\"https://www.rolimons.com/player/{base['id']}\">Rolimons profile</a>\n"
    )
    if roli_err:
        header += f"\n⚠️ Rolimons value issue: <code>{esc(roli_err)}</code>\n"

    text = header + "\n" + "\n".join(lines)
    await message.answer(text)


@dp.message(Command("rolimons"))
async def cmd_rolimons(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        return await message.answer("Usage: <code>/rolimons username</code>")

    base = await roblox.get_user_by_username(u)
    if not base:
        return await message.answer("User not found.")

    uid = base["id"]
    try:
        data = await roli_get(f"https://api.rolimons.com/players/v1/playerinfo/{uid}")
    except Exception as e:
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

    text = (
        f"📊 <b>Rolimons stats for {esc(base['name'])}</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💰 RAP: <code>{rap:,}</code>\n"
        f"💎 Value: <code>{value:,}</code>\n"
        f"⭐ Premium: <code>{premium}</code>\n"
        f"📦 Inventory public: <code>{inv_public}</code>\n"
        f"⏱ Last online: <code>{last_online_str}</code>\n\n"
        f"<a href=\"https://www.rolimons.com/player/{uid}\">Open on Rolimons</a>"
    )

    await message.answer(text)


# --------------- DEVEX ------------------------

@dp.message(Command("devex"))
async def cmd_devex(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/devex robux</code>")

    r = int(raw)
    usd = r * USD_PER_ROBUX
    await message.answer(
        f"💵 <code>{r:,}</code> R$ ≈ <b>${usd:,.2f}</b> USD (approx.)"
    )


@dp.message(Command("devexcad"))
async def cmd_devexcad(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        return await message.answer("Usage: <code>/devexcad robux</code>")

    r = int(raw)
    cad = (r * USD_PER_ROBUX) * USD_TO_CAD
    await message.answer(
        f"💵 <code>{r:,}</code> R$ ≈ <b>${cad:,.2f}</b> CAD (approx.)"
    )


# =========================================================
# CALLBACKS (help & rolimons stats from /user button)
# =========================================================
from aiogram.types import CallbackQuery
from aiogram import F


@dp.callback_query(F.data == "help_open")
async def cb_help_open(cb: CallbackQuery):
    await cb.message.answer(
        "🧑‍🔧 Quick usage:\n\n"
        "• /user <code>username</code> — full profile\n"
        "• /limiteds <code>username</code> — all limiteds with RAP & value\n"
        "• /rolimons <code>username</code> — Rolimons stats\n"
        "• /assetid <code>asset_id</code> — item info\n"
        "• /devex <code>robux</code> — approx cash value\n\n"
        "Use /help for everything."
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("roli_stats:"))
async def cb_roli_stats(cb: CallbackQuery):
    try:
        uid = int(cb.data.split(":", 1)[1])
    except Exception:
        return await cb.answer("Invalid data", show_alert=True)

    # We don't know username here, only ID → try fetch
    user = await roblox.get_user_by_id(uid)
    name = user.get("name", str(uid)) if user else str(uid)

    try:
        data = await roli_get(f"https://api.rolimons.com/players/v1/playerinfo/{uid}")
    except Exception as e:
        await cb.message.answer(
            f"Rolimons error: <code>{esc(str(e))}</code>\n\n"
            f"https://www.rolimons.com/player/{uid}"
        )
        return await cb.answer()

    rap = data.get("rap")
    value = data.get("value")
    text = (
        f"📈 <b>Rolimons stats for {esc(name)}</b>\n"
        f"RAP: <code>{rap:,}</code>\n"
        f"Value: <code>{value:,}</code>\n"
        f"https://www.rolimons.com/player/{uid}"
    )
    await cb.message.answer(text)
    await cb.answer()


# =========================================================
# LAUNCH
# =========================================================

async def main():
    # set bot commands for Telegram menu
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start / short help"),
            BotCommand(command="help", description="Full command list"),

            # Users
            BotCommand(command="user", description="Lookup user by username"),
            BotCommand(command="id", description="Lookup user by ID"),
            BotCommand(command="username", description="Lookup username from name"),
            BotCommand(command="displayname", description="Find username by display name"),
            BotCommand(command="copyid", description="Copy user ID"),
            BotCommand(command="idtousername", description="Convert IDs to usernames"),
            BotCommand(command="banned", description="Check if user is banned"),
            BotCommand(command="accountage", description="Show account age"),
            BotCommand(command="lastonline", description="Show last online time"),

            # Avatar
            BotCommand(command="avatar", description="Avatar render"),
            BotCommand(command="headshot", description="Headshot render"),
            BotCommand(command="bust", description="Bust render"),

            # Assets
            BotCommand(command="assetid", description="Asset info by ID"),
            BotCommand(command="asseticon", description="Asset icon"),

            # Groups
            BotCommand(command="groupid", description="Group by ID"),
            BotCommand(command="group", description="Search group by name"),
            BotCommand(command="groupicon", description="Group icon"),
            BotCommand(command="groups", description="Show user groups"),

            # Social
            BotCommand(command="friends", description="Show user's friends"),
            BotCommand(command="followers", description="Show user's followers"),
            BotCommand(command="followings", description="Show user's followings"),

            # Limiteds & Value
            BotCommand(command="limiteds", description="Show user limiteds"),
            BotCommand(command="rolimons", description="Rolimons stats"),
            BotCommand(command="devex", description="Robux → USD"),
            BotCommand(command="devexcad", description="Robux → CAD"),
        ]
    )

    print("Bot running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
