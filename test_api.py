import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
print("Key loaded:", "Yes" if api_key else "No (Check .env)")

try:
    print("Connecting to Gemini...")
    client = genai.Client(api_key=api_key)
    
    # We will try 3.5-flash-lite just in case 3.8 is overloaded
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents="Say exactly: 'Hello Absa'"
    )
    print("\n--- SUCCESS ---")
    print(response.text)
except Exception as e:
    print("\n--- ERROR ---")
    print(e)
