#!/usr/bin/env python3
"""
MT4 EA パラメータ最適化スクリプト (Strategy Tester 直接実行版)

使用方法:
  python mt4_optimizer.py -o evolve   -k PDX+SAR_0.0.2   # 遺伝的アルゴリズム最適化
  python mt4_optimizer.py -o grid     -k PDX+SAR_0.0.2   # グリッドサーチ
  python mt4_optimizer.py -o refine   -k PDX+SAR_0.0.2   # ベストパラメータ周辺絞り込み
  python mt4_optimizer.py -o adaptive -k PDX+SAR_0.0.2   # アダプティブストップ最適化
  python mt4_optimizer.py -o atr      -k PDX+SAR_0.0.2   # ATRフィルター最適化
  python mt4_optimizer.py -o backtest -k PDX+SAR_0.0.2   # best_params.json で詳細バックテスト

  -k を省略した場合は EA_CONFIGS の先頭エントリを使用する。
  新しい EA を追加するには EA_CONFIGS にエントリを追加すること。

注意: 実行中は MT4 を手動で開かないこと（プロセス競合）。
"""

import struct
import subprocess
import re
import sys
import csv
import json
import itertools
import random
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

# ===== パス設定 =====
MT4_DATA    = Path(r"C:\Users\rinns\AppData\Roaming\MetaQuotes\Terminal\082F53F5881F3D6022DF806C3D307B50")
HST_PATH    = MT4_DATA / "history" / "XMTrading-Demo 6" / "GOLD15.hst"
RESULTS_DIR = Path(__file__).parent / "results"


# ===== EA 設定ファイル =====
# EA ごとの設定は tools/configs/<EA名>.json に記述する。
# 新しい EA を追加するには JSON ファイルを追加するだけでよい。
CONFIGS_DIR     = Path(__file__).parent / "configs"
_DEFAULT_EA_KEY = "PDX+SAR_0.0.2"

# 起動時は空。_apply_ea_config() で JSON から読み込む。
GRID: dict           = {}
_INT_EA_PARAMS: set  = set()
MAX_SAMPLES          = 100

# ===== 評価スコア重み =====
SCORE_WEIGHT_NET_PROFIT   = 0.7  # 純益の重み
SCORE_WEIGHT_TOTAL_TRADES = 0.3  # 総トレード数の重み

# ===== MT4 テスター設定 =====
MT4_EXE     = Path(r"C:\Program Files (x86)\XMTrading MT4\terminal.exe")
TERM_INI    = MT4_DATA / "config" / "terminal.ini"
TESTER_DIR  = MT4_DATA / "tester"
REPORTS_DIR = MT4_DATA / "reports"
EA_FILE     = f"{_DEFAULT_EA_KEY}.ex4"
RPT_NAME    = "py_opt_result"
RPT_PATH    = REPORTS_DIR / f"{RPT_NAME}.htm"
TESTER_INI  = TESTER_DIR / f"{_DEFAULT_EA_KEY}.ini"

TESTER_FROM          = "2023.06.06"
TESTER_TO            = "2026.06.05"
TESTER_SYMBOL        = "GOLD"
TESTER_PERIOD        = "15"    # M15
TESTER_MODEL_FAST    = "2"     # 始値のみ (Open Prices Only): 2-6秒/回 (GA最適化用)
TESTER_MODEL_PRECISE = "1"     # コントロールポイント (Control Points): 最終検証用
TESTER_SPREAD        = "current"
TESTER_DEPOSIT       = 50000000  # 50M JPY: 残高不足を防ぐ

# M5=1, M15=2, M30=3, H1=4, H4=5 (MT4 テスター Period コンボボックスのインデックス)
PERIOD_INDEX_MAP = {'1': 0, '5': 1, '15': 2, '30': 3, '60': 4, '240': 5, '1440': 6}



def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ===== HST 読み込み =====

def load_hst(path: Path) -> pd.DataFrame:
    """MT4 .hst を読み込み DataFrame を返す

    v400: header=148, bar=44  int32(time)+double*4(open,low,high,close)+int64(vol)
    v401: header=148, bar=60  int64(time)+double*4(open,high,low,close)+int64(tv)+int32(spread)+int64(rv)
    v401 は high/low の順が v400 と逆なので注意
    """
    data    = path.read_bytes()
    version = struct.unpack_from('<i', data, 0)[0]

    HEADER = 148
    if version == 400:
        BAR = 44
        fmt = '<iddddq'   # time(int32), open, low, high, close, volume
        cols = ['time', 'open', 'low', 'high', 'close', 'volume']
    elif version == 401:
        BAR = 60
        fmt = '<qddddqiq'  # time(int64), open, HIGH, LOW, close, tv, spread, rv
        cols = ['time', 'open', 'high', 'low', 'close', 'tick_vol', 'spread', 'real_vol']
    else:
        raise ValueError(f"未対応の HST バージョン: {version}")

    n    = (len(data) - HEADER) // BAR
    rows = [struct.unpack_from(fmt, data, HEADER + i * BAR) for i in range(n)]

    df = pd.DataFrame(rows, columns=cols)
    df['dt'] = pd.to_datetime(df['time'], unit='s')

    df = df[(df['dt'] >= FROM_DATE) & (df['dt'] <= TO_DATE)].reset_index(drop=True)
    return df


# ===== インジケーター =====

def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """ADX (Wilder's smoothing) を計算する"""
    n    = len(close)
    tr   = np.zeros(n)
    dm_p = np.zeros(n)
    dm_m = np.zeros(n)

    for i in range(1, n):
        tr[i]   = max(high[i] - low[i],
                      abs(high[i] - close[i-1]),
                      abs(low[i]  - close[i-1]))
        up      = high[i] - high[i-1]
        dn      = low[i-1] - low[i]
        dm_p[i] = up if up > dn and up > 0 else 0.0
        dm_m[i] = dn if dn > up and dn > 0 else 0.0

    atr = np.zeros(n)
    sdp = np.zeros(n)
    sdm = np.zeros(n)
    atr[period] = tr[1:period+1].sum()
    sdp[period] = dm_p[1:period+1].sum()
    sdm[period] = dm_m[1:period+1].sum()

    for i in range(period + 1, n):
        atr[i] = atr[i-1] - atr[i-1] / period + tr[i]
        sdp[i] = sdp[i-1] - sdp[i-1] / period + dm_p[i]
        sdm[i] = sdm[i-1] - sdm[i-1] / period + dm_m[i]

    di_p   = np.where(atr > 0, 100 * sdp / atr, 0.0)
    di_m   = np.where(atr > 0, 100 * sdm / atr, 0.0)
    di_sum = di_p + di_m
    dx     = np.where(di_sum > 0, 100 * np.abs(di_p - di_m) / di_sum, 0.0)

    adx   = np.zeros(n)
    start = 2 * period
    if start < n:
        adx[start] = dx[period:start+1].mean()
        for i in range(start + 1, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

    return adx


def calc_atr_ratio(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                   short_period: int, baseline_period: int) -> np.ndarray:
    """短期ATR / 基準ATR の比率を返す (1.0超 = 平常より高ボラ状態)

    固定閾値でなく比率で判定するため、GOLD価格水準が変わっても機能する。
    """
    tr = np.empty(len(close))
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]),
                   np.abs(low[1:]  - close[:-1]))
    )
    tr_s       = pd.Series(tr)
    atr_short  = tr_s.rolling(short_period,    min_periods=short_period).mean().fillna(0).values
    atr_base   = tr_s.rolling(baseline_period, min_periods=baseline_period).mean().fillna(0).values
    return np.where(atr_base > 0, atr_short / atr_base, 1.0)


def calc_sar(high: np.ndarray, low: np.ndarray, step: float, max_val: float) -> np.ndarray:
    """Parabolic SAR を計算する"""
    n    = len(high)
    sar  = np.zeros(n)
    bull = True
    af   = step
    ep   = high[0]
    sar[0] = low[0]

    for i in range(1, n):
        if bull:
            sar[i] = sar[i-1] + af * (ep - sar[i-1])
            if i >= 2:
                sar[i] = min(sar[i], low[i-1], low[i-2])
            else:
                sar[i] = min(sar[i], low[i-1])

            if low[i] < sar[i]:
                bull   = False
                sar[i] = ep
                ep     = low[i]
                af     = step
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + step, max_val)
        else:
            sar[i] = sar[i-1] - af * (sar[i-1] - ep)
            if i >= 2:
                sar[i] = max(sar[i], high[i-1], high[i-2])
            else:
                sar[i] = max(sar[i], high[i-1])

            if high[i] > sar[i]:
                bull   = True
                sar[i] = ep
                ep     = high[i]
                af     = step
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + step, max_val)

    return sar


# ===== EA シミュレーション =====

