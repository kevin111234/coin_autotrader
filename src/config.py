import os
from dotenv import load_dotenv

class Config():
    def __init__(self):
        print("환경변수 로드 중...")
        load_dotenv()
        self.binance_access_key = os.getenv("BINANCE_ACCESS_KEY")
        self.binance_secret_key = os.getenv("BINANCE_SECRET_KEY")
        self.slack_api_key = os.getenv("SLACK_API_KEY")
        self.slack_asset_channel_id = os.getenv("SLACK_ASSET_CHANNEL_ID")
        self.slack_trade_channel_id = os.getenv("SLACK_TRADE_CHANNEL_ID")
        self.slack_error_channel_id = os.getenv("SLACK_ERROR_CHANNEL_ID")

        self.seed_money = os.getenv("SEED_MONEY")
        self.coin_tickers = os.getenv("COIN_TICKERS")

        self.futures_use = os.getenv("FUTURES_USE").lower() == "true"
        self.futures_leverage = int(os.getenv("FUTURES_LEVERAGE"))
        self.futures_margin_type = os.getenv("FUTURES_MARGIN_TYPE")
        self.futures_coin_tickers = os.getenv("FUTURES_COIN_TICKERS")

        print("환경변수 로드 완료")
        
        print("환경변수 검증중...")
        self.verify()
        print("환경변수 검증 완료!")

    def verify(self):
        if not self.binance_access_key:
            raise ValueError("binance access key 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.binance_secret_key:
            raise ValueError("binance secret key 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.slack_api_key:
            raise ValueError("slack api key환경변수가 제대로 설정되지 않았습니다.")
        elif not self.slack_asset_channel_id:
            raise ValueError("slack asset channel id 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.slack_trade_channel_id:
            raise ValueError("slack trade channel id 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.slack_error_channel_id:
            raise ValueError("slack error channel id 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.seed_money:
            raise ValueError("seed money 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.coin_tickers:
            raise ValueError("coin tickers 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.futures_leverage:
            raise ValueError("futures_leverage 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.futures_margin_type:
            raise ValueError("futures_margin_type 환경변수가 제대로 설정되지 않았습니다.")
        elif not self.futures_coin_tickers:
            raise ValueError("futures_coin_tickers 환경변수가 제대로 설정되지 않았습니다.")
