import os
import telebot
from google import genai

# Автоматически берем токен Telegram из переменных окружения Bothost
# Мы проверяем сразу два варианта названия, чтобы наверняка
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Проверка, что всё успешно прочиталось
if not TELEGRAM_TOKEN:
    raise ValueError("Ошибка: Токен Telegram не найден в переменных окружения!")
if not GEMINI_API_KEY:
    raise ValueError("Ошибка: Ключ GEMINI_API_KEY не найден в переменных окружения!")

# Инициализируем бота и ИИ-клиент
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# Обработка команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "Привет! Я твой персональный ассистент Gemini. "
        "Напиши мне любой вопрос, и я постараюсь развернуто на него ответить!"
    )

# Обработка обычных текстовых сообщений
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    
    # Отправляем статус "печатает...", пока ждем ответ от Google
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Запрос к актуальной и быстрой модели Gemini 2.5 Flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при ответе Gemini: {e}")

# Запуск бесконечного цикла работы бота
if __name__ == '__main__':
    print("Бот успешно запущен и слушает команды...")
    bot.infinity_polling()