def simulate_ea(df: pd.DataFrame, params: dict) -> list:
    """PDX+SAR_0.0.1 EA ロジックをバー単位でシミュレートする

    バー i でのシミュレーション順序:
      1. 保有ポジションの SL/TP チェック (バーの high/low で判定)
      2. SAR 反転による決済 (バーの close で判定)
      3. 適応型停止チェック（直近勝率が閾値未満なら停止中）
      4. クールダウンチェック
      5. エントリー条件チェック → 新規建て

    適応型停止パラメータ (AdaptiveWindow=0 で無効):
      AdaptiveWindow    : 監視する直近トレード数
      AdaptivePauseWR   : 停止トリガー勝率 (例: 0.10 = 10%)
      AdaptivePauseHours: 停止する時間数 (例: 48 = 48時間)
    """
    lots     = params['Lots']
    period   = params['ADX_Period']
    adx_thr  = params['ADX_Threshold']
    s_step   = params['SAR_Step']
    s_max    = params['SAR_Max']
    s_trend  = int(params['SAR_Min_Trend'])
    sl_pts   = params['StopLoss']
    tp_pts   = params['TakeProfit']
    cooldown = params['CooldownSeconds']

    # 適応型停止パラメータ (デフォルト: 無効)
    adap_window     = int(params.get('AdaptiveWindow', 0))
    adap_pause_wr   = float(params.get('AdaptivePauseWR', 0.10))
    adap_pause_secs = int(params.get('AdaptivePauseHours', 48)) * 3600

    # ATRフィルターパラメータ (ATR_Short=0 で無効)
    atr_short_p = int(params.get('ATR_Short', 0))
    atr_base_p  = int(params.get('ATR_Baseline', 100))
    atr_mult    = float(params.get('ATR_Multiplier', 1.5))

    high_a  = df['high'].values
    low_a   = df['low'].values
    close_a = df['close'].values
    time_a  = df['time'].values.astype(np.int64)
    dt_a    = df['dt'].dt.to_pydatetime()

    adx_a = calc_adx(high_a, low_a, close_a, period)
    sar_a = calc_sar(high_a, low_a, s_step, s_max)
    atr_ratio_a = (calc_atr_ratio(high_a, low_a, close_a, atr_short_p, atr_base_p)
                   if atr_short_p > 0 else None)

    warmup = max(2 * period + s_trend + 2,
                 atr_base_p if atr_short_p > 0 else 0)
    trades = []
    pos    = None
    last_exit_ts    = 0
    adap_resume_ts  = 0   # 適応停止の解除タイムスタンプ (0=停止中でない)
    recent_wins: list = []  # 直近 adap_window 件の勝敗 (True=勝, False=負)

    for i in range(warmup, len(df)):
        bid    = close_a[i]
        ask    = bid + SPREAD * POINT
        ts     = int(time_a[i])
        bar_dt = dt_a[i]

        # --- CheckExit ---
        if pos is not None:
            exited      = False
            exit_price  = 0.0
            exit_reason = ''

            if pos['type'] == 'buy':
                if low_a[i] <= pos['sl']:
                    exit_price, exit_reason, exited = pos['sl'], 'SL', True
                elif high_a[i] >= pos['tp']:
                    exit_price, exit_reason, exited = pos['tp'], 'TP', True
                elif sar_a[i] > bid:
                    exit_price, exit_reason, exited = bid,       'SAR', True
            else:  # sell
                if high_a[i] >= pos['sl']:
                    exit_price, exit_reason, exited = pos['sl'], 'SL', True
                elif low_a[i] <= pos['tp']:
                    exit_price, exit_reason, exited = pos['tp'], 'TP', True
                elif sar_a[i] < ask:
                    exit_price, exit_reason, exited = ask,       'SAR', True

            if exited:
                if pos['type'] == 'buy':
                    profit_usd = (exit_price - pos['entry']) * lots * LOT_SIZE
                else:
                    profit_usd = (pos['entry'] - exit_price) * lots * LOT_SIZE
                profit_jpy = profit_usd * JPY_RATE
                trades.append({
                    'open_dt':     pos['open_dt'],
                    'close_dt':    bar_dt,
                    'type':        pos['type'],
                    'entry':       pos['entry'],
                    'exit':        exit_price,
                    'profit_usd':  profit_usd,
                    'profit_jpy':  profit_jpy,
                    'exit_reason': exit_reason,
                })
                last_exit_ts = ts
                pos = None

                # --- 適応型停止: 直近勝率を更新して停止判定 ---
                if adap_window > 0:
                    recent_wins.append(profit_jpy > 0)
                    if len(recent_wins) > adap_window:
                        recent_wins.pop(0)
                    if len(recent_wins) >= adap_window:
                        wr = sum(recent_wins) / adap_window
                        if wr < adap_pause_wr and adap_resume_ts == 0:
                            adap_resume_ts = ts + adap_pause_secs
                            recent_wins = []  # 再開後は新しいウィンドウで再評価

        # --- 適応型停止チェック ---
        if adap_window > 0 and adap_resume_ts > 0:
            if ts >= adap_resume_ts:
                adap_resume_ts = 0  # 停止期間終了
            else:
                continue  # まだ停止中

        # --- エントリー条件 ---
        if pos is not None:
            continue
        if ts - last_exit_ts < cooldown:
            continue
        # ATRフィルター: 短期ATR が基準の atr_mult 倍超なら高ボラとみなしスキップ
        if atr_ratio_a is not None and atr_ratio_a[i] > atr_mult:
            continue

        adx_cur  = adx_a[i]
        adx_prev = adx_a[i - 1]
        sar_cur  = sar_a[i]

        if adx_cur <= adx_thr or adx_cur <= adx_prev:
            continue

        # IsSARTrend: 直近 s_trend 本のバー (index 1..s_trend) が同方向か確認
        buy_ok = all(sar_a[i - j] < close_a[i - j] for j in range(1, s_trend + 1))
        sel_ok = all(sar_a[i - j] > close_a[i - j] for j in range(1, s_trend + 1))

        if sar_cur < bid and buy_ok:
            pos = {
                'type':    'buy',
                'entry':   ask,
                'sl':      ask - sl_pts * POINT,
                'tp':      ask + tp_pts * POINT,
                'open_dt': bar_dt,
            }
        elif sar_cur > ask and sel_ok:
            pos = {
                'type':    'sell',
                'entry':   bid,
                'sl':      bid + sl_pts * POINT,
                'tp':      bid - tp_pts * POINT,
                'open_dt': bar_dt,
            }

    return trades


# ===== メトリクス計算 =====

def calc_metrics(trades: list, params: dict) -> dict:
    if not trades:
        return {
            'net_profit': 0, 'profit_factor': 0, 'max_drawdown': 0,
            'total_trades': 0, 'win_rate_pct': 0, 'avg_win': 0, 'avg_loss': 0,
            'monthly_trades': {}, 'params': params,
        }

    profits = [t['profit_jpy'] for t in trades]
    wins    = [p for p in profits if p > 0]
    losses  = [p for p in profits if p < 0]

    gross_win  = sum(wins)   if wins   else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else 9999.0

    equity = np.cumsum([0.0] + profits)
    peak   = np.maximum.accumulate(equity)
    max_dd = float(np.max(peak - equity))

    monthly = defaultdict(int)
    for t in trades:
        monthly[t['open_dt'].strftime('%Y-%m')] += 1

    consec = _analyze_consecutive_losses(trades, profits)

    return {
        'net_profit':    round(gross_win - gross_loss, 0),
        'profit_factor': round(pf, 4),
        'max_drawdown':  round(max_dd, 0),
        'total_trades':  len(trades),
        'win_rate_pct':  round(len(wins) / len(profits) * 100, 2),
        'avg_win':       round(gross_win  / len(wins)   if wins   else 0, 0),
        'avg_loss':      round(sum(losses) / len(losses) if losses else 0, 0),
        'monthly_trades': dict(sorted(monthly.items())),
        **consec,
        'params': params,
    }


def _analyze_consecutive_losses(trades: list, profits: list) -> dict:
    streaks: list = []
    cur: list = []
    for t, p in zip(trades, profits):
        if p < 0:
            cur.append(t)
        else:
            if cur:
                streaks.append(cur[:])
                cur = []
    if cur:
        streaks.append(cur)

    if not streaks:
        return {}

    longest   = max(streaks, key=len)
    hour_dist: dict = defaultdict(int)
    for s in streaks:
        if len(s) >= 2:
            hour_dist[s[0]['open_dt'].hour] += 1

    return {
        'longest_consec_loss_count': len(longest),
        'longest_consec_loss_start': longest[0]['open_dt'].strftime('%Y.%m.%d %H:%M'),
        'longest_consec_loss_hours': [t['open_dt'].hour for t in longest],
        'consec_loss_start_hour_dist': dict(sorted(hour_dist.items())),
    }


# ===== 出力 =====

