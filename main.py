"""
Проект "Даром [Город]" — полная рабочая реализация
Стек: aiogram >= 3.22, aiosqlite, google-generativeai, python-dotenv
"""

import os
import sys
import asyncio
import logging
import platform
import signal
import aiosqlite
from datetime import datetime, timedelta
from dotenv import load_dotenv

import google.generativeai as genai

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, BotCommand,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

# ─── Конфигурация ────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

TOKEN         = os.getenv("TOKEN", "")
CHANNEL_ID    = os.getenv("CHANNEL_ID", "").strip().strip("'\"")
MODERATOR_ID  = int(os.getenv("MODERATOR_ID", "0"))
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
DISCUSSION_GROUP_ID = int(os.getenv("DISCUSSION_GROUP_ID", "0"))  # ID группы обсуждений канала
DATA_DIR           = os.getenv("DATA_DIR", "./data")

if not TOKEN:
    raise ValueError("TOKEN не задан в .env")
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID не задан в .env")
if not MODERATOR_ID:
    raise ValueError("MODERATOR_ID не задан в .env")

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "darom.db")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

router = Router()

# ─── FSM ─────────────────────────────────────────────────────────────────────

class AdminSearch(StatesGroup):
    query    = State()
    wait_id  = State()   # ожидание ID для удаления из канала

class AdForm(StatesGroup):
    title       = State()   # название/что отдаёте
    photos      = State()   # фото (до 5 штук)
    description = State()   # описание
    address     = State()   # адрес/район
    confirm     = State()   # финальное подтверждение

# ─── База данных ─────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                username       TEXT,
                full_name      TEXT,
                title          TEXT NOT NULL,
                description    TEXT NOT NULL,
                address        TEXT NOT NULL,
                status         TEXT DEFAULT 'pending',
                channel_msg_id INTEGER,
                created_at     TEXT NOT NULL,
                published_at   TEXT,
                closed_at      TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ad_photos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_id      INTEGER NOT NULL,
                file_id    TEXT NOT NULL,
                position   INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (ad_id) REFERENCES ads(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ad_comments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_id         INTEGER NOT NULL,
                tg_message_id INTEGER,
                user_id       INTEGER,
                username      TEXT,
                full_name     TEXT,
                text          TEXT,
                media_type    TEXT,
                media_file_id TEXT,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (ad_id) REFERENCES ads(id)
            )
        """)
        await db.commit()
    log.info("БД инициализирована: %s", DB_PATH)


async def create_ad(
    user_id: int,
    username: str | None,
    full_name: str,
    title: str,
    description: str,
    address: str,
) -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO ads (user_id, username, full_name, title, description,
                                address, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (user_id, username, full_name, title, description, address, now),
        )
        ad_id = cur.lastrowid
        await db.commit()
    return ad_id


async def save_photos(ad_id: int, file_ids: list[str]):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        for pos, fid in enumerate(file_ids):
            await db.execute(
                "INSERT INTO ad_photos (ad_id, file_id, position, created_at) VALUES (?, ?, ?, ?)",
                (ad_id, fid, pos, now),
            )
        await db.commit()


async def get_ad(ad_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_ad_photos(ad_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT file_id FROM ad_photos WHERE ad_id = ? ORDER BY position",
            (ad_id,),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def set_ad_status(ad_id: int, status: str, channel_msg_id: int | None = None):
    now = datetime.utcnow().isoformat()
    if status == "approved":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE ads SET status=?, channel_msg_id=?, published_at=? WHERE id=?",
                (status, channel_msg_id, now, ad_id),
            )
            await db.commit()
    elif status == "closed":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE ads SET status=?, closed_at=? WHERE id=?",
                (status, now, ad_id),
            )
            await db.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE ads SET status=? WHERE id=?", (status, ad_id))
            await db.commit()


async def get_user_ads(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ads WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_ads_to_close(days: int = 30) -> list[int]:
    limit = (datetime.utcnow() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM ads WHERE status='approved' AND created_at < ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]



async def get_stats() -> dict:
    """Сводная статистика по базе данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        stats = {}
        for status in ("pending", "approved", "rejected", "closed"):
            cur = await db.execute("SELECT COUNT(*) FROM ads WHERE status=?", (status,))
            row = await cur.fetchone()
            stats[status] = row[0]
        cur = await db.execute("SELECT COUNT(*) FROM ads")
        row = await cur.fetchone()
        stats["total"] = row[0]
        cur = await db.execute("SELECT COUNT(DISTINCT user_id) FROM ads")
        row = await cur.fetchone()
        stats["users"] = row[0]
        return stats


