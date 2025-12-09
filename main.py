import os
import asyncio
import datetime as dt
from typing import List, Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "PUT_YOUR_TOKEN_HERE"
USD_PER_ROBUX = 0.0038
USD_TO_CAD = 1.35
PARSE_MODE = "HTML"

class RobloxAPI:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=15)

    async def ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request_json(self, method: str, url: str, **kwargs):
        session = await self.ensure_session()
        async with session.request(method, url, **kwargs) as resp:
            try:
                data = await resp.json(content_type=None)
            except:
                data = None
            if resp.status == 404:
                return None
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} for {url}: {data}")
            return data

    async def get_user_by_username(self, username: str):
        url = "https://users.roblox.com/v1/usernames/users"
        payload = {"usernames": [username], "excludeBannedUsers": False}
        data = await self._request_json("POST", url, json=payload)
        if not data or not data.get("data"):
            return None
        return data["data"][0]

    async def get_user_by_id(self, user_id: int):
        return await self._request_json("GET", f"https://users.roblox.com/v1/users/{user_id}")

    async def get_user_details_by_username(self, username: str):
        basic = await self.get_user_by_username(username)
        if not basic:
            return None
        return await self.get_user_by_id(basic["id"])

    async def get_users_by_ids(self, ids: List[int]):
        if not ids:
            return {}
        url = "https://users.roblox.com/v1/users"
        data = await self._request_json("POST", url, json={"userIds": ids})
        result = {}
        if data and data.get("data"):
            for entry in data["data"]:
                result[entry["id"]] = entry
        return result

    async def search_displayname(self, displayname: str, limit: int = 10):
        from urllib.parse import urlencode
        url = f"https://users.roblox.com/v1/users/search?{urlencode({'keyword': displayname, 'limit': limit})}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    async def get_presence(self, user_ids: List[int]):
        if not user_ids:
            return None
        return await self._request_json("POST", "https://presence.roblox.com/v1/presence/users", json={"userIds": user_ids})

    async def get_user_thumbnail(self, user_id: int, ttype: str):
        base = "https://thumbnails.roblox.com/v1/users"
        path = "avatar" if ttype == "avatar" else "avatar-headshot" if ttype == "headshot" else "avatar-bust"
        url = f"{base}/{path}?userIds={user_id}&size=720x720&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    async def get_asset_icon(self, asset_id: int, size="512x512"):
        url = f"https://thumbnails.roblox.com/v1/assets?assetIds={asset_id}&size={size}&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    async def get_group_icon(self, group_id: int, size="420x420"):
        url = f"https://thumbnails.roblox.com/v1/groups/icons?groupIds={group_id}&size={size}&format=Png&isCircular=false"
        data = await self._request_json("GET", url)
        if not data or not data.get("data"):
            return None
        return data["data"][0].get("imageUrl")

    async def get_asset_info(self, asset_id: int):
        return await self._request_json("GET", f"https://api.roblox.com/marketplace/productinfo?assetId={asset_id}")

    async def get_group_by_id(self, group_id: int):
        return await self._request_json("GET", f"https://groups.roblox.com/v1/groups/{group_id}")

    async def search_group_by_name(self, name: str, limit: 10):
        from urllib.parse import urlencode
        url = f"https://groups.roblox.com/v1/groups/search?{urlencode({'keyword': name, 'limit': limit})}"
        data = await self._request_json("GET", url)
        return data.get("data", []) if data else []

    async def get_user_groups(self, user_id: int):
        return await self._request_json("GET", f"https://groups.roblox.com/v1/users/{user_id}/groups/roles") or []

    async def get_friends(self, user_id: int, limit=50):
        data = await self._request_json("GET", f"https://friends.roblox.com/v1/users/{user_id}/friends?userSort=Alphabetical&limit={limit}")
        return data.get("data", []) if data else []

    async def get_followers(self, user_id: int, limit=50):
        data = await self._request_json("GET", f"https://friends.roblox.com/v1/users/{user_id}/followers?limit={limit}")
        return data.get("data", []) if data else []

    async def get_followings(self, user_id: int, limit=50):
        data = await self._request_json("GET", f"https://friends.roblox.com/v1/users/{user_id}/followings?limit={limit}")
        return data.get("data", []) if data else []

    async def get_collectibles(self, user_id: int):
        url_base = f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles"
        items = []
        cursor = None
        from urllib.parse import urlencode
        while True:
            params = {"sortOrder": "Asc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = await self._request_json("GET", url_base + "?" + urlencode(params))
            if not data:
                break
            items.extend(data.get("data", []))
            cursor = data.get("nextPageCursor")
            if not cursor:
                break
        return items

def parse_ids(raw: str, max_count=20):
    ids = []
    for p in raw.replace(",", " ").split():
        if p.isdigit():
            ids.append(int(p))
        if len(ids) >= max_count:
            break
    return ids

def parse_iso8601(s: str):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def esc(t: str):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

bot = Bot(token=TELEGRAM_TOKEN, parse_mode=PARSE_MODE)
dp = Dispatcher()
roblox = RobloxAPI()
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Roblox lookup bot</b>\n\n"
        "<code>/user username</code>\n"
        "<code>/avatar username</code>\n"
        "<code>/limiteds username</code>\n"
        "<code>/assetid id</code>\n"
        "<code>/groupid id</code>\n"
        "<code>/devex robux</code>\n"
        "Use /help to view all commands."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Commands:</b>\n\n"
        "<b>Users:</b>\n"
        "/user, /id, /username, /displayname, /copyid, /idtousername, /banned,\n"
        "/accountage, /lastonline\n\n"
        "<b>Avatar:</b>\n"
        "/avatar, /headshot, /bust\n\n"
        "<b>Assets:</b>\n"
        "/assetid, /asseticon\n\n"
        "<b>Groups:</b>\n"
        "/groupid, /group, /groupicon, /groups\n\n"
        "<b>Friends:</b>\n"
        "/friends, /followers, /followings\n\n"
        "<b>Limiteds:</b>\n"
        "/limiteds, /rolimons\n\n"
        "<b>DevEx:</b>\n"
        "/devex, /devexcad"
    )