def print_report(data: dict, params: dict = None):
    sep = '=' * 60
    print(f'\n{sep}')
    print('【バックテスト結果】')
    if params:
        print('【パラメータ】')
        for k, v in params.items():
            print(f'  {k}: {v}')
    print(sep)
    print(f"  純益              : {data.get('net_profit', 'N/A'):>10} JPY")
    print(f"  プロフィットF     : {data.get('profit_factor', 'N/A')}")
    print(f"  最大ドローダウン  : {data.get('max_drawdown', 'N/A'):>10} JPY")
    print(f"  総トレード数      : {data.get('total_trades', 'N/A')}")

    # 月平均取引数: monthly_trades があればそこから、なければ期間で割る
    monthly = data.get('monthly_trades', {})
    if monthly:
        avg_monthly = sum(monthly.values()) / len(monthly)
    else:
        try:
            tf = datetime.strptime(TESTER_FROM, '%Y.%m.%d')
            tt = datetime.strptime(TESTER_TO,   '%Y.%m.%d')
            n_months = (tt.year - tf.year) * 12 + (tt.month - tf.month) + 1
            avg_monthly = (data.get('total_trades', 0) or 0) / n_months if n_months > 0 else None
        except Exception:
            avg_monthly = None
    if avg_monthly is not None:
        print(f"  月平均取引数      : {avg_monthly:>10.1f} 回/月")

    print(f"  勝率              : {data.get('win_rate_pct', 'N/A')}%")
    print(f"  平均勝ち          : {data.get('avg_win', 'N/A'):>10} JPY")
    print(f"  平均負け          : {data.get('avg_loss', 'N/A'):>10} JPY")

    if monthly:
        print('\n【月次取引数】')
        for month, count in monthly.items():
            print(f'  {month}  :  {count:3d} 回')

    if data.get('consec_loss_start_hour_dist'):
        print('\n【連続負け開始時間帯 (サーバー時刻)】')
        for h, cnt in sorted(data['consec_loss_start_hour_dist'].items(),
                             key=lambda x: -x[1])[:5]:
            print(f'  {h:02d}:00〜{h:02d}:59  :  {cnt} 回')

    n = data.get('longest_consec_loss_count')
    if n:
        start = data.get('longest_consec_loss_start', '')
        hours = data.get('longest_consec_loss_hours', [])
        print(f'\n【最長連続負け】{n} 連敗  開始: {start}')
        print(f'  時間帯: {[f"{h:02d}:xx" for h in hours]}')


def save_results(results: list, prefix: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if results:
        csv_path   = RESULTS_DIR / f'{prefix}_{ts}.csv'
        param_keys = list(results[0]['params'].keys())
        met_keys   = ['composite_score', 'net_profit', 'profit_factor', 'max_drawdown',
                      'total_trades', 'win_rate_pct', 'avg_win', 'avg_loss',
                      'longest_consec_loss_count']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=['rank'] + met_keys + param_keys,
                               extrasaction='ignore')
            w.writeheader()
            for i, r in enumerate(results, 1):
                row = {'rank': i}
                row.update({k: r.get(k, '') for k in met_keys})
                row.update(r['params'])
                w.writerow(row)
        log(f'CSV 保存: {csv_path}')

    json_path = RESULTS_DIR / f'{prefix}_{ts}.json'
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                         encoding='utf-8')
    log(f'JSON 保存: {json_path}')
    return json_path


# ===== MT4 テスター直接実行 =====

def _write_ea_ini(params: dict):
    """EA パラメータを MT4 tester/*.ini (XML形式) に書き込む"""
    TESTER_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        '<common>',
        'positions=2',
        f'deposit={TESTER_DEPOSIT}',
        'currency=JPY',
        'fitnes=2',
        'genetic=0',
        '</common>',
        '',
        '<inputs>',
    ]
    for name, val in params.items():
        if name in _INT_EA_PARAMS:
            v = int(val)
            lines += [
                f'{name}={v}',
                f'{name},F=0',
                f'{name},1=0',
                f'{name},2=0',
                f'{name},3=0',
            ]
        else:
            v = float(val)
            lines += [
                f'{name}={v:.8f}',
                f'{name},F=0',
                f'{name},1=0.00000000',
                f'{name},2=0.00000000',
                f'{name},3=0.00000000',
            ]
    lines.append('</inputs>')
    TESTER_INI.write_text('\n'.join(lines), encoding='utf-8')


def _update_tester_config(model: str = TESTER_MODEL_FAST):
    """terminal.ini の [Tester] セクションをバックテスト用設定に更新する"""
    content = TERM_INI.read_text(encoding='utf-8')
    new_section = (
        '[Tester]\n'
        f'Expert={EA_FILE}\n'
        f'Symbol={TESTER_SYMBOL}\n'
        f'Period={TESTER_PERIOD}\n'
        f'Model={model}\n'
        f'FromDate={TESTER_FROM}\n'
        f'ToDate={TESTER_TO}\n'
        'Optimization=0\n'
        f'Report={RPT_NAME}\n'
        'ReplaceReport=1\n'
        'ShutdownTerminal=0\n'
        'VisualChart=0\n'
        f'Spread={TESTER_SPREAD}\n'
    )
    # [Tester] セクション全体を置き換える（次のセクションまたは末尾まで）
    new_content = re.sub(r'\[Tester\].*?(?=\n\[|\Z)', new_section,
                         content, flags=re.DOTALL)
    if '[Tester]' not in content:
        new_content = content + '\n' + new_section
    TERM_INI.write_text(new_content, encoding='utf-8')


def _parse_mt4_report() -> dict:
    """MT4 HTML レポートからメトリクスを抽出して返す"""
    if not RPT_PATH.exists():
        log(f'[警告] レポートが見つかりません: {RPT_PATH}')
        return {}

    # MT4 レポートは UTF-16 LE または UTF-8 で保存される
    for enc in ('utf-16', 'utf-8', 'cp932'):
        try:
            html = RPT_PATH.read_text(encoding=enc, errors='strict')
            break
        except (UnicodeDecodeError, UnicodeError):
            html = ''
    if not html:
        log('[警告] レポートの文字コード読み取りに失敗しました')
        return {}

    # HTMLタグを除いたトークン列を作成
    tokens = [t.strip() for t in re.findall(r'>([^<]+)<', html) if t.strip()]

    def find_next(label: str) -> str:
        """ラベルに一致するトークンの直後の値トークンを返す"""
        for i, t in enumerate(tokens):
            if label.lower() in t.lower() and i + 1 < len(tokens):
                v = tokens[i + 1]
                if re.match(r'^-?[\d,. ]+', v):
                    return v
        return ''

    def to_float(s: str) -> float:
        s = s.replace(',', '').replace(' ', '').strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    pf         = to_float(find_next('Profit Factor'))
    net_profit = to_float(find_next('Total Net Profit'))
    max_dd     = to_float(find_next('Absolute Drawdown'))
    if max_dd == 0:
        max_dd = to_float(find_next('Maximal drawdown'))
    total      = int(to_float(find_next('Total Trades')))

    # 勝率: "38 (25.85%)" のような形式を解析
    wr = 0.0
    avg_win  = 0.0
    avg_loss = 0.0
    for i, t in enumerate(tokens):
        if 'profit trades' in t.lower() and i + 1 < len(tokens):
            m = re.search(r'([\d.]+)\s*%', tokens[i + 1])
            if m:
                wr = float(m.group(1))
        if 'average profit trade' in t.lower() and i + 1 < len(tokens):
            avg_win = to_float(tokens[i + 1])
        if 'average loss trade' in t.lower() and i + 1 < len(tokens):
            avg_loss = to_float(tokens[i + 1])

    return {
        'profit_factor': pf,
        'net_profit':    net_profit,
        'max_drawdown':  max_dd,
        'total_trades':  total,
        'win_rate_pct':  round(wr, 2),
        'avg_win':       avg_win,
        'avg_loss':      avg_loss,
    }



def _cb_select(ctrl, index: int) -> None:
    """ComboBox を SendMessage(CB_SETCURSEL) で直接選択する。

    pywinauto の select() は 64-bit Python + 32-bit MT4 の組み合わせで失敗するため、
    Win32 メッセージを直接送って回避する。
    """
    import win32gui
    CB_SETCURSEL = 0x014E
    result = win32gui.SendMessage(ctrl.handle, CB_SETCURSEL, index, 0)
    if result == -1:
        raise RuntimeError(f'CB_SETCURSEL index={index} failed (CB_ERR)')


def _collect_tester_controls(app) -> dict:
    """テスターパネル内のコントロールをまとめて収集して返す"""
    found = {
        'start':      None,   # Button "スタート"
        'expert':     None,   # ComboBox EA名 (PDX+SAR_0.0.2)
        'period':     None,   # ComboBox 期間 (M15 など)
        'model':      None,   # ComboBox モデル
        'date_start': None,   # SysDateTimePick32 開始日
        'date_end':   None,   # SysDateTimePick32 終了日
    }
    # Period ComboBox に表示されうる文字列（表示中の期間名 or 空文字）
    _PERIOD_STRS = frozenset({
        'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN', '',
        '1', '5', '15', '30', '60', '240', '1440',
    })
    # テスターパネル配下のみ対象とするため "テスター" パネルを先に探す
    tester_root = [None]

    def _find_tester_root(win, depth=0):
        if tester_root[0]:
            return
        try:
            for child in win.children():
                if tester_root[0]:
                    return
                try:
                    title = child.window_text().strip()
                    cls   = child.class_name()
                    if 'テスター' in title or cls == 'Afx:003A0000:b:00010003:00000000:00000000':
                        if 'テスター' in title:
                            tester_root[0] = child
                            return
                    if depth < 4:
                        _find_tester_root(child, depth + 1)
                except Exception:
                    pass
        except Exception:
            pass

    for win in app.windows():
        _find_tester_root(win)

    root = tester_root[0] if tester_root[0] else app.top_window()
    date_picks = []

    def _collect(win, depth=0):
        try:
            for child in win.children():
                try:
                    cls  = child.class_name()
                    text = child.window_text().strip()
                    if cls == 'Button' and text in ('スタート', 'Start') and found['start'] is None:
                        found['start'] = child
                    elif cls == 'ComboBox':
                        if any(kw in text for kw in ('全ティック', 'コントロールポイント', '始値のみ',
                                                      'Every', 'Control', 'Open')):
                            found['model'] = child
                        elif EA_FILE.replace('.ex4', '') in text and found['expert'] is None:
                            found['expert'] = child
                        elif text in _PERIOD_STRS and found['period'] is None:
                            found['period'] = child
                    elif cls == 'SysDateTimePick32':
                        date_picks.append(child)
                    if depth < 5:
                        _collect(child, depth + 1)
                except Exception:
                    pass
        except Exception:
            pass

    _collect(root)

    # 開始日 / 終了日 は出現順に最初と2番目
    if len(date_picks) >= 2:
        found['date_start'] = date_picks[0]
        found['date_end']   = date_picks[1]

    return found


