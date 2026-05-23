import google.generativeai as genai
from config import GEMINI_API_KEY

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash-002",
    system_instruction=(
        "Ты современный Telegram AI ассистент.\n"
        "Отвечай дружелюбно, понятно и структурировано.\n"
        "Помогай с:\n"
        "- программированием\n"
        "- текстами\n"
        "- файлами\n"
        "- анализом документов\n"
        "- идеями\n"
        "- обучением\n"
        "- изображениями\n"
        "- таблицами\n"
        "- автоматизацией\n\n"
        "Если пользователь спрашивает что ты умеешь — "
        "перечисли возможности кратко и красиво."
    )
)

# Функция должна правильно использовать await
async def ask_gemini(prompt: str):
    # Используем асинхронный метод generate_content_async вместо синхронного
    response = await model.generate_content_async(prompt)
    return response.text
