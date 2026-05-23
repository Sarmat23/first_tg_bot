import asyncio
import logging
import os
import traceback

import aiofiles
import pandas as pd

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from PyPDF2 import PdfReader
from docx import Document

from config import (
    BOT_TOKEN,
    MAX_FILE_SIZE_MB,
    TEMP_DIR
)

from services.gemini_service import ask_gemini


# ====================================
# ЛОГИ
# ====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ====================================
# ПАПКИ
# ====================================

os.makedirs(TEMP_DIR, exist_ok=True)


# ====================================
# BOT
# ====================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# память диалога
dialog_memory = {}


# ====================================
# START
# ====================================

@dp.message(CommandStart())
async def start(message: Message):

    text = (
        "🤖 Gemini AI Bot\n\n"
        "Поддерживается:\n"
        "• текст\n"
        "• txt\n"
        "• pdf\n"
        "• docx\n"
        "• csv\n"
        "• xlsx\n"
        "• json\n"
        "• фото\n\n"
        "Просто отправь сообщение."
    )

    await message.answer(text)


# ====================================
# TEXT
# ====================================

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
            f"Пользователь: {message.text}"
        )

        prompt = "\n".join(history[-10:])

        answer = await ask_gemini(prompt)

        history.append(
            f"AI: {answer}"
        )

        dialog_memory[user_id] = history

        # защита Telegram лимита
        if len(answer) > 4000:

            for i in range(0, len(answer), 4000):

                await message.answer(
                    answer[i:i + 4000]
                )

        else:

            await message.answer(answer)

    except Exception as e:

        logger.error(traceback.format_exc())

        error_text = str(e)

        if "429" in error_text:

            await message.answer(
                "⏳ Превышен лимит запросов."
            )

        elif "API_KEY" in error_text:

            await message.answer(
                "❌ Ошибка API ключа Gemini."
            )

        else:

            await message.answer(
                f"❌ Ошибка:\n{error_text}"
            )


# ====================================
# PHOTO
# ====================================

@dp.message(F.photo)
async def handle_photo(message: Message):

    try:

        await message.answer(
            "🖼 Анализ фото можно "
            "добавить через Gemini Vision API."
        )

    except Exception as e:

        logger.error(traceback.format_exc())

        await message.answer(str(e))


# ====================================
# DOCUMENTS
# ====================================

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

        file = await bot.get_file(
            document.file_id
        )

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

        # =========================
        # TXT
        # =========================

        if ext == "txt":

            async with aiofiles.open(
                save_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                extracted_text = await f.read()

        # =========================
        # PDF
        # =========================

        elif ext == "pdf":

            reader = PdfReader(save_path)

            for page in reader.pages:

                text = page.extract_text()

                if text:
                    extracted_text += text

        # =========================
        # DOCX
        # =========================

        elif ext == "docx":

            doc = Document(save_path)

            extracted_text = "\n".join(
                [p.text for p in doc.paragraphs]
            )

        # =========================
        # CSV
        # =========================

        elif ext == "csv":

            df = pd.read_csv(save_path)

            extracted_text = df.head(50).to_string()

        # =========================
        # XLSX
        # =========================

        elif ext == "xlsx":

            df = pd.read_excel(save_path)

            extracted_text = df.head(50).to_string()

        # =========================
        # JSON
        # =========================

        elif ext == "json":

            async with aiofiles.open(
                save_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                extracted_text = await f.read()

        else:

            await message.answer(
                "⚠️ Формат пока не поддерживается."
            )

            return

        extracted_text = extracted_text[:15000]

        await bot.send_chat_action(
            message.chat.id,
            "typing"
        )

        prompt = (
            "Проанализируй файл "
            "и кратко объясни содержимое:\n\n"
            f"{extracted_text}"
        )

        answer = await ask_gemini(prompt)

        if len(answer) > 4000:

            for i in range(0, len(answer), 4000):

                await message.answer(
                    answer[i:i + 4000]
                )

        else:

            await message.answer(answer)

    except Exception as e:

        logger.error(traceback.format_exc())

        await message.answer(
            f"❌ Ошибка файла:\n{str(e)}"
        )


# ====================================
# VOICE
# ====================================

@dp.message(F.voice)
async def handle_voice(message: Message):

    await message.answer(
        "🎤 Voice поддержка "
        "будет добавлена позже."
    )


# ====================================
# AUDIO
# ====================================

@dp.message(F.audio)
async def handle_audio(message: Message):

    await message.answer(
        "🎵 Audio поддержка "
        "будет добавлена позже."
    )


# ====================================
# VIDEO
# ====================================

@dp.message(F.video)
async def handle_video(message: Message):

    await message.answer(
        "🎬 Анализ видео "
        "будет добавлен позже."
    )


# ====================================
# UNKNOWN
# ====================================

@dp.message()
async def unknown(message: Message):

    await message.answer(
        "⚠️ Неизвестный тип сообщения."
    )


# ====================================
# MAIN
# ====================================

async def main():

    logger.info("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())
