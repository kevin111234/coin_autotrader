# 전략/파라미터 사양서

> 목적: 전략 정의와 파라미터 의미/권장 범위를 \*\*설정(base.yaml)\*\*과 분리해 관리.
> 이 문서를 참고해 어떤 심볼이든 전략을 바꾸되, `base.yaml` 주석을 수정할 필요가 없게 한다.

## 공통 규칙

* **전략 식별자**: `@register("<name>")`의 `<name>` 값과 동일 (YAML의 `strategy` 값)
* **필수 필드**: 각 전략별 `params`에 명시
* **min\_history**: 전략이 신호를 내기 위해 필요한 최소 캔들 수 (NaN 방지)
* **권장 탐색 범위**: 실험 시 Grid/Random/BO에 바로 쓰도록 “typical / wide” 범위 제시
* **신호 정책**: BUY/SELL 기준 요약 + 간단한 변형 옵션

---

## 1 `ma_rsi` (EMA Cross + RSI 필터)

**식별자:** `ma_rsi`
**의존 지표:** EMA, RSI
**min\_history:** `max(short_window, long_window, rsi_period) + 2`

### 파라미터

| 파라미터           | 타입    | 의미               | typical | wide   |
| -------------- | ----- | ---------------- | ------- | ------ |
| `short_window` | int   | 단기 EMA 기간        | 7\~12   | 5\~20  |
| `long_window`  | int   | 장기 EMA 기간        | 20\~40  | 15\~80 |
| `rsi_period`   | int   | RSI 기간           | 14      | 7\~21  |
| `rsi_buy`      | float | 매수 RSI 상한(이하 매수) | 30\~40  | 25\~50 |
| `rsi_sell`     | float | 매도 RSI 하한(이상 매도) | 60\~70  | 55\~75 |

### 신호 정책

* **BUY:** 직전봉 `ma_short ≤ ma_long` 이고 최신봉 `ma_short > ma_long` **AND** `RSI < rsi_buy`
* **SELL:** 직전봉 `ma_short ≥ ma_long` 이고 최신봉 `ma_short < ma_long` **AND** `RSI > rsi_sell`

### 권장 세팅 팁

* 장단기 간격이 너무 좁으면 노이즈 ↑ (false signal), 너무 넓으면 반응성 ↓
* RSI 임계는 과도하게 낮추면 진입 빈도 ↓, 너무 높이면 과열 구간에서 추격매수 ↑
* 변형: RSI 필터를 “크로스 발생 후 n봉 내 RSI 조건 만족”으로 완화 가능

### YAML 스니펫

```yaml
- symbol: BTCUSDT
  strategy: ma_rsi
  params:
    short_window: 9
    long_window: 26
    rsi_period: 14
    rsi_buy: 35
    rsi_sell: 65
```

---

## 2 `bb_breakout` (Bollinger Band Breakout)

**식별자:** `bb_breakout`
**의존 지표:** Bollinger Bands (MID/UP/DN)
**min\_history:** `period + 2`

### 파라미터

| 파라미터     | 타입    | 의미             | typical | wide     |
| -------- | ----- | -------------- | ------- | -------- |
| `period` | int   | 중심선(단순이동평균) 기간 | 20      | 10\~40   |
| `k`      | float | 표준편차 계수        | 2.0     | 1.5\~3.0 |

### 신호 정책

* **BUY:** 직전봉 `close ≤ bb_up` & 최신봉 `close > bb_up` (상단선 돌파)
* **SELL:** 직전봉 `close ≥ bb_dn` & 최신봉 `close < bb_dn` (하단선 이탈)

### 권장 세팅 팁

* `k`가 낮으면 돌파 빈도 ↑(노이즈↑), 높으면 희소(신뢰도↑, 기회↓)
* 추세장에서는 성능↑, 박스장에서는 whipsaw↑ → 추세 필터(ADX, EMA slope)와 결합 추천

### YAML 스니펫

```yaml
- symbol: ETHUSDT
  strategy: bb_breakout
  params:
    period: 20
    k: 2.0
```

---

## 3 `macd_trend` (MACD Trend-Following) — *제안 전략*

**식별자:** `macd_trend`
**의존 지표:** MACD(12,26,9 기본), 선택형 ADX 필터
**min\_history:** `max(slow, signal) + 2` (기본 28\~30)

### 파라미터

| 파라미터      | 타입  | 의미                | typical | wide           |
| --------- | --- | ----------------- | ------- | -------------- |
| `fast`    | int | MACD 빠른 EMA       | 12      | 8\~20          |
| `slow`    | int | MACD 느린 EMA       | 26      | 20\~40         |
| `signal`  | int | 시그널 EMA           | 9       | 5\~15          |
| `adx_min` | int | (옵션) 추세 필터 최소 ADX | 20\~25  | 15\~35         |
| `tf_adx`  | str | (옵션) ADX 계산 타임프레임 | `"1h"`  | `"15m" ~ "4h"` |

### 신호 정책