def _configure_and_start_tester(app, model: str) -> bool:
    """
    テスターの期間・モデル・日付を UI から直接設定し、
    スタートボタンを2回 SendMessage(BM_CLICK) で押す。

    BM_CLICK を2回送る理由: 1回目でフォーカスが当たり、
    2回目で実際のテスト開始トリガーになる（MT4の動作）。
    """
    import win32gui
    import win32con
    import time

    PERIOD_INDEX = PERIOD_INDEX_MAP.get(TESTER_PERIOD, 2)

    # モデル: terminal.ini の Model= 値をそのまま ComboBox インデックスとして使う
    try:
        model_index = int(model)
    except ValueError:
        model_index = 1  # デフォルト = Control Points

    ctrl = _collect_tester_controls(app)
    log(f'[MT4] コントロール収集結果: '
        f'start={ctrl["start"] is not None} '
        f'period={ctrl["period"] is not None} '
        f'model={ctrl["model"] is not None} '
        f'date_start={ctrl["date_start"] is not None} '
        f'date_end={ctrl["date_end"] is not None}')

    if ctrl['start'] is None:
        return False

    # 期間を設定
    if ctrl['period'] is not None:
        try:
            _cb_select(ctrl['period'], PERIOD_INDEX)
            time.sleep(0.3)
            log(f'[MT4] 期間設定: インデックス {PERIOD_INDEX}')
        except Exception as e:
            log(f'[MT4] 期間設定スキップ: {e}')

    # モデルを設定
    if ctrl['model'] is not None:
        try:
            _cb_select(ctrl['model'], model_index)
            time.sleep(0.3)
            log(f'[MT4] モデル設定: インデックス {model_index}')
        except Exception as e:
            log(f'[MT4] モデル設定スキップ: {e}')

    # 開始日・終了日を設定
    if ctrl['date_start'] is not None:
        try:
            ctrl['date_start'].set_time(year=int(TESTER_FROM[:4]),
                                        month=int(TESTER_FROM[5:7]),
                                        day=int(TESTER_FROM[8:10]))
            time.sleep(0.3)
            log(f'[MT4] 開始日設定: {TESTER_FROM}')
        except Exception as e:
            log(f'[MT4] 開始日設定スキップ: {e}')

    if ctrl['date_end'] is not None:
        try:
            ctrl['date_end'].set_time(year=int(TESTER_TO[:4]),
                                      month=int(TESTER_TO[5:7]),
                                      day=int(TESTER_TO[8:10]))
            time.sleep(0.3)
            log(f'[MT4] 終了日設定: {TESTER_TO}')
        except Exception as e:
            log(f'[MT4] 終了日設定スキップ: {e}')

    # スタートボタンを SendMessage(BM_CLICK) で1回押す
    hwnd = ctrl['start'].handle
    try:
        win32gui.SendMessage(hwnd, win32con.BM_CLICK, 0, 0)
        log('[MT4] Start クリック (SendMessage BM_CLICK)')
        return True
    except Exception as e:
        log(f'[MT4] BM_CLICK 失敗 ({e})')
        return False


def _dump_mt4_controls(app):
    """デバッグ用: MT4 の全コントロール一覧をログ出力する"""
    def _walk(win, prefix=''):
        try:
            for child in win.children():
                try:
                    cls  = child.class_name()
                    text = child.window_text().strip()
                    log(f'{prefix}[{cls}] "{text}"')
                    _walk(child, prefix + '  ')
                except Exception:
                    pass
        except Exception:
            pass

    for win in app.windows():
        try:
            log(f'[MT4 window] "{win.window_text()}" class={win.class_name()}')
            _walk(win, '  ')
        except Exception as e:
            log(f'[MT4 dump error] {e}')


_TESTER_TOGGLE_CMD: int = 0  # Strategy Tester トグルのメニューコマンドID (0=未取得)


def _find_tester_toggle_cmd(hwnd: int) -> int:
    """MT4 メニューから Strategy Tester のコマンドIDを探して返す (0=見つからず)"""
    import win32gui, win32con
    try:
        menu = win32gui.GetMenu(hwnd)
        if not menu:
            log('[MT4] GetMenu()=NULL (カスタムメニューのため WM_KEYDOWN 方式を使用)')
            return 0
        for i in range(win32gui.GetMenuItemCount(menu)):
            sub = win32gui.GetSubMenu(menu, i)
            if not sub:
                continue
            for j in range(win32gui.GetMenuItemCount(sub)):
                try:
                    cmd_id = win32gui.GetMenuItemID(sub, j)
                    if cmd_id <= 0:
                        continue
                    text = win32gui.GetMenuString(sub, j, win32con.MF_BYPOSITION)
                    if any(kw in text for kw in ('テスター', 'Tester', 'tester', 'Strategy')):
                        log(f'[MT4] Strategy Tester メニューID: {cmd_id}  ("{text}")')
                        return cmd_id
                except Exception:
                    pass
        log('[MT4] Strategy Tester メニュー項目が見つからず → WM_KEYDOWN 方式を使用')
    except Exception as e:
        log(f'[MT4] メニュー探索エラー: {e}')
    return 0


def _toggle_tester_bg(app) -> bool:
    """フォーカスを最小限に抑えて Strategy Tester を開閉する。

    優先順位:
      1. WM_COMMAND (メニューコマンドID) — フォーカス不要
      2. AttachThreadInput + keybd_event (Ctrl+R)
         keybd_event は GetKeyState を更新するため MT4 が確実に Ctrl+R と認識する。
         前面切替は ~50ms のみ。
      3. set_focus + type_keys — 最終フォールバック (フォーカス奪取あり)
    """
    global _TESTER_TOGGLE_CMD
    import win32gui, win32con, win32api, win32process, ctypes, time

    hwnd = app.top_window().handle

    # 方法1: WM_COMMAND (メニューコマンドID)
    if _TESTER_TOGGLE_CMD == 0:
        _TESTER_TOGGLE_CMD = _find_tester_toggle_cmd(hwnd)

    if _TESTER_TOGGLE_CMD != 0:
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, _TESTER_TOGGLE_CMD, 0)
        log(f'[MT4] テスタートグル: WM_COMMAND (id={_TESTER_TOGGLE_CMD})')
        return True

    # 方法2: AttachThreadInput + keybd_event
    # PostMessage(WM_KEYDOWN) は GetKeyState を更新しないため Ctrl+R が届かない。
    # keybd_event はカーネルレベルで GetKeyState を更新するため確実。
    try:
        KEYEVENTF_KEYUP = 0x0002
        VK_R = 0x52

        prev_hwnd  = win32gui.GetForegroundWindow()
        our_tid    = ctypes.windll.kernel32.GetCurrentThreadId()
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]

        # 入力スレッドをアタッチ → SetForegroundWindow が確実に動作する
        # フォアグラウンドスレッドと MT4 スレッドの両方にアタッチする。
        # SetForegroundWindow の呼び出し権限はフォアグラウンドスレッドへの
        # AttachThreadInput によって得られるため、両方が必要。
        fg_tid       = win32process.GetWindowThreadProcessId(prev_hwnd)[0] if prev_hwnd else 0
        attached_fg  = False
        attached_tgt = False
        if our_tid != fg_tid and fg_tid:
            attached_fg  = bool(ctypes.windll.user32.AttachThreadInput(our_tid, fg_tid, True))
        if our_tid != target_tid:
            attached_tgt = bool(ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, True))

        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.08)

        # Ctrl+R を keybd_event で送信 (GetKeyState が "押下中" になる)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(VK_R, 0, 0, 0)
        win32api.keybd_event(VK_R, 0, KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.08)

        # 直前のウィンドウをフォアグラウンドに戻す (AttachThreadInput が有効な間に実行)
        if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
            win32gui.SetForegroundWindow(prev_hwnd)
        time.sleep(0.05)

        if attached_fg:
            ctypes.windll.user32.AttachThreadInput(our_tid, fg_tid, False)
        if attached_tgt:
            ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, False)

        log('[MT4] テスタートグル: AttachThreadInput + keybd_event (Ctrl+R)')
        return True
    except Exception as e:
        log(f'[MT4] keybd_event 失敗: {e}')

    # 方法3: 最終フォールバック (フォーカスを奪う)
    log('[MT4] テスタートグル: フォールバック set_focus + type_keys')
    try:
        _prev_fg_m3 = win32gui.GetForegroundWindow()
        app.top_window().set_focus()
        time.sleep(0.2)
        app.top_window().type_keys('^r')
        _restore_fg(_prev_fg_m3)
        return True
    except Exception as e:
        log(f'[MT4] テスタートグル失敗: {e}')
        return False


