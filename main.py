import os
import telebot
from google import genai

# Читаем токены из переменных окружения сервера
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Проверяем, что переменные вообще были заданы на хостинге
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError(
        "Критические переменные окружения (TELEGRAM_TOKEN или GEMINI_API_KEY) не найдены! "
        "Проверьте настройки в панели хостинга Bothost."
    )

# Инициализация бота и ИИ-клиента
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой безопасный ИИ-ассистент. Задай мне любой вопрос!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Ошибка Gemini: {e}")

if __name__ == '__main__':
    print("Бот успешно запущен в безопасном режиме!")
    bot.infinity_polling()
