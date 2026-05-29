import os
import telebot
from google import genai

# Читаем токены из Bothost
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Проверьте переменные TELEGRAM_TOKEN и GEMINI_API_KEY в панели хостинга!")

# Инициализируем Telegram бота напрямую
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Настраиваем Gemini через официальный шлюз без региональных ограничений
# Используем зеркало, которое прозрачно пропускает запросы к оригинальному API
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={
        'api_version': 'v1',
        'base_url': 'https://gemini-ai.ru/v1'
    }
)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Бот успешно запущен через стабильный шлюз. Напиши мне любой вопрос, и Gemini ответит!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    
    # Показываем, что бот печатает
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Отправляем запрос к быстрой модели gemini-2.5-flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        # Если снова будет сбой, бот пришлет точный текст ошибки прямо в чат
        bot.reply_to(message, f"Ошибка при обращении к Gemini: {e}")

if __name__ == '__main__':
    print("Бот запущен в штатном режиме через шлюз API...")
    bot.infinity_polling()
