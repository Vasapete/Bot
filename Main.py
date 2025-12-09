import os
import asyncio
import math
import datetime as dt
from typing import List, Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

# ========================
# CONFIG
# ========================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "PUT_YOUR_TOKEN_HERE"
USD_PER_ROBUX = 0.0038   # Approx DevEx rate, adjust as wanted
USD_TO_CAD = 1.35        # Approx FX rate, adjust as wanted

PARSE_MODE = "HTML"


# ========================
# ROBLOX API CLIENT
# ========================

class RobloxAPI:
    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=15)

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request_json(self, method: str, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        session = await self.ensure_session()
        async with session.request(method, url, **kwargs) as resp:
            # Many Roblox endpoints return JSON even on error
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None
            if resp.status == 404:
                return None
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} for {url}: {data}")
            return data

    # ---------- USERS ----------

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """
        POST /v1/usernames/users to get id from username.
        """
        url = "https://users.roblox.com/v1/usernames/users"
        payload = {"usernames": [username], "excludeBannedUsers": False}
        data = await self._request_json("POST", url, json=payload)
        if not data or not data.get("data"):
            return None
        return data["data"][0]

    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        url = f"https://users.roblox.com/v1/users/{user_id}"
        return await self._request_json("GET", url)

    async def get_user_details_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        basic = await self.get_user_by_username(username)
        if not basic:
            return None
        full = await self.get_user_by_id(basic["id"])
        return full

    async def get_users_by_ids(self, ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        POST /v1/users with userIds[] array.
        """
        if not ids:
            return {}
        url = "https://users.roblox.com/v1/users"
        payload = {"userIds": ids}
        data = await self._request_json("POST", url, json=payload)
        result: Dict[int, Dict[str, Any]] = {}
        if not data or not data.get("data"):
            return result
        for entry in data["data"]:
            result[entry["id"]] = entry
        return result

    async def search_displayname(self, displayname: str, limit: int = 10) -> List[Dict[str, Any]]:
        from urllib.parse import urlencode
        params = urlencode({"keyword": displayname, "limit": limit})
        url = f"https://users.roblox.com/v1/users/search?{params}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    # ---------- PRESENCE ----------

    async def get_presence(self, user_ids: List[int]) -> Optional[Dict[str, Any]]:
        if not user_ids:
            return None
        url = "https://presence.roblox.com/v1/presence/users"
        payload = {"userIds": user_ids}
        return await self._request_json("POST", url, json=payload)

    # ---------- THUMBNAILS ----------

    async def get_user_thumbnail(self, user_id: int, ttype: str = "avatar") -> Optional[str]:
        base = "https://thumbnails.roblox.com/v1/users"
        if ttype == "headshot":
            path = "avatar-headshot"
        elif ttype == "bust":
            path = "avatar-bust"
        else:
            path = "avatar"
        url = f"{base}/{path}?userIds={user_id}&size=720x720&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    async def get_asset_icon(self, asset_id: int, size: str = "512x512") -> Optional[str]:
        url = f"https://thumbnails.roblox.com/v1/assets?assetIds={asset_id}&size={size}&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    async def get_group_icon(self, group_id: int, size: str = "420x420") -> Optional[str]:
        url = f"https://thumbnails.roblox.com/v1/groups/icons?groupIds={group_id}&size={size}&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    # ---------- ASSETS / CATALOG ----------

    async def get_asset_info(self, asset_id: int) -> Optional[Dict[str, Any]]:
        # Classic marketplace productinfo
        url = f"https://api.roblox.com/marketplace/productinfo?assetId={asset_id}"
        return await self._request_json("GET", url)

    # ---------- GROUPS ----------

    async def get_group_by_id(self, group_id: int) -> Optional[Dict[str, Any]]:
        url = f"https://groups.roblox.com/v1/groups/{group_id}"
        return await self._request_json("GET", url)

    async def search_group_by_name(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        from urllib.parse import urlencode
        params = urlencode({"keyword": name, "limit": limit})
        url = f"https://groups.roblox.com/v1/groups/search?{params}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    async def get_user_groups(self, user_id: int) -> List[Dict[str, Any]]:
        url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
        data = await self._request_json("GET", url)
        return data or []

    # ---------- FRIENDS / FOLLOWERS ----------

    async def get_friends(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"https://friends.roblox.com/v1/users/{user_id}/friends?userSort=Alphabetical&limit={limit}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    async def get_followers(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"https://friends.roblox.com/v1/users/{user_id}/followers?limit={limit}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    async def get_followings(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"https://friends.roblox.com/v1/users/{user_id}/followings?limit={limit}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    # ---------- LIMITEDS / COLLECTIBLES ----------

    async def get_collectibles(self, user_id: int) -> List[Dict[str, Any]]:
        url_base = f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles"
        items: List[Dict[str, Any]] = []
        cursor = None

        while True:
            from urllib.parse import urlencode
            params = {"sortOrder": "Asc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            url = url_base + "?" + urlencode(params)
            data = await self._request_json("GET", url)
            if not data:
                break
            items.extend(data.get("data", []))
            cursor = data.get("nextPageCursor")
            if not cursor:
                break
        return items


# ========================
# UTILS
# ========================

def parse_ids(raw: str, max_count: int = 20) -> List[int]:
    parts = [p.strip() for p in raw.replace(",", " ").split()]
    ids: List[int] = []
    for p in parts:
        if p.isdigit():
            ids.append(int(p))
        if len(ids) >= max_count:
            break
    return ids


def parse_iso8601(s: str) -> dt.datetime:
    # Roblox: "2020-01-01T12:34:56.789Z"
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ========================
# BOT SETUP
# ========================

bot = Bot(token=TELEGRAM_TOKEN, parse_mode=PARSE_MODE)
dp = Dispatcher()
roblox = RobloxAPI()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Roblox lookup bot</b>\n\n"
        "Basic commands:\n"
        "<code>/user username</code> – user info\n"
        "<code>/avatar username</code> – avatar image\n"
        "<code>/limiteds username</code> – RAP of collectibles\n"
        "<code>/assetid asset_id</code> – asset info\n"
        "<code>/groupid group_id</code> – group info\n"
        "<code>/devex robux</code> – DevEx USD estimate\n\n"
        "Send /help for full list."
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Available commands (major ones implemented):</b>\n\n"
        "<b>Users:</b>\n"
        "/user &lt;username&gt; – user info\n"
        "/id &lt;user_id&gt; – user info by ID\n"
        "/username &lt;username&gt; – check if username is taken\n"
        "/displayname &lt;display_name&gt; – check display name usage\n"
        "/copyid &lt;username&gt; – get ID\n"
        "/idtousername &lt;ids...&gt; – IDs → usernames\n"
        "/banned &lt;ids...&gt; – check ban flag\n"
        "/accountage &lt;username&gt; – creation + age\n"
        "/lastonline &lt;username&gt; – last online/presence\n\n"
        "<b>Avatar:</b>\n"
        "/avatar &lt;username&gt; – full avatar\n"
        "/headshot &lt;username&gt; – headshot\n"
        "/bust &lt;username&gt; – bust\n\n"
        "<b>Assets:</b>\n"
        "/assetid &lt;asset_id&gt; – asset info\n"
        "/asseticon &lt;asset_id&gt; – asset icon\n\n"
        "<b>Groups:</b>\n"
        "/groupid &lt;group_id&gt; – group info\n"
        "/group &lt;name&gt; – search group by name\n"
        "/groupicon &lt;group_id&gt; – group icon\n"
        "/groups &lt;username&gt; – groups of user\n\n"
        "<b>Friends:</b>\n"
        "/friends &lt;username&gt; – friends list\n"
        "/followers &lt;username&gt; – followers list\n"
        "/followings &lt;username&gt; – followings list\n\n"
        "<b>Limiteds / Value:</b>\n"
        "/limiteds &lt;username&gt; – RAP sum\n"
        "/rolimons &lt;username&gt; – RAP + Rolimons link\n\n"
        "<b>DevEx:</b>\n"
        "/devex &lt;robux&gt; – USD\n"
        "/devexcad &lt;robux&gt; – CAD\n"
    )


# ========================
# USER / ID / NAMES
# ========================

@dp.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: <code>/user username</code>")
        return

    try:
        user = await roblox.get_user_details_by_username(username)
    except Exception as e:
        await message.answer(f"Error contacting Roblox: <code>{fmt_html_escape(str(e))}</code>")
        return

    if not user:
        await message.answer("User not found.")
        return

    created_str = ""
    if "created" in user:
        created = parse_iso8601(user["created"])
        created_str = created.strftime("%Y-%m-%d %H:%M UTC")

    desc = user.get("description") or ""
    if desc:
        if len(desc) > 600:
            desc = desc[:600] + "..."
        desc = fmt_html_escape(desc)

    text = (
        f"<b>{fmt_html_escape(user['name'])}</b> "
        f"(<i>{fmt_html_escape(user['displayName'])}</i>)\n"
        f"ID: <code>{user['id']}</code>\n"
        f"Created: <code>{created_str}</code>\n"
        f"Verified Badge: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"Banned: <code>{user.get('isBanned', False)}</code>\n"
        f"Profile: https://www.roblox.com/users/{user['id']}/profile\n"
    )
    if desc:
        text += f"\n<b>Description:</b>\n{desc}"

    thumb = await roblox.get_user_thumbnail(user["id"], "headshot")
    if thumb:
        await message.answer_photo(photo=thumb, caption=text)
    else:
        await message.answer(text)


@dp.message(Command("id"))
async def cmd_id(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg or not arg.isdigit():
        await message.answer("Usage: <code>/id user_id</code>")
        return

    user_id = int(arg)
    try:
        user = await roblox.get_user_by_id(user_id)
    except Exception as e:
        await message.answer(f"Error contacting Roblox: <code>{fmt_html_escape(str(e))}</code>")
        return

    if not user:
        await message.answer("User not found.")
        return

    created_str = ""
    if "created" in user:
        created = parse_iso8601(user["created"])
        created_str = created.strftime("%Y-%m-%d %H:%M UTC")

    desc = user.get("description") or ""
    if desc:
        if len(desc) > 600:
            desc = desc[:600] + "..."
        desc = fmt_html_escape(desc)

    text = (
        f"<b>{fmt_html_escape(user['name'])}</b> "
        f"(<i>{fmt_html_escape(user['displayName'])}</i>)\n"
        f"ID: <code>{user['id']}</code>\n"
        f"Created: <code>{created_str}</code>\n"
        f"Verified Badge: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"Banned: <code>{user.get('isBanned', False)}</code>\n"
        f"Profile: https://www.roblox.com/users/{user['id']}/profile\n"
    )
    if desc:
        text += f"\n<b>Description:</b>\n{desc}"

    thumb = await roblox.get_user_thumbnail(user["id"], "headshot")
    if thumb:
        await message.answer_photo(photo=thumb, caption=text)
    else:
        await message.answer(text)


@dp.message(Command("username"))
async def cmd_username(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: <code>/username username</code>")
        return

    try:
        user = await roblox.get_user_by_username(username)
    except Exception as e:
        await message.answer(f"Error: <code>{fmt_html_escape(str(e))}</code>")
        return

    if user:
        await message.answer(
            f"❌ Username <code>{fmt_html_escape(username)}</code> is <b>taken</b> "
            f"by <code>{fmt_html_escape(user['name'])}</code> (ID: <code>{user['id']}</code>)."
        )
    else:
        await message.answer(
            f"✅ Username <code>{fmt_html_escape(username)}</code> appears to be <b>available</b> "
            f"(or only used by a fully deleted/banned account)."
        )


@dp.message(Command("displayname"))
async def cmd_displayname(message: Message, command: CommandObject):
    displayname = (command.args or "").strip()
    if not displayname:
        await message.answer("Usage: <code>/displayname display_name</code>")
        return

    try:
        results = await roblox.search_displayname(displayname, limit=10)
    except Exception as e:
        await message.answer(f"Error: <code>{fmt_html_escape(str(e))}</code>")
        return

    exact = [u for u in results if u.get("displayName", "").lower() == displayname.lower()]

    if not results:
        await message.answer(
            f"Display name <code>{fmt_html_escape(displayname)}</code> is not found in search results. "
            f"It might be available."
        )
        return

    lines = []
    if exact:
        lines.append(
            f"❌ Display name <code>{fmt_html_escape(displayname)}</code> "
            f"is in use by <b>{len(exact)}</b> user(s):"
        )
        for u in exact[:5]:
            lines.append(
                f"- <code>{fmt_html_escape(u['displayName'])}</code> / "
                f"<code>{fmt_html_escape(u['name'])}</code> (ID: <code>{u['id']}</code>)"
            )
    else:
        lines.append(
            f"⚠️ No exact matches for <code>{fmt_html_escape(displayname)}</code>, "
            f"but similar display names exist:"
        )
        for u in results[:5]:
            lines.append(
                f"- <code>{fmt_html_escape(u['displayName'])}</code> / "
                f"<code>{fmt_html_escape(u['name'])}</code> (ID: <code>{u['id']}</code>)"
            )

    await message.answer("\n".join(lines))


@dp.message(Command("copyid"))
async def cmd_copyid(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: <code>/copyid username</code>")
        return

    try:
        u = await roblox.get_user_by_username(username)
    except Exception as e:
        await message.answer(f"Error: <code>{fmt_html_escape(str(e))}</code>")
        return

    if not u:
        await message.answer("User not found.")
        return

    await message.answer(
        f"User <code>{fmt_html_escape(u['name'])}</code> ID: <code>{u['id']}</code>"
    )


@dp.message(Command("idtousername"))
async def cmd_idtousername(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Usage: <code>/idtousername id1 id2 id3 ...</code>")
        return

    ids = parse_ids(raw, max_count=50)
    if not ids:
        await message.answer("No valid numeric IDs provided.")
        return

    try:
        info = await roblox.get_users_by_ids(ids)
    except Exception as e:
        await message.answer(f"Error: <code>{fmt_html_escape(str(e))}</code>")
        return

    lines = []
    for i in ids:
        u = info.get(i)
        if u:
            lines.append(
                f"<code>{i}</code> → <code>{fmt_html_escape(u['name'])}</code> / "
                f"<code>{fmt_html_escape(u['displayName'])}</code>"
            )
        else:
            lines.append(f"<code>{i}</code> → not found")

    await message.answer("\n".join(lines))


@dp.message(Command("banned"))
async def cmd_banned(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Usage: <code>/banned id1 id2 id3 ...</code>")
        return

    ids = parse_ids(raw, max_count=20)
    if not ids:
        await message.answer("No valid numeric IDs provided.")
        return

    lines = []
    for i in ids:
        try:
            u = await roblox.get_user_by_id(i)
        except Exception as e:
            lines.append(f"<code>{i}</code> → error: <code>{fmt_html_escape(str(e))}</code>")
            continue

        if not u:
            lines.append(f"<code>{i}</code> → not found")
        else:
            lines.append(
                f"<code>{i}</code> (<code>{fmt_html_escape(u['name'])}</code>) → "
                f"isBanned = <code>{u.get('isBanned', False)}</code>"
            )

    await message.answer("\n".join(lines))


@dp.message(Command("accountage"))
async def cmd_accountage(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: <code>/accountage username</code>")
        return

    try:
        u = await roblox.get_user_details_by_username(username)
    except Exception as e:
        await message.answer(f"Error: <c
