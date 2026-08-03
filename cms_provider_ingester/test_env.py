from dotenv import load_dotenv
import os
load_dotenv()
print("DB URL:", os.getenv("DATABASE_URL"))
print("CMS API Base:", os.getenv("CMS_API_BASE"))
print("Groq API Key present:", bool(os.getenv("GROQ_API_KEY")))