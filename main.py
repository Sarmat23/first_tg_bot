"""
Проект "Даром [Город]" — полная рабочая реализация
Стек: aiogram >= 3.22, aiosqlite, google-generativeai, python-dotenv
"""

import os
import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from dotenv import load_dotenv

import google.generativeai as genai

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove,
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
CHANNEL_ID    = os.getenv("CHANNEL_ID", "")          # "@mychannel" или "-100..."
MODERATOR_ID  = int(os.getenv("MODERATOR_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATA_DIR      = os.getenv("DATA_DIR", "./data")

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

# ─── Gemini модерация ─────────────────────────────────────────────────────────

GEMINI_SYSTEM = """Ты — модератор доски объявлений «Даром».
Проверь объявление на:
1. Отсутствие запрещённых товаров (оружие, наркотики, алкоголь, табак, лекарства, животные, б/у нижнее бельё).
2. Коммерческий характер — объявление должно быть о безвозмездной передаче, а не о продаже.
3. Спам, мошенничество, оскорбления.
Ответь строго JSON: {"ok": true/false, "reason": "причина если ok=false, иначе пусто"}"""


async def gemini_check(title: str, description: str, address: str) -> tuple[bool, str]:
    if not GEMINI_API_KEY:
        return True, ""
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=GEMINI_SYSTEM,
        )
        prompt = f"Название: {title}\nОписание: {description}\nАдрес: {address}"
        response = await asyncio.to_thread(model.generate_content, prompt)
        import json, re
        text = response.text.strip()
        # убираем markdown-обёртку если есть
        text = re.sub(r"```(?:json)?|```", "", text).strip()
        data = json.loads(text)
        return bool(data.get("ok", True)), data.get("reason", "")
    except Exception as e:
        log.warning("Gemini error: %s", e)
        return True, ""  # при ошибке пропускаем на ручную модерацию

# ─── Клавиатуры ───────────────────────────────────────────────────────────────

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
            return msgs[0].message_id
        else:
            msg = await bot.send_message(
                chat_id=CHANNEL_ID,
                text=caption,
                parse_mode="HTML",
            )
            return msg.message_id
    except TelegramBadRequest as e:
        log.error("send_ad_to_channel error: %s", e)
        return None

# ─── Хендлеры: общие ─────────────────────────────────────────────────────────

@router.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎁 <b>Добро пожаловать в Даром!</b>\n\n"
        "Здесь люди бесплатно отдают ненужные вещи.\n\n"
        "• /newad — подать объявление\n"
        "• /myads — мои объявления\n"
        "• /cancel — отменить текущее действие",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("myads"))
async def my_ads(message: Message):
    ads = await get_user_ads(message.from_user.id)
    if not ads:
        await message.answer("У вас пока нет объявлений. /newad — подать новое.")
        return

    text_lines = ["<b>Ваши объявления:</b>\n"]
    for ad in ads:
        status = STATUS_LABELS.get(ad["status"], ad["status"])
        text_lines.append(f"• <b>{ad['title']}</b> — {status}")

    await message.answer("\n".join(text_lines), parse_mode="HTML")

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
        reply_markup=ReplyKeyboardRemove(),
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

    log.info("Бот запущен.")
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
