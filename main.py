import os
import telebot
from google import genai

# Подтягиваем скрытые переменные окружения из панели Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Проверьте переменные TELEGRAM_TOKEN и GEMINI_API_KEY на хостинге!")

# Инициализируем Telegram бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Инициализируем Gemini с прокси-сервером для обхода блокировки по IP
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={
        'api_version': 'v1beta', 
        'base_url': 'https://gateway.ai.cloudflare.com/v1/public/gemini-proxy'
    }
)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой ассистент Gemini, успешно работающий в обход ограничений. Задай мне вопрос!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Отправляем запрос через прокси-шлюз
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при ответе Gemini: {e}")

if __name__ == '__main__':
    print("Бот успешно запущен через прокси-сервер!")
    bot.infinity_polling()
