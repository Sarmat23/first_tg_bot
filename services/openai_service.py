from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


async def ask_gpt(user_text: str):

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты полезный Telegram AI ассистент. "
                    "Отвечай понятно и структурировано."
                )
            },
            {
                "role": "user",
                "content": user_text
            }
        ],
        temperature=0.7,
        max_tokens=2000
    )

    return response.choices[0].message.content
