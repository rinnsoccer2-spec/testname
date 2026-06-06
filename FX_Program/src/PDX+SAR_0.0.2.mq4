//=========================
// ■ 基本設定
//=========================
input double Lots             = 0.01;
input int    Slippage         = 10;

//=========================
// ■ ADX設定
//=========================
input int    ADX_Period       = 18;
input double ADX_Threshold    = 40.0;

//=========================
// ■ SAR設定
//=========================
input double SAR_Step         = 0.025;
input double SAR_Max          = 0.3;
input int    SAR_Min_Trend    = 4;   // 最低連続数

//=========================
// ■ TP / SL
//=========================
input double StopLoss         = 200; // pips (GOLD: 1pip=0.01)
input double TakeProfit       = 1000;

//=========================
// ■ クールダウン
//=========================
input int    CooldownSeconds  = 900;

//=========================
// ■ アダプティブストップ
//=========================
input int    AdaptiveWindow   = 20;  // 判定に使う直近トレード数（0=無効）
input double AdaptivePauseWR  = 0.30; // 勝率がこれを下回ったら一時停止
input int    AdaptivePauseHours = 72; // 停止時間（時間）

//=========================
// ■ ATRフィルター
//=========================
input int    ATR_Short        = 10;  // 短期ATR期間（0=無効）
input int    ATR_Baseline     = 200; // 長期ATR期間
input double ATR_Multiplier   = 1.3; // 短期/長期がこれを超えたら高ボラとみなす

//--------------------------------------------------
// グローバル変数
//--------------------------------------------------
datetime lastExitTime    = 0;
int      lastHistoryTotal = 0;

// アダプティブストップ用
bool     recentResults[];  // true=勝ち, false=負け
int      recentCount     = 0;
datetime adaptResumeTime = 0;

//--------------------------------------------------
int OnInit()
{
   ArrayResize(recentResults, AdaptiveWindow > 0 ? AdaptiveWindow : 1);
   ArrayFill(recentResults, 0, ArraySize(recentResults), false);
   recentCount      = 0;
   adaptResumeTime  = 0;
   lastHistoryTotal = OrdersHistoryTotal();
   lastExitTime     = 0;
   return INIT_SUCCEEDED;
}

//--------------------------------------------------
bool HasOpenPosition()
{
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         if(OrderSymbol() == Symbol()) return true;
   }
   return false;
}

//--------------------------------------------------
// ■ SAR連続チェック
//--------------------------------------------------
bool IsSARTrend(bool isBuy)
{
   int count = 0;
   for(int i = 1; i <= SAR_Min_Trend; i++)
   {
      double sar   = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, i);
      double price = iClose(NULL, PERIOD_M15, i);
      if( isBuy && sar < price) count++;
      if(!isBuy && sar > price) count++;
   }
   return (count == SAR_Min_Trend);
}

//--------------------------------------------------
// ■ ATRフィルター（高ボラ判定）
//--------------------------------------------------
bool IsHighVolatility()
{
   if(ATR_Short <= 0) return false;
   double atrShort = iATR(NULL, PERIOD_M15, ATR_Short,    0);
   double atrBase  = iATR(NULL, PERIOD_M15, ATR_Baseline, 0);
   if(atrBase <= 0) return false;
   return (atrShort / atrBase > ATR_Multiplier);
}

//--------------------------------------------------
// ■ アダプティブストップ更新
//--------------------------------------------------
void UpdateAdaptiveStop(bool isWin)
{
   if(AdaptiveWindow <= 0) return;

   // 古い結果をシフトして末尾に追加
   if(recentCount < AdaptiveWindow)
   {
      recentResults[recentCount] = isWin;
      recentCount++;
   }
   else
   {
      for(int i = 0; i < AdaptiveWindow - 1; i++)
         recentResults[i] = recentResults[i + 1];
      recentResults[AdaptiveWindow - 1] = isWin;
   }

   // ウィンドウが埋まったら勝率チェック
   if(recentCount >= AdaptiveWindow)
   {
      int wins = 0;
      for(int j = 0; j < AdaptiveWindow; j++)
         if(recentResults[j]) wins++;
      double wr = (double)wins / AdaptiveWindow;

      if(wr < AdaptivePauseWR && adaptResumeTime == 0)
      {
         adaptResumeTime = TimeCurrent() + (datetime)(AdaptivePauseHours * 3600);
         recentCount = 0;  // 停止後はリセット
      }
   }
}

