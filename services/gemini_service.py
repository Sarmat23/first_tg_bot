import google.generativeai as genai

from config import GEMINI_API_KEY


genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash"
)


async def ask_gemini(prompt: str):

    response = model.generate_content(prompt)

    return response.text
