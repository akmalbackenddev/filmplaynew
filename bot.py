# bot.py (FINAL)
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Union

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatJoinRequest,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ===================== CONFIG =====================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "bot_data.db").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("BOT_TOKEN .env da yo'q!")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID .env da yo'q yoki 0!")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("kino_bot_final")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


def get_utc_now():
    return datetime.now(timezone.utc)


def normalize_channel_identifier(text: str) -> str:
    """
    Admin kanal qo'shganda:
    - @username
    - -100....
    - https://t.me/username
    - t.me/username
    """
    t = (text or "").strip()

    if "t.me/" in t:
        part = t.split("t.me/", 1)[1].strip()
        part = part.split("?", 1)[0].strip().strip("/")
        # invite link bo'lsa (+xxxx) get_chat ishlamasligi mumkin, lekin biz uni keyingi qadamda invite_link sifatida olamiz
        if part.startswith("+"):
            return t
        if not part.startswith("@"):
            part = "@" + part
        return part

    return t


# ===================== STATES =====================
class AdminStates(StatesGroup):
    add_admin = State()
    remove_admin = State()

    add_channel = State()
    add_channel_invite = State()
    remove_channel = State()

    add_instagram_title = State()
    add_instagram_url = State()
    remove_instagram = State()

    broadcast = State()

    wait_for_serial_id = State()
    wait_for_part_video = State()

    remove_content = State()

    add_movie = State()

    add_serial = State()
    add_serial_description = State()


