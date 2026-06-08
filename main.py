"""
Бот "Даром Тейково" — бесплатные объявления о вещах
Размещение на bothost.ru
"""
 
import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
 
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
import google.generativeai as genai
import aiosqlite
 

# ─── Настройки ────────────────────────────────────────────────────────────────
BOT_TOKEN = "8045514027:AAGhkexJ5AjQcIm95qDA1TQLIkYSd_vS-4s"

CHANNEL_ID = "@darom_tkv"          # username или -100xxxxxxxxxx @darom_tkv
MODERATOR_ID = 310342334               # Telegram ID модератора (число)
GEMINI_API_KEY = "AIzaSyAZpG9ioi1Iw-27p1--h3U-sD4_XBQ10rQ"
DB_PATH = "darom.db"
AD_LIFETIME_DAYS = 30                  # через сколько дней зачёркивать объявление

CHANNEL_URL = "https://t.me/darom_tkv"         # ссылка на канал для подписки
BOT_USERNAME = "ps_darom_teykovo_bot"           # username бота без @
 
# ─── Логирование ──────────────────────────────────────────────────────────────
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
 
# ─── Gemini ───────────────────────────────────────────────────────────────────
 
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")
 
GEMINI_PROMPT = """Ты — модератор доски объявлений «Даром Тейково».
Люди отдают ненужные вещи бесплатно.
 
Проверь объявление на наличие ЗАПРЕЩЁННОГО контента:
- продажа товаров (это бесплатная доска, не барахолка)
- оружие, наркотики, опасные вещества
- мошенничество, фишинг
- оскорбления, ненормативная лексика
- реклама сторонних сервисов
- персональные данные третьих лиц
- 18+ контент
 
Объявление:
Название: {title}
Описание: {description}
Адрес: {address}
 
Ответь СТРОГО в формате JSON:
{{"ok": true/false, "reason": "причина отказа или пусто если ok"}}
"""
 
async def check_with_gemini(title: str, description: str, address: str) -> tuple[bool, str]:
    """Проверяет объявление через Gemini. Возвращает (одобрено, причина)."""
    prompt = GEMINI_PROMPT.format(title=title, description=description, address=address)
    try:
        response = await asyncio.to_thread(gemini_model.generate_content, prompt)
        text = response.text.strip()
        # Убираем возможные markdown-блоки
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        return bool(data.get("ok", False)), data.get("reason", "")
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        # При ошибке Gemini пропускаем на ручную модерацию
        return True, ""
 
# ─── База данных ──────────────────────────────────────────────────────────────
 
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                address TEXT NOT NULL,
                photos TEXT NOT NULL,          -- JSON-список file_id
                status TEXT DEFAULT 'pending', -- pending / approved / rejected / closed
                channel_msg_id INTEGER,        -- id сообщения в канале
                created_at TEXT NOT NULL,
                published_at TEXT,
                closed_at TEXT
            )
        """)
        await db.commit()
 
async def save_ad(user_id: int, username: str, title: str, description: str,
                  address: str, photos: list[str]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO ads (user_id, username, title, description, address, photos, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username or "", title, description, address,
              json.dumps(photos), datetime.now().isoformat()))
        await db.commit()
        return cursor.lastrowid
 
async def get_ad(ad_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ads WHERE id = ?", (ad_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
 
async def update_ad_status(ad_id: int, status: str, channel_msg_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if channel_msg_id:
            await db.execute(
                "UPDATE ads SET status=?, channel_msg_id=?, published_at=? WHERE id=?",
                (status, channel_msg_id, datetime.now().isoformat(), ad_id)
            )
        else:
            await db.execute("UPDATE ads SET status=? WHERE id=?", (status, ad_id))
        await db.commit()
 
async def get_ad_by_channel_msg_id(channel_msg_id: int) -> dict | None:
    """Ищет объявление по ID сообщения в канале."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ads WHERE channel_msg_id = ?", (channel_msg_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
 
async def get_user_ads(user_id: int) -> list[dict]:
    """Возвращает активные объявления пользователя (pending + approved)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM ads
            WHERE user_id = ? AND status IN ('pending', 'approved')
            ORDER BY created_at DESC
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
 
async def get_ads_to_close() -> list[dict]:
    """Возвращает опубликованные объявления старше AD_LIFETIME_DAYS дней."""
    threshold = (datetime.now() - timedelta(days=AD_LIFETIME_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM ads
            WHERE status = 'approved' AND published_at < ?
        """, (threshold,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
 
# ─── FSM — сбор данных объявления ─────────────────────────────────────────────
 
class AdForm(StatesGroup):
    title = State()
    photos = State()
    description = State()
    address = State()
    confirm = State()
 
# ─── Роутер ───────────────────────────────────────────────────────────────────
 
router = Router()
 
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Подать объявление", callback_data="new_ad")
    ]])
 
