//=========================
// ■ 基本設定
//=========================
input double Lots = 0.01;
input int Slippage = 10;

//=========================
// ■ ADX設定
//=========================
input int ADX_Period = 14;
input double ADX_Threshold = 38.0;

//=========================
// ■ SAR設定
//=========================
input double SAR_Step = 0.02;
input double SAR_Max = 0.2;
input int SAR_Min_Trend = 3; // 最低連続数

//=========================
// ■ TP / SL
//=========================
input double StopLoss = 25;
input double TakeProfit = 25;

//=========================
// ■ クールダウン
//=========================
input int CooldownSeconds = 600;

datetime lastExitTime = 0;

//--------------------------------------------------
bool HasOpenPosition()
{
   for(int i=0;i<OrdersTotal();i++)
   {
      if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES))
      {
         if(OrderSymbol()==Symbol()) return true;
      }
   }
   return false;
}

//--------------------------------------------------
// ■ SAR連続チェック
//--------------------------------------------------
bool IsSARTrend(bool isBuy)
{
   int count = 0;

   for(int i=1; i<=SAR_Min_Trend; i++)
   {
      double sar = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, i);
      double price = iClose(NULL, PERIOD_M15, i);

      if(isBuy && sar < price) count++;
      if(!isBuy && sar > price) count++;
   }

   return (count == SAR_Min_Trend);
}

//--------------------------------------------------
// ■ 決済（SAR反転）
 //--------------------------------------------------
void CheckExit()
{
   for(int i=0;i<OrdersTotal();i++)
   {
      if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES))
      {
         if(OrderSymbol()!=Symbol()) continue;

         double sar = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, 0);

         if(OrderType()==OP_BUY && sar > Bid)
         {
            if(OrderClose(OrderTicket(),OrderLots(),Bid,Slippage,clrWhite))
               lastExitTime = TimeCurrent();
         }

         if(OrderType()==OP_SELL && sar < Ask)
         {
            if(OrderClose(OrderTicket(),OrderLots(),Ask,Slippage,clrWhite))
               lastExitTime = TimeCurrent();
         }
      }
   }
}

//--------------------------------------------------
void OnTick()
{
   CheckExit();

   if(HasOpenPosition()) return;
   if(TimeCurrent() - lastExitTime < CooldownSeconds) return;

   double adx = iADX(NULL, PERIOD_M15, ADX_Period, PRICE_CLOSE, MODE_MAIN, 0);
   double adx_prev = iADX(NULL, PERIOD_M15, ADX_Period, PRICE_CLOSE, MODE_MAIN, 1);

   double sar = iSAR(NULL, PERIOD_M15, SAR_Step, SAR_Max, 0);

   double ask = Ask;
   double bid = Bid;

   //=========================
   // ■ 状態表示
   //=========================
   Comment(
   "PDX EA\n",
   "ADX:", DoubleToString(adx,2), "\n",
   "ADX prev:", DoubleToString(adx_prev,2), "\n",
   "SAR:", DoubleToString(sar,5), "\n",
   "Last Exit:", TimeToString(lastExitTime)
   );

   //=========================
   // ■ エントリー条件
   //=========================

   bool adxStrong = (adx > ADX_Threshold);
   bool adxRising = (adx > adx_prev);

   // BUY条件
   if(adxStrong && adxRising && sar < bid && IsSARTrend(true))
   {
      OrderSend(Symbol(), OP_BUY, Lots, ask, Slippage,
                ask - StopLoss * Point,
                ask + TakeProfit * Point,
                "PDX BUY", 0, 0, clrBlue);
   }

   // SELL条件
   if(adxStrong && adxRising && sar > ask && IsSARTrend(false))
   {
      OrderSend(Symbol(), OP_SELL, Lots, bid, Slippage,
                bid + StopLoss * Point,
                bid - TakeProfit * Point,
                "PDX SELL", 0, 0, clrRed);
   }
}