* **BUY:** MACD 히스토그램 0 상향 돌파 **AND** (옵션) ADX ≥ `adx_min`
* **SELL:** MACD 히스토그램 0 하향 돌파 **OR** (옵션) ADX < `adx_min`

### YAML 스니펫

```yaml
- symbol: BTCUSDT
  strategy: macd_trend
  params:
    fast: 12
    slow: 26
    signal: 9
    adx_min: 25      # 옵션
    tf_adx: "1h"     # 옵션
```

---

## 4 `rsi_revert` (RSI Mean-Reversion) — *제안 전략*

**식별자:** `rsi_revert`
**의존 지표:** RSI
**min\_history:** `rsi_period + 2`

### 파라미터

| 파라미터         | 타입    | 의미                   | typical | wide   |
| ------------ | ----- | -------------------- | ------- | ------ |
| `rsi_period` | int   | RSI 기간               | 14      | 7\~21  |
| `buy_th`     | float | 매수 트리거 (이하)          | 30\~35  | 20\~45 |
| `sell_th`    | float | 매도 트리거 (이상)          | 65\~70  | 55\~80 |
| `cooldown`   | int   | 같은 방향 재진입 최소 간격(봉 수) | 3\~5    | 1\~10  |

### 신호 정책

* **BUY:** `RSI < buy_th` **AND** (옵션) 마지막 BUY 후 `cooldown` 경과
* **SELL:** `RSI > sell_th` **AND** (옵션) 마지막 SELL 후 `cooldown` 경과

### YAML 스니펫

```yaml
- symbol: SOLUSDT
  strategy: rsi_revert
  params:
    rsi_period: 14
    buy_th: 32
    sell_th: 68
    cooldown: 5
```

---

## 5 공용 보조 옵션 (전략 공통에 붙이기 좋은 것)

> 구현 시 `Strategy.params`에 **옵션 키가 있으면 적용**하는 식으로 가볍게 확장.

| 옵션 키           | 타입    | 의미                             | 예시                           |
| -------------- | ----- | ------------------------------ | ---------------------------- |
| `min_volume`   | float | 최소 거래량 필터(최근 n봉 합/평균)          | `min_volume: 5000`           |
| `slope_filter` | dict  | MA/EMA 기울기 필터                  | `{ma: 50, min_slope_bps: 2}` |
| `confirm_n`    | int   | 신호 후 n봉 연속 확인 (false break 감소) | `confirm_n: 2`               |
| `cooldown`     | int   | 재진입/재청산 쿨다운(봉 수)               | `cooldown: 5`                |

---

## 6 실험용 파라미터 그리드(추천)

* **ma\_rsi**

  * `short_window`: \[7, 9, 12]
  * `long_window`:  \[20, 26, 35]
  * `rsi_buy`:      \[30, 35, 40]
  * `rsi_sell`:     \[60, 65, 70]

* **bb\_breakout**

  * `period`: \[14, 20, 28]
  * `k`:      \[1.8, 2.0, 2.2, 2.5]

* **macd\_trend**

  * `fast`: \[10, 12, 15]
  * `slow`: \[24, 26, 30]
  * `signal`: \[7, 9, 12]
  * `adx_min`: \[20, 25, 30] (옵션)

* **rsi\_revert**

  * `rsi_period`: \[10, 14, 18]
  * `buy_th`: \[28, 32, 36]
  * `sell_th`: \[64, 68, 72]
  * `cooldown`: \[3, 5, 7]

---

## 7 base.yaml에 전략을 적용하는 방법

* **문서는 고정**, `base.yaml`에서는 심볼/전략 매핑만 바꾼다.
* 예: ETHUSDT에 `ma_rsi` 적용하려면:

```yaml
trading:
  interval: "1m"
  symbols:
    - symbol: BTCUSDT
      strategy: bb_breakout
      params: { period: 20, k: 2.0 }

    - symbol: ETHUSDT
      strategy: ma_rsi
      params:
        short_window: 9
        long_window: 26
        rsi_period: 14
        rsi_buy: 35
        rsi_sell: 65
```

---

## 8 검증 체크리스트

* 파라미터 타입/키는 **pydantic 스키마**로 검증 (`extra="forbid"`)
* **min\_history**를 충족하는 limit로 캔들 요청
* NaN 존재 체크 → 신호 계산 전 `dropna()` 또는 `min_history` 방어
* 전략 간 **쿨다운/충돌 규칙** 별도 관리(동일 심볼에서 상반 신호 발생 시 우선순위)
* 실험 시 **거래 비용/슬리피지 모델** 고정하여 비교 가능성 확보

---

## 9 확장 전략 후보 (요약만)

* `ema_triple`: EMA(10/20/50) 정렬 기반 추세 추종
* `supertrend`: ATR 기반 추세 전환 신호
* `ichimoku`: 구름대 돌파/지지 저항, 호전/역전 교차
* `mfi_revert`: MFI 과매수/과매도 역추세

> 새 전략 추가 시: `src/strategy/<name>.py` + `@register("<name>")` + 여기 문서에 항목 추가.