def _launch_mt4_session(model: str = TESTER_MODEL_FAST):
    """MT4 を起動してテスター UI の初期設定（期間・モデル・日付）を行い (proc, app) を返す。

    Start は押さない。以降は _run_single_test() で繰り返しテストを実行できる。
    """
    import time
    from pywinauto import Application, Desktop

    _prevent_sleep()
    _update_tester_config(model)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if not MT4_EXE.exists():
        raise FileNotFoundError(f'MT4 が見つかりません: {MT4_EXE}')

    proc = subprocess.Popen([str(MT4_EXE)])
    log(f'[MT4] 起動 (PID={proc.pid})')

    app = None
    for _ in range(40):
        time.sleep(1)
        try:
            for w in Desktop(backend='win32').windows():
                try:
                    title = w.window_text()
                    if ('XMTrading' in title or 'MetaTrader' in title) and w.is_visible():
                        app = Application(backend='win32').connect(handle=w.handle)
                        log(f'[MT4] ウィンドウ検出: "{title}"')
                        break
                except Exception:
                    pass
            if app:
                break
        except Exception:
            pass
    if app is None:
        proc.kill()
        raise RuntimeError('MT4 ウィンドウが見つかりません（起動タイムアウト）')

    time.sleep(15)
    import win32gui, win32con
    _prev_fg_init = win32gui.GetForegroundWindow()
    app.top_window().set_focus()
    time.sleep(0.3)
    _restore_fg(_prev_fg_init)
    time.sleep(0.5)

    # 期間・モデル・日付を設定（Startは押さない）
    PERIOD_INDEX = PERIOD_INDEX_MAP.get(TESTER_PERIOD, 2)
    try:
        model_index = int(model)
    except ValueError:
        model_index = 1

    ctrl = _collect_tester_controls(app)
    log(f'[MT4] コントロール収集: start={ctrl["start"] is not None} '
        f'expert={ctrl["expert"] is not None} '
        f'period={ctrl["period"] is not None} model={ctrl["model"] is not None} '
        f'date_start={ctrl["date_start"] is not None} date_end={ctrl["date_end"] is not None}')

    if ctrl['start'] is None:
        proc.kill()
        raise RuntimeError('テスターパネルが見つかりません')

    for key, fn in [
        ('period',     lambda: _cb_select(ctrl['period'], PERIOD_INDEX)),
        ('model',      lambda: _cb_select(ctrl['model'],  model_index)),
        ('date_start', lambda: ctrl['date_start'].set_time(
            year=int(TESTER_FROM[:4]), month=int(TESTER_FROM[5:7]), day=int(TESTER_FROM[8:10]))),
        ('date_end',   lambda: ctrl['date_end'].set_time(
            year=int(TESTER_TO[:4]), month=int(TESTER_TO[5:7]), day=int(TESTER_TO[8:10]))),
    ]:
        if ctrl[key] is not None:
            try:
                fn()
                time.sleep(0.3)
            except Exception as e:
                log(f'[MT4] {key} 設定スキップ: {e}')

    log('[MT4] セッション準備完了')
    return proc, app


def _restore_fg(saved_hwnd: int) -> None:
    """MT4 がフォアグラウンドを奪った後、saved_hwnd (ユーザーの作業ウインドウ) に戻す。

    現フォアグラウンドのスレッドに AttachThreadInput することで
    SetForegroundWindow の呼び出し権限を取得してから復元する。
    """
    import ctypes, win32gui, win32process
    if not saved_hwnd or not win32gui.IsWindow(saved_hwnd):
        return
    cur = win32gui.GetForegroundWindow()
    if cur == saved_hwnd:
        return
    try:
        our_tid  = ctypes.windll.kernel32.GetCurrentThreadId()
        cur_tid  = win32process.GetWindowThreadProcessId(cur)[0] if cur else 0
        attached = False
        if our_tid != cur_tid and cur_tid:
            attached = bool(ctypes.windll.user32.AttachThreadInput(our_tid, cur_tid, True))
        win32gui.SetForegroundWindow(saved_hwnd)
        if attached:
            ctypes.windll.user32.AttachThreadInput(our_tid, cur_tid, False)
    except Exception:
        pass


def _run_single_test(app, params: dict) -> dict:
    """既存の MT4 セッションで EA パラメータを更新し 1 回テストを実行してメトリクスを返す。

    MT4 は閉じない。連続して呼び出すことで複数評価を同じセッションで実行できる。
    Ctrl+R でテスターを閉じ→再開することで MT4 に ini を確実に再読み込みさせる。
    """
    import time, win32gui, win32con

    zero = {
        'profit_factor': 0.0, 'net_profit': 0.0, 'max_drawdown': 0.0,
        'total_trades': 0, 'win_rate_pct': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0,
        'params': params,
    }

    # 1. EA ini を書き込む
    _write_ea_ini(params)

    # 2. テスターを閉じ→再開 (ini 再読み込みトリガー)
    #    MT4 は ini を「テスターパネルを開くとき」に読み込む。
    #    確実に閉じたことを確認してから開く。
    try:
        # テスターが開いていれば先に閉じる
        if _collect_tester_controls(app)['start'] is not None:
            _toggle_tester_bg(app)
            log('[MT4] テスター閉じる')
            # 実際に閉じたか確認 (最大 3 秒)
            for _chk in range(6):
                time.sleep(0.5)
                if _collect_tester_controls(app)['start'] is None:
                    log('[MT4] テスター閉じる確認')
                    break
            else:
                # トグルが効いていない → set_focus フォールバックで確実に閉じる
                log('[警告] テスターが閉じない → set_focus フォールバックで強制閉じ')
                _prev_fg_fb = win32gui.GetForegroundWindow()
                app.top_window().set_focus()
                time.sleep(0.2)
                app.top_window().type_keys('^r')
                _restore_fg(_prev_fg_fb)
                time.sleep(1.5)

        # テスターを開く (ini 再読み込み)
        _toggle_tester_bg(app)
        log('[MT4] テスター開く → ini 再読み込み')
        time.sleep(3.5)
    except Exception as e:
        log(f'[MT4] テスター再起動スキップ: {e}')

    # 3. Start ボタンが出現するまでポーリングし、期間・モデル・日付を設定
    PERIOD_INDEX = PERIOD_INDEX_MAP.get(TESTER_PERIOD, 2)
    try:
        model_index = int(TESTER_MODEL_FAST)
    except ValueError:
        model_index = 2

    start_hwnd  = None
    _prev_fg_op = win32gui.GetForegroundWindow()  # コントロール操作前のフォーカス保存
    for attempt in range(30):
        ctrl = _collect_tester_controls(app)
        if ctrl['start'] is not None:
            log(f'[MT4] Start ボタン確認 (試行{attempt + 1}回目)')
            for key, fn in [
                ('period',     lambda: _cb_select(ctrl['period'], PERIOD_INDEX)),
                ('model',      lambda: _cb_select(ctrl['model'],  model_index)),
                ('date_start', lambda: ctrl['date_start'].set_time(
                    year=int(TESTER_FROM[:4]), month=int(TESTER_FROM[5:7]), day=int(TESTER_FROM[8:10]))),
                ('date_end',   lambda: ctrl['date_end'].set_time(
                    year=int(TESTER_TO[:4]), month=int(TESTER_TO[5:7]), day=int(TESTER_TO[8:10]))),
            ]:
                if ctrl[key] is not None:
                    try:
                        fn()
                        time.sleep(0.3)
                    except Exception as e:
                        log(f'[MT4] {key} 設定スキップ: {e}')
            # コントロール設定後に MT4 がフォーカスを奪っていた場合は復元
            _restore_fg(_prev_fg_op)
            start_hwnd = ctrl['start'].handle
            break
        time.sleep(1)
        if attempt % 5 == 4:
            log(f'[MT4] Start ボタン待機中... ({attempt + 1}/30)')

    if start_hwnd is None:
        log('[MT4] Start ボタンが見つかりません（30秒タイムアウト）')
        return zero

    # 4. テスト開始前のレポートファイルの更新時刻を記録
    tester_log      = TESTER_DIR / 'logs' / f'{datetime.now().strftime("%Y%m%d")}.log'
    log_size_before = tester_log.stat().st_size if tester_log.exists() else 0
    rpt_mtime_before = RPT_PATH.stat().st_mtime if RPT_PATH.exists() else 0.0

    # 5. Start クリック (BM_CLICK で MT4 がフォーカスを奪うため直後に復元)
    time.sleep(0.5)
    _prev_fg_click = win32gui.GetForegroundWindow()
    win32gui.SendMessage(start_hwnd, win32con.BM_CLICK, 0, 0)
    _restore_fg(_prev_fg_click)
    log('[MT4] Start → テスト実行中...')

    # 6. テスト完了まで待機（ログファイルの変化を監視）
    last_log_size = log_size_before
    last_change_t = time.time()
    test_started  = False
    QUIET_SECS    = 15
    MAX_WAIT      = 300  # 始値のみモードは ~6秒/回

    deadline = time.time() + MAX_WAIT
    while time.time() < deadline:
        cur_size = tester_log.stat().st_size if tester_log.exists() else last_log_size
        if cur_size != last_log_size:
            if not test_started:
                test_started = True
            last_log_size = cur_size
            last_change_t = time.time()
        elif test_started and (time.time() - last_change_t) > QUIET_SECS:
            break
        elif not test_started and (time.time() - last_change_t) > 60:
            log('[警告] テスト未開始 (60秒タイムアウト)')
            return zero
        time.sleep(1)
    else:
        log(f'[警告] テストが {MAX_WAIT} 秒以内に完了しませんでした')
        return zero

    # 7. MT4 が HTML レポートを書き込むまで待機（最大 30 秒）
    log('[MT4] テスト完了 → HTMLレポート待機中...')
    for _ in range(30):
        if RPT_PATH.exists() and RPT_PATH.stat().st_mtime > rpt_mtime_before:
            break
        time.sleep(1)
    else:
        log('[警告] HTMLレポートが更新されませんでした（レポートなしで続行）')

    # 8. MT4 の HTML レポートを解析（自前の損益計算は行わない）
    metrics = _parse_mt4_report()
    if not metrics:
        metrics = {
            'profit_factor': 0.0, 'net_profit': 0.0, 'max_drawdown': 0.0,
            'total_trades': 0, 'win_rate_pct': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0,
        }
    metrics['params'] = params
    log(f'[MT4]   → PF={metrics["profit_factor"]:.4f}  '
        f'純益={metrics["net_profit"]:,.0f}  '
        f'取引={metrics["total_trades"]}')
    return metrics