def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить на модерацию", callback_data="confirm_ad")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_ad")],
    ])
 
def channel_post_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под каждым объявлением в канале."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Подписаться на Даром Тейково", url=CHANNEL_URL),
    ], [
        InlineKeyboardButton(text="🎁 Отдать вещь даром", url=f"https://t.me/{BOT_USERNAME}"),
    ]])
 
def moderation_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve:{ad_id}"),
        InlineKeyboardButton(text="❌ Отказать",    callback_data=f"reject:{ad_id}"),
    ]])
 
# ─── /start ───────────────────────────────────────────────────────────────────
 
@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот канала <b>«Даром Тейково»</b>.\n\n"
        "Здесь люди отдают ненужные вещи бесплатно 🎁\n\n"
        "📌 Чтобы подать объявление:\n"
        "1. Подпишитесь на канал\n"
        "2. Нажмите кнопку ниже\n\n"
        "📋 /myads — ваши объявления и управление ими\n\n"
        f"Канал: {CHANNEL_ID}",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
 
# ─── Начало подачи объявления ─────────────────────────────────────────────────
 
@router.callback_query(F.data == "new_ad")
async def start_new_ad(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # Проверяем подписку на канал
    try:
        member = await bot.get_chat_member(CHANNEL_ID, callback.from_user.id)
        if member.status in ("left", "kicked", "banned"):
            await callback.answer("Сначала подпишитесь на канал!", show_alert=True)
            return
    except Exception:
        await callback.answer("Не могу проверить подписку. Убедитесь, что подписаны на канал.", show_alert=True)
        return
 
    await state.set_state(AdForm.title)
    await callback.message.answer(
        "📝 <b>Шаг 1 из 4</b>\n\nВведите <b>название</b> вещи (кратко, например: «Детская коляска»):",
        parse_mode="HTML"
    )
    await callback.answer()
 
# ─── Название ─────────────────────────────────────────────────────────────────
 
@router.message(AdForm.title)
async def get_title(message: Message, state: FSMContext):
    if len(message.text.strip()) < 3:
        await message.answer("Слишком короткое название. Попробуйте ещё раз:")
        return
    await state.update_data(title=message.text.strip(), photos=[])
    await state.set_state(AdForm.photos)
    await message.answer(
        "📸 <b>Шаг 2 из 4</b>\n\n"
        "Отправьте <b>фото</b> вещи (можно несколько, до 5 штук).\n"
        "Когда закончите — отправьте <b>/done</b>",
        parse_mode="HTML"
    )
 
# ─── Фотографии ───────────────────────────────────────────────────────────────
 
@router.message(AdForm.photos, F.photo)
async def get_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: list = data.get("photos", [])
    if len(photos) >= 5:
        await message.answer("Максимум 5 фото. Введите /done чтобы продолжить.")
        return
    # Берём наибольшее разрешение
    file_id = message.photo[-1].file_id
    photos.append(file_id)
    await state.update_data(photos=photos)
    await message.answer(f"✅ Фото {len(photos)}/5 добавлено. Ещё или /done:")
 
@router.message(AdForm.photos, Command("done"))
async def photos_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer("Добавьте хотя бы одно фото!")
        return
    await state.set_state(AdForm.description)
    await message.answer(
        "📋 <b>Шаг 3 из 4</b>\n\nНапишите <b>описание</b> вещи "
        "(состояние, особенности, размер и т.д.):",
        parse_mode="HTML"
    )
 
# ─── Описание ─────────────────────────────────────────────────────────────────
 
@router.message(AdForm.description)
async def get_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AdForm.address)
    await message.answer(
        "📍 <b>Шаг 4 из 4</b>\n\nУкажите <b>адрес</b>, откуда можно забрать вещь "
        "(улица, ориентир — точный адрес не обязателен):",
        parse_mode="HTML"
    )
 