//--------------------------------------------------
// ■ 一時停止中か判定
//--------------------------------------------------
bool IsAdaptivePaused()
{
   if(AdaptiveWindow <= 0 || adaptResumeTime == 0) return false;
   if(TimeCurrent() >= adaptResumeTime)
   {
      adaptResumeTime = 0;
      return false;
   }
   return true;
}

//--------------------------------------------------
// ■ ブローカー決済（SL/TP）検出
//--------------------------------------------------
void CheckNewClosedOrders()
{
   int currentTotal = OrdersHistoryTotal();
   if(currentTotal <= lastHistoryTotal)
   {
      lastHistoryTotal = currentTotal;
      return;
   }

   // 新しく追加された履歴を走査
   for(int i = lastHistoryTotal; i < currentTotal; i++)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      if(OrderSymbol() != Symbol()) continue;
      // SL/TP決済のみ対象（手動決済は除外しない）
      bool isWin = (OrderProfit() + OrderSwap() + OrderCommission() > 0);
      UpdateAdaptiveStop(isWin);
      lastExitTime = OrderCloseTime();
   }
   lastHistoryTotal = currentTotal;
}

//--------------------------------------------------
// ■ 決済（SAR反転）
//--------------------------------------------------
void CheckExit()
{
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != Symbol()) continue;

      double sar = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, 0);

      double profit = OrderProfit() + OrderSwap() + OrderCommission();

      if(OrderType() == OP_BUY && sar > Bid)
      {
         if(OrderClose(OrderTicket(), OrderLots(), Bid, Slippage, clrWhite))
         {
            UpdateAdaptiveStop(profit > 0);
            lastExitTime = TimeCurrent();
            lastHistoryTotal = OrdersHistoryTotal();
         }
      }

      if(OrderType() == OP_SELL && sar < Ask)
      {
         if(OrderClose(OrderTicket(), OrderLots(), Ask, Slippage, clrWhite))
         {
            UpdateAdaptiveStop(profit > 0);
            lastExitTime = TimeCurrent();
            lastHistoryTotal = OrdersHistoryTotal();
         }
      }
   }
}

//--------------------------------------------------
void OnTick()
{
   CheckNewClosedOrders();
   CheckExit();

   if(HasOpenPosition()) return;
   if(TimeCurrent() - lastExitTime < CooldownSeconds) return;
   if(IsAdaptivePaused()) return;
   if(IsHighVolatility()) return;

   double adx      = iADX(NULL, PERIOD_M15, ADX_Period, PRICE_CLOSE, MODE_MAIN, 0);
   double adx_prev = iADX(NULL, PERIOD_M15, ADX_Period, PRICE_CLOSE, MODE_MAIN, 1);
   double sar      = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, 0);
   double ask      = Ask;
   double bid      = Bid;

   bool adxStrong = (adx > ADX_Threshold);
   bool adxRising = (adx > adx_prev);

   //=========================
   // ■ 状態表示
   //=========================
   string pauseInfo = adaptResumeTime > 0
      ? "PAUSED until " + TimeToString(adaptResumeTime)
      : "Active";
   double atrShort = ATR_Short > 0 ? iATR(NULL, PERIOD_M15, ATR_Short, 0) : 0;
   double atrBase  = ATR_Short > 0 ? iATR(NULL, PERIOD_M15, ATR_Baseline, 0) : 1;

   Comment(
      "PDX+SAR v0.0.2\n",
      "ADX: ", DoubleToString(adx, 2),
      "  prev: ", DoubleToString(adx_prev, 2), "\n",
      "SAR: ", DoubleToString(sar, 5), "\n",
      "ATR ratio: ", DoubleToString(atrBase > 0 ? atrShort / atrBase : 0, 3), "\n",
      "AdaptStop: ", pauseInfo, "\n",
      "Last Exit: ", TimeToString(lastExitTime)
   );

   //=========================
   // ■ エントリー条件
   //=========================

   // BUY条件
   if(adxStrong && adxRising && sar < bid && IsSARTrend(true))
   {
      int ticketBuy = OrderSend(Symbol(), OP_BUY, Lots, ask, Slippage,
                                ask - StopLoss  * Point,
                                ask + TakeProfit * Point,
                                "PDX BUY", 0, 0, clrBlue);
   }

   // SELL条件
   if(adxStrong && adxRising && sar > ask && IsSARTrend(false))
   {
      int ticketSell = OrderSend(Symbol(), OP_SELL, Lots, bid, Slippage,
                                 bid + StopLoss  * Point,
                                 bid - TakeProfit * Point,
                                 "PDX SELL", 0, 0, clrRed);
   }
}
