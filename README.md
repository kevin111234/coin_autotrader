# coin-autotrader

Binance API + Slack 알림 기반 **멀티코인 자동매매 프레임워크**.
전략/파라미터/리스크/운영 임계치를 **YAML로 단일 진실(SoT)** 로 관리하며, Testnet/Mainnet 전환, 시간 동기화, 주문 가드 등을 갖춘다.

> 핵심 철학: **전략 실험이 빠르고 재현 가능해야 한다.**
> 코드 변경 없이 YAML만 바꿔 실험 · 배포 · 롤백이 가능하도록 설계.

---

## ✨ 주요 특징

* **멀티코인 × 멀티전략**: `config/base.yaml`의 심볼/전략 매핑만 바꿔서 운용
* **전략 플러그인 시스템**: `@register("name")`로 전략 등록 → YAML의 `strategy: name`과 자동 연결
* **지표 모듈화**: 순수 `pandas` 기반 TA 모듈(SMA/EMA/RSI/MACD/BB/ATR/VWAP)
* **Testnet/Mainnet 스위칭**: `.env`/`settings.py`로 안전 전환
* **시간 동기화 & 드리프트 방어**: RTT 보정, `recvWindow` 자동 확장, `-1021` 재동기화 재시도
* **주문 레이어 분리**: 시세/계정/주문/WS를 모듈화(`src/exchange/*`)
* **확장성**: Slack, 포지션/리스크, DB(선택: MySQL/Redis) 추가 용이

---

## 🗂 폴더 구조

```
config/
  base.yaml               # 전략/심볼/운영 임계치 (단일 진실)
  config_loader.py        # YAML 로딩 + 검증 + (선택)핫리로드

src/
  main.py                 # 실행 진입점 (데이터→전략→시그널)
  strategy_manager.py     # YAML→전략 인스턴스 생성/실행
  strategy/
    base.py               # Strategy 인터페이스
    registry.py           # @register 레지스트리
    ma_rsi.py             # EMA+RSI 전략
    bbands_breakout.py    # 볼린저 돌파 전략
    # ... 새 전략은 여기에 파일 추가 + @register
  indicators/
    ta.py                 # SMA/EMA/RSI/MACD/BB/ATR/VWAP
    utils.py              # 데이터 검증/타입 변환
  exchange/
    __init__.py           # 외부 노출 모듈 집계
    core.py               # 서명/요청/시간동기화/재시도
    market.py             # 가격/캔들/심볼 정보
    account.py            # 계정/잔고/주문조회
    orders.py             # test order/실주문/취소
    # ws.py               # (선택) 실시간 WS

docs/
  strategy-params.md      # 전략/파라미터 사양서 (참고 문서)

README.md
```

---

## 📚 Documentation

- [Architecture Overview](docs/architecture.md)
- [Strategy Parameters](docs/strategy-params.md)  ← 전략별 파라미터 설명 문서
- [Experiment Logs](experiments/)

---

## ⚙️ 설치 & 환경 변수

```bash
pip install -r requirements.txt
# 필요한 핵심: requests, pandas, python-dotenv, ruamel.yaml, pydantic
```

`.env` (레포 루트에 두고 `.gitignore` 필수)

```
BINANCE_ENV=testnet              # or mainnet
BINANCE_MAINNET_API_KEY=xxx
BINANCE_MAINNET_API_SECRET=yyy
BINANCE_TESTNET_API_KEY=aaa
BINANCE_TESTNET_API_SECRET=bbb
SLACK_API_KEY=xoxb-...           # (선택) 알림
# SLACK_CHANNEL=#trading-log     # (선택) 기본 #general
```

---

## 🧾 설정: `config/base.yaml`

전략/심볼을 이 파일에서 통제. (주석 버전 예시는 이미 제공했음)

```yaml
version: 1
project: coin-autotrader

trading:
  interval: "1m"
  symbols:
    - symbol: BTCUSDT
      strategy: ma_rsi
      params:
        short_window: 7
        long_window: 25
        rsi_period: 14
        rsi_buy: 35
        rsi_sell: 65
    - symbol: ETHUSDT
      strategy: bb_breakout
      params:
        period: 20
        k: 2.0

clock_guard:
  max_offset_ms: 1000

alerts:
  warn: { clock_offset_ms_gt: 250 }
  critical: { order_fail_rate_gt: 0.01 }
```

> 전략별 파라미터 의미/권장 범위는 `docs/strategy-params.md` 참고(전략 사양서).

---

## 🧠 전략 시스템

* **인터페이스**: `Strategy(name/min_history/compute_indicators/generate_signal)`
* **등록**: 전략 파일에서 `@register("ma_rsi")` 같은 식별자로 레지스트리에 등록
* **매핑**: `base.yaml`의 `strategy: ma_rsi`가 해당 클래스에 연결
* **호출 흐름**
  `main.py` → `StrategyRunner`
  → 심볼별 `get_ohlcv()` 호출 → `compute_indicators()` → `generate_signal()`
  → `"BUY"|"SELL"|None` 반환

새 전략 추가 절차:

1. `src/strategy/my_strat.py` 생성 + `@register("my_strat")`
2. `docs/strategy-params.md`에 파라미터 사양서 추가
3. `config/base.yaml`에 심볼 매핑 추가

