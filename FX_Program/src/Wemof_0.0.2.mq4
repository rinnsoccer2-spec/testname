//+------------------------------------------------------------------+
//|  Wemof_0.0.2.mq4                                                 |
//|  Wemof (Wakkyai's Entry Model of FX) — 逆張りスキャルピング EA   |
//|  対象: USDJPY M5                                                  |
//|  0.0.2 変更点:                                                    |
//|    - 全ティック判定（5分足完成前エントリー対応）                    |
//|    - BBバンド幅フィルター追加 (BBWidthMax)                         |
//|    - 現在バー変動幅比率フィルター追加 (MoveRatioMax)               |
//+------------------------------------------------------------------+
#property strict
#property copyright "Wemof_0.0.2"
#property version   "0.02"

// ===== 入力パラメータ =====
extern double Lots            = 0.01;   // ロット数
extern int    Slippage        = 10;     // スリッページ (points)

extern int    BB_Period       = 20;     // ボリンジャーバンド期間
// BB 偏差は 3σ 固定（Wemof の定義）

extern int    PurityWindow    = 10;     // 純度判定: 直近 N 本
extern double PurityThreshold = 80.0;  // 純度閾値 (%)
extern double PurityWickRatio = 1.0;   // 逆ヒゲ許容率 (0.0=逆ヒゲ禁止 / 1.0=ヒゲ無視)

extern int    TakeProfit      = 3;      // 利確 (pips)
extern int    StopLoss        = 20;     // 損切り (pips)
extern int    CooldownSeconds = 60;     // エントリー後の再エントリー抑制 (秒)

extern int    BBWidthMax      = 30;     // BBバンド幅上限 (pips, 0=フィルター無効)
// バンド幅 = 上限 - 下限。値が大きい = ボラティリティ高 = トレンド相場。

extern double MoveRatioMax    = 1.0;   // 直前確定足の変動幅に対する現在バー変動幅の比率上限
// 例: 直前足が20pips・比率1.0 → 現在バーの変動が20pips以下のみエントリー許可

// TODO: 除外条件フィルター（経済指標・ニュース・重要サポレジ・ラウンドナンバー）
// TODO: 天底紐理論による利確ターゲット計算

// ===== 定数 =====
#define MAGIC          20250002
#define BB_DEVIATION   3.0
#define MAX_MOVE_PIPS  50.0   // 現在バーの変動がこの値(pips)以上なら常にエントリー禁止

// ===== 状態変数 =====
datetime g_lastTradeTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
    // クールダウン中はスキップ
    if (TimeCurrent() - g_lastTradeTime < CooldownSeconds) return;

    // 未決済ポジションがあればスキップ
    if (CountPositions() > 0) return;

    // ----- pip サイズ計算 -----
    int    digits  = (int)MarketInfo(Symbol(), MODE_DIGITS);
    double pipSize = Point * (digits == 3 || digits == 5 ? 10.0 : 1.0);

    // ----- インジケーター取得（index=0: 現在バーのリアルタイム BB） -----
    // 5分足完成前エントリーのため、現在価格が現在バーの BB を超えているか判定する
    double bbUpper = iBands(NULL, PERIOD_M5, BB_Period, BB_DEVIATION, 0, PRICE_CLOSE, MODE_UPPER, 0);
    double bbLower = iBands(NULL, PERIOD_M5, BB_Period, BB_DEVIATION, 0, PRICE_CLOSE, MODE_LOWER, 0);

    // ----- BBバンド幅フィルター -----
    double bbWidthPips = (bbUpper - bbLower) / pipSize;
    if (BBWidthMax > 0 && bbWidthPips > BBWidthMax) return;

    // ----- 現在バー変動幅フィルター -----
    double prevRangePips = (iHigh(NULL, PERIOD_M5, 1) - iLow(NULL, PERIOD_M5, 1)) / pipSize;
    double curMovePips   = MathAbs(Bid - iOpen(NULL, PERIOD_M5, 0)) / pipSize;

    // 現在バーの変動が 50pips 以上はエントリー禁止
    if (curMovePips >= MAX_MOVE_PIPS) return;

    // 直前確定足の変動幅に対する現在バー変動の比率チェック
    if (prevRangePips > 0.0 && curMovePips / prevRangePips > MoveRatioMax) return;

    // ----- 純度計算（確定足ベース） -----
    double upPurity   = CalcPurity(PurityWindow, true);   // 陽線比率 (%)
    double downPurity = CalcPurity(PurityWindow, false);  // 陰線比率 (%)

    // ----- TP/SL 計算 -----
    double tpPts = TakeProfit * pipSize;
    double slPts = StopLoss   * pipSize;

    // ----- ショートエントリー: 現在 Bid ≥ +3σ かつ 純度条件 -----
    if (Bid >= bbUpper && upPurity >= PurityThreshold)
    {
        double entry = Bid;
        double sl    = NormalizeDouble(entry + slPts, digits);
        double tp    = NormalizeDouble(entry - tpPts, digits);
        int ticket = OrderSend(Symbol(), OP_SELL, Lots, entry, Slippage,
                               sl, tp, "Wemof", MAGIC, 0, clrRed);
        if (ticket > 0) g_lastTradeTime = TimeCurrent();
    }
    // ----- ロングエントリー: 現在 Ask ≤ -3σ かつ 純度条件 -----
    else if (Ask <= bbLower && downPurity >= PurityThreshold)
    {
        double entry = Ask;
        double sl    = NormalizeDouble(entry - slPts, digits);
        double tp    = NormalizeDouble(entry + tpPts, digits);
        int ticket = OrderSend(Symbol(), OP_BUY, Lots, entry, Slippage,
                               sl, tp, "Wemof", MAGIC, 0, clrBlue);
        if (ticket > 0) g_lastTradeTime = TimeCurrent();
    }
}

//+------------------------------------------------------------------+
// 純度計算: 直近 n 本の純粋な陽線/陰線の比率 (%)
//
// isBull=true  (上昇純度): close > open かつ 上ヒゲ/range <= PurityWickRatio
// isBull=false (下落純度): close < open かつ 下ヒゲ/range <= PurityWickRatio
//
// PurityWickRatio=0.0 → 逆ヒゲゼロのローソク足のみカウント（記事の元定義）
// PurityWickRatio=1.0 → ヒゲを無視、方向だけで判定（旧動作）
double CalcPurity(int n, bool isBull)
{
    int count = 0;
    for (int i = 1; i <= n; i++)
    {
        double o     = iOpen(NULL, PERIOD_M5, i);
        double h     = iHigh(NULL, PERIOD_M5, i);
        double l     = iLow(NULL, PERIOD_M5, i);
        double c     = iClose(NULL, PERIOD_M5, i);
        double range = h - l;

        if (isBull)
        {
            if (c <= o) continue;
            // 上ヒゲ（売り方の抵抗）が range に占める割合を確認
            if (range > 0 && (h - c) / range > PurityWickRatio) continue;
            count++;
        }
        else
        {
            if (c >= o) continue;
            // 下ヒゲ（買い方の抵抗）が range に占める割合を確認
            if (range > 0 && (c - l) / range > PurityWickRatio) continue;
            count++;
        }
    }
    return (double)count / n * 100.0;
}

//+------------------------------------------------------------------+
// 自 EA のポジション数をカウント
int CountPositions()
{
    int count = 0;
    for (int i = 0; i < OrdersTotal(); i++)
    {
        if (OrderSelect(i, SELECT_BY_POS, MODE_TRADES) &&
            OrderSymbol()      == Symbol() &&
            OrderMagicNumber() == MAGIC)
            count++;
    }
    return count;
}
