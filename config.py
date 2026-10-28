import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)

def normalize_database_url(raw_url):
    """Postgres do Supabase quando DATABASE_URL existe; SQLite local quando não existe."""
    raw = (raw_url or '').strip()
    if not raw:
        return f'sqlite:///{os.path.join(INSTANCE_DIR, "financial_bot.db")}'
    if raw.startswith('postgres://'):
        raw = 'postgresql+psycopg://' + raw[len('postgres://'):]
    elif raw.startswith('postgresql://') and '+' not in raw.split('://', 1)[0]:
        raw = 'postgresql+psycopg://' + raw[len('postgresql://'):]
    if 'supabase.co' in raw and 'sslmode=' not in raw:
        raw += ('&' if '?' in raw else '?') + 'sslmode=require'
    return raw


class Config:
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.getenv('DATABASE_URL'))
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT') or 587)
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', '')
    SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() not in {'0', 'false', 'no'}
    if SQLALCHEMY_DATABASE_URI.startswith('postgresql'):
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}