---

## 📈 지표(Indicators)

`src/indicators/ta.py`에 순수 pandas 기반 지표 구현:
SMA, EMA, RSI(Wilder), MACD, Bollinger Bands, ATR, VWAP (+ 합성 `add_indicators`).

원칙:

* 입력 df **복사 → 컬럼 추가 → 반환**
* NaN은 전략의 `min_history()`로 방어
* dtype 강제 변환으로 실수형 보장

---

## 🔌 Binance 연동 레이어

* `exchange/core.py`

  * `_TIME_OFFSET_MS` 보정 + `now_ms()`
  * 서명: `HMAC_SHA256(secret, urlencode(params))`
  * `request()`에서 HTTP 오류 파싱, `-1021` 감지 시 **time sync 후 1회 재시도**
  * `sync_time()`은 RTT 보정·median 사용, drift 크면 `recvWindow` 자동 확대(옵션)

* `exchange/market.py`

  * `get_ohlcv(symbol, interval, limit)` → DataFrame
  * `get_price(symbol)`, `get_exchange_info(symbol)`

* `exchange/account.py`

  * `get_account()`, `get_open_orders()`, `get_order(...)`

* `exchange/orders.py`

  * `place_test_order(...)` → **유효성만 검사(응답 `{}` 정상)**
  * `place_order(...)` → 실주문 (기본 `allow_mainnet=False` 가드)
  * `cancel_order(...)`, `cancel_open_orders(symbol)`

> 실주문 붙일 때는 **LOT\_SIZE/MIN\_NOTIONAL/PRICE\_FILTER** 보정 유틸을 추가해 수량/가격을 필터에 맞춰 정규화하는 것을 권장.

---

## ▶️ 실행

```bash
# 1) 환경 확인 (키/권한/시계)
python config/test_connection.py     # (원하면 유지) 서버시간/키 로드 확인
# 또는: from src.exchange import sync_time, ping; sync_time(); ping()

# 2) 메인 루프
python src/main.py
# 출력 예: 
# [BTCUSDT] WAIT @ 116489.99
# [ETHUSDT] SIGNAL=BUY @ 3450.12
```

> 현재 메인은 “데이터→전략→시그널”까지만.
> **주문/Slack 연결 지점**은 `main.py`에서 TODO로 표시된 곳에 결선.

---

## 🧪 주문 레이어 테스트

```python
from src.exchange import sync_time, get_account, get_price, place_test_order
sync_time()
print(get_account()["balances"][:2])        # testnet 기본 잔고
print(get_price("BTCUSDT"))
print(place_test_order("BTCUSDT", "BUY", "MARKET", quote_order_qty=10))  # {}면 OK
```

> **주의**: `/api/v3/order/test`는 항상 `{}`. 실체결이 아님.
> 실주문은 `place_order(..., allow_mainnet=False)`로 testnet에서만 시도 권장.

---

## 🛠 개발 워크플로우(권장)

1. **전략 아이디어/실험 설계**: `docs/` 또는 개인 노트
2. **전략 구현**: `src/strategy/*.py` + `@register`
3. **파라미터 정의**: `docs/strategy-params.md` 업데이트
4. **실험 구성**: `config/base.yaml` 수정(심볼/전략/파라미터)
5. **실행 & 로깅**: 콘솔/CSV/Slack(추가 예정)
6. **회고**: 결과 정리 → `experiments/날짜-설명/README.md`

> Git에서 실험 단위로 **branch/tag** 운용 추천.

---

## 🧯 트러블슈팅

* **`-1021 Timestamp ...`**
  `sync_time()` 호출. drift 크면 `auto_increase_recv_window=True` 옵션 사용.
  네트워크/OS 시계도 NTP로 맞출 것.

* **`-1013 / -1111` 수량/가격 에러**
  심볼의 `LOT_SIZE`, `PRICE_FILTER`, `MIN_NOTIONAL` 확인 → 주문 전 보정.

* **`test order: {}`만 나옴**
  정상. 유효성 검사를 통과했다는 의미. 실체결이 아님.

* **Mainnet 오발주 방지**
  `place_order(..., allow_mainnet=False)` 기본. Mainnet에서 True로 바꾸지 않으면 예외.

---

## 📚 참고 문서

* 전략/파라미터 사양서: `docs/strategy-params.md`
  (전략 목록, 파라미터 의미, 권장 범위, YAML 스니펫 모음)

---

## 🗺 로드맵(요약)

* 주문 수량/가격 **필터 보정 유틸**
* **Slack 알림**(시그널/주문/오류/상태 보고)
* **OCO 기반 TP/SL** 자동화
* 포트폴리오/리스크 엔진(일손실Cap, 심볼별 상한, 쿨다운)
* WebSocket 실시간 틱 → 인크리멘탈 업데이트
* 백테스트 파이프 및 자동 리포트

---

### 마지막 체크리스트

* `.env`에 키 추가 및 `BINANCE_ENV=testnet` 확인
* `config/base.yaml` 심볼/전략/파라미터 셋업
* `python src/main.py`로 시그널 출력 확인
* 주문을 붙일 땐 test order → 실주문 순으로 점진적 검증
