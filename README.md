# coin_autotrader
암호화폐 자동투자 프로그램

## **암호화폐 자동투자 프로그램 기획서**

### 1. **프로젝트 개요**

* **목표**:

  * Binance API에서 제공하는 실시간 OCHL 데이터 기반의 멀티코인 자동매매 프로그램 제작.
  * 기술적 지표 중심의 보편적 매매 전략을 적용하여 높은 수익률과 안정적인 포지션 관리를 동시에 달성.
* **특징**:

  * **멀티코인 지원** (초기 설계 단계부터 확장 가능성 내장)
  * **테스트 환경 지원** (Binance Testnet과 Mainnet 모두 사용 가능)
  * **DB 선택적 사용** (MySQL 또는 Redis 중 하나만 채택, 필요 시 DB 없이도 실행 가능)
  * **Slack 실시간 보고** (거래 상황, 잔고, 수익률 보고)

---

### 2. **사용 기술**

* **언어**: Python 3.x
* **API**:

  * Binance API & WebSocket (실시간 가격, 주문)
  * Binance Testnet 지원 (개발/테스트용 환경 전환 가능)
  * Slack API (실시간 알림)
* **데이터 저장(옵션)**:

  * MySQL (대규모 데이터 기록/분석)
  * Redis (저지연·경량 데이터 캐싱)
* **기타 라이브러리**:

  * Pandas, Numpy (데이터 처리)
  * TA-Lib / pandas\_ta (기술적 지표)
  * Requests / aiohttp (비동기 API 호출)

---

### 3. **매매 전략 (기본 구조)**

* **기술적 지표 후보**:

  * EMA, MACD, RSI, ATR, Bollinger Bands, OBV
* **기본 로직 예시**:

  * 매수: EMA20 > EMA60, MACD 상향 돌파, RSI > 50
  * 매도: EMA20 < EMA60 또는 MACD 하향 돌파
  * TP/SL 자동 설정 (진입 시 동시에 지정)
* **멀티코인 고려 사항**:

  * 각 코인별 독립 전략/포지션 관리 가능
  * 공용 전략 파라미터 or 코인별 맞춤 전략 선택 가능

---

### 4. **아키텍처 설계**
```
COIN_AUTOTRADER/
│
├── config/                  # 환경설정, API키, 심볼/전략 설정
│   ├── settings.py           # API 키, 환경(testnet/mainnet) 전환, Slack 키 로드
│   ├── symbols.json          # 타겟 코인/전략/파라미터 정의
│   └── test_connection.py    # Binance/Slack 연결 테스트 스크립트
│
├── src/                      # 핵심 코드
│   ├── exchange/             # Binance API 연동 레이어
│   │   ├── __init__.py        # 외부 노출용 통합 import
│   │   ├── core.py            # 공용 서명/요청/시간 동기화
│   │   ├── market.py          # 시세/캔들/심볼 정보
│   │   ├── account.py         # 계정/잔고/주문조회
│   │   ├── orders.py          # 주문/취소 실행
│   │   └── ws.py              # (예정) WebSocket 실시간 데이터
│   │
│   ├── indicators/           # 기술적 지표 모듈
│   │   ├── __init__.py
│   │   ├── ta.py              # SMA, EMA, RSI, MACD, BBands, ATR, VWAP 등
│   │   └── utils.py           # OHLCV 데이터 검증, 창 계산
│   │
│   ├── strategy/              # 전략 모듈
│   │   ├── base.py            # 모든 전략의 추상 인터페이스
│   │   ├── registry.py        # @register 데코레이터, 전략 레지스트리
│   │   ├── ma_rsi.py          # EMA+RSI 전략
│   │   ├── bbands_breakout.py # 볼린저 밴드 돌파 전략
│   │   └── ...                # 추가 전략
│   │
│   ├── utils/                 # 공용 유틸 함수/로그
│   │   └── __init__.py
│   │
│   ├── strategy_manager.py    # 설정파일 기반 멀티코인/멀티전략 로딩 & 실행
│   └── main.py                # 실행 진입점 (루프: 데이터→전략→시그널 출력)
│
├── experiments/               # 실험별 코드, 데이터, 분석노트
│   ├── 2025-08-10-testnet-ma-rsi/
│   │   ├── notebook.ipynb
│   │   ├── results.csv
│   │   ├── config.json
│   │   └── README.md
│   └── ...
│
├── docs/                      # 문서/GitHub Pages
│   ├── index.md
│   ├── strategy-overview.md
│   └── architecture.md
│
├── .env                        # 환경 변수 파일(API 키 등, gitignore)
├── requirements.txt
├── LICENSE
└── README.md
```

* **멀티코인 설계**:

  * `symbols.json`에 코인 목록 & 전략 파라미터 정의
  * 메인 루프에서 각 코인별로 비동기 처리
  * 포지션, 주문, TP/SL 관리도 코인별 객체 단위로 분리

---

### 5. **프로그램 동작 흐름**

1. **초기화**

   * 환경 설정 (Mainnet / Testnet 선택)
   * 타겟 코인 목록 로드
   * 전략 파라미터 로드
   * Slack 시작 알림
2. **메인 루프 (1초 단위)**

   * WebSocket 실시간 가격 수집
   * 각 코인별 전략 실행 → 매매 판단
   * 주문 실행 → TP/SL 설정
3. **정기 보고 (30분 단위)**

   * 잔고, 포지션, 수익률 Slack 전송
4. **종료**

   * 최종 보고 및 로그 저장

---

### 6. **개발 단계 로드맵**

1. **기본 골격 제작**

   * Binance WebSocket → 멀티코인 가격 수집
   * Slack 알림 연결
   * Mainnet/Testnet 스위치 기능
2. **전략 & 주문 실행**

   * 기술적 지표 계산
   * TP/SL 자동 주문
3. **포지션 관리**

   * 코인별 포지션 독립 관리
   * 주문 실패 시 재시도
4. **DB 연동 (선택)**

   * MySQL 또는 Redis 한쪽만 채택
5. **안정화 & 확장**

   * 전략 모듈화
   * 멀티코인 병렬 처리 최적화

---

### 7. **향후 확장**

* 강화학습 전략 모듈 추가
* 전략 자동 최적화
* 다중 거래소 지원 (Bybit, KuCoin 등)
