# config/settings.py

import os
from dotenv import load_dotenv

load_dotenv()

# 테스트서버에 연결할 것인지, 메인 서버에 연결할 것인지를 정해줌
BINANCE_ENV = "testnet"  # "mainnet" or "testnet"

# API KEY들: 보안조치를 신경써서 .env에 저장.
BINANCE_API_KEYS = {
    "mainnet": {
        "API_KEY": os.getenv("BINANCE_MAINNET_API_KEY"),
        "API_SECRET": os.getenv("BINANCE_MAINNET_API_SECRET")
    },
    "testnet": {
        "API_KEY": os.getenv("BINANCE_TESTNET_API_KEY"),
        "API_SECRET": os.getenv("BINANCE_TESTNET_API_SECRET")
    }
}

SLACK_API_KEY = os.getent("SLACK_API_KEY")

# Binance 메인넷과 테스트넷 주소
BINANCE_BASE_URL = {
    "mainnet": "https://api.binance.com",
    "testnet": "https://testnet.binance.vision"
}

# 설정된 서버에 따라 api key를 반환하는 코드
def get_api_config():
    env = BINANCE_ENV
    return {
        "API_KEY": BINANCE_API_KEYS[env]["API_KEY"],
        "API_SECRET": BINANCE_API_KEYS[env]["API_SECRET"],
        "BASE_URL": BINANCE_BASE_URL[env],
        "SLACK_API": SLACK_API_KEY
    }