# ─── Адрес → предпросмотр ─────────────────────────────────────────────────────
 
@router.message(AdForm.address)
async def get_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip())
    data = await state.get_data()
 
    preview = build_ad_text(data["title"], data["description"], data["address"])
    await message.answer(
        f"👀 <b>Предпросмотр вашего объявления:</b>\n\n{preview}\n\n"
        "Всё верно? Отправьте на модерацию или отмените.",
        parse_mode="HTML",
        reply_markup=confirm_keyboard()
    )
    await state.set_state(AdForm.confirm)
 
# ─── Подтверждение ────────────────────────────────────────────────────────────
 
@router.callback_query(AdForm.confirm, F.data == "confirm_ad")
async def confirm_ad(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
 
    user = callback.from_user
    await callback.message.answer("⏳ Проверяем объявление через AI...")
 
    # Проверка Gemini
    ok, reason = await check_with_gemini(data["title"], data["description"], data["address"])
 
    if not ok:
        await callback.message.answer(
            f"🚫 Объявление не прошло автоматическую проверку.\n\n"
            f"Причина: {reason}\n\n"
            "Пожалуйста, исправьте объявление и попробуйте снова."
        )
        await callback.answer()
        return
 
    # Сохраняем в БД
    ad_id = await save_ad(
        user_id=user.id,
        username=user.username,
        title=data["title"],
        description=data["description"],
        address=data["address"],
        photos=data["photos"]
    )
 
    # Отправляем модератору
    ad_text = build_ad_text(data["title"], data["description"], data["address"])
    mod_text = (
        f"📬 <b>Новое объявление #{ad_id}</b>\n"
        f"👤 От: @{user.username or user.full_name} (id: {user.id})\n\n"
        f"{ad_text}"
    )
 
    photos = data["photos"]
    try:
        if len(photos) == 1:
            await bot.send_photo(
                MODERATOR_ID, photos[0],
                caption=mod_text, parse_mode="HTML",
                reply_markup=moderation_keyboard(ad_id)
            )
        else:
            media = [InputMediaPhoto(media=pid) for pid in photos]
            media[0] = InputMediaPhoto(media=photos[0], caption=mod_text, parse_mode="HTML")
            msgs = await bot.send_media_group(MODERATOR_ID, media)
            # Кнопки отдельным сообщением (media_group не поддерживает reply_markup)
            await bot.send_message(
                MODERATOR_ID,
                f"⬆️ Объявление #{ad_id} — выберите действие:",
                reply_markup=moderation_keyboard(ad_id)
            )
    except Exception as e:
        logger.error(f"Не удалось отправить модератору: {e}")
 
    await callback.message.answer(
        "✅ Объявление отправлено на модерацию!\n"
        "Мы уведомим вас о решении."
    )
    await callback.answer()
 
@router.callback_query(AdForm.confirm, F.data == "cancel_ad")
async def cancel_ad(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Объявление отменено.", reply_markup=main_keyboard())
    await callback.answer()
 
# ─── Модерация ────────────────────────────────────────────────────────────────
 
@router.callback_query(F.data.startswith("approve:"))
async def approve_ad(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("Нет прав.", show_alert=True)
        return
 
    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await callback.answer("Объявление уже обработано.", show_alert=True)
        return
 
    photos = json.loads(ad["photos"])
    ad_text = build_ad_text(ad["title"], ad["description"], ad["address"])
    channel_text = f"🎁 <b>{ad['title']}</b>\n\n{ad_text}"
 
    try:
        if len(photos) == 1:
            msg = await bot.send_photo(
                CHANNEL_ID, photos[0],
                caption=channel_text, parse_mode="HTML",
                reply_markup=channel_post_keyboard()
            )
            channel_msg_id = msg.message_id
        else:
            media = [InputMediaPhoto(media=pid) for pid in photos]
            media[0] = InputMediaPhoto(media=photos[0], caption=channel_text, parse_mode="HTML")
            msgs = await bot.send_media_group(CHANNEL_ID, media)
            channel_msg_id = msgs[0].message_id
            # Кнопки отдельным сообщением под альбомом
            await bot.send_message(
                CHANNEL_ID,
                "👇",
                reply_markup=channel_post_keyboard()
            )
 
        await update_ad_status(ad_id, "approved", channel_msg_id)
 
        # Уведомляем автора
        await bot.send_message(
            ad["user_id"],
            f"🎉 Ваше объявление <b>«{ad['title']}»</b> опубликовано в канале!",
            parse_mode="HTML"
        )
 
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"✅ Объявление #{ad_id} опубликовано.")
 
    except Exception as e:
        logger.error(f"Ошибка публикации #{ad_id}: {e}")
        await callback.answer(f"Ошибка: {e}", show_alert=True)
 
    await callback.answer()
 
@router.callback_query(F.data.startswith("reject:"))
async def reject_ad(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != MODERATOR_ID:
        await callback.answer("Нет прав.", show_alert=True)
        return
 
    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await callback.answer("Объявление уже обработано.", show_alert=True)
        return
 
    await update_ad_status(ad_id, "rejected")
 
    await bot.send_message(
        ad["user_id"],
        f"😔 Ваше объявление <b>«{ad['title']}»</b> отклонено модератором.\n\n"
        "Если у вас есть вопросы — свяжитесь с администратором канала.",
        parse_mode="HTML"
    )
 
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ Объявление #{ad_id} отклонено.")
    await callback.answer()
 
# ─── /myads — мои объявления ──────────────────────────────────────────────────
 
STATUS_LABELS = {
    "pending":  "⏳ На модерации",
    "approved": "✅ Опубликовано",
    "rejected": "❌ Отклонено",
    "closed":   "🔒 Закрыто",
}
 
@router.message(Command("myads"))
async def cmd_myads(message: Message):
    ads = await get_user_ads(message.from_user.id)
    if not ads:
        await message.answer(
            "У вас пока нет активных объявлений.\n\nПодать новое — кнопка ниже 👇",
            reply_markup=main_keyboard()
        )
        return
 
    await message.answer(f"📋 <b>Ваши объявления ({len(ads)}):</b>", parse_mode="HTML")
 
    for ad in ads:
        status = STATUS_LABELS.get(ad["status"], ad["status"])
        published = ""
        if ad["published_at"]:
            pub_date = datetime.fromisoformat(ad["published_at"]).strftime("%d.%m.%Y")
            published = f"\n📅 Опубликовано: {pub_date}"
 
        text = (
            f"<b>#{ad['id']} — {ad['title']}</b>\n"
            f"Статус: {status}{published}\n"
            f"📍 {ad['address']}"
        )
 
        # Кнопка «Вещь забрана» только для опубликованных
        keyboard = None
        if ad["status"] == "approved":
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Вещь забрана — закрыть",
                    callback_data=f"close_my:{ad['id']}"
                )
            ]])
 
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
 