@dp.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /user username")
        return
    try:
        user = await roblox.get_user_details_by_username(username)
    except Exception as e:
        await message.answer(f"Error: <code>{esc(str(e))}</code>")
        return
    if not user:
        await message.answer("User not found.")
        return

    created = parse_iso8601(user["created"]) if "created" in user else None
    desc = esc((user.get("description") or "")[:600])
    created_str = created.strftime("%Y-%m-%d %H:%M UTC") if created else "N/A"

    text = (
        f"<b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
        f"ID: <code>{user['id']}</code>\n"
        f"Created: <code>{created_str}</code>\n"
        f"Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"Banned: <code>{user.get('isBanned', False)}</code>\n"
        f"https://www.roblox.com/users/{user['id']}/profile\n"
    )
    if desc:
        text += "\n<b>Description:</b>\n" + desc

    thumb = await roblox.get_user_thumbnail(user["id"], "headshot")
    if thumb:
        await message.answer_photo(thumb, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("id")))
async def cmd_id(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Usage: /id user_id")
        return
    user_id = int(arg)
    try:
        user = await roblox.get_user_by_id(user_id)
    except Exception as e:
        await message.answer(f"Error: <code>{esc(str(e))}</code>")
        return
    if not user:
        await message.answer("User not found.")
        return

    created = parse_iso8601(user["created"]) if "created" in user else None
    created_str = created.strftime("%Y-%m-%d %H:%M UTC") if created else "N/A"
    desc = esc((user.get("description") or "")[:600])

    text = (
        f"<b>{esc(user['name'])}</b> (<i>{esc(user['displayName'])}</i>)\n"
        f"ID: <code>{user['id']}</code>\n"
        f"Created: <code>{created_str}</code>\n"
        f"Verified: <code>{user.get('hasVerifiedBadge', False)}</code>\n"
        f"Banned: <code>{user.get('isBanned', False)}</code>\n"
        f"https://www.roblox.com/users/{user['id']}/profile\n"
    )
    if desc:
        text += "\n<b>Description:</b>\n" + desc

    thumb = await roblox.get_user_thumbnail(user["id"], "headshot")
    if thumb:
        await message.answer_photo(thumb, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("username"))
async def cmd_username(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /username username")
        return
    try:
        user = await roblox.get_user_by_username(username)
    except Exception as e:
        await message.answer(f"Error: <code>{esc(str(e))}</code>")
        return

    if user:
        await message.answer(f"❌ Taken by <code>{esc(user['name'])}</code> (ID {user['id']})")
    else:
        await message.answer(f"✅ <code>{esc(username)}</code> seems available")

@dp.message(Command("displayname"))
async def cmd_displayname(message: Message, command: CommandObject):
    dn = (command.args or "").strip()
    if not dn:
        await message.answer("Usage: /displayname name")
        return
    try:
        results = await roblox.search_displayname(dn)
    except Exception as e:
        await message.answer(f"Error: <code>{esc(str(e))}</code>")
        return

    exact = [u for u in results if u.get("displayName", "").lower() == dn.lower()]
    if not results:
        await message.answer(f"No users found with display name <code>{esc(dn)}</code>")
        return

    lines = []
    if exact:
        lines.append(f"Exact matches ({len(exact)}):")
        for u in exact[:5]:
            lines.append(f"- {esc(u['displayName'])} / {esc(u['name'])} (ID {u['id']})")
    else:
        lines.append("Similar names:")
        for u in results[:5]:
            lines.append(f"- {esc(u['displayName'])} / {esc(u['name'])} (ID {u['id']})")

    await message.answer("\n".join(lines))

@dp.message(Command("copyid"))
async def cmd_copyid(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /copyid username")
        return
    try:
        u = await roblox.get_user_by_username(username)
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not u:
        await message.answer("User not found.")
        return
    await message.answer(f"ID of <code>{esc(u['name'])}</code>: <code>{u['id']}</code>")

@dp.message(Command("idtousername"))
async def cmd_idtousername(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    ids = parse_ids(raw, 50)
    if not ids:
        await message.answer("Usage: /idtousername id1 id2 id3")
        return
    try:
        info = await roblox.get_users_by_ids(ids)
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return

    lines = []
    for i in ids:
        u = info.get(i)
        if u:
            lines.append(f"{i} → {esc(u['name'])} / {esc(u['displayName'])}")
        else:
            lines.append(f"{i} → not found")
    await message.answer("\n".join(lines))

@dp.message(Command("banned"))
async def cmd_banned(message: Message, command: CommandObject):
    ids = parse_ids((command.args or ""), 20)
    if not ids:
        await message.answer("Usage: /banned id1 id2 id3")
        return
    lines = []
    for i in ids:
        try:
            u = await roblox.get_user_by_id(i)
        except Exception as e:
            lines.append(f"{i} → error")
            continue
        if not u:
            lines.append(f"{i} → not found")
        else:
            lines.append(f"{i} ({esc(u['name'])}) → banned={u.get('isBanned', False)}")
    await message.answer("\n".join(lines))

@dp.message(Command("accountage"))
async def cmd_accountage(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /accountage username")
        return
    try:
        u = await roblox.get_user_details_by_username(username)
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not u:
        await message.answer("User not found.")
        return

    created = parse_iso8601(u["created"])
    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    age_days = (now - created).days
    age_years = age_days / 365

    await message.answer(
        f"<b>{esc(u['name'])}</b>\n"
        f"Created: {created.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Age: {age_days} days ({age_years:.2f} years)"
    )

@dp.message(Command("lastonline"))
async def cmd_lastonline(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /lastonline username")
        return
    try:
        u = await roblox.get_user_by_username(username)
        if not u:
            await message.answer("User not found.")
            return
        pr = await roblox.get_presence([u["id"]])
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return

    p = pr.get("userPresences", [{}])[0]
    last = p.get("lastOnline")
    loc = p.get("lastLocation") or "Unknown"
    if last:
        dt_last = parse_iso8601(last)
        last_str = dt_last.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        last_str = "Unknown"

    await message.answer(
        f"{esc(u['name'])}\n"
        f"Location: {esc(loc)}\n"
        f"Last online: <b>{last_str}</b>"
    )
@dp.message(Command("avatar"))
async def cmd_avatar(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /avatar username")
        return
    try:
        u = await roblox.get_user_by_username(username)
        if not u:
            await message.answer("User not found.")
            return
        url = await roblox.get_user_thumbnail(u["id"], "avatar")
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not url:
        await message.answer("Could not fetch avatar.")
        return
    await message.answer_photo(url, caption=f"Avatar of {esc(u['name'])} (ID {u['id']})")

@dp.message(Command("headshot"))
async def cmd_headshot(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /headshot username")
        return
    try:
        u = await roblox.get_user_by_username(username)
        if not u:
            await message.answer("User not found.")
            return
        url = await roblox.get_user_thumbnail(u["id"], "headshot")
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not url:
        await message.answer("Could not fetch headshot.")
        return
    await message.answer_photo(url, caption=f"Headshot of {esc(u['name'])} (ID {u['id']})")

@dp.message(Command("bust"))
async def cmd_bust(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /bust username")
        return
    try:
        u = await roblox.get_user_by_username(username)
        url = await roblox.get_user_thumbnail(u["id"], "bust")
    except:
        await message.answer("Error fetching bust.")
        return
    if not url:
        await message.answer("Could not fetch bust.")
        return
    await message.answer_photo(url, caption=f"Bust of {esc(u['name'])}")

@dp.message(Command("assetid"))
async def cmd_assetid(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /assetid id")
        return
    aid = int(raw)
    try:
        info = await roblox.get_asset_info(aid)
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not info:
        await message.answer("Asset not found.")
        return

    desc = esc((info.get("Description") or "")[:600])
    c = info.get("Creator") or {}
    text = (
        f"<b>{esc(info.get('Name','?'))}</b>\n"
        f"Asset ID: {aid}\n"
        f"Creator: {esc(c.get('Name','?'))} ({c.get('Id','?')})\n"
        f"Price: {info.get('PriceInRobux','N/A')}\n"
        f"Limited: {info.get('IsLimited')}\n"
        f"LU: {info.get('IsLimitedUnique')}\n"
        f"https://www.roblox.com/catalog/{aid}\n"
    )
    if desc:
        text += "\n" + desc

    icon = await roblox.get_asset_icon(aid)
    if icon:
        await message.answer_photo(icon, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("asseticon"))
async def cmd_asseticon(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /asseticon id")
        return
    aid = int(raw)
    try:
        icon = await roblox.get_asset_icon(aid)
    except:
        await message.answer("Error fetching icon.")
        return
    if not icon:
        await message.answer("Icon unavailable.")
        return
    await message.answer_photo(icon, caption=f"Asset {aid}")

@dp.message(Command("groupid"))
async def cmd_groupid(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /groupid id")
        return
    gid = int(raw)
    try:
        g = await roblox.get_group_by_id(gid)
    except Exception as e:
        await message.answer(f"Error: {esc(str(e))}")
        return
    if not g:
        await message.answer("Group not found.")
        return

    desc = esc((g.get("description") or "")[:600])
    owner = g.get("owner") or {}
    text = (
        f"<b>{esc(g.get('name','?'))}</b>\n"
        f"Group ID: {gid}\n"
        f"Owner: {owner.get('userId','N/A')}\n"
        f"Members: {g.get('memberCount','?')}\n"
        f"https://www.roblox.com/groups/{gid}\n"
    )
    if desc:
        text += "\n" + desc

    icon = await roblox.get_group_icon(gid)
    if icon:
        await message.answer_photo(icon, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("group"))
async def cmd_group(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        await message.answer("Usage: /group name")
        return
    try:
        results = await roblox.search_group_by_name(name)
    except:
        await message.answer("Error searching group.")
        return
    if not results:
        await message.answer("No groups found.")
        return

    g = results[0]
    gid = g["id"]
    full = await roblox.get_group_by_id(gid) or g
    desc = esc((full.get("description") or "")[:600])
    text = (
        f"<b>{esc(full.get('name','?'))}</b>\n"
        f"Group ID: {gid}\n"
        f"Members: {full.get('memberCount','?')}\n"
        f"https://www.roblox.com/groups/{gid}\n"
    )
    if desc:
        text += "\n" + desc

    icon = await roblox.get_group_icon(gid)
    if icon:
        await message.answer_photo(icon, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("groupicon")))
async def cmd_groupicon(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /groupicon id")
        return
    gid = int(raw)
    icon = await roblox.get_group_icon(gid)
    if not icon:
        await message.answer("No icon.")
        return
    await message.answer_photo(icon, caption=f"Group {gid}")

@dp.message(Command("groups"))
async def cmd_groups(message: Message, command: CommandObject):
    username = (command.args or "").strip()
    if not username:
        await message.answer("Usage: /groups username")
        return
    try:
        u = await roblox.get_user_by_username(username)
        groups = await roblox.get_user_groups(u["id"])
    except:
        await message.answer("Error fetching groups.")
        return

    if not groups:
        await message.answer("No groups or private.")
        return

    lines = [f"Groups for {esc(u['name'])}:"]
    for g in groups[:15]:
        group = g.get("group", {})
        role = g.get("role", {})
        lines.append(f"- {esc(group.get('name','?'))} (ID {group.get('id')}) role={esc(role.get('name','?'))}")
    await message.answer("\n".join(lines))

@dp.message(Command("friends"))
async def cmd_friends(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        await message.answer("Usage: /friends username")
        return
    try:
        user = await roblox.get_user_by_username(u)
        data = await roblox.get_friends(user["id"])
    except:
        await message.answer("Error.")
        return
    if not data:
        await message.answer("No friends.")
        return
    lines = [f"Friends of {esc(user['name'])}:"]
    for f in data[:25]:
        lines.append(f"- {esc(f['name'])} ({f['id']})")
    await message.answer("\n".join(lines))

@dp.message(Command("followers"))
async def cmd_followers(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        await message.answer("Usage: /followers username")
        return
    try:
        user = await roblox.get_user_by_username(u)
        data = await roblox.get_followers(user["id"])
    except:
        await message.answer("Error.")
        return
    if not data:
        await message.answer("No followers.")
        return
    lines = [f"Followers of {esc(user['name'])}:"]
    for f in data[:25]:
        lines.append(f"- {esc(f['name'])} ({f['id']})")
    await message.answer("\n".join(lines))

@dp.message(Command("followings"))
async def cmd_followings(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        await message.answer("Usage: /followings username")
        return
    try:
        user = await roblox.get_user_by_username(u)
        data = await roblox.get_followings(user["id"])
    except:
        await message.answer("Error.")
        return
    if not data:
        await message.answer("No followings.")
        return
    lines = [f"Followings of {esc(user['name'])}:"]
    for f in data[:25]:
        lines.append(f"- {esc(f['name'])} ({f['id']})")
    await message.answer("\n".join(lines))

@dp.message(Command("limiteds")))
async def cmd_limiteds(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        await message.answer("Usage: /limiteds username")
        return
    try:
        user = await roblox.get_user_by_username(u)
        col = await roblox.get_collectibles(user["id"])
    except:
        await message.answer("Error.")
        return
    if not col:
        await message.answer("No collectibles.")
        return
    rap = sum(i.get("recentAveragePrice") or 0 for i in col)
    await message.answer(
        f"{esc(user['name'])}\n"
        f"Items: {len(col)}\n"
        f"Total RAP: {rap:,}"
    )

@dp.message(Command("rolimons"))
async def cmd_rolimons(message: Message, command: CommandObject):
    u = (command.args or "").strip()
    if not u:
        await message.answer("Usage: /rolimons username")
        return
    try:
        user = await roblox.get_user_by_username(u)
        col = await roblox.get_collectibles(user["id"])
    except:
        await message.answer("Error.")
        return
    rap = sum(i.get("recentAveragePrice") or 0 for i in col)
    await message.answer(
        f"{esc(user['name'])}\n"
        f"RAP ≈ {rap:,}\n"
        f"https://www.rolimons.com/player/{user['id']}"
    )

@dp.message(Command("devex"))
async def cmd_devex(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /devex robux")
        return
    r = int(raw)
    usd = r * USD_PER_ROBUX
    await message.answer(f"{r:,} R$ ≈ ${usd:,.2f} USD")

@dp.message(Command("devexcad"))
async def cmd_devexcad(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Usage: /devexcad robux")
        return
    r = int(raw)
    usd = r * USD_PER_ROBUX
    cad = usd * USD_TO_CAD
    await message.answer(f"{r:,} R$ ≈ ${cad:,.2f} CAD")

async def main():
    if TELEGRAM_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise RuntimeError("Set TELEGRAM_TOKEN in .env")
    print("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except:
        pass