async def get_recent_ads(limit: int = 10, status: str | None = None) -> list[dict]:
    """Последние объявления (опционально — фильтр по статусу)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM ads WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM ads ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def search_ads(query: str) -> list[dict]:
    """Полнотекстовый поиск по title и description."""
    q = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ads WHERE title LIKE ? OR description LIKE ? ORDER BY created_at DESC LIMIT 20",
            (q, q),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_ad_hard(ad_id: int):
    """Полное удаление объявления и его фото из БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM ad_photos WHERE ad_id=?", (ad_id,))
        await db.execute("DELETE FROM ad_comments WHERE ad_id=?", (ad_id,))
        await db.execute("DELETE FROM ads WHERE id=?", (ad_id,))
        await db.commit()


async def get_ad_by_channel_msg(channel_msg_id: int) -> dict | None:
    """Ищет объявление по ID сообщения в канале."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ads WHERE channel_msg_id = ?", (channel_msg_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def save_comment(
    ad_id: int,
    tg_message_id: int,
    user_id: int | None,
    username: str | None,
    full_name: str | None,
    text: str | None,
    media_type: str | None,
    media_file_id: str | None,
):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO ad_comments
               (ad_id, tg_message_id, user_id, username, full_name,
                text, media_type, media_file_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ad_id, tg_message_id, user_id, username, full_name,
             text, media_type, media_file_id, now),
        )
        await db.commit()

# ─── Gemini модерация ─────────────────────────────────────────────────────────

GEMINI_SYSTEM = """Ты — модератор доски объявлений «Даром».
Проверь объявление на:
1. Отсутствие запрещённых товаров (оружие, наркотики, алкоголь, табак, лекарства, животные, б/у нижнее бельё).
2. Коммерческий характер — объявление должно быть о безвозмездной передаче, а не о продаже.
3. Спам, мошенничество, оскорбления.
Ответь строго JSON: {"ok": true/false, "reason": "причина если ok=false, иначе пусто"}"""


# Список моделей Gemini для перебора (актуальные названия на 2025)
GEMINI_MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash"]


async def gemini_check(title: str, description: str, address: str) -> tuple[bool, str]:
    if not GEMINI_API_KEY:
        log.info("Gemini: ключ не задан, проверка пропущена")
        return True, ""

    import json, re
    prompt = f"Название: {title}\nОписание: {description}\nАдрес: {address}"
    last_error = None

    for model_name in GEMINI_MODELS:
        try:
            log.info("Gemini: пробуем модель %s", model_name)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=GEMINI_SYSTEM,
            )
            response = await asyncio.to_thread(model.generate_content, prompt)
            text = response.text.strip()
            log.info("Gemini raw response: %s", text)
            # убираем markdown-обёртку если есть
            text = re.sub(r"```(?:json)?|```", "", text).strip()
            data = json.loads(text)
            result_ok = bool(data.get("ok", True))
            reason = data.get("reason", "")
            log.info("Gemini результат: ok=%s reason=%s", result_ok, reason)
            return result_ok, reason
        except json.JSONDecodeError as e:
            log.warning("Gemini %s: не удалось разобрать JSON: %s | текст: %s", model_name, e, text)
            return True, ""  # ответ получен, но не JSON — пропускаем
        except Exception as e:
            last_error = e
            log.warning("Gemini %s недоступна: %s", model_name, e)
            continue

    log.error("Gemini: все модели недоступны. Последняя ошибка: %s", last_error)
    return True, ""  # при полном сбое — на ручную модерацию

# ─── Клавиатуры ───────────────────────────────────────────────────────────────


def kb_main_menu() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура — всегда видна внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Подать объявление")],
            [KeyboardButton(text="📂 Мои объявления"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,   # не скрывается после нажатия
    )


def kb_admin() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура администратора."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📋 Объявления")],
            [KeyboardButton(text="⏳ На модерации"), KeyboardButton(text="🔍 Поиск")],
            [KeyboardButton(text="📡 Статус канала"), KeyboardButton(text="🔧 Диагностика")],
            [KeyboardButton(text="👥 Главное меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def kb_admin_ads_filter() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Все",       callback_data="adlist:all"),
            InlineKeyboardButton(text="✅ Одобрены", callback_data="adlist:approved"),
        ],
        [
            InlineKeyboardButton(text="❌ Отклонены", callback_data="adlist:rejected"),
            InlineKeyboardButton(text="🔒 Закрыты",   callback_data="adlist:closed"),
        ],
    ])


def kb_ad_admin_actions(ad_id: int, has_channel_msg: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_channel_msg:
        buttons.append(InlineKeyboardButton(text="🗑 Удалить из канала", callback_data=f"adm:delch:{ad_id}"))
    buttons.append(InlineKeyboardButton(text="💣 Удалить из БД", callback_data=f"adm:deldb:{ad_id}"))
    buttons.append(InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"close:{ad_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[[b] for b in buttons])

def kb_cancel() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_skip_photos() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="➡️ Пропустить фото")], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_done_photos() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Готово")], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_confirm() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Опубликовать")],
            [KeyboardButton(text="🔄 Начать заново")],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_moderation(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod:approve:{ad_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{ad_id}"),
    ]])

def kb_close_ad(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔒 Закрыть объявление", callback_data=f"close:{ad_id}"),
    ]])

# ─── Вспомогательные функции ──────────────────────────────────────────────────

STATUS_LABELS = {
    "pending":  "⏳ На модерации",
    "approved": "✅ Опубликовано",
    "rejected": "❌ Отклонено",
    "closed":   "🔒 Закрыто",
}

def format_ad(ad: dict) -> str:
    status = STATUS_LABELS.get(ad["status"], ad["status"])
    lines = [
        f"📦 <b>{ad['title']}</b>",
        f"📝 {ad['description']}",
        f"📍 {ad['address']}",
        f"Статус: {status}",
    ]
    return "\n".join(lines)


async def send_ad_to_channel(bot: Bot, ad: dict, photos: list[str]) -> int | None:
    """Публикует объявление в канал, возвращает message_id."""
    caption = (
        f"📦 <b>{ad['title']}</b>\n\n"
        f"📝 {ad['description']}\n\n"
        f"📍 {ad['address']}\n\n"
        f"👤 @{ad['username'] or 'аноним'}"
    )
    log.info(
        "send_ad_to_channel: ad_id=%s CHANNEL_ID=%r photos=%d",
        ad["id"], CHANNEL_ID, len(photos),
    )
    try:
        if photos:
            media = [
                InputMediaPhoto(
                    media=photos[0],
                    caption=caption,
                    parse_mode="HTML",
                )
            ] + [InputMediaPhoto(media=fid) for fid in photos[1:5]]
            msgs = await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
            log.info("send_ad_to_channel: опубликовано с фото, msg_id=%s", msgs[0].message_id)
            return msgs[0].message_id
        else:
            msg = await bot.send_message(
                chat_id=CHANNEL_ID,
                text=caption,
                parse_mode="HTML",
            )
            log.info("send_ad_to_channel: опубликовано без фото, msg_id=%s", msg.message_id)
            return msg.message_id
    except Exception as e:
        # Ловим ВСЕ исключения — TelegramForbiddenError, TelegramBadRequest и др.
        log.error(
            "send_ad_to_channel ОШИБКА (тип=%s): %s | CHANNEL_ID=%r",
            type(e).__name__, e, CHANNEL_ID,
        )
        return None


async def notify_admin(bot: Bot, text: str):
    """Отправляет служебное уведомление администратору."""
    try:
        await bot.send_message(chat_id=MODERATOR_ID, text=text, parse_mode="HTML")
    except Exception as e:
        log.warning("notify_admin failed: %s", e)

# ─── Хендлеры: общие ─────────────────────────────────────────────────────────

@router.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎁 <b>Добро пожаловать в Даром!</b>\n\n"
        "Здесь люди бесплатно отдают ненужные вещи.\n\n"
        "Используйте кнопки меню внизу экрана 👇",
        parse_mode="HTML",
        reply_markup=kb_main_menu(),
    )


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=kb_main_menu(),
    )


@router.message(Command("myads"))
async def my_ads(message: Message):
    ads = await get_user_ads(message.from_user.id)
    if not ads:
        await message.answer(
            "У вас пока нет объявлений.",
            reply_markup=kb_main_menu(),
        )
        return

    text_lines = ["<b>Ваши объявления:</b>\n"]
    for ad in ads:
        status = STATUS_LABELS.get(ad["status"], ad["status"])
        text_lines.append(f"• <b>{ad['title']}</b> — {status}")

    await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=kb_main_menu())


@router.message(F.text == "📋 Подать объявление")
async def btn_new_ad(message: Message, state: FSMContext):
    await new_ad_start(message, state)


@router.message(F.text == "📂 Мои объявления")
async def btn_my_ads(message: Message):
    await my_ads(message)


@router.message(F.text == "ℹ️ Помощь")
async def btn_help(message: Message):
    await message.answer(
        "📌 <b>Как пользоваться ботом:</b>\n\n"
        "1. Нажмите <b>📋 Подать объявление</b>\n"
        "2. Заполните название, фото, описание и адрес\n"
        "3. Объявление уйдёт на модерацию\n"
        "4. После одобрения оно появится в канале\n"
        "5. Когда вещь отдана — закройте объявление кнопкой\n\n"
        "❓ Вопросы? Пишите модератору.",
        parse_mode="HTML",
        reply_markup=kb_main_menu(),
    )


# ─── Админ-панель ────────────────────────────────────────────────────────────

def is_admin(message: Message) -> bool:
    return message.from_user.id == MODERATOR_ID


@router.message(Command("admin"))
@router.message(F.text == "👥 Главное меню")
async def admin_panel(message: Message, state: FSMContext):
    if not is_admin(message):
        await message.answer("⛔ Нет доступа.")
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Панель администратора</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=kb_admin(),
    )


@router.message(F.text == "📊 Статистика")
async def admin_stats(message: Message, bot: Bot):
    if not is_admin(message):
        return
    stats = await get_stats()
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        channel_name = chat.title
        # подписчики доступны только для каналов
        member_count = await bot.get_chat_member_count(CHANNEL_ID)
    except Exception as e:
        channel_name = f"ошибка: {e}"
        member_count = "—"

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"📦 Всего объявлений: <b>{stats['total']}</b>\n"
        f"⏳ На модерации:     <b>{stats['pending']}</b>\n"
        f"✅ Опубликовано:     <b>{stats['approved']}</b>\n"
        f"❌ Отклонено:        <b>{stats['rejected']}</b>\n"
        f"🔒 Закрыто:          <b>{stats['closed']}</b>\n\n"
        f"👥 Уникальных пользователей: <b>{stats['users']}</b>\n\n"
        f"📡 Канал: <b>{channel_name}</b>\n"
        f"👁 Подписчиков: <b>{member_count}</b>\n"
        f"🕐 Время сервера: <code>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</code>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📡 Статус канала")
async def admin_channel_status(message: Message, bot: Bot):
    if not is_admin(message):
        return
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        me = await bot.get_me()
        member = await bot.get_chat_member(CHANNEL_ID, me.id)
        can_post    = getattr(member, "can_post_messages", "—")
        can_delete  = getattr(member, "can_delete_messages", "—")
        can_edit    = getattr(member, "can_edit_messages", "—")
        member_count = await bot.get_chat_member_count(CHANNEL_ID)
        text = (
            f"📡 <b>Состояние канала</b>\n\n"
            f"Название: <b>{chat.title}</b>\n"
            f"ID: <code>{chat.id}</code>\n"
            f"Тип: {chat.type}\n"
            f"Подписчиков: <b>{member_count}</b>\n\n"
            f"<b>Права бота:</b>\n"
            f"  Постить:  {'✅' if can_post else '❌'}\n"
            f"  Удалять:  {'✅' if can_delete else '❌'}\n"
            f"  Редактировать: {'✅' if can_edit else '❌'}"
        )
    except Exception as e:
        text = f"❌ Не удалось получить данные канала:\n<code>{e}</code>"
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📋 Объявления")
async def admin_ads_list(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "Выберите фильтр:",
        reply_markup=kb_admin_ads_filter(),
    )


@router.callback_query(F.data.startswith("adlist:"))
async def admin_ads_filter_cb(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    status_map = {"all": None, "approved": "approved", "rejected": "rejected", "closed": "closed"}
    key = callback.data.split(":")[1]
    status = status_map.get(key)
    ads = await get_recent_ads(limit=10, status=status)
    if not ads:
        await callback.message.answer("Объявлений не найдено.")
        await callback.answer()
        return
    label = {"all": "Последние 10", "approved": "✅ Опубликованные",
             "rejected": "❌ Отклонённые", "closed": "🔒 Закрытые"}.get(key, "")
    await callback.message.answer(f"<b>{label} объявления:</b>", parse_mode="HTML")
    for ad in ads:
        status_label = STATUS_LABELS.get(ad["status"], ad["status"])
        text = (
            f"<b>#{ad['id']}</b> | {status_label}\n"
            f"📦 {ad['title']}\n"
            f"👤 @{ad['username'] or '—'} | {ad['full_name'] or '—'}\n"
            f"📍 {ad['address']}\n"
            f"🕐 {ad['created_at'][:16]}"
        )
        has_ch = bool(ad.get("channel_msg_id"))
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb_ad_admin_actions(ad["id"], has_ch),
        )
    await callback.answer()


@router.message(F.text == "⏳ На модерации")
async def admin_pending(message: Message):
    if not is_admin(message):
        return
    ads = await get_recent_ads(limit=20, status="pending")
    if not ads:
        await message.answer("Нет объявлений на модерации. 🎉")
        return
    await message.answer(f"<b>На модерации: {len(ads)} шт.</b>", parse_mode="HTML")
    for ad in ads:
        text = (
            f"<b>#{ad['id']}</b> — {ad['title']}\n"
            f"👤 @{ad['username'] or '—'}\n"
            f"📝 {ad['description'][:200]}\n"
            f"📍 {ad['address']}"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb_moderation(ad["id"]),
        )


@router.message(F.text == "🔍 Поиск")
async def admin_search_start(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.set_state(AdminSearch.query)
    await message.answer(
        "🔍 Введите поисковый запрос (по названию или описанию):",
        reply_markup=kb_cancel(),
    )


@router.message(AdminSearch.query)
async def admin_search_query(message: Message, state: FSMContext):
    query = message.text.strip() if message.text else ""
    if not query:
        return
    await state.clear()
    ads = await search_ads(query)
    if not ads:
        await message.answer("Ничего не найдено.", reply_markup=kb_admin())
        return
    await message.answer(f"<b>Найдено: {len(ads)}</b>", parse_mode="HTML", reply_markup=kb_admin())
    for ad in ads:
        status_label = STATUS_LABELS.get(ad["status"], ad["status"])
        text = (
            f"<b>#{ad['id']}</b> | {status_label}\n"
            f"📦 {ad['title']}\n"
            f"👤 @{ad['username'] or '—'}\n"
            f"📍 {ad['address']}\n"
            f"🕐 {ad['created_at'][:16]}"
        )
        has_ch = bool(ad.get("channel_msg_id"))
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb_ad_admin_actions(ad["id"], has_ch),
        )


@router.callback_query(F.data.startswith("adm:"))
async def admin_ad_action(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = callback.data.split(":")
    action  = parts[1]
    ad_id   = int(parts[2])
    ad = await get_ad(ad_id)
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return

    if action == "delch":
        # Удалить сообщение из канала
        if ad.get("channel_msg_id"):
            try:
                await bot.delete_message(chat_id=CHANNEL_ID, message_id=ad["channel_msg_id"])
                await set_ad_status(ad_id, "closed")
                await callback.answer("✅ Удалено из канала, статус → закрыто.")
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.reply(f"🗑 Объявление #{ad_id} удалено из канала.")
            except Exception as e:
                await callback.answer(f"Ошибка: {e}", show_alert=True)
        else:
            await callback.answer("Нет сообщения в канале.", show_alert=True)

    elif action == "deldb":
        # Полное удаление из БД
        channel_msg_id = ad.get("channel_msg_id")
        if channel_msg_id:
            try:
                await bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_msg_id)
            except Exception:
                pass
        await delete_ad_hard(ad_id)
        await callback.answer("💣 Полностью удалено из БД.")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"💣 Объявление #{ad_id} удалено из БД.")


@router.message(Command("debug"))
@router.message(F.text == "🔧 Диагностика")
async def debug_cmd(message: Message, bot: Bot):
    """Диагностика для администратора."""
    if not is_admin(message):
        return
    lines = [
        "🔧 <b>Диагностика</b>", "",
        f"<b>CHANNEL_ID:</b> <code>{CHANNEL_ID}</code>",
        f"<b>Python:</b> {sys.version.split()[0]}",
        f"<b>Платформа:</b> {platform.system()} {platform.release()}",
        f"<b>БД:</b> <code>{DB_PATH}</code>",
        f"<b>Размер БД:</b> {os.path.getsize(DB_PATH) // 1024} КБ" if os.path.exists(DB_PATH) else "БД не найдена",
        "",
    ]
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        me = await bot.get_me()
        member = await bot.get_chat_member(CHANNEL_ID, me.id)
        can_post = getattr(member, "can_post_messages", None)
        lines.append(f"✅ Канал: <b>{chat.title}</b>  (постить: {'да' if can_post else 'нет'})")
    except Exception as e:
        lines.append(f"❌ Канал: <code>{type(e).__name__}: {e}</code>")
    if GEMINI_API_KEY:
        ok, reason = await gemini_check("тест", "тест", "тест")
        lines.append(f"{'✅' if ok else '⚠️'} Gemini: {'работает' if ok else reason}")
    else:
        lines.append("⚠️ Gemini: ключ не задан")
    await message.answer("\n".join(lines), parse_mode="HTML")

# ─── FSM: подача объявления ───────────────────────────────────────────────────

@router.message(Command("newad"))
async def new_ad_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdForm.title)
    await message.answer(
        "📋 <b>Подача объявления</b>\n\nШаг 1/4\n\n"
        "Напишите, <b>что вы отдаёте</b> (кратко, до 100 символов):",
        parse_mode="HTML",
        reply_markup=kb_cancel(),
    )


@router.message(AdForm.title)
async def ad_title(message: Message, state: FSMContext):
    title = message.text.strip() if message.text else ""
    if not title or len(title) > 100:
        await message.answer("Пожалуйста, введите название от 1 до 100 символов.")
        return
    await state.update_data(title=title, photos=[])
    await state.set_state(AdForm.photos)
    await message.answer(
        "📷 Шаг 2/4\n\nПришлите <b>до 5 фотографий</b> товара.\n"
        "Когда загрузите все — нажмите <b>✅ Готово</b>.\n"
        "Если фото нет — нажмите <b>➡️ Пропустить фото</b>.",
        parse_mode="HTML",
        reply_markup=kb_skip_photos(),
    )


@router.message(AdForm.photos, F.photo)
async def ad_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: list = data.get("photos", [])
    if len(photos) >= 5:
        await message.answer("Максимум 5 фото. Нажмите ✅ Готово.")
        return
    # берём наибольший размер
    file_id = message.photo[-1].file_id
    photos.append(file_id)
    await state.update_data(photos=photos)
    await message.answer(
        f"Фото {len(photos)}/5 добавлено. Пришлите ещё или нажмите ✅ Готово.",
        reply_markup=kb_done_photos(),
    )


@router.message(AdForm.photos, F.text.in_({"✅ Готово", "➡️ Пропустить фото"}))
async def ad_photos_done(message: Message, state: FSMContext):
    await state.set_state(AdForm.description)
    await message.answer(
        "📝 Шаг 3/4\n\nОпишите вещь подробнее: состояние, размер, причина отдачи и т.д.",
        reply_markup=kb_cancel(),
    )


@router.message(AdForm.photos)
async def ad_photos_wrong(message: Message):
    await message.answer("Пожалуйста, пришлите фото или нажмите кнопку.")


@router.message(AdForm.description)
async def ad_description(message: Message, state: FSMContext):
    desc = message.text.strip() if message.text else ""
    if not desc or len(desc) > 1000:
        await message.answer("Описание должно быть от 1 до 1000 символов.")
        return
    await state.update_data(description=desc)
    await state.set_state(AdForm.address)
    await message.answer(
        "📍 Шаг 4/4\n\nУкажите <b>район или адрес</b> для передачи вещи:",
        parse_mode="HTML",
        reply_markup=kb_cancel(),
    )


@router.message(AdForm.address)
async def ad_address(message: Message, state: FSMContext):
    address = message.text.strip() if message.text else ""
    if not address or len(address) > 200:
        await message.answer("Адрес должен быть от 1 до 200 символов.")
        return
    await state.update_data(address=address)
    data = await state.get_data()

    preview = (
        f"📋 <b>Проверьте объявление:</b>\n\n"
        f"📦 <b>{data['title']}</b>\n"
        f"📝 {data['description']}\n"
        f"📍 {data['address']}\n"
        f"🖼 Фото: {len(data.get('photos', []))} шт.\n\n"
        f"Всё верно?"
    )
    await state.set_state(AdForm.confirm)
    await message.answer(preview, parse_mode="HTML", reply_markup=kb_confirm())


@router.message(AdForm.confirm, F.text == "🔄 Начать заново")
async def ad_restart(message: Message, state: FSMContext):
    await state.clear()
    await new_ad_start(message, state)


@router.message(AdForm.confirm, F.text == "✅ Опубликовать")
async def ad_submit(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    # Gemini-проверка
    ok, reason = await gemini_check(data["title"], data["description"], data["address"])
    if not ok:
        await message.answer(
            f"❌ Объявление не прошло автоматическую проверку:\n{reason}\n\n"
            "Отредактируйте и попробуйте снова — /newad",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    user = message.from_user
    ad_id = await create_ad(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        title=data["title"],
        description=data["description"],
        address=data["address"],
    )
    photos = data.get("photos", [])
    if photos:
        await save_photos(ad_id, photos)

    await message.answer(
        "✅ Объявление отправлено на модерацию!\nМы сообщим о результате.",
        reply_markup=kb_main_menu(),
    )

    # Уведомление модератору
    ad = await get_ad(ad_id)
    mod_text = (
        f"🆕 <b>Новое объявление #{ad_id}</b>\n\n"
        f"{format_ad(ad)}\n\n"
        f"👤 {user.full_name} (@{user.username or '—'})"
    )
    try:
        if photos:
            media = [InputMediaPhoto(media=photos[0], caption=mod_text, parse_mode="HTML")]
            media += [InputMediaPhoto(media=f) for f in photos[1:5]]
            await bot.send_media_group(chat_id=MODERATOR_ID, media=media)
            await bot.send_message(
                chat_id=MODERATOR_ID,
                text="Действие по объявлению:",
                reply_markup=kb_moderation(ad_id),
            )
        else:
            await bot.send_message(
                chat_id=MODERATOR_ID,
                text=mod_text,
                parse_mode="HTML",
                reply_markup=kb_moderation(ad_id),
            )
    except Exception as e:
        log.error("Не удалось уведомить модератора: %s", e)

# ─── Модерация ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mod:"))
async def moderation_callback(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("Нет прав.", show_alert=True)
        return

    _, action, ad_id_str = callback.data.split(":")
    ad_id = int(ad_id_str)
    ad = await get_ad(ad_id)

    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    if ad["status"] != "pending":
        await callback.answer(f"Уже обработано: {ad['status']}", show_alert=True)
        return

    if action == "approve":
        photos = await get_ad_photos(ad_id)
        channel_msg_id = await send_ad_to_channel(bot, ad, photos)
        await set_ad_status(ad_id, "approved", channel_msg_id)

        try:
            await bot.send_message(
                chat_id=ad["user_id"],
                text=(
                    f"🎉 Ваше объявление <b>«{ad['title']}»</b> одобрено и опубликовано!\n\n"
                    f"Когда вещь будет отдана — закройте объявление:"
                ),
                parse_mode="HTML",
                reply_markup=kb_close_ad(ad_id),
            )
        except Exception:
            pass

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ Одобрено и опубликовано.")
        await callback.message.reply(f"✅ Объявление #{ad_id} опубликовано.")

    elif action == "reject":
        await set_ad_status(ad_id, "rejected")

        try:
            await bot.send_message(
                chat_id=ad["user_id"],
                text=(
                    f"😔 Ваше объявление <b>«{ad['title']}»</b> отклонено модератором.\n\n"
                    "Причины: несоответствие правилам сервиса.\n"
                    "Можно подать новое объявление: /newad"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("❌ Отклонено.")
        await callback.message.reply(f"❌ Объявление #{ad_id} отклонено.")


@router.callback_query(F.data.startswith("close:"))
async def close_ad_callback(callback: CallbackQuery, bot: Bot):
    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)

    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    if ad["user_id"] != callback.from_user.id:
        await callback.answer("Это не ваше объявление.", show_alert=True)
        return
    if ad["status"] == "closed":
        await callback.answer("Уже закрыто.", show_alert=True)
        return

    await set_ad_status(ad_id, "closed")

    # Пытаемся удалить сообщение из канала
    if ad.get("channel_msg_id"):
        try:
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=ad["channel_msg_id"])
        except Exception:
            pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🔒 Объявление закрыто.")
    await callback.message.reply("🔒 Объявление закрыто. Спасибо!")


# ─── Комментарии из группы обсуждений ────────────────────────────────────────

@router.message(F.chat.id == DISCUSSION_GROUP_ID)
async def handle_discussion_comment(message: Message, bot: Bot):
    """
    Слушает группу обсуждений канала.
    Каждое сообщение — потенциальный комментарий к объявлению.
    message.reply_to_message.forward_from_message_id — ID поста в канале.
    """
    if not DISCUSSION_GROUP_ID:
        return

    # Telegram пересылает исходный пост канала как reply_to_message
    reply = message.reply_to_message
    if not reply:
        return  # не ответ на пост — игнорируем

    # ID поста в канале берём из forward_origin или sender_chat
    channel_msg_id = None

    # aiogram 3.x: reply_to_message.forward_from_message_id для постов канала
    if reply.forward_from_message_id:
        channel_msg_id = reply.forward_from_message_id
    elif reply.message_id:
        # fallback: в некоторых конфигурациях ID совпадает
        channel_msg_id = reply.message_id

    if not channel_msg_id:
        return

    ad = await get_ad_by_channel_msg(channel_msg_id)
    if not ad:
        return  # пост не связан с объявлением

    # Пропускаем авто-пересылку самого поста из канала
    sender = message.from_user
    if message.sender_chat and message.sender_chat.id == int(CHANNEL_ID.replace("@", "") if "@" in CHANNEL_ID else CHANNEL_ID):
        return

    # Определяем медиа
    media_type = None
    media_file_id = None
    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id
    elif message.document:
        media_type = "document"
        media_file_id = message.document.file_id
    elif message.sticker:
        media_type = "sticker"
        media_file_id = message.sticker.file_id

    comment_text = message.text or message.caption or ""

    # Сохраняем в БД
    await save_comment(
        ad_id=ad["id"],
        tg_message_id=message.message_id,
        user_id=sender.id if sender else None,
        username=sender.username if sender else None,
        full_name=sender.full_name if sender else None,
        text=comment_text,
        media_type=media_type,
        media_file_id=media_file_id,
    )

    # Не уведомляем автора о его же комментарии
    if sender and sender.id == ad["user_id"]:
        return

    # Формируем превью текста комментария
    preview = comment_text[:200] if comment_text else f"[{media_type or 'сообщение'}]"
    commenter = f"@{sender.username}" if (sender and sender.username) else (sender.full_name if sender else "Аноним")

    notify_text = (
        f"💬 <b>Новый комментарий к вашему объявлению</b>\n\n"
        f"📦 «{ad['title']}»\n\n"
        f"👤 {commenter}:\n"
        f"{preview}"
    )

    # Пересылаем медиа если есть
    try:
        if media_type == "photo":
            await bot.send_photo(
                chat_id=ad["user_id"],
                photo=media_file_id,
                caption=notify_text,
                parse_mode="HTML",
            )
        elif media_type == "video":
            await bot.send_video(
                chat_id=ad["user_id"],
                video=media_file_id,
                caption=notify_text,
                parse_mode="HTML",
            )
        elif media_type in ("document", "sticker"):
            await bot.send_message(chat_id=ad["user_id"], text=notify_text, parse_mode="HTML")
            await bot.send_document(chat_id=ad["user_id"], document=media_file_id)
        else:
            await bot.send_message(chat_id=ad["user_id"], text=notify_text, parse_mode="HTML")
        log.info(
            "Уведомление о комментарии отправлено user_id=%s, ad_id=%s",
            ad["user_id"], ad["id"],
        )
    except Exception as e:
        log.warning("Не удалось уведомить автора объявления #%s: %s", ad["id"], e)

# ─── Фоновая задача: автозакрытие ─────────────────────────────────────────────

async def close_old_ads(bot: Bot):
    """Каждый час проверяет объявления старше 30 дней и закрывает их."""
    while True:
        await asyncio.sleep(3600)
        try:
            old_ids = await get_ads_to_close(days=30)
            for ad_id in old_ids:
                ad = await get_ad(ad_id)
                if not ad:
                    continue
                await set_ad_status(ad_id, "closed")
                if ad.get("channel_msg_id"):
                    try:
                        await bot.delete_message(
                            chat_id=CHANNEL_ID,
                            message_id=ad["channel_msg_id"],
                        )
                    except Exception:
                        pass
                try:
                    await bot.send_message(
                        chat_id=ad["user_id"],
                        text=(
                            f"🔒 Ваше объявление <b>«{ad['title']}»</b> "
                            "автоматически закрыто (прошло 30 дней).",
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("Автозакрыто объявление #%d", ad_id)
        except Exception as e:
            log.error("close_old_ads error: %s", e)

# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    await init_db()

    bot = Bot(TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    task = asyncio.create_task(close_old_ads(bot))

    # Команды в меню Telegram (кнопка "/" слева от поля ввода)
    await bot.set_my_commands([
        BotCommand(command="start",  description="Главное меню"),
        BotCommand(command="newad",  description="Подать объявление"),
        BotCommand(command="myads",  description="Мои объявления"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="admin",  description="Панель администратора"),
    ])

    started_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await notify_admin(
        bot,
        f"🟢 <b>Бот запущен</b>\n"
        f"🕐 {started_at}\n"
        f"🖥 {platform.system()} {platform.release()}\n"
        f"🐍 Python {sys.version.split()[0]}\n"
        f"📡 Канал: <code>{CHANNEL_ID}</code>\n"
        f"💬 Группа обсуждений: <code>{DISCUSSION_GROUP_ID or 'не задана'}</code>"
    )
    log.info("Бот запущен, уведомление отправлено администратору.")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        await notify_admin(bot, f"💥 <b>Бот упал с ошибкой:</b>\n<code>{e}</code>")
        raise
    finally:
        stopped_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        await notify_admin(bot, f"🔴 <b>Бот остановлен</b>\n🕐 {stopped_at}")
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