# ===================== DATABASE =====================
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Users
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    joined_at TEXT,
                    last_active TEXT
                )
            """)

            # Channels
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    username TEXT,
                    added_at TEXT
                )
            """)

            # Admins
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_at TEXT
                )
            """)

            # Content
            await db.execute("""
                CREATE TABLE IF NOT EXISTS content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT,
                    title TEXT,
                    description TEXT,
                    content_type TEXT DEFAULT 'movie',
                    added_by INTEGER,
                    added_at TEXT
                )
            """)

            # Serial parts
            await db.execute("""
                CREATE TABLE IF NOT EXISTS serial_parts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial_id INTEGER,
                    part_number INTEGER,
                    file_id TEXT NOT NULL,
                    title TEXT,
                    added_by INTEGER,
                    added_at TEXT,
                    FOREIGN KEY (serial_id) REFERENCES content (id)
                )
            """)

            # Activity (for statistics)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT,
                    action_at TEXT
                )
            """)

            # Join request tracking
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_join_requests (
                    chat_id INTEGER,
                    user_id INTEGER,
                    requested_at TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)

            # Unique downloads per content
            await db.execute("""
                CREATE TABLE IF NOT EXISTS content_downloads (
                    content_id INTEGER,
                    user_id INTEGER,
                    downloaded_at TEXT,
                    PRIMARY KEY (content_id, user_id)
                )
            """)

            # Instagram links (multiple)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS instagram_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    url TEXT NOT NULL,
                    added_at TEXT
                )
            """)

            await db.commit()

            # ---- ALTER safe adds ----
            # users.started_once
            try:
                await db.execute("ALTER TABLE users ADD COLUMN started_once INTEGER DEFAULT 0")
                await db.commit()
            except:
                pass

            # channels.invite_link
            try:
                await db.execute("ALTER TABLE channels ADD COLUMN invite_link TEXT")
                await db.commit()
            except:
                pass

            # content.downloads_count
            try:
                await db.execute("ALTER TABLE content ADD COLUMN downloads_count INTEGER DEFAULT 0")
                await db.commit()
            except:
                pass

            # Main admin ensure
            await db.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, ?)",
                (ADMIN_ID, get_utc_now().isoformat())
            )
            await db.commit()

    # ---------- USERS ----------
    async def add_user(self, user) -> None:
        """
        TALAB: /start statistikaga faqat 1 marta yozilsin.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT started_once FROM users WHERE user_id=?", (user.id,))
            row = await cur.fetchone()
            now = get_utc_now().isoformat()

            if row is None:
                await db.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_active, started_once)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    user.id, user.username, user.first_name or "",
                    user.last_name or "", now, now
                ))
                await db.execute(
                    "INSERT INTO user_activity (user_id, action, action_at) VALUES (?, ?, ?)",
                    (user.id, "start", now)
                )
            else:
                await db.execute(
                    "UPDATE users SET username=?, first_name=?, last_name=?, last_active=? WHERE user_id=?",
                    (user.username, user.first_name or "", user.last_name or "", now, user.id)
                )

            await db.commit()

    async def update_user_activity(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (get_utc_now().isoformat(), user_id)
            )
            await db.commit()

    async def get_all_users(self) -> List[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT user_id FROM users")
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    # ---------- ADMINS ----------
    async def is_admin(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return await cur.fetchone() is not None

    async def get_admins(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT user_id, added_at FROM admins")
            rows = await cur.fetchall()
            return [{"user_id": r[0], "added_at": r[1]} for r in rows]

    async def add_admin(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO admins (user_id, added_at) VALUES (?, ?)",
                    (user_id, get_utc_now().isoformat())
                )
                await db.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding admin: {e}")
                return False

    async def remove_admin(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await db.commit()
            return cur.rowcount > 0

    # ---------- CHANNELS ----------
    async def get_channels(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT chat_id, title, username, COALESCE(invite_link,'') FROM channels")
            rows = await cur.fetchall()
            return [{"chat_id": r[0], "title": r[1], "username": r[2], "invite_link": r[3]} for r in rows]

    async def add_channel(self, chat_id: int, title: str, username: str = "", invite_link: str = "") -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO channels (chat_id, title, username, invite_link, added_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, title, username, invite_link, get_utc_now().isoformat())
            )
            await db.commit()

    async def remove_channel(self, chat_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
            await db.commit()
            return cur.rowcount > 0

    # Join request tracking
    async def save_join_request(self, chat_id: int, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO channel_join_requests (chat_id, user_id, requested_at) VALUES (?, ?, ?)",
                (chat_id, user_id, get_utc_now().isoformat())
            )
            await db.commit()

    async def has_join_request(self, chat_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM channel_join_requests WHERE chat_id=? AND user_id=?",
                (chat_id, user_id)
            )
            return await cur.fetchone() is not None

    # ---------- INSTAGRAM LINKS ----------
    async def add_instagram_link(self, title: str, url: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO instagram_links (title, url, added_at) VALUES (?, ?, ?)",
                (title.strip() or "Instagram", url.strip(), get_utc_now().isoformat())
            )
            await db.commit()
            return cur.lastrowid

    async def remove_instagram_link(self, link_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM instagram_links WHERE id=?", (link_id,))
            await db.commit()
            return cur.rowcount > 0

    async def get_instagram_links(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT id, title, url FROM instagram_links ORDER BY id")
            rows = await cur.fetchall()
            return [{"id": r[0], "title": r[1], "url": r[2]} for r in rows]

    # ---------- CONTENT ----------
    async def add_content(self, file_id: Optional[str], title: str, description: str, content_type: str, added_by: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cur = await db.execute(
                    """INSERT INTO content (file_id, title, description, content_type, added_by, added_at, downloads_count)
                       VALUES (?, ?, ?, ?, ?, ?, COALESCE(?,0))""",
                    (file_id, title, description, content_type, added_by, get_utc_now().isoformat(), 0)
                )
                await db.commit()
                return cur.lastrowid
            except Exception as e:
                logger.error(f"Error adding content: {e}")
                return 0

    async def get_content(self, content_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT id, file_id, title, description, content_type, COALESCE(downloads_count,0) FROM content WHERE id=?",
                (content_id,)
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "file_id": row[1],
                "title": row[2],
                "description": row[3],
                "content_type": row[4],
                "downloads_count": row[5],
            }

    async def delete_content(self, content_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM serial_parts WHERE serial_id = ?", (content_id,))
            await db.execute("DELETE FROM content_downloads WHERE content_id = ?", (content_id,))
            cur = await db.execute("DELETE FROM content WHERE id = ?", (content_id,))
            await db.commit()
            return cur.rowcount > 0

    async def get_all_content(self, content_type: str = None) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            if content_type:
                cur = await db.execute(
                    "SELECT id, title, description, content_type, added_at, COALESCE(downloads_count,0) "
                    "FROM content WHERE content_type=? ORDER BY id",
                    (content_type,)
                )
            else:
                cur = await db.execute(
                    "SELECT id, title, description, content_type, added_at, COALESCE(downloads_count,0) "
                    "FROM content ORDER BY id"
                )
            rows = await cur.fetchall()
            return [{
                "id": r[0],
                "title": r[1],
                "description": r[2],
                "content_type": r[3],
                "added_at": r[4],
                "downloads_count": r[5],
            } for r in rows]

    async def get_content_count(self, content_type: str = None) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            if content_type:
                cur = await db.execute("SELECT COUNT(*) FROM content WHERE content_type=?", (content_type,))
            else:
                cur = await db.execute("SELECT COUNT(*) FROM content")
            res = await cur.fetchone()
            return res[0] if res else 0

    async def register_download(self, content_id: int, user_id: int) -> bool:
        """
        Unique download:
        - 1 user -> 1 count
        """
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT OR IGNORE INTO content_downloads (content_id, user_id, downloaded_at) VALUES (?, ?, ?)",
                (content_id, user_id, get_utc_now().isoformat())
            )
            await db.commit()
            if cur.rowcount > 0:
                await db.execute(
                    "UPDATE content SET downloads_count = COALESCE(downloads_count,0) + 1 WHERE id=?",
                    (content_id,)
                )
                await db.commit()
                return True
            return False

    # ---------- SERIAL PARTS ----------
    async def add_serial_part(self, serial_id: int, part_number: int, file_id: str, title: str, added_by: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    """INSERT INTO serial_parts (serial_id, part_number, file_id, title, added_by, added_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (serial_id, part_number, file_id, title, added_by, get_utc_now().isoformat())
                )
                await db.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding serial part: {e}")
                return False

    async def get_serial_parts(self, serial_id: int) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT part_number, file_id, title FROM serial_parts WHERE serial_id=? ORDER BY part_number",
                (serial_id,)
            )
            rows = await cur.fetchall()
            return [{"part_number": r[0], "file_id": r[1], "title": r[2]} for r in rows]

    async def get_serial_parts_count(self, serial_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM serial_parts WHERE serial_id=?", (serial_id,))
            res = await cur.fetchone()
            return res[0] if res else 0

    # ---------- STATISTICS (old types kept, correct) ----------
    async def get_statistics(self) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cur.fetchone())[0]

            monthly_date = (get_utc_now() - timedelta(days=30)).isoformat()
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (monthly_date,))
            monthly_users = (await cur.fetchone())[0]

            weekly_date = (get_utc_now() - timedelta(days=7)).isoformat()
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (weekly_date,))
            weekly_users = (await cur.fetchone())[0]

            today = get_utc_now().date().isoformat()
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE substr(joined_at,1,10) = ?", (today,))
            daily_users = (await cur.fetchone())[0]

            cur = await db.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (weekly_date,))
            active_users = (await cur.fetchone())[0]

            movies_count = await self.get_content_count('movie')
            serials_count = await self.get_content_count('serial')

            return {
                "total_users": total_users,
                "monthly_users": monthly_users,
                "weekly_users": weekly_users,
                "daily_users": daily_users,
                "active_users": active_users,
                "movies_count": movies_count,
                "serials_count": serials_count
            }


db = DatabaseManager(DB_PATH)


# ===================== SUBSCRIPTION CHECK =====================
async def check_subscription(user_id: int) -> List[Dict]:
    """
    TALAB:
    - kanal obuna bo'lganini ham tekshiradi
    - join request yuborgan bo'lsa ham (private kanal) vaqtincha ok bo'ladi
    """
    channels = await db.get_channels()
    not_subscribed = []

    for ch in channels:
        chat_id = ch["chat_id"]
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                if not await db.has_join_request(chat_id, user_id):
                    not_subscribed.append(ch)
        except Exception:
            if not await db.has_join_request(chat_id, user_id):
                not_subscribed.append(ch)

    return not_subscribed


def build_subscribe_keyboard(channels: List[Dict], instagram_links: List[Dict]) -> InlineKeyboardMarkup:
    keyboard = []

    for ch in channels:
        if ch.get("invite_link"):
            url = ch["invite_link"]
        elif ch.get("username"):
            url = f"https://t.me/{ch['username']}"
        else:
            url = f"https://t.me/c/{str(ch['chat_id']).replace('-100','')}"
        keyboard.append([InlineKeyboardButton(text=f"📢 {ch['title']}", url=url)])

    for ig in instagram_links:
        keyboard.append([InlineKeyboardButton(text=f"📷 {ig['title']}", url=ig["url"])])

    keyboard.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===================== JOIN REQUEST HANDLER =====================
@router.chat_join_request()
async def on_join_request(update: ChatJoinRequest):
    try:
        await db.save_join_request(update.chat.id, update.from_user.id)
    except Exception as e:
        logger.error(f"join_request save error: {e}")


# ===================== ADMIN NOTIFY =====================
async def send_admin_notification(user, action: str = "start"):
    admins = await db.get_admins()
    msg = (
        f"👤 Foydalanuvchi:\n"
        f"ID: {user.id}\n"
        f"Ism: {user.first_name or 'N/A'}\n"
        f"Username: @{user.username or 'N/A'}\n"
        f"Harakat: /{action}\n"
        f"Vaqt(UTC): {get_utc_now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    for a in admins:
        try:
            await bot.send_message(a["user_id"], msg)
        except:
            pass


# ===================== START / ADMIN =====================
@router.message(CommandStart())
async def start_handler(message: Message):
    user = message.from_user
    await db.add_user(user)

    await send_admin_notification(user, "start")

    instagram_links = await db.get_instagram_links()
    channels = await db.get_channels()

    if channels and not await db.is_admin(user.id):
        not_subscribed = await check_subscription(user.id)
        if not_subscribed:
            kb = build_subscribe_keyboard(not_subscribed, instagram_links)
            await message.answer(
                "📺 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling.\n"
                "Agar kanal private bo'lsa, link orqali request yuborasiz:",
                reply_markup=kb
            )
            return

    await message.answer("🎬 Xush kelibsiz!\nKino yoki serialni ko'rish uchun kodini yuboring.")


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    user = callback.from_user
    instagram_links = await db.get_instagram_links()

    not_subscribed = await check_subscription(user.id)
    if not_subscribed:
        kb = build_subscribe_keyboard(not_subscribed, instagram_links)
        await callback.message.edit_text("❌ Hali barcha kanallarga obuna bo'lmagansiz:", reply_markup=kb)
        await callback.answer()
        return

    stats = await db.get_statistics()
    text = (
        "✅ Tabriklaymiz! Barcha kanallarga obuna bo'ldingiz.\n\n"
        f"📁 Jami kinolar: {stats['movies_count']} ta\n"
        f"📺 Jami seriallar: {stats['serials_count']} ta\n\n"
        "Endi kod yuboring."
    )
    if await db.is_admin(user.id):
        text += "\n\n💠 Admin panel: /admin"

    await callback.message.edit_text(text)
    await callback.answer()


@router.message(Command("admin"))
async def admin_command_handler(message: Message):
    if not await db.is_admin(message.from_user.id):
        await message.answer("❌ Sizda admin huquqi yo'q.")
        return
    await show_admin_panel(message)


# ===================== ADMIN PANEL UI =====================
async def show_admin_panel(message: Union[Message, CallbackQuery]):
    stats = await db.get_statistics()
    channels = await db.get_channels()
    ig = await db.get_instagram_links()

    keyboard = [
        [InlineKeyboardButton(text="👥 Adminlar", callback_data="admin_manage")],
        [InlineKeyboardButton(text=f"📺 Kanallar ({len(channels)})", callback_data="channel_manage")],
        [InlineKeyboardButton(text=f"📷 Instagram ({len(ig)})", callback_data="instagram_manage")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text=f"🎬 Kontent ({stats['movies_count'] + stats['serials_count']})", callback_data="content_manage")],
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="broadcast")]
    ]

    text = "🛠 Admin Panel"

    if isinstance(message, Message):
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    else:
        await message.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await show_admin_panel(callback)

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_admin_panel(callback)


# ===================== ADMIN MANAGEMENT =====================
@router.callback_query(F.data == "admin_manage")
async def admin_manage(callback: CallbackQuery):
    admins = await db.get_admins()
    admin_list = "\n".join([f"• {a['user_id']}" for a in admins]) or "yo'q"

    keyboard = [
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="add_admin")],
        [InlineKeyboardButton(text="➖ Admin o'chirish", callback_data="remove_admin")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(
        f"👥 Adminlar ({len(admins)} ta):\n\n{admin_list}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(F.data == "add_admin")
async def add_admin_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("Yangi adminning user ID sini yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.add_admin)

@router.message(AdminStates.add_admin)
async def add_admin_process(message: Message, state: FSMContext):
    try:
        uid = int((message.text or "").strip())
        ok = await db.add_admin(uid)
        await message.answer("✅ Admin qo'shildi." if ok else "❌ Qo'shilmadi (mavjud bo'lishi mumkin).")
    except:
        await message.answer("❌ Faqat raqam yuboring.")
    await state.clear()
    await show_admin_panel(message)

@router.callback_query(F.data == "remove_admin")
async def remove_admin_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("O'chirish uchun admin user ID sini yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.remove_admin)

@router.message(AdminStates.remove_admin)
async def remove_admin_process(message: Message, state: FSMContext):
    try:
        uid = int((message.text or "").strip())
        if uid == ADMIN_ID:
            await message.answer("❌ Asosiy adminni o'chirib bo'lmaydi.")
        else:
            ok = await db.remove_admin(uid)
            await message.answer("✅ Admin o'chirildi." if ok else "❌ Admin topilmadi.")
    except:
        await message.answer("❌ Faqat raqam yuboring.")
    await state.clear()
    await show_admin_panel(message)


# ===================== INSTAGRAM MANAGEMENT =====================
@router.callback_query(F.data == "instagram_manage")
async def instagram_manage(callback: CallbackQuery):
    links = await db.get_instagram_links()

    text = "📷 Instagram linklar:\n\n"
    if not links:
        text += "Hali link yo'q."
    else:
        for ig in links:
            text += f"• {ig['id']}) {ig['title']} — {ig['url']}\n"

    kb = [
        [InlineKeyboardButton(text="➕ Link qo'shish", callback_data="ig_add")],
        [InlineKeyboardButton(text="➖ Link o'chirish", callback_data="ig_remove")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "ig_add")
async def ig_add(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text(
        "Instagram link uchun nom yozing (masalan: Mella Luxe):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(AdminStates.add_instagram_title)

@router.message(AdminStates.add_instagram_title)
async def ig_add_title(message: Message, state: FSMContext):
    await state.update_data(ig_title=(message.text or "").strip())
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await message.answer(
        "Endi Instagram URL yuboring (https://instagram.com/...):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(AdminStates.add_instagram_url)

@router.message(AdminStates.add_instagram_url)
async def ig_add_url(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("❌ To'g'ri URL yuboring (http/https).")
        return

    data = await state.get_data()
    title = data.get("ig_title") or "Instagram"
    await db.add_instagram_link(title, url)

    await message.answer("✅ Instagram link qo'shildi.")
    await state.clear()
    await show_admin_panel(message)

@router.callback_query(F.data == "ig_remove")
async def ig_remove(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text(
        "O'chirish uchun Instagram link ID yuboring:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(AdminStates.remove_instagram)

@router.message(AdminStates.remove_instagram)
async def ig_remove_process(message: Message, state: FSMContext):
    try:
        link_id = int((message.text or "").strip())
        ok = await db.remove_instagram_link(link_id)
        await message.answer("✅ O'chirildi." if ok else "❌ ID topilmadi.")
    except:
        await message.answer("❌ Faqat raqam yuboring.")
    await state.clear()
    await show_admin_panel(message)


# ===================== CHANNEL MANAGEMENT =====================
@router.callback_query(F.data == "channel_manage")
async def channel_manage(callback: CallbackQuery):
    channels = await db.get_channels()
    if channels:
        channel_list = ""
        for c in channels:
            channel_list += f"• {c['title']} (ID: {c['chat_id']})"
            if c.get("username"):
                channel_list += f"  @{c['username']}"
            if c.get("invite_link"):
                channel_list += f"\n   link: {c['invite_link']}"
            channel_list += "\n"
    else:
        channel_list = "Hech qanday kanal qo'shilmagan"

    keyboard = [
        [InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="add_channel")],
        [InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="remove_channel")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(
        f"📺 Kanallar ({len(channels)} ta):\n\n{channel_list}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(F.data == "add_channel")
async def add_channel_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text(
        "Kanalni yuboring:\n"
        "1) @username\n"
        "2) chat_id (-100...)\n"
        "3) https://t.me/username\n\n"
        "Eslatma: private invite link (+xxxx) bu bosqichda emas, keyingi bosqichda beriladi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(AdminStates.add_channel)

@router.message(AdminStates.add_channel)
async def add_channel_process(message: Message, state: FSMContext):
    try:
        ident_raw = (message.text or "").strip()
        ident = normalize_channel_identifier(ident_raw)

        # Agar ident invite link (+xxxx) bo'lsa, get_chat ishlamasligi mumkin.
        # Shuning uchun bu holatda admindan chat_id yoki @username so'raymiz.
        if "t.me/" in ident and "/+" in ident:
            await message.answer("❌ Private invite linkni bu bosqichda qabul qilmaymiz.\nChat ID yoki @username yuboring.")
            return

        chat = await bot.get_chat(ident)

        await state.update_data(chat_id=chat.id, title=chat.title, username=chat.username or "")
        await message.answer(
            "✅ Endi kanal uchun invite link yuboring.\n"
            "Agar public kanal bo'lsa va @username bor bo'lsa, 'skip' deb yuboring.\n"
            "Private kanal bo'lsa: https://t.me/+xxxxxx"
        )
        await state.set_state(AdminStates.add_channel_invite)

    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}")
        await state.clear()
        await show_admin_panel(message)

@router.message(AdminStates.add_channel_invite)
async def add_channel_invite_process(message: Message, state: FSMContext):
    invite = (message.text or "").strip()
    if invite.lower() == "skip":
        invite = ""
    data = await state.get_data()

    await db.add_channel(data["chat_id"], data["title"], data["username"], invite)
    await message.answer(f"✅ Kanal qo'shildi: {data['title']}")
    await state.clear()
    await show_admin_panel(message)

@router.callback_query(F.data == "remove_channel")
async def remove_channel_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("O'chirish uchun kanal chat ID sini yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.remove_channel)

@router.message(AdminStates.remove_channel)
async def remove_channel_process(message: Message, state: FSMContext):
    try:
        chat_id = int((message.text or "").strip())
        ok = await db.remove_channel(chat_id)
        await message.answer("✅ Kanal o'chirildi." if ok else "❌ Kanal topilmadi.")
    except:
        await message.answer("❌ Faqat raqam yuboring.")
    await state.clear()
    await show_admin_panel(message)


# ===================== STATISTICS (ADMIN PANEL) =====================
@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    stats = await db.get_statistics()
    msg = (
        "📊 Bot Statistikasi:\n\n"
        f"👥 Jami obunachilar: {stats['total_users']}\n"
        f"📈 Oylik obunachilar: {stats['monthly_users']}\n"
        f"📅 Haftalik obunachilar: {stats['weekly_users']}\n"
        f"📆 Kunlik obunachilar: {stats['daily_users']}\n"
        f"🔥 Faol obunachilar (haftalik): {stats['active_users']}\n"
        f"🎬 Jami kinolar: {stats['movies_count']}\n"
        f"📺 Jami seriallar: {stats['serials_count']}"
    )
    kb = [[InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")]]
    await callback.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# ===================== CONTENT MANAGEMENT =====================
@router.callback_query(F.data == "content_manage")
async def content_manage(callback: CallbackQuery):
    movies_count = await db.get_content_count('movie')
    serials_count = await db.get_content_count('serial')

    keyboard = [
        [InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="➕ Serial qo'shish", callback_data="add_serial")],
        [InlineKeyboardButton(text="➕ Serialga qism qo'shish", callback_data="add_serial_part")],
        [InlineKeyboardButton(text="📋 Kinolar ro'yxati", callback_data="movie_list")],
        [InlineKeyboardButton(text="📋 Seriallar ro'yxati", callback_data="serial_list")],
        [InlineKeyboardButton(text="➖ Kontent o'chirish", callback_data="remove_content")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(
        f"🎬 Kontent boshqaruvi:\n\nKinolar: {movies_count}\nSeriallar: {serials_count}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

# ---- Add Movie ----
@router.callback_query(F.data == "add_movie")
async def add_movie_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text(
        "🎬 Kino qo'shish uchun video yuboring.\n\n"
        "Caption: Kino nomi - tavsif\n"
        "Misol: Interstellar - Fantastika",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(AdminStates.add_movie)

@router.message(AdminStates.add_movie, F.video)
async def handle_movie_upload(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        await message.answer("❌ Faqat adminlar qo'shadi.")
        return

    video = message.video
    caption = message.caption or ""

    if " - " in caption:
        title, description = caption.split(" - ", 1)
        title = title.strip()
        description = description.strip()
    else:
        title = caption.strip() or f"Kino #{await db.get_content_count('movie') + 1}"
        description = ""

    content_id = await db.add_content(video.file_id, title, description, "movie", message.from_user.id)
    if content_id:
        await message.answer(
            f"✅ Kino qo'shildi!\n\nID: {content_id}\nNomi: {title}\n"
            f"{('Tavsif: ' + description) if description else ''}\n\n"
            f"Userlar {content_id} yuborib ko'radi."
        )
    else:
        await message.answer("❌ Xatolik: saqlanmadi.")

    await state.clear()
    await show_admin_panel(message)

# ---- Add Serial (name then desc, no video) ----
@router.callback_query(F.data == "add_serial")
async def add_serial_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("📺 Serial nomini yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.add_serial)

@router.message(AdminStates.add_serial)
async def process_serial_name(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Matn yuboring.")
        return
    title = message.text.strip()
    await state.update_data(serial_title=title)
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await message.answer(f"📝 '{title}' uchun tavsif yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.add_serial_description)

@router.message(AdminStates.add_serial_description)
async def process_serial_description(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Matn yuboring.")
        return
    data = await state.get_data()
    title = data["serial_title"]
    description = message.text.strip()

    serial_id = await db.add_content(None, title, description, "serial", message.from_user.id)
    if serial_id:
        await message.answer(
            f"✅ Serial qo'shildi!\n\nID: {serial_id}\nNomi: {title}\nTavsif: {description}\n\n"
            "Endi 'Serialga qism qo'shish' orqali qismlar qo'shasiz."
        )
    else:
        await message.answer("❌ Xatolik: saqlanmadi.")

    await state.clear()
    await show_admin_panel(message)

# ---- Add Serial Part ----
@router.callback_query(F.data == "add_serial_part")
async def add_serial_part_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("Qism qo'shish uchun serial kodini yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.wait_for_serial_id)

@router.message(AdminStates.wait_for_serial_id)
async def process_serial_id(message: Message, state: FSMContext):
    try:
        serial_id = int((message.text or "").strip())
        serial = await db.get_content(serial_id)
        if not serial or serial["content_type"] != "serial":
            await message.answer("❌ Bunday serial topilmadi.")
            return

        parts_count = await db.get_serial_parts_count(serial_id)
        next_part = parts_count + 1
        await state.update_data(serial_id=serial_id, next_part=next_part)

        kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
        await message.answer(
            f"📺 Serial: {serial['title']}\n"
            f"🔢 Keyingi qism: {next_part}\n\n"
            "Endi video yuboring.\nCaption ixtiyoriy: qism nomi",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        await state.set_state(AdminStates.wait_for_part_video)
    except:
        await message.answer("❌ Faqat raqam yuboring.")

@router.message(AdminStates.wait_for_part_video, F.video)
async def process_part_video(message: Message, state: FSMContext):
    data = await state.get_data()
    serial_id = data["serial_id"]
    part_number = data["next_part"]
    title = (message.caption or f"{part_number}-qism").strip()

    ok = await db.add_serial_part(serial_id, part_number, message.video.file_id, title, message.from_user.id)
    await message.answer("✅ Qism qo'shildi!" if ok else "❌ Qism saqlanmadi.")
    await state.clear()
    await show_admin_panel(message)

# ---- Lists ----
@router.callback_query(F.data == "movie_list")
async def movie_list(callback: CallbackQuery):
    movies = await db.get_all_content("movie")
    if not movies:
        await callback.message.edit_text("📭 Kino yo'q.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="content_manage")]]
        ))
        return

    text = "📋 Kinolar:\n\n"
    for m in movies:
        text += f"🎬 {m['id']}. {m['title']}  (⬇️ {m['downloads_count']})\n"
        if m["description"]:
            text += f"   📝 {m['description']}\n"
        text += "\n"

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="content_manage")]]
    ))

@router.callback_query(F.data == "serial_list")
async def serial_list(callback: CallbackQuery):
    serials = await db.get_all_content("serial")
    if not serials:
        await callback.message.edit_text("📭 Serial yo'q.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="content_manage")]]
        ))
        return

    text = "📋 Seriallar:\n\n"
    for s in serials:
        parts = await db.get_serial_parts_count(s["id"])
        text += f"📺 {s['id']}. {s['title']} ({parts} qism) (⬇️ {s['downloads_count']})\n"
        if s["description"]:
            text += f"   📝 {s['description']}\n"
        text += "\n"

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="content_manage")]]
    ))

# ---- Remove content ----
@router.callback_query(F.data == "remove_content")
async def remove_content_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("O'chirish uchun kod yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.remove_content)

@router.message(AdminStates.remove_content)
async def remove_content_process(message: Message, state: FSMContext):
    try:
        cid = int((message.text or "").strip())
        content = await db.get_content(cid)
        if not content:
            await message.answer("❌ Kontent topilmadi.")
        else:
            ok = await db.delete_content(cid)
            await message.answer("✅ O'chirildi." if ok else "❌ O'chmadi.")
    except:
        await message.answer("❌ Faqat raqam yuboring.")
    await state.clear()
    await show_admin_panel(message)


# ===================== BROADCAST =====================
@router.callback_query(F.data == "broadcast")
async def broadcast_handler(callback: CallbackQuery, state: FSMContext):
    kb = [[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]]
    await callback.message.edit_text("Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.broadcast)

@router.message(AdminStates.broadcast)
async def broadcast_process(message: Message, state: FSMContext):
    users = await db.get_all_users()
    await message.answer(f"📢 Yuborilmoqda... ({len(users)} user)")

    sent = 0
    failed = 0
    for uid in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ Yuborildi: {sent}\n❌ Xato: {failed}")
    await state.clear()
    await show_admin_panel(message)


# ===================== USER: CONTENT VIEW =====================
@router.message(F.text & ~F.text.startswith('/'))
async def handle_content_request(message: Message):
    user = message.from_user
    text = (message.text or "").strip()
    await db.update_user_activity(user.id)

    if not await db.is_admin(user.id):
        channels = await db.get_channels()
        if channels:
            not_subscribed = await check_subscription(user.id)
            if not_subscribed:
                instagram_links = await db.get_instagram_links()
                kb = build_subscribe_keyboard(not_subscribed, instagram_links)
                await message.answer("❌ Avval kanallarga obuna bo'ling:", reply_markup=kb)
                return

    try:
        content_id = int(text)
    except:
        await message.answer("❌ Iltimos, faqat kod yuboring (1,2,3...).")
        return

    content = await db.get_content(content_id)
    if not content:
        await message.answer(f"❌ {content_id} kodli kontent topilmadi.")
        return

    try:
        await db.register_download(content_id, user.id)
    except Exception as e:
        logger.error(f"register_download error: {e}")

    if content["content_type"] == "movie":
        caption = f"🎬 {content['title']}\n🔗 ID: {content['id']}"
        if content["description"]:
            caption += f"\n📝 {content['description']}"

        try:
            await message.answer_video(
                video=content["file_id"],
                caption=caption,
                protect_content=True
            )
        except:
            await message.answer("❌ Xatolik: Kino yuborilmadi.")
        return

    parts = await db.get_serial_parts(content_id)
    if not parts:
        await message.answer("❌ Bu serialda hali qismlar yo'q.")
        return

    part_number = 1
    current_part = parts[0]

    caption = (
        f"📺 {content['title']} - {current_part['title']}\n"
        f"🔗 ID: {content_id}\n"
        f"🔢 Qism: {part_number}/{len(parts)}"
    )
    if content["description"]:
        caption += f"\n📝 {content['description']}"

    keyboard = []
    if len(parts) > 1:
        keyboard.append([InlineKeyboardButton(text="➡️ Keyingi qism", callback_data=f"serial_{content_id}_2")])

    await message.answer_video(
        video=current_part["file_id"],
        caption=caption,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None,
        protect_content=True
    )


# ===================== SERIAL NAVIGATION (send new video for protect_content) =====================
@router.callback_query(F.data.startswith("serial_"))
async def handle_serial_navigation(callback: CallbackQuery):
    try:
        _, sid, pno = callback.data.split("_")
        serial_id = int(sid)
        part_number = int(pno)
    except:
        await callback.answer("❌ Xatolik.")
        return

    if not await db.is_admin(callback.from_user.id):
        channels = await db.get_channels()
        if channels:
            not_subscribed = await check_subscription(callback.from_user.id)
            if not_subscribed:
                await callback.answer("❌ Avval obuna bo'ling.")
                return

    content = await db.get_content(serial_id)
    if not content or content["content_type"] != "serial":
        await callback.answer("❌ Serial topilmadi.")
        return

    parts = await db.get_serial_parts(serial_id)
    if not parts or part_number < 1 or part_number > len(parts):
        await callback.answer("❌ Qism topilmadi.")
        return

    current_part = parts[part_number - 1]

    caption = (
        f"📺 {content['title']} - {current_part['title']}\n"
        f"🔗 ID: {serial_id}\n"
        f"🔢 Qism: {part_number}/{len(parts)}"
    )
    if content["description"]:
        caption += f"\n📝 {content['description']}"

    keyboard = []
    row = []
    if part_number > 1:
        row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"serial_{serial_id}_{part_number-1}"))
    if part_number < len(parts):
        row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"serial_{serial_id}_{part_number+1}"))
    if row:
        keyboard.append(row)

    try:
        await callback.message.answer_video(
            video=current_part["file_id"],
            caption=caption,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None,
            protect_content=True
        )
        await callback.answer()
    except:
        await callback.answer("❌ Video yuborilmadi.")


# ===================== MAIN =====================
async def main():
    await db.init_db()
    logger.info("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
