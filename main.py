import telebot
import google.generativeai as genai

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = 'ВСТАВЬТЕ_СЮДА_ТОКЕН_ОТ_BOTFATHER'
GEMINI_API_KEY = 'ВСТАВЬТЕ_СЮДА_КЛЮЧ_ОТ_GEMINI'

# Инициализация Telegram бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
# Используем актуальную и быструю модель flash или pro
model = genai.GenerativeModel('gemini-1.5-flash') 

# Обработка команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я бот, подключенный к Gemini AI. Задай мне любой вопрос!")

# Обработка всех текстовых сообщений
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    user_text = message.text

    # Отправляем в чат статус, что бот "печатает", пока ИИ думает
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        # Отправляем запрос в нейросеть
        response = model.generate_content(user_text)
        # Отвечаем пользователю текстом из нейросети
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при обращении к Gemini: {e}")

# Запуск бота
if __name__ == '__main__':
    print("Бот запущен...")
    bot.infinity_polling()
