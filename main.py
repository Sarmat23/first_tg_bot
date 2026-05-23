import telebot
from google import genai



# Инициализация Telegram бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Инициализация нового клиента Gemini API
client = genai.Client(api_key=GEMINI_API_KEY)

# Обработка команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой ИИ-ассистент на базе актуальной модели Gemini. Задай мне любой вопрос!")

# Обработка текстовых сообщений
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    
    # Показываем пользователю статус "печатает..."
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Используем современную быструю модель gemini-2.5-flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при обращении к Gemini: {e}")

# Запуск
if __name__ == '__main__':
    print("Бот успешно запущен и готов к работе!")
    bot.infinity_polling()
