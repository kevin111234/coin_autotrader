# test_connection.py 
from settings import get_api_config 
import requests 
def test_binance_connection(): 
    config = get_api_config() 
    headers = { "X-MBX-APIKEY": config["API_KEY"] } 
    url = f"{config['BASE_URL']}/api/v3/time" 
    try: 
        r = requests.get(url, headers=headers, timeout=5) 
        r.raise_for_status() 
        print(f"[✅] Binance {config['BASE_URL']} 연결 성공!") 
        print("서버 시간:", r.json()) 
    except requests.exceptions.RequestException as e: 
        print(f"[❌] 연결 실패: {e}") 

def test_slack_key(): 
    config = get_api_config() 
    if not config["SLACK_API"]: 
        print("[❌] Slack API 키 없음") 
    else: 
        print("[✅] Slack API 키 로드 성공") 

if __name__ == "__main__": 
    test_binance_connection() 
    test_slack_key()