//=========================
// Signal_MA.mq4
// MAクロスシグナルインジケーター
// Buffer[0]: 1.0=BUY, -1.0=SELL, 0.0=なし
//=========================
#property indicator_chart_window
#property indicator_buffers 1
#property indicator_color1  clrNONE

//=========================
// ■ 設定
//=========================
input int    MA_Period = 12;
input int    MA_Shift  = 6;
input int    MA_Method = MODE_SMA;    // 0=SMA,1=EMA,2=SMMA,3=LWMA
input int    MA_Price  = PRICE_CLOSE; // 適用価格

//=========================
// ■ バッファ
//=========================
double SignalBuffer[];

//--------------------------------------------------
int OnInit()
{
   SetIndexBuffer(0, SignalBuffer);
   SetIndexLabel(0, "MA Signal");
   SetIndexStyle(0, DRAW_NONE);
   IndicatorShortName("Signal_MA(" + IntegerToString(MA_Period) + "," + IntegerToString(MA_Shift) + ")");
   return INIT_SUCCEEDED;
}

//--------------------------------------------------
int OnCalculate(const int      rates_total,
                const int      prev_calculated,
                const datetime &time[],
                const double   &open[],
                const double   &high[],
                const double   &low[],
                const double   &close[],
                const long     &tick_volume[],
                const long     &volume[],
                const int      &spread[])
{
   // 計算に必要な最低バー数チェック
   if(rates_total < MA_Period + MA_Shift + 2)
      return 0;

   int start = (prev_calculated <= 1) ? 1 : prev_calculated - 1;

   for(int i = start; i < rates_total - 1; i++)
   {
      double ma = iMA(NULL, 0, MA_Period, MA_Shift, MA_Method, MA_Price, i);

      // BUY: 前足がMAをローソク足で上抜け
      if(open[i] < ma && close[i] > ma)
         SignalBuffer[i] = 1.0;
      // SELL: 前足がMAをローソク足で下抜け
      else if(open[i] > ma && close[i] < ma)
         SignalBuffer[i] = -1.0;
      else
         SignalBuffer[i] = 0.0;
   }

   // 最新バーはシグナル未確定のためクリア
   SignalBuffer[rates_total - 1] = 0.0;

   return rates_total;
}
