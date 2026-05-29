import os
import telebot
from google import genai

# Читаем токены из панели Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Проверьте переменные TELEGRAM_TOKEN и GEMINI_API_KEY в панели хостинга!")

# Инициализируем Telegram бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Настраиваем Gemini через специальный рабочий прокси-шлюз
# Он принудительно пустит запросы в обход любых региональных ограничений
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={
        'api_version': 'v1beta',
        'base_url': 'https://proxy.cors.sh/https://generativelanguage.googleapis.com'
    }
)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Бот успешно запущен через гарантированный обход ограничений. Задайте мне любой вопрос!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Запрос к Gemini 2.5 Flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Ошибка Gemini: {e}")

if __name__ == '__main__':
    print("Бот запущен с обходом региональных ограничений...")
    bot.infinity_polling()