@router.callback_query(F.data.startswith("close_my:"))
async def close_my_ad(callback: CallbackQuery, bot: Bot):
    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
 
    # Проверяем что это объявление принадлежит тому, кто нажал
    if not ad or ad["user_id"] != callback.from_user.id:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return
    if ad["status"] != "approved":
        await callback.answer("Объявление уже закрыто.", show_alert=True)
        return
 
    # Зачёркиваем пост в канале
    try:
        await bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=ad["channel_msg_id"],
            caption=build_closed_caption(ad["title"], ad["description"], ad["address"]),
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        logger.warning(f"Не удалось зачеркнуть пост #{ad_id} в канале: {e}")
 
    # Обновляем статус в БД
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ads SET status='closed', closed_at=? WHERE id=?",
            (datetime.now().isoformat(), ad_id)
        )
        await db.commit()
 
    # Убираем кнопку и сообщаем пользователю
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"🔒 Объявление <b>«{ad['title']}»</b> закрыто. Спасибо, что поделились вещью!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
    await callback.answer()
 
 
 
@router.message(F.reply_to_message & F.chat.type.in_({"group", "supergroup"}))
async def on_comment(message: Message, bot: Bot):
    """Перехватывает комментарии в группе обсуждений и уведомляет автора объявления."""
    # Комментарии к постам канала приходят как reply на «пересланное» сообщение канала.
    # forward_from_message_id — ID оригинального поста в канале.
    origin = message.reply_to_message
    if not origin:
        return
 
    # Определяем ID поста в канале
    channel_msg_id = None
    if origin.forward_from_chat and str(origin.forward_from_chat.id) in str(CHANNEL_ID):
        channel_msg_id = origin.forward_from_message_id
    elif origin.sender_chat and str(origin.sender_chat.id) in str(CHANNEL_ID):
        # Иногда Telegram отдаёт иначе
        channel_msg_id = origin.message_id
 
    if not channel_msg_id:
        return
 
    ad = await get_ad_by_channel_msg_id(channel_msg_id)
    if not ad:
        return
 
    # Не уведомляем автора о его же комментариях
    if message.from_user and message.from_user.id == ad["user_id"]:
        return
 
    commenter = message.from_user
    commenter_name = f"@{commenter.username}" if commenter.username else commenter.full_name
 
    try:
        notify_text = (
            f"💬 <b>Новый комментарий</b> к вашему объявлению «{ad['title']}»\n\n"
            f"👤 {commenter_name}:\n"
            f"{message.text or '[медиафайл]'}"
        )
        await bot.send_message(ad["user_id"], notify_text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Не удалось уведомить автора объявления #{ad['id']}: {e}")
 
 
 
def build_ad_text(title: str, description: str, address: str) -> str:
    return (
        f"📦 <b>{title}</b>\n\n"
        f"📝 {description}\n\n"
        f"📍 <b>Адрес:</b> {address}\n\n"
        f"💬 Пишите в комментариях, если хотите забрать!"
    )
 
def strikethrough(text: str) -> str:
    """Зачёркивает текст через Unicode-combining."""
    return "".join(c + "\u0336" for c in text)
 
def build_closed_caption(title: str, description: str, address: str) -> str:
    return (
        f"🚫 <s>ОБЪЯВЛЕНИЕ ЗАКРЫТО</s>\n\n"
        f"<s>📦 {title}</s>\n\n"
        f"<s>📝 {description}</s>\n\n"
        f"<s>📍 Адрес: {address}</s>\n\n"
        f"<s>💬 Пишите в комментариях, если хотите забрать!</s>"
    )
 
# ─── Планировщик: закрытие старых объявлений ──────────────────────────────────
 
async def close_old_ads(bot: Bot):
    """Запускается периодически. Зачёркивает старые объявления."""
    while True:
        await asyncio.sleep(3600)  # раз в час
        ads = await get_ads_to_close()
        for ad in ads:
            try:
                photos = json.loads(ad["photos"])
                await bot.edit_message_caption(
                    chat_id=CHANNEL_ID,
                    message_id=ad["channel_msg_id"],
                    caption=build_closed_caption(ad["title"], ad["description"], ad["address"]),
                    parse_mode="HTML"
                )
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE ads SET status='closed', closed_at=? WHERE id=?",
                        (datetime.now().isoformat(), ad["id"])
                    )
                    await db.commit()
                logger.info(f"Объявление #{ad['id']} закрыто.")
            except TelegramBadRequest as e:
                logger.warning(f"Не удалось закрыть #{ad['id']}: {e}")
            except Exception as e:
                logger.error(f"Ошибка закрытия #{ad['id']}: {e}")
 
# ─── Запуск ───────────────────────────────────────────────────────────────────
 
async def main():
    await init_db()
 
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
 
    # Запускаем планировщик параллельно
    asyncio.create_task(close_old_ads(bot))
 
    logger.info("Бот запущен.")
    await dp.start_polling(bot)
 
if __name__ == "__main__":
    asyncio.run(main())
