import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.config import Config

import pandas as pd
import numpy as np
class Data_Control():
    def __init__(self):
        pass
    
    def cal_moving_average(self, df, period=[20, 60, 120]):
        """
        period 리스트에 명시된 기간별 이동평균선을 구해주는 함수.
        이미 계산된 구간은 건너뛰고, NaN인 부분만 업데이트함.
        예: period=[20,60,120] -> ma_20, ma_60, ma_120 열 생성/갱신
        """
        
        # 예외 처리
        if 'Close' not in df.columns or df.empty:
            raise ValueError("DataFrame에 'Close' 열이 없거나 데이터가 없습니다.")
        
        # period 리스트에 있는 각 기간별로 이동평균선 계산
        for p in period:
            col_name = f"SMA_{p}"
            
            # (1) SMA 컬럼 없으면 생성
            if col_name not in df.columns:
                df[col_name] = np.nan
            
            # (2) NaN 부분만 필터링하여 계산
            nan_indices = df[df[col_name].isna()].index
            for i in nan_indices:
                # p일치 안 되면 계산 불가
                if i < p - 1:
                    continue
                
                # 직전 p개 구간 mean
                window_close = df.loc[max(0, i - p + 1) : i, 'Close']
                df.loc[i, col_name] = window_close.mean()
        
        return df
    
    def cal_rsi(self, df, period = 14, signal_period = 14):
    
        # 1) rsi / rsi_signal 컬럼이 없으면 만들어 둠
        if 'rsi' not in df.columns:
            df['rsi'] = np.nan
        if 'rsi_signal' not in df.columns:
            df['rsi_signal'] = np.nan

        # 2) 이미 계산된 구간(last_valid_rsi) 찾아서, 그 다음 행부터 재계산
        last_valid_rsi = df['rsi'].last_valid_index()
        if last_valid_rsi is None:
            last_valid_rsi = -1  # 전부 NaN이면 -1로 설정 -> 0부터 계산

        # 3) for문으로 last_valid_rsi + 1 ~ 끝까지 순회
        for i in range(last_valid_rsi + 1, len(df)):
            # period 미만 구간은 RSI를 정확히 구하기 어려우니 skip
            if i < period:
                continue
            
            # 이미 NaN이 아니면 = 계산돼 있다면 패스
            if pd.isna(df.loc[i, 'rsi']):
                # (a) rolling 방식으로 i번째 행까지 slice하여 RSI 계산
                window_df = df.loc[:i].copy()
                window_df['diff'] = window_df['Close'].diff()
                window_df['gain'] = window_df['diff'].clip(lower=0)
                window_df['loss'] = -window_df['diff'].clip(upper=0)
                window_df['avg_gain'] = window_df['gain'].rolling(period).mean()
                window_df['avg_loss'] = window_df['loss'].rolling(period).mean()
                
                last_avg_gain = window_df['avg_gain'].iloc[-1]
                last_avg_loss = window_df['avg_loss'].iloc[-1]
                
                if pd.isna(last_avg_gain) or pd.isna(last_avg_loss):
                    # rolling 구간이 아직 안 찼다면 NaN 유지
                    continue
                
                if last_avg_loss == 0:
                    # 하락이 전혀 없으면 RSI=100 처리
                    rsi_val = 100.0
                else:
                    rs = last_avg_gain / last_avg_loss
                    rsi_val = 100 - (100 / (1 + rs))
                
                df.loc[i, 'rsi'] = rsi_val
            
            # (b) signal_period가 있다면, i번째 RSI까지 rolling으로 rsi_signal 계산
            if signal_period > 0 and i >= signal_period and not pd.isna(df.loc[i, 'rsi']):
                rsi_signal_val = df.loc[:i, 'rsi'].rolling(signal_period).mean().iloc[-1]
                df.loc[i, 'rsi_signal'] = rsi_signal_val

        return df
    
    def nor_rsi(self, rsi):
        if rsi >= 50:
            rsi = 100 - rsi
        if rsi <= 20:
            return 20
        elif rsi <= 25:
            return 25
        elif rsi <= 30:
            return 30
        elif rsi <= 35:
            return 35
        else:
            return 50
    
    def cal_bollinger_band(self, df, period=20, num_std=2):
        """
        볼린저 밴드, %b 및 Bandwidth를 계산하는 함수
        
        매개변수:
        df : pandas DataFrame - 'Close' 열이 포함된 데이터프레임
        period : int - 볼린저 밴드 이동평균 기간 (기본값 20)
        num_std : int - 표준편차 배수 (기본값 2)
        
        반환:
        df : pandas DataFrame - 수정된 볼린저 밴드, %b 및 밴드폭 포함
        """
        
        # 1) 볼린저 및 %b, 밴드폭 컬럼들이 없으면 만들어 둠
        if 'middle_boll' not in df.columns:
            df['middle_boll'] = np.nan
        if 'upper_boll' not in df.columns:
            df['upper_boll'] = np.nan
        if 'lower_boll' not in df.columns:
            df['lower_boll'] = np.nan
        if 'percent_b' not in df.columns:
            df['percent_b'] = np.nan
        if 'bandwidth' not in df.columns:
            df['bandwidth'] = np.nan

        # 2) 마지막으로 유효한 볼린저밴드 인덱스 확인
        last_valid_boll = df['middle_boll'].last_valid_index()
        if last_valid_boll is None:
            last_valid_boll = -1  # NaN이면 -1로 설정해서 처음부터 계산

        # 3) last_valid_boll + 1부터 끝까지 순회하며 볼린저 밴드 및 추가 지표 계산
        for i in range(last_valid_boll + 1, len(df)):
            # period 미만 구간은 볼린저밴드 계산 불가
            if i < period:
                continue
            
            # 볼린저밴드 값이 이미 존재하면 건너뛰기
            if (pd.notna(df.loc[i, 'middle_boll']) and 
                pd.notna(df.loc[i, 'upper_boll']) and
                pd.notna(df.loc[i, 'lower_boll']) and
                pd.notna(df.loc[i, 'percent_b']) and
                pd.notna(df.loc[i, 'bandwidth'])):
                continue

            # i번째까지 슬라이스하여 롤링 평균 및 표준편차 계산
            window_series = df.loc[:i, 'Close'].rolling(period)
            mean_val = window_series.mean().iloc[-1]
            std_val = window_series.std().iloc[-1]

            # 볼린저밴드 계산
            upper_val = mean_val + num_std * std_val
            lower_val = mean_val - num_std * std_val

            # %b 계산
            current_price = df.loc[i, 'Close']
            percent_b = (current_price - lower_val) / (upper_val - lower_val)

            # 밴드폭(Bandwidth) 계산
            bandwidth = ((upper_val - lower_val) / mean_val) * 100

            # 결과 반영
            df.loc[i, 'middle_boll'] = mean_val
            df.loc[i, 'upper_boll'] = upper_val
            df.loc[i, 'lower_boll'] = lower_val
            df.loc[i, 'percent_b'] = percent_b
            df.loc[i, 'bandwidth'] = bandwidth

        return df

    def cal_obv(self, df, price_col='Close', volume_col='Volume',
                obv_col='obv', period_1=5, period_2 = 60):
        """
        1) OBV(On-Balance Volume) 계산
          - 이미 계산된 구간( NaN이 아닌 부분 )은 건너뛰고, NaN인 곳만 업데이트
          - OBV[i] = OBV[i-1] ± volume  (종가 상승/하락 시)
        2) 최근 p개 봉(또는 일)에 대한 OBV 고점/저점 계산 (현재 OBV 제외)
          - obv_max_p, obv_min_p
        3) (고점 - 현재값) 기울기, (저점 - 현재값) 기울기
          - 예: obv_slope_from_max = ( current_obv - max_obv_in_p ) / p
          - 예: obv_slope_from_min = ( current_obv - min_obv_in_p ) / p
        4) 반환: df ( obv, obv_slope_from_max, obv_slope_from_min 컬럼 포함 )
        """

        # ---------------------------
        # 0) 컬럼 준비
        # ---------------------------
        # obv가 없으면 생성
        if obv_col not in df.columns:
            df[obv_col] = np.nan

        # 고점/저점 & 기울기 컬럼들
        obv_max_col = f'{obv_col}_max_{period_1}'
        obv_min_col = f'{obv_col}_min_{period_1}'
        slope_from_max_col = f'{obv_col}_slope_from_max'
        slope_from_min_col = f'{obv_col}_slope_from_min'

        for col in [obv_max_col, obv_min_col, slope_from_max_col, slope_from_min_col]:
            if col not in df.columns:
                df[col] = np.nan

        # ---------------------------
        # 1) OBV 계산
        # ---------------------------
        last_valid = df[obv_col].last_valid_index()
        if last_valid is None:
            last_valid = -1

        for i in range(last_valid + 1, len(df)):
            if i == 0:
                df.loc[i, obv_col] = df.loc[i, volume_col]
                continue

            # 이미 값이 있으면 스킵
            if not pd.isna(df.loc[i, obv_col]):
                continue

            prev_price = df.loc[i - 1, price_col]
            curr_price = df.loc[i, price_col]
            prev_obv = df.loc[i - 1, obv_col]
            if pd.isna(prev_obv):
                prev_obv = 0

            curr_vol = df.loc[i, volume_col]

            # period_2보다 작을 때는 기존 OBV 계산법 적용
            if i <= period_2:
                if curr_price > prev_price:
                    df.loc[i, obv_col] = prev_obv + curr_vol
                elif curr_price < prev_price:
                    df.loc[i, obv_col] = prev_obv - curr_vol
                else:
                    df.loc[i, obv_col] = prev_obv
            else:
                # Rolling Window에서 가장 오래된 기간의 거래량 계산
                oldest_vol = df.loc[i - period_2, volume_col]
                oldest_price = df.loc[i - period_2, price_col]

                if curr_price > prev_price:
                    # 상승 시 최신 거래량 더하고, 가장 오래된 거래량의 영향 제거
                    if oldest_price > df.loc[i - period_2 - 1, price_col]:
                        df.loc[i, obv_col] = prev_obv + curr_vol - oldest_vol
                    else:
                        df.loc[i, obv_col] = prev_obv + curr_vol + oldest_vol
                elif curr_price < prev_price:
                    # 하락 시 최신 거래량 빼고, 가장 오래된 거래량의 영향 제거
                    if oldest_price < df.loc[i - period_2 - 1, price_col]:
                        df.loc[i, obv_col] = prev_obv - curr_vol + oldest_vol
                    else:
                        df.loc[i, obv_col] = prev_obv - curr_vol - oldest_vol
                else:
                    df.loc[i, obv_col] = prev_obv
        # ---------------------------
        # 2) p개 구간의 OBV 고점/저점 계산 (현재 OBV 제외)
        # ---------------------------
        # 고점/저점 컬럼의 last_valid
        last_valid_max = df[obv_max_col].last_valid_index()
        last_valid_min = df[obv_min_col].last_valid_index()
        candidate_idx = []
        if last_valid_max is not None:
            candidate_idx.append(last_valid_max)
        if last_valid_min is not None:
            candidate_idx.append(last_valid_min)

        if len(candidate_idx) > 0:
            last_valid_highlow = min(candidate_idx)
        else:
            last_valid_highlow = -1

        for i in range(last_valid_highlow + 1, len(df)):
            current_obv_val = df.loc[i, obv_col]
            if pd.isna(current_obv_val):
                continue  # OBV가 NaN이면 건너뜀

            # p봉 전부터 i-1까지 구간 (현재 i는 제외)
            if i < period_1:
                # p개 이전 인덱스가 유효하지 않으면 패스
                continue

            # 이미 값 있으면 스킵
            if (not pd.isna(df.loc[i, obv_max_col])) and (not pd.isna(df.loc[i, obv_min_col])):
                continue

            start_idx = i - period_1
            end_idx = i - 1
            obv_subset = df.loc[start_idx:end_idx, obv_col]

            if len(obv_subset) < period_1:
                # 충분한 데이터가 없으면 패스
                continue

            df.loc[i, obv_max_col] = obv_subset.max()
            df.loc[i, obv_min_col] = obv_subset.min()

        # ---------------------------
        # 3) (고점 - 현재값), (저점 - 현재값) 기울기 계산
        # ---------------------------
        # 여기서는 (현재 - 고점)/p, (현재 - 저점)/p 로 정의함
        # 질문 내용이 "고점 - 현재값 기울기"였지만, 부호 방향을 어떻게 할지는 자유
        # 원하는 대로 수정해서 쓰세요 :)
        last_valid_slope_max = df[slope_from_max_col].last_valid_index()
        last_valid_slope_min = df[slope_from_min_col].last_valid_index()
        candidate_idx_2 = []
        if last_valid_slope_max is not None:
            candidate_idx_2.append(last_valid_slope_max)
        if last_valid_slope_min is not None:
            candidate_idx_2.append(last_valid_slope_min)

        if len(candidate_idx_2) > 0:
            last_valid_slope = min(candidate_idx_2)
        else:
            last_valid_slope = -1

        for i in range(last_valid_slope + 1, len(df)):
            current_obv_val = df.loc[i, obv_col]
            max_val = df.loc[i, obv_max_col]
            min_val = df.loc[i, obv_min_col]

            if pd.isna(current_obv_val) or pd.isna(max_val) or pd.isna(min_val):
                continue

            # (현재 OBV - 고점) / p
            # 질문에서 "고점 - 현재값 기울기"라고 했으니 부호 반대로 할 수도 있음
            df.loc[i, slope_from_max_col] = (max_val - current_obv_val) / period_1
            df.loc[i, slope_from_min_col] = (min_val - current_obv_val) / period_1

        # ---------------------------
        # 4) 반환
        # ---------------------------
        # 최종적으로 obv, obv_slope_from_max, obv_slope_from_min 등을 포함한 df 반환
        return df

    def cal_atr(self, df, period=14):
        """
        ATR(Average True Range)을 순차적으로 계산하는 함수.
        
        매개변수:
          df : pandas DataFrame - 'High', 'Low', 'Close' 컬럼 필수.
          period : int - ATR 계산 기간 (기본값 14)
        
        반환:
          df : ATR 컬럼이 추가된 DataFrame.
        """
        # ATR 컬럼이 없으면 생성
        if 'ATR' not in df.columns:
            df['ATR'] = np.nan

        # 중간 계산을 위한 True Range(TR) 컬럼 생성 (임시)
        if 'TR' not in df.columns:
            df['TR'] = np.nan

        # 각 행마다 TR 계산 (0번 행은 High-Low)
        for i in range(len(df)):
            if i == 0:
                df.loc[i, 'TR'] = df.loc[i, 'High'] - df.loc[i, 'Low']
            else:
                diff1 = df.loc[i, 'High'] - df.loc[i, 'Low']
                diff2 = abs(df.loc[i, 'High'] - df.loc[i - 1, 'Close'])
                diff3 = abs(df.loc[i, 'Low'] - df.loc[i - 1, 'Close'])
                df.loc[i, 'TR'] = max(diff1, diff2, diff3)

        # ATR은 period 기간의 TR 평균 (최소 period 개수부터 계산)
        last_valid_atr = df['ATR'].last_valid_index()
        if last_valid_atr is None:
            last_valid_atr = -1

        for i in range(last_valid_atr + 1, len(df)):
            if i < period - 1:
                continue  # 데이터가 충분하지 않으면 계산하지 않음
            atr_val = df.loc[i - period + 1:i, 'TR'].mean()
            df.loc[i, 'ATR'] = atr_val

        # 임시 TR 컬럼 삭제
        df.drop(columns=['TR'], inplace=True)
        return df

    def cal_macd(self, df, fast_period=12, slow_period=26, signal_period=9):
        """
        MACD, MACD Signal, MACD Histogram 계산 함수 (수정 버전)
        """
        # MACD 관련 컬럼 생성
        if 'MACD' not in df.columns:
            df['MACD'] = np.nan
        if 'MACD_signal' not in df.columns:
            df['MACD_signal'] = np.nan
        if 'MACD_histogram' not in df.columns:
            df['MACD_histogram'] = np.nan

        # EMA 계산을 위한 multiplier
        multiplier_fast = 2 / (fast_period + 1)
        multiplier_slow = 2 / (slow_period + 1)
        multiplier_signal = 2 / (signal_period + 1)

        ema_fast = None
        ema_slow = None
        macd_signal = None

        for i in range(len(df)):
            close_val = df.loc[i, 'Close']

            if i == 0:
                ema_fast = close_val
                ema_slow = close_val
                macd = 0.0
                macd_signal = 0.0
            else:
                ema_fast = (close_val - ema_fast) * multiplier_fast + ema_fast
                ema_slow = (close_val - ema_slow) * multiplier_slow + ema_slow
                macd = ema_fast - ema_slow
                macd_signal = (macd - macd_signal) * multiplier_signal + macd_signal

            df.loc[i, 'MACD'] = macd
            df.loc[i, 'MACD_signal'] = macd_signal
            df.loc[i, 'MACD_histogram'] = macd - macd_signal

        return df

    def cal_adx(self, df, period=14):
        """
        ADX(Average Directional Index)를 순차적으로 계산하는 함수.
        
        매개변수:
          df : pandas DataFrame - 'High', 'Low', 'Close' 컬럼이 포함되어 있어야 함.
          period : int - ADX 계산에 사용할 기간 (기본값 14)
        
        반환:
          df : 'ADX' 컬럼이 추가된 DataFrame.
        
        주의: 이미 ADX 값이 존재하는 행은 재계산하지 않음.
        """
        # ADX 컬럼이 없으면 생성
        if 'ADX' not in df.columns:
            df['ADX'] = np.nan
        # 임시 계산용 컬럼들도 생성 (이미 존재한다면 그대로 사용)
        for col in ['TR', '+DM', '-DM']:
            if col not in df.columns:
                df[col] = np.nan

        n = len(df)
        if n < period + 1:
            # 충분한 데이터가 없으면 그대로 반환
            return df

        # 기존에 계산된 마지막 인덱스 확인
        last_valid = df['ADX'].last_valid_index()
        if last_valid is None:
            last_valid = -1  # 전부 NaN인 경우

        # 먼저, 모든 행에 대해 TR, +DM, -DM 계산 (이미 값이 있으면 건너뜀)
        for i in range(1, n):
            if pd.isna(df.loc[i, 'TR']):
                high = df.loc[i, 'High']
                low = df.loc[i, 'Low']
                prev_close = df.loc[i-1, 'Close']
                df.loc[i, 'TR'] = max(high - low, abs(high - prev_close), abs(low - prev_close))
            if pd.isna(df.loc[i, '+DM']):
                up_move = df.loc[i, 'High'] - df.loc[i-1, 'High']
                down_move = df.loc[i-1, 'Low'] - df.loc[i, 'Low']
                df.loc[i, '+DM'] = up_move if (up_move > down_move and up_move > 0) else 0
            if pd.isna(df.loc[i, '-DM']):
                up_move = df.loc[i, 'High'] - df.loc[i-1, 'High']
                down_move = df.loc[i-1, 'Low'] - df.loc[i, 'Low']
                df.loc[i, '-DM'] = down_move if (down_move > up_move and down_move > 0) else 0

        # 시작 인덱스: 이미 계산된 마지막 인덱스 + 1, 단 최소 period 인덱스부터 계산
        start_index = max(last_valid + 1, period)
        
        # 초기 스무딩: 인덱스 1부터 period까지의 합을 사용 (단, start_index가 period인 경우)
        if start_index == period:
            sm_TR = df['TR'].iloc[1:period+1].sum()
            sm_plusDM = df['+DM'].iloc[1:period+1].sum()
            sm_minusDM = df['-DM'].iloc[1:period+1].sum()
            # 초기 DX 계산
            if sm_TR == 0:
                DI_plus = 0
                DI_minus = 0
            else:
                DI_plus = 100 * (sm_plusDM / sm_TR)
                DI_minus = 100 * (sm_minusDM / sm_TR)
            DX = 100 * abs(DI_plus - DI_minus) / (DI_plus + DI_minus) if (DI_plus + DI_minus) != 0 else 0
            # 초기 ADX는 아직 미정으로 남김
            df.loc[period, 'ADX'] = np.nan
            # 누적 DX 합, 개수 (초기 ADX 계산용)
            dx_sum = DX
            dx_count = 1
            current_sm_TR = sm_TR
            current_sm_plusDM = sm_plusDM
            current_sm_minusDM = sm_minusDM
        else:
            # 이미 일부 ADX가 계산되어 있다면, 복원
            # (이 경우, 이전 스무딩 값은 계산되지 않았으므로, 여기서는 다시 계산하지 않고 이어서 처리합니다.)
            # 주의: 연속성이 깨질 수 있으므로, 데이터 업데이트 시에는 전체 계산을 권장합니다.
            current_sm_TR = df['TR'].iloc[start_index-1]
            current_sm_plusDM = df['+DM'].iloc[start_index-1]
            current_sm_minusDM = df['-DM'].iloc[start_index-1]
            dx_sum = 0
            dx_count = 0

        # Wilder 스무딩 방식으로 ADX 계산: start_index부터 n-1까지
        for i in range(start_index, n):
            current_sm_TR = current_sm_TR - (current_sm_TR / period) + df.loc[i, 'TR']
            current_sm_plusDM = current_sm_plusDM - (current_sm_plusDM / period) + df.loc[i, '+DM']
            current_sm_minusDM = current_sm_minusDM - (current_sm_minusDM / period) + df.loc[i, '-DM']
            
            if current_sm_TR == 0:
                DI_plus = 0
                DI_minus = 0
            else:
                DI_plus = 100 * (current_sm_plusDM / current_sm_TR)
                DI_minus = 100 * (current_sm_minusDM / current_sm_TR)
            DX = 100 * abs(DI_plus - DI_minus) / (DI_plus + DI_minus) if (DI_plus + DI_minus) != 0 else 0
            
            # 초기 ADX 구간: i가 period*2 미만인 경우, 누적 DX로 평균을 구함
            if i < period * 2:
                dx_sum += DX
                dx_count += 1
                df.loc[i, 'ADX'] = np.nan
            elif i == period * 2:
                dx_sum += DX
                dx_count += 1
                adx = dx_sum / dx_count
                df.loc[i, 'ADX'] = adx
            else:
                prev_adx = df.loc[i-1, 'ADX']
                adx = (prev_adx * (period - 1) + DX) / period
                df.loc[i, 'ADX'] = adx

        # 기존에 이미 계산된 ADX 값은 유지하고, 새로 계산한 값만 업데이트하였으므로 완료 후, 임시 컬럼 삭제
        df.drop(columns=['TR', '+DM', '-DM'], inplace=True)
        return df

    def LT_trand_check(self, df):
        """
        상승 및 하락 추세 판단 함수
        볼린저 밴드 %b 및 Bandwidth 기반으로 추세 분석 강화
        Relative Bandwidth 계산을 포함하여 추세 전환 가능성까지 감지
        
        매개변수:
        df : pandas DataFrame - 볼린저 밴드 및 가격 데이터가 포함된 데이터프레임
        
        반환:
        df : pandas DataFrame - Trend 열에 추세 레벨을 반영하여 반환
        """

        # MA 트렌드 판별 함수
        def check_ma_trend(sma20, sma60, sma120, rbw, tol_ratio=0.005):
            """
            이동평균선 상태 및 상대 밴드폭(RBW)을 바탕으로 추세 상태를 정수로 매핑하는 함수
            
            매개변수:
            sma20 : float - 20일 이동평균선
            sma60 : float - 60일 이동평균선
            sma120 : float - 120일 이동평균선
            rbw : float - 상대 밴드폭 (Relative Bandwidth)
            
            반환:
            int - 트렌드 상태 (-9 ~ 9)
            """
            # -------------------------------------------------------
            # 1) 보조 함수: 두 SMA가 tol_ratio 이내로 근접한지 판별
            # -------------------------------------------------------
            def is_near(a, b, tolerance=tol_ratio):
                # 분모를 b 혹은 (a+b)/2 등으로 조정 가능
                # 절대값 기준으로 쓰고 싶다면 abs(a - b) < 특정값으로 변경
                return abs(a - b) <= abs(b) * tolerance

            near_20_60 = is_near(sma20, sma60)
            near_20_120 = is_near(sma20, sma120)
            near_60_120 = is_near(sma60, sma120)

            # -------------------------------------------------------
            # 모든 SMA(20,60,120)가 서로 근접 → 완전 박스권
            # -------------------------------------------------------
            if near_20_60 and near_20_120 and near_60_120:
                return 0  # Code 0: 단기·중기·장기 선이 사실상 동일 -> 강한 횡보(추세 모호)

            # (A) 강한 상승 배열: sma120 < sma60 < sma20
            if sma120 < sma60 < sma20:
                if rbw > 1.1:
                    return 1
                elif 0.8 <= rbw <= 1.1:
                    return 2
                else:  # rbw < 0.8
                    return 3

            # (B) 불안정 상승 배열: sma60 < sma120 < sma20
            elif sma60 < sma120 < sma20:
                if rbw > 1.1:
                    return 4
                elif 0.8 <= rbw <= 1.1:
                    return 5
                else:
                    return 6

            # (C) 약세 반등 배열: sma120 < sma20 < sma60
            elif sma120 < sma20 < sma60:
                if rbw > 1.1:
                    return 7
                elif 0.8 <= rbw <= 1.1:
                    return 8
                else:
                    return 9

            # (D) 강한 하락 배열: sma20 < sma120 < sma60
            elif sma20 < sma120 < sma60:
                if rbw > 1.1:
                    return -1
                elif 0.8 <= rbw <= 1.1:
                    return -2
                else:
                    return -3

            # (E) 불안정 하락 배열: sma60 < sma20 < sma120
            elif sma60 < sma20 < sma120:
                if rbw > 1.1:
                    return -4
                elif 0.8 <= rbw <= 1.1:
                    return -5
                else:
                    return -6

            # (F) 급격한 하락 배열: sma20 < sma60 < sma120
            elif sma20 < sma60 < sma120:
                if rbw > 1.1:
                    return -7
                elif 0.8 <= rbw <= 1.1:
                    return -8
                else:
                    return -9

            # (G) 그 외 → 횡보 or 추세 모호
            else:
                return 0

        # 1) 필요한 컬럼들이 없으면 새로 만듦
        needed_cols = ['trend', 'RBW']
        for col in needed_cols:
            if col not in df.columns:
                df[col] = np.nan

        # 2) 'Trend' 컬럼을 기준으로 last_valid_index 가져오기
        last_valid = df['trend'].last_valid_index()
        if last_valid is None:
            last_valid = -1  # 전부 NaN이면 -1로 설정

        # 3) MA 트렌드 및 횡보 상태 판별
        for i in range(last_valid + 1, len(df)):
            if pd.notna(df.loc[i, 'trend']):
                continue
            
            # 이동평균선 데이터
            sma20 = df.loc[i, 'SMA_20']
            sma60 = df.loc[i, 'SMA_60']
            sma120 = df.loc[i, 'SMA_120']
            bandwidth = df.loc[i, 'bandwidth']

            # 데이터 누락 시 건너뛰기
            if pd.isna(sma20) or pd.isna(sma60) or pd.isna(sma120) or pd.isna(bandwidth):
                continue

            # RBW(상대 밴드폭) 계산
            rbw = bandwidth / df['bandwidth'].rolling(60).mean().iloc[i]
            df.loc[i, 'RBW'] = rbw

            # MA 트렌드 판별 (새로운 함수 활용)
            trend_state = int(check_ma_trend(sma20, sma60, sma120, rbw))

            # Trend 컬럼에 상태 업데이트
            df.loc[i, 'trend'] = trend_state

        return df


    def data(self,client,symbol,timeframe, limit = 300, futures=False):
        symbol = f"{symbol}USDT"
        if futures:
            candles = client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
        else:
            candles = client.get_klines(symbol=symbol, interval=timeframe, limit=limit)

        # 새로운 데이터를 DataFrame으로 변환
        data = pd.DataFrame(candles, columns=[
            "Open Time", "Open", "High", "Low", "Close", "Volume",
            "Close Time", "Quote Asset Volume", "Number of Trades",
            "Taker Buy Base Asset Volume", "Taker Buy Quote Asset Volume", "Ignore"
        ])
        selected_columns = ["Open Time", "Open", "High", "Low", "Close", "Volume", "Taker Buy Base Asset Volume"]
        data = data[selected_columns]
        # 자료형 변환
        data["Open"] = data["Open"].astype(float)
        data["High"] = data["High"].astype(float)
        data["Low"] = data["Low"].astype(float)
        data["Close"] = data["Close"].astype(float)
        data["Volume"] = data["Volume"].astype(float)
        data["Taker Buy Base Asset Volume"] = data["Taker Buy Base Asset Volume"].astype(float)

        # 시간 변환
        data["Open Time"] = pd.to_datetime(data["Open Time"], unit='ms')
        data["Taker Sell Base Asset Volume"] = data["Volume"] - data["Taker Buy Base Asset Volume"]
        data["Open Time"] = pd.to_datetime(data["Open Time"], unit='ms')  # 시간 변환
        data = data.sort_values(by="Open Time").reset_index(drop=True)
        
        if futures:
            # Funding Rate 수집
            funding_rate = client.futures_funding_rate(symbol=symbol)
            funding_df = pd.DataFrame(funding_rate)
            funding_df = funding_df.tail(300)  # 최근 300개의 Funding Rate만 유지
            funding_df["fundingRate"] = funding_df["fundingRate"].astype(float)
            funding_df["fundingTime"] = pd.to_datetime(funding_df["fundingTime"], unit='ms')
            
            # 5. 데이터 병합 (가장 최근 값으로 병합)
            data = pd.merge_asof(data.sort_values("Open Time"), funding_df.sort_values("fundingTime"), 
                                  left_on="Open Time", right_on="fundingTime", direction="backward")
            
            # 필요 없는 열 정리
            data.drop(columns=["fundingTime", "symbol", "markPrice"], inplace=True)

        return data
    
    def update_data(self, client, symbol, timeframe, existing_data, futures=False, funding_limit=3):
        try:
            # 새 데이터 수집 (3개 캔들 데이터만 요청)
            symbol = f"{symbol}USDT"
            candles = None
            if futures:
                candles = client.futures_klines(symbol=symbol, interval=timeframe, limit=3)
            else:
                candles = client.get_klines(symbol=symbol, interval=timeframe, limit=3)

            # 새로운 데이터를 DataFrame으로 변환
            temp_data = pd.DataFrame(candles, columns=[
                "Open Time", "Open", "High", "Low", "Close", "Volume",
                "Close Time", "Quote Asset Volume", "Number of Trades",
                "Taker Buy Base Asset Volume", "Taker Buy Quote Asset Volume", "Ignore"
            ])
            
            # 필요한 컬럼만 선택 및 전처리
            selected_columns = ["Open Time", "Open", "High", "Low", "Close", "Volume", "Taker Buy Base Asset Volume"]
            temp_data = temp_data[selected_columns]
            temp_data["Open"]  = temp_data["Open"].astype(float)
            temp_data["High"]  = temp_data["High"].astype(float)
            temp_data["Low"]   = temp_data["Low"].astype(float)
            temp_data["Close"] = temp_data["Close"].astype(float)
            temp_data["Volume"] = temp_data["Volume"].astype(float)
            temp_data["Taker Buy Base Asset Volume"]  = temp_data["Taker Buy Base Asset Volume"].astype(float)
            temp_data["Taker Sell Base Asset Volume"] = temp_data["Volume"] - temp_data["Taker Buy Base Asset Volume"]
            temp_data["Open Time"] = pd.to_datetime(temp_data["Open Time"], unit='ms')
            
            # 선물 데이터에 추가 정보를 병합
            if futures:
                # Funding Rate 수집
                funding_rate = client.futures_funding_rate(symbol=symbol, limit=funding_limit)
                funding_df = pd.DataFrame(funding_rate)
                funding_df["fundingRate"] = funding_df["fundingRate"].astype(float)
                funding_df["fundingTime"] = pd.to_datetime(funding_df["fundingTime"], unit='ms')

                # 데이터 병합
                temp_data = pd.merge_asof(temp_data.sort_values("Open Time"), funding_df.sort_values("fundingTime"),
                                          left_on="Open Time", right_on="fundingTime", direction="backward")
                
                # 필요 없는 열 제거
                temp_data.drop(columns=["fundingTime", "symbol", "markPrice"], inplace=True)

            ## `Open Time` 기준 병합
            combined_data = pd.concat([existing_data, temp_data]).drop_duplicates(subset="Open Time", keep="last")
            combined_data = combined_data.sort_values(by="Open Time").reset_index(drop=True)

            if len(combined_data) > 140:
                combined_data = combined_data.iloc[-140:].reset_index(drop=True)

            return combined_data
            
        except Exception as e:
            print(f"데이터 업데이트 중 오류 발생: {e}")
            return existing_data
        
    def cal_indicator(self, data):
        data = self.cal_moving_average(data)
        data = self.cal_rsi(data)
        # data = self.cal_bollinger_band(data)
        # data = self.cal_obv(data)
        # data = self.LT_trand_check(data)
        # data = self.cal_atr(data)
        data = self.cal_macd(data)
        # data = self.cal_adx(data)

        return data