def _prevent_sleep():
    """最適化中にディスプレイスリープ・システムスリープを抑制する"""
    import ctypes
    ES_CONTINUOUS       = 0x80000000
    ES_SYSTEM_REQUIRED  = 0x00000001  # システムスリープ抑制
    ES_DISPLAY_REQUIRED = 0x00000002  # ディスプレイオフ抑制
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    log('[MT4] スリープ抑制: ON (ディスプレイ・システムスリープを無効化)')


def _restore_sleep():
    """スリープ抑制を解除してOSのデフォルト設定に戻す"""
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    log('[MT4] スリープ抑制: OFF (OS デフォルトに戻す)')


def _close_mt4(proc) -> None:
    """MT4 プロセスを安全に終了する"""
    _restore_sleep()
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


def run_mt4_backtest(params: dict, fast_mode: bool = True) -> dict:
    """スタンドアロン: MT4 を起動→テスト実行→MT4 を開いたまま返す（backtest コマンド用）

    Results/Graph タブを確認できるよう MT4 は終了しない。
    """
    model = TESTER_MODEL_FAST if fast_mode else TESTER_MODEL_PRECISE
    _, app = _launch_mt4_session(model)
    result = _run_single_test(app, params)
    _restore_sleep()
    log('[MT4] バックテスト完了。MT4 は開いたまま終了します（Results/Graph タブを確認してください）')
    return result


# ===== 遺伝的アルゴリズム =====

# GA 設定
GA_POP_SIZE   = 5   # 1世代の個体数  ※動作確認用（本番は50）
GA_N_GEN      = 10    # 最大世代数     ※動作確認用（本番は100）
GA_ELITE_N    = 10   # エリート継承数（上位N体をそのまま次世代へ）
GA_N_CROSS    = 30   # 交叉で生成する個体数
GA_N_RANDOM   = 10   # ランダム新規個体数（多様性維持）
GA_MUT_RATE   = 0.5  # 突然変異率（1遺伝子あたりの変化確率）（デフォルト：0.2）
GA_TOP_PARENT = 20   # 交叉に使う親の候補数（上位N体から選ぶ）
GA_PATIENCE   = 1    # ベスト更新なしがこの世代数続いたら早期終了（デフォルト：8）


def _random_individual(grid: dict) -> dict:
    """grid から各パラメータをランダムに選んで個体を生成する"""
    return {k: random.choice(v) for k, v in grid.items()}


def _crossover(p1: dict, p2: dict) -> dict:
    """一様交叉: 各遺伝子を 50% の確率で p1 か p2 から継承する"""
    return {k: random.choice([p1[k], p2[k]]) for k in p1}


def _mutate(ind: dict, grid: dict, rate: float = GA_MUT_RATE) -> dict:
    """突然変異: 各遺伝子を rate の確率で grid 内のランダム値に置換する"""
    result = dict(ind)
    for k, choices in grid.items():
        if random.random() < rate:
            result[k] = random.choice(choices)
    return result


def _ind_key(ind: dict) -> tuple:
    """個体を辞書順ソートしたタプルに変換（重複検出・キャッシュキー用）"""
    return tuple(sorted(ind.items()))


def _score_results(results: list) -> None:
    """results リストに composite_score を追加する（in-place）。

    純益・トレード数をリスト内の最大値で正規化し、重み付き合計を計算する。
    純益が負のものはスコア 0 として扱う。
    """
    np_vals = [r.get('net_profit', 0) or 0 for r in results]
    tr_vals = [r.get('total_trades', 0) or 0 for r in results]
    max_np  = max((v for v in np_vals if v > 0), default=1.0)
    max_tr  = max(tr_vals, default=1.0) or 1.0
    for r, np_val, tr_val in zip(results, np_vals, tr_vals):
        r['composite_score'] = (SCORE_WEIGHT_NET_PROFIT   * max(np_val, 0) / max_np
                              + SCORE_WEIGHT_TOTAL_TRADES  * tr_val         / max_tr)


