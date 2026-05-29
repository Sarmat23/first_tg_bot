import os
import telebot
from google import genai

# Извлекаем токены из настроек Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Критические переменные окружения не найдены в панели Bothost!")

# Инициализируем бота напрямую
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Чистая инициализация Gemini API без каких-либо шлюзов и прокси
client = genai.Client(api_key=GEMINI_API_KEY)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "Привет! Мы успешно переехали на сервер в Нидерландах. "
        "Теперь я работаю напрямую без ограничений и готов отвечать на любые вопросы!"
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Прямой и быстрый запрос к оригинальному Google API
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Ошибка Gemini: {e}")

if __name__ == '__main__':
    print("Бот запущен напрямую из локации Нидерланды!")
    bot.infinity_polling()
