# test_connection.py 
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from config.settings import get_api_config 
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