def _run_evolve(grid: dict, label: str,
                evaluator=None,
                n_gen: int = GA_N_GEN, pop_size: int = GA_POP_SIZE,
                patience: int = GA_PATIENCE,
                seed_results: list = None) -> list:
    """GA の共通ループ。grid で探索空間を指定し、結果リストを返す。

    evaluator:    params -> dict の評価関数。None の場合は run_mt4_backtest を使う。
    seed_results: チェックポイントから読み込んだ評価済み結果リスト。
                  渡すとキャッシュに引き継ぎ、上位個体を初期集団の種に使う。
    """
    if evaluator is None:
        evaluator = run_mt4_backtest

    elite_n    = GA_ELITE_N
    n_cross    = GA_N_CROSS
    n_random   = pop_size - elite_n - n_cross
    top_parent = GA_TOP_PARENT

    log(f'GA設定: 最大{n_gen}世代 × {pop_size}個体  '
        f'エリート={elite_n}  交叉={n_cross}  ランダム={n_random}  '
        f'突然変異率={GA_MUT_RATE}  early_stop_patience={patience}')

    # チェックポイントがあればキャッシュと初期集団を復元
    cache: dict   = {}
    all_results: list = []
    if seed_results:
        for r in seed_results:
            if 'params' in r:
                cache[_ind_key(r['params'])] = r
        all_results = list(seed_results)
        # 上位 elite_n 体を初期集団の種に、残りはランダムで多様性を確保
        _score_results(seed_results)
        sorted_seeds = sorted(seed_results, key=lambda x: x.get('composite_score', 0), reverse=True)
        seed_inds    = [r['params'] for r in sorted_seeds[:elite_n]]
        population   = seed_inds + [_random_individual(grid) for _ in range(pop_size - len(seed_inds))]
        log(f'チェックポイント引き継ぎ: 評価済み {len(cache)} 件 / 初期種 {len(seed_inds)} 体')
    else:
        population = [_random_individual(grid) for _ in range(pop_size)]

    best_score = -1.0
    no_improve = 0

    for gen in range(1, n_gen + 1):

        # --- 評価 ---
        new_evals = 0
        gen_items: list = []
        for i, ind in enumerate(population):
            key = _ind_key(ind)
            if key not in cache:
                log(f'  [{label}] 第{gen}世代 個体{i+1}/{pop_size} 評価中...')
                data       = evaluator(ind)
                cache[key] = data
                new_evals += 1
            gen_items.append((cache[key], ind))

        # 世代内で正規化してスコア計算 (純益 70% + トレード数 30%)
        np_vals = [d.get('net_profit', 0) or 0 for d, _ in gen_items]
        tr_vals = [d.get('total_trades', 0) or 0 for d, _ in gen_items]
        max_np  = max((v for v in np_vals if v > 0), default=1.0)
        max_tr  = max(tr_vals, default=1.0) or 1.0
        scored  = []
        for (data, ind), np_val, tr_val in zip(gen_items, np_vals, tr_vals):
            score = (SCORE_WEIGHT_NET_PROFIT   * max(np_val, 0) / max_np
                   + SCORE_WEIGHT_TOTAL_TRADES  * tr_val         / max_tr)
            scored.append((score, data, ind))
        scored.sort(key=lambda x: x[0], reverse=True)

        # --- 世代ログ ---
        gen_score = scored[0][0]
        gen_np    = scored[0][1].get('net_profit', 0)
        gen_tr    = scored[0][1].get('total_trades', 0)
        star      = ''
        if gen_score > best_score:
            best_score = gen_score
            no_improve = 0
            star       = '  ★ ベスト更新'
        else:
            no_improve += 1
            star        = f'  (改善なし {no_improve}/{patience})'
        log(f'[{label} 第{gen:2d}世代] スコア={gen_score:.4f}  純益={gen_np:>8,.0f}JPY'
            f'  取引={gen_tr}  新規={new_evals:2d}  累計={len(cache)}{star}')

        all_results.extend([s[1] for s in scored])

        # --- チェックポイント保存 (中断してもここまでの最善が残る) ---
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        # best_params.json を毎世代上書き
        best_params_path = RESULTS_DIR / 'best_params.json'
        best_params_path.write_text(
            json.dumps(scored[0][2], ensure_ascii=False, indent=2),
            encoding='utf-8')
        # ユニーク結果のチェックポイントを保存
        ckpt_seen: set   = set()
        ckpt_unique: list = []
        for r in all_results:
            k = _ind_key(r['params'])
            if k not in ckpt_seen:
                ckpt_seen.add(k)
                ckpt_unique.append(r)
        _score_results(ckpt_unique)
        ckpt_unique.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
        ckpt_path = RESULTS_DIR / f'{label}_checkpoint.json'
        ckpt_path.write_text(
            json.dumps(ckpt_unique, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8')

        if gen == n_gen:
            log(f'[{label}] 最大世代数 ({n_gen}) に到達。終了。')
            break
        if no_improve >= patience:
            log(f'[{label}] {patience}世代連続でベスト未更新 → Early Stopping (第{gen}世代)。')
            break

        # --- 次世代生成 ---
        elites  = [s[2] for s in scored[:elite_n]]
        parents = [s[2] for s in scored[:top_parent]]

        next_pop = list(elites)
        while len(next_pop) < elite_n + n_cross:
            p1, p2 = random.sample(parents, 2)
            next_pop.append(_mutate(_crossover(p1, p2), grid))
        for _ in range(n_random):
            next_pop.append(_random_individual(grid))

        population = next_pop

    # ユニーク化・スコア降順ソート
    seen: set  = set()
    unique: list = []
    for r in all_results:
        k = _ind_key(r['params'])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    _score_results(unique)
    unique.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
    return unique


def cmd_evolve():
    """遺伝的アルゴリズムでパラメータ最適化する（全グリッド対象）

    evolve_checkpoint.json が存在する場合は自動的に引き継いで続きから再開する。
    最初からやり直したい場合は evolve_checkpoint.json を削除してから実行すること。
    """
    # チェックポイントがあれば引き継ぐ
    ckpt_path    = RESULTS_DIR / 'evolve_checkpoint.json'
    seed_results = None
    if ckpt_path.exists():
        try:
            seed_results = json.loads(ckpt_path.read_text(encoding='utf-8'))
            log(f'チェックポイント検出: {len(seed_results)} 件を引き継いで再開します')
            log(f'  (最初からやり直す場合は {ckpt_path} を削除してください)')
        except Exception as e:
            log(f'チェックポイント読み込みスキップ: {e}')

    proc, app = _launch_mt4_session(TESTER_MODEL_FAST)
    try:
        evaluator = lambda p: _run_single_test(app, p)
        unique = _run_evolve(GRID, label='evolve', evaluator=evaluator,
                             seed_results=seed_results)
    finally:
        _close_mt4(proc)

    log(f'\n=== 進化完了: {len(unique)} ユニーク組み合わせを評価 ===')
    print_report(unique[0], unique[0]['params'])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    best_path = RESULTS_DIR / 'best_params.json'
    best_path.write_text(
        json.dumps(unique[0]['params'], ensure_ascii=False, indent=2),
        encoding='utf-8')
    log(f'ベストパラメータ保存: {best_path}')
    save_results(unique, 'evolve_results')


def cmd_refine():
    """ベストパラメータ周辺の絞り込みグリッドで再探索する

    各パラメータを GRID の中でベスト値の前後 ±1 ステップに絞り、
    さらに数値パラメータには中間値を追加して細かく探索する。
    """
    best_path = RESULTS_DIR / 'best_params.json'
    if not best_path.exists():
        log('[エラー] best_params.json が見つかりません。先に evolve を実行してください。')
        sys.exit(1)

    best = json.loads(best_path.read_text(encoding='utf-8'))
    log(f'ベストパラメータ: {best}')

    def neighbors(vals: list, best_val, n: int = 1) -> list:
        """vals の中で best_val に最も近いインデックスの前後 n 個を返す"""
        closest = min(vals, key=lambda x: abs(x - best_val))
        idx = vals.index(closest)
        lo  = max(0, idx - n)
        hi  = min(len(vals) - 1, idx + n)
        return vals[lo:hi+1]

    def midpoints(vals: list) -> list:
        """隣り合う値の中間値を追加して返す"""
        result = list(vals)
        for a, b in zip(vals, vals[1:]):
            mid = round((a + b) / 2, 8)
            if mid not in result:
                result.append(mid)
        return sorted(set(result))

    # ベスト値の ±1 ステップに絞り、数値パラメータは中間値も追加
    numeric_params = {'ADX_Threshold', 'SAR_Step', 'SAR_Max', 'StopLoss', 'TakeProfit'}
    refined: dict = {}
    for k in GRID:
        base = neighbors(GRID[k], best[k], n=1)
        refined[k] = midpoints(base) if k in numeric_params else base

    total = 1
    for v in refined.values():
        total *= len(v)

    log(f'\n絞り込みグリッド ({total} 組み合わせ):')
    for k, v in refined.items():
        log(f'  {k}: {v}')

    # 世代数を増やして細かく探索
    proc, app = _launch_mt4_session(TESTER_MODEL_FAST)
    try:
        evaluator = lambda p: _run_single_test(app, p)
        unique = _run_evolve(refined, label='refine', n_gen=40, patience=15, evaluator=evaluator)
    finally:
        _close_mt4(proc)

    log(f'\n=== 再探索完了: {len(unique)} ユニーク組み合わせを評価 ===')
    print_report(unique[0], unique[0]['params'])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    best_path.write_text(
        json.dumps(unique[0]['params'], ensure_ascii=False, indent=2),
        encoding='utf-8')
    log(f'ベストパラメータ保存: {best_path}')
    save_results(unique, 'refine_results')


def cmd_adaptive():
    """適応型停止パラメータを最適化する（ベストパラメータに重ねて全探索）

    AdaptiveWindow / AdaptivePauseWR / AdaptivePauseHours の
    全組み合わせ (27通り) を試し、ベストパラメータとして保存する。
    """
    best_path = RESULTS_DIR / 'best_params.json'
    if not best_path.exists():
        log('[エラー] best_params.json が見つかりません。先に evolve/refine を実行してください。')
        sys.exit(1)

    base = json.loads(best_path.read_text(encoding='utf-8'))
    log(f'ベースパラメータ: {base}')

    # 適応型停止パラメータの探索グリッド
    adap_grid = {
        'AdaptiveWindow':     [10, 15, 20],              # 監視ウィンドウ (トレード数)
        'AdaptivePauseWR':    [0.15, 0.20, 0.25, 0.30], # 停止トリガー勝率
        'AdaptivePauseHours': [24, 48, 72],              # 停止時間 (時間)
    }

    keys    = list(adap_grid.keys())
    combos  = list(itertools.product(*[adap_grid[k] for k in keys]))
    results = []
    best_pf = -1.0

    log(f'\n適応パラメータ探索: {len(combos)} 組み合わせ (全探索)\n')

    proc, app = _launch_mt4_session(TESTER_MODEL_FAST)
    try:
        evaluator = lambda p: _run_single_test(app, p)

        # ベースライン (適応停止なし) を最初に計算
        baseline = evaluator(base)
        log(f'\n[ベースライン (適応停止なし)]  '
            f'PF={baseline["profit_factor"]:.4f}  '
            f'純益={baseline["net_profit"]:,.0f}JPY')

        for idx, combo in enumerate(combos, 1):
            adap_params = dict(zip(keys, combo))
            params      = {**base, **adap_params}

            data = evaluator(params)
            results.append(data)

            pf   = data.get('profit_factor', 0) or 0
            np_  = data.get('net_profit', 0) or 0
            star = ''
            if pf > best_pf:
                best_pf = pf
                star    = '  ★'
            log(f'[{idx:2d}/{len(combos)}]'
                f'  W={adap_params["AdaptiveWindow"]:2d}'
                f'  WR<{adap_params["AdaptivePauseWR"]:.0%}'
                f'  {adap_params["AdaptivePauseHours"]:2d}h停止'
                f'  →  PF={pf:.4f}  純益={np_:>8,.0f}JPY{star}')
    finally:
        _close_mt4(proc)

    _score_results(results)
    results.sort(key=lambda x: x.get('composite_score', 0), reverse=True)

    log(f'\n=== 適応パラメータ最適化完了 ===')
    log(f'ベースライン比較:')
    log(f'  Before: PF={baseline["profit_factor"]:.4f}  '
        f'純益={baseline["net_profit"]:,.0f}JPY')
    best_r = results[0]
    log(f'  After : PF={best_r["profit_factor"]:.4f}  '
        f'純益={best_r["net_profit"]:,.0f}JPY')
    print_report(best_r, best_r['params'])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    best_path.write_text(
        json.dumps(best_r['params'], ensure_ascii=False, indent=2),
        encoding='utf-8')
    log(f'ベストパラメータ保存: {best_path}')
    save_results(results, 'adaptive_results')


def cmd_atr():
    """ATRフィルターパラメータを最適化する（ベストパラメータに重ねて全探索）

    ATR_Short / ATR_Baseline / ATR_Multiplier の全27通りを試し、
    ベースライン（フィルターなし）と比較してベストを保存する。
    """
    best_path = RESULTS_DIR / 'best_params.json'
    if not best_path.exists():
        log('[エラー] best_params.json が見つかりません。先に adaptive を実行してください。')
        sys.exit(1)

    base = json.loads(best_path.read_text(encoding='utf-8'))
    log(f'ベースパラメータ: {base}')

    atr_grid = {
        'ATR_Short':      [10, 14, 20],     # 短期ATRの計算期間
        'ATR_Baseline':   [50, 100, 200],   # 基準ATRの計算期間
        'ATR_Multiplier': [1.3, 1.5, 2.0],  # 停止閾値 (短期/基準 がこの値を超えたら禁止)
    }

    keys   = list(atr_grid.keys())
    combos = list(itertools.product(*[atr_grid[k] for k in keys]))
    results: list = []
    best_pf = -1.0

    log(f'\nATRフィルター探索: {len(combos)} 組み合わせ (全探索)\n')

    proc, app = _launch_mt4_session(TESTER_MODEL_FAST)
    try:
        evaluator = lambda p: _run_single_test(app, p)

        # ベースライン (ATRフィルターなし) を計算
        baseline = evaluator(base)
        log(f'\n[ベースライン (ATRフィルターなし)]  '
            f'PF={baseline["profit_factor"]:.4f}  '
            f'純益={baseline["net_profit"]:,.0f}JPY  '
            f'取引={baseline["total_trades"]}')

        for idx, combo in enumerate(combos, 1):
            atr_params = dict(zip(keys, combo))
            params     = {**base, **atr_params}

            data = evaluator(params)
            results.append(data)

            pf      = data.get('profit_factor', 0) or 0
            np_     = data.get('net_profit', 0) or 0
            ntrades = data.get('total_trades', 0)
            star    = ''
            if pf > best_pf:
                best_pf = pf
                star    = '  ★'
            log(f'[{idx:2d}/{len(combos)}]'
                f'  S={atr_params["ATR_Short"]:2d}'
                f'  B={atr_params["ATR_Baseline"]:3d}'
                f'  x{atr_params["ATR_Multiplier"]:.1f}'
                f'  →  PF={pf:.4f}  純益={np_:>8,.0f}JPY'
                f'  取引={ntrades:3d}{star}')
    finally:
        _close_mt4(proc)

    _score_results(results)
    results.sort(key=lambda x: x.get('composite_score', 0), reverse=True)

    best_r = results[0]
    log(f'\n=== ATRフィルター最適化完了 ===')
    log(f'ベースライン比較:')
    log(f'  Before: PF={baseline["profit_factor"]:.4f}  '
        f'純益={baseline["net_profit"]:,.0f}JPY  '
        f'取引={baseline["total_trades"]}')
    log(f'  After : PF={best_r["profit_factor"]:.4f}  '
        f'純益={best_r["net_profit"]:,.0f}JPY  '
        f'取引={best_r["total_trades"]}')
    print_report(best_r, best_r['params'])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    best_path.write_text(
        json.dumps(best_r['params'], ensure_ascii=False, indent=2),
        encoding='utf-8')
    log(f'ベストパラメータ保存: {best_path}')
    save_results(results, 'atr_results')


# ===== EA 切り替え =====

def _apply_ea_config(key: str):
    """tools/configs/<key>.json を読み込んでモジュールグローバルに適用する。"""
    global EA_FILE, TESTER_INI, GRID, _INT_EA_PARAMS, RESULTS_DIR, TESTER_SYMBOL, TESTER_PERIOD

    cfg_path = CONFIGS_DIR / f'{key}.json'
    if not cfg_path.exists():
        available = [p.stem for p in sorted(CONFIGS_DIR.glob('*.json'))] \
                    if CONFIGS_DIR.exists() else []
        print(f'[エラー] EA 設定ファイルが見つかりません: {cfg_path}\n'
              f'  利用可能: {", ".join(available) if available else "(なし)"}\n'
              f'  tools/configs/{key}.json を作成してから再実行してください。')
        sys.exit(1)

    cfg            = json.loads(cfg_path.read_text(encoding='utf-8'))
    EA_FILE        = f'{key}.ex4'
    TESTER_INI     = TESTER_DIR / f'{key}.ini'
    GRID           = cfg['grid']
    _INT_EA_PARAMS = set(cfg['int_params'])
    RESULTS_DIR    = Path(__file__).parent / 'results' / key
    TESTER_SYMBOL  = cfg.get('symbol', TESTER_SYMBOL)
    TESTER_PERIOD  = cfg.get('period', TESTER_PERIOD)
    log(f'EA 設定読み込み: {cfg_path.name}  '
        f'({len(GRID)} パラメータ / {TESTER_SYMBOL} M{TESTER_PERIOD} / 結果: {RESULTS_DIR})')


# ===== コマンド =====

def cmd_grid():
    keys       = list(GRID.keys())
    all_combos = list(itertools.product(*[GRID[k] for k in keys]))
    total      = len(all_combos)

    if MAX_SAMPLES and total > MAX_SAMPLES:
        log(f'組み合わせ数 {total} → ランダムサンプリング {MAX_SAMPLES} 件')
        random.seed(42)
        combos = random.sample(all_combos, MAX_SAMPLES)
    else:
        log(f'組み合わせ数: {total}')
        combos = all_combos

    results = []
    best_pf = -1.0

    proc, app = _launch_mt4_session(TESTER_MODEL_FAST)
    try:
        for idx, combo in enumerate(combos, 1):
            params = dict(zip(keys, combo))
            data   = _run_single_test(app, params)
            results.append(data)

            pf   = data.get('profit_factor', 0) or 0
            np_  = data.get('net_profit', 0) or 0
            star = ''
            if pf > best_pf:
                best_pf = pf
                star    = '  ★ 新ベスト'
            log(f'[{idx:3d}/{len(combos)}] PF={pf:.4f}  純益={np_:>8,.0f}JPY'
                f'  取引={data["total_trades"]:3d}{star}')
    finally:
        _close_mt4(proc)

    _score_results(results)
    results.sort(key=lambda x: x.get('composite_score', 0), reverse=True)

    log(f'\n=== グリッドサーチ完了 ({len(results)}/{len(combos)} 件) ===')
    print_report(results[0], results[0]['params'])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    best_path = RESULTS_DIR / 'best_params.json'
    best_path.write_text(
        json.dumps(results[0]['params'], ensure_ascii=False, indent=2),
        encoding='utf-8')
    log(f'ベストパラメータ保存: {best_path}')
    save_results(results, 'grid_results')


def cmd_backtest():
    best_path = RESULTS_DIR / 'best_params.json'
    if not best_path.exists():
        log(f'[エラー] {best_path} が見つかりません。先に evolve を実行してください。')
        sys.exit(1)

    params = json.loads(best_path.read_text(encoding='utf-8'))
    log(f'パラメータ: {params}')

    data = run_mt4_backtest(params, fast_mode=False)  # Control Points: 精度優先
    print_report(data, params)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f'backtest_detail_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                   encoding='utf-8')
    log(f'詳細結果保存: {out}')


# ===== エントリーポイント =====

if __name__ == '__main__':
    import argparse

    _ops = ['grid', 'evolve', 'refine', 'adaptive', 'atr', 'backtest']
    parser = argparse.ArgumentParser(
        description='MT4 EA パラメータ最適化',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '使用例:\n'
            '  python mt4_optimizer.py -o evolve -k PDX+SAR_0.0.2\n'
            '  python mt4_optimizer.py -o grid   -k PDX+SAR_0.0.2\n'
            '  利用可能な EA キー: tools/configs/ 内の .json ファイル名 (拡張子なし)'
        ),
    )
    parser.add_argument(
        '-o', '--operation',
        default='evolve',
        choices=_ops,
        help='実行モード (デフォルト: evolve)',
    )
    parser.add_argument(
        '-k', '--key',
        default=_DEFAULT_EA_KEY,
        help=f'EA キー名 (拡張子なし, デフォルト: {_DEFAULT_EA_KEY})',
    )
    args = parser.parse_args()

    _apply_ea_config(args.key)

    if args.operation == 'grid':
        cmd_grid()
    elif args.operation == 'evolve':
        cmd_evolve()
    elif args.operation == 'refine':
        cmd_refine()
    elif args.operation == 'adaptive':
        cmd_adaptive()
    elif args.operation == 'atr':
        cmd_atr()
    elif args.operation == 'backtest':
        cmd_backtest()
