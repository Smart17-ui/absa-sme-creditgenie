"""Settings read from .env, file locations and app-wide constants."""
import base64
import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH, override=True)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def api_key():
    """Re-reads .env each time, so an edited key is picked up without restarting the app."""
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY")


def data_key():
    """The key that encrypts everything in data/. Created on first use and saved to .env (git-ignored).
    Losing it makes stored applications unreadable, so back up .env with the data."""
    key = os.getenv("CREDITGENIE_DATA_KEY")
    if not key:
        key = Fernet.generate_key().decode()
        with open(ENV_PATH, "a", encoding="utf-8") as env:
            env.write("\n# Encrypts stored applications in data/ - keep it safe, never commit it\n"
                      f"CREDITGENIE_DATA_KEY={key}\n")
        os.environ["CREDITGENIE_DATA_KEY"] = key
    return key

LOGO_PATH = os.path.join(BASE_DIR, "assets", "absa-logo.png")
with open(LOGO_PATH, "rb") as logo_file:
    LOGO_URI = "data:image/png;base64," + base64.b64encode(logo_file.read()).decode()

# Applications and customer documents (git-ignored, encrypted at rest)
DATA_DIR = os.path.join(BASE_DIR, "data")
RETENTION_DAYS = 365  # decided applications are erased this long after their last update

PERSONAS = ("Strict Risk Manager", "Financial Growth Coach", "Synthetic Data Engineer")
ROLES = ("SME Portal", "Absa Analyst")
LOAN_PURPOSES = ("Stock purchase", "Equipment", "Business expansion", "Working capital", "Other")
MAX_IMAGES_PER_RUN = 5
DECIDED = ("Approved", "Declined")
