import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'super-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://user:pass@db:5432/minewithme')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Твои настройки майнинга (из .env)
    XMR_WALLET = os.getenv('XMR_WALLET', '47p33B681MuTNp6AfgieQsV5TUPGUfKEM1JC2PbCpvEm3mrxUBDpaDe8b3GkQCPXw3cgHHjKxBKLDZaxEptGW5no4rESTx2')
    DEV_FEE = float(os.getenv('DEV_FEE', '0.15'))
    POOL_URL = os.getenv('POOL_URL', 'gulf.moneroocean.stream:10004')
