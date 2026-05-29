import os
import sys
import telebot
import requests
from google import genai

# --- НАСТРОЙКА ГЛОБАЛЬНОГО ПРОКСИ ---
# Используем бесплатный рабочий SOCKS5 прокси (Германия/Нидерланды), 
# чтобы завернуть туда запросы к Google, минуя блокировки Bothost.
PROXY_URL = "socks5://104.248.48.183:1080"  # Если этот устареет, заменим на другой

os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['HTTPS_PROXY'] = PROXY_URL
# -------------------------------------

# Читаем токены из Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Проверьте переменные TELEGRAM_TOKEN и GEMINI_API_KEY на хостинге!")

# Инициализируем Telegram бота (для него прокси обычно не нужен, Bothost дружит с TG)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Инициализируем Gemini (он автоматически подхватит HTTPS_PROXY из окружения)
client = genai.Client(api_key=GEMINI_API_KEY)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой ассистент Gemini. Теперь мы работаем через SOCKS5-туннель. Задай мне вопрос!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Прямой запрос к Google (но благодаря os.environ он пойдет через прокси)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при ответе Gemini: {e}")

if __name__ == '__main__':
    print("Бот успешно запущен через SOCKS5 туннель!")
    bot.infinity_polling()
