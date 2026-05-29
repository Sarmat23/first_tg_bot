import os
import telebot
from google import genai

# Читаем токены из Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Проверьте переменные TELEGRAM_TOKEN и OPENAI_API_KEY на хостинге!")

# Инициализируем Telegram бота напрямую (без глобальных прокси)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Настраиваем прокси ИСКЛЮЧИТЕЛЬНО для Gemini
# Используем стабильный альтернативный адрес, который подменяет IP для Google
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={
        'api_version': 'v1beta',
        'base_url': 'https://gemini.api.proxyapi.ru/v1'
    }
)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Связь с Telegram восстановлена, а Gemini настроен через изолированный шлюз. Задай мне вопрос!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Запрос идет к зеркалу Gemini, не затрагивая трафик Telegram бота
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при ответе Gemini: {e}")

if __name__ == '__main__':
    print("Бот успешно запущен. Telegram работает напрямую, Gemini — через шлюз.")
    bot.infinity_polling()
