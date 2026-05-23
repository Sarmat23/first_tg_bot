import asyncio
import logging
import os
import traceback

import aiofiles
import pandas as pd

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    FSInputFile
)

from PyPDF2 import PdfReader
from docx import Document

from config import (
    BOT_TOKEN,
    MAX_FILE_SIZE_MB,
    TEMP_DIR
)

from services.openai_service import ask_gpt


# ===================================
# ЛОГИ
# ===================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ===================================
# СОЗДАНИЕ ПАПОК
# ===================================

os.makedirs(TEMP_DIR, exist_ok=True)


# ===================================
# BOT
# ===================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# память диалогов
dialog_memory = {}


# ===================================
# СТАРТ
# ===================================

@dp.message(CommandStart())
async def start(message: Message):

    text = (
        "🤖 AI бот запущен\n\n"
        "Поддерживается:\n"
        "- текст\n"
        "- фото\n"
        "- pdf\n"
        "- docx\n"
        "- txt\n"
        "- csv\n"
        "- xlsx\n"
        "- voice\n"
        "- audio\n"
        "- видео\n"
    )

    await message.answer(text)


# ===================================
# ОБРАБОТКА ТЕКСТА
# ===================================

@dp.message(F.text)
async def handle_text(message: Message):

    try:

        await bot.send_chat_action(
            message.chat.id,
            "typing"
        )

        user_id = message.from_user.id

        history = dialog_memory.get(user_id, [])

        history.append(
            {
                "role": "user",
                "content": message.text
            }
        )

        prompt = "\n".join(
            [f"{x['role']}: {x['content']}" for x in history]
        )

        answer = await ask_gpt(prompt)

        history.append(
            {
                "role": "assistant",
                "content": answer
            }
        )

        dialog_memory[user_id] = history[-10:]

        await message.answer(answer)

    except Exception as e:

        logger.error(traceback.format_exc())

        await message.answer(
            f"❌ Ошибка:\n{str(e)}"
        )


# ===================================
# ФОТО
# ===================================

@dp.message(F.photo)
async def handle_photo(message: Message):

    try:

        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)

        file_path = file.file_path

        save_path = os.path.join(
            TEMP_DIR,
            f"{photo.file_id}.jpg"
        )

        await bot.download_file(
            file_path,
            save_path
        )

        answer = await ask_gpt(
            "Пользователь отправил изображение. "
            "Сообщи, что обработка изображений "
            "может быть добавлена через Vision API."
        )

        await message.answer(answer)

    except Exception as e:

        logger.error(traceback.format_exc())

        await message.answer(str(e))


# ===================================
# DOCUMENTS
# ===================================

@dp.message(F.document)
async def handle_document(message: Message):

    try:

        document = message.document

        size_mb = document.file_size / 1024 / 1024

        if size_mb > MAX_FILE_SIZE_MB:

            await message.answer(
                "❌ Файл слишком большой"
            )
            return

        file = await bot.get_file(document.file_id)

        ext = document.file_name.split(".")[-1].lower()

        save_path = os.path.join(
            TEMP_DIR,
            document.file_name
        )

        await bot.download_file(
            file.file_path,
            save_path
        )

        extracted_text = ""

        # ==========================
        # TXT
        # ==========================

        if ext == "txt":

            async with aiofiles.open(
                save_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                extracted_text = await f.read()

        # ==========================
        # PDF
        # ==========================

        elif ext == "pdf":

            reader = PdfReader(save_path)

            for page in reader.pages:
                extracted_text += page.extract_text()

        # ==========================
        # DOCX
        # ==========================

        elif ext == "docx":

            doc = Document(save_path)

            extracted_text = "\n".join(
                [p.text for p in doc.paragraphs]
            )

        # ==========================
        # CSV
        # ==========================

        elif ext == "csv":

            df = pd.read_csv(save_path)

            extracted_text = df.head(50).to_string()

        # ==========================
        # XLSX
        # ==========================

        elif ext == "xlsx":

            df = pd.read_excel(save_path)

            extracted_text = df.head(50).to_string()

        # ==========================
        # JSON
        # ==========================

        elif ext == "json":

            async with aiofiles.open(
                save_path,
                "r",
                encoding="utf-8"
            ) as f:

                extracted_text = await f.read()

        else:

            await message.answer(
                "⚠️ Формат пока не поддерживается"
            )
            return

        extracted_text = extracted_text[:15000]

        prompt = (
            "Проанализируй файл:\n\n"
            f"{extracted_text}"
        )

        answer = await ask_gpt(prompt)

        await message.answer(answer)

    except Exception as e:

        logger.error(traceback.format_exc())

        await message.answer(
            f"❌ Ошибка файла:\n{str(e)}"
        )


# ===================================
# VOICE
# ===================================

@dp.message(F.voice)
async def handle_voice(message: Message):

    await message.answer(
        "🎤 Voice обработка может быть "
        "добавлена через Whisper API"
    )


# ===================================
# AUDIO
# ===================================

@dp.message(F.audio)
async def handle_audio(message: Message):

    await message.answer(
        "🎵 Audio обработка может быть "
        "добавлена позже"
    )


# ===================================
# VIDEO
# ===================================

@dp.message(F.video)
async def handle_video(message: Message):

    await message.answer(
        "🎬 Анализ видео можно подключить "
        "через Vision API"
    )


# ===================================
# НЕИЗВЕСТНЫЙ ТИП
# ===================================

@dp.message()
async def unknown(message: Message):

    await message.answer(
        "⚠️ Неизвестный тип сообщения"
    )


# ===================================
# ЗАПУСК
# ===================================

async def main():

    logger.info("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())
