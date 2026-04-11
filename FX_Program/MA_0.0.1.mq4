

//=========================
// ■ 基本設定
//=========================
input double Lots = 0.01;
input int MAPeriod = 20;

//=========================
// ■ 傾き関連
//=========================
input int H1_SlopeBars = 18;
input double H1_AngleThreshold = 4.5;   // H1傾きしきい値

input int M5_Lookback = 2.75;               // M5傾き比較バー数
input double M5_AngleThreshold = 2.6;    // M5傾きしきい値

//=========================
// ■ ボリンジャーバンド
//=========================
input int BB_Period = 20;
input double BB_Deviation = 1.42857142857143;

//=========================
// ■ 決済ロジック
//=========================
input int ExitMAPeriod = 8;              // 決済用平均本数

//=========================
// ■ リスク管理
//=========================
input double StopLoss = 11;
input int Slippage = 10;

//=========================
// ■ クールダウン
//=========================
input int CooldownSeconds = 630;

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
double M5_LastN_Close_Avg(int N)
{
double sum = 0;
for(int i=0; i<N; i++)
sum += iClose(NULL, PERIOD_M5, i);
return sum / N;
}

//--------------------------------------------------
void CheckExit()
{
double ma_m5 = M5_LastN_Close_Avg(ExitMAPeriod);

for(int i=0;i<OrdersTotal();i++)
{
if(OrderSelect(i,SELECT_BY_POS,MODE_TRADES))
{
if(OrderSymbol()!=Symbol()) continue;

if(OrderType()==OP_BUY && Bid <= ma_m5)
{
if(OrderClose(OrderTicket(),OrderLots(),Bid,Slippage,clrWhite))
lastExitTime = TimeCurrent();
}

if(OrderType()==OP_SELL && Ask >= ma_m5)
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

double ask = Ask;
double bid = Bid;

//=========================
// ■ H1 傾き
//=========================
double ma_h1_now  = iMA(NULL,PERIOD_H1,MAPeriod,0,MODE_EMA,PRICE_CLOSE,0);
double ma_h1_past = iMA(NULL,PERIOD_H1,MAPeriod,0,MODE_EMA,PRICE_CLOSE,H1_SlopeBars);
double slope_h1 = (ma_h1_now - ma_h1_past)/H1_SlopeBars;

//=========================
// ■ M5 傾き
//=========================
double ma_m5_now  = iMA(NULL,PERIOD_M5,MAPeriod,0,MODE_EMA,PRICE_CLOSE,0);
double ma_m5_past = iMA(NULL,PERIOD_M5,MAPeriod,0,MODE_EMA,PRICE_CLOSE,M5_Lookback);
double slope_m5 = (ma_m5_now - ma_m5_past)/M5_Lookback;

//=========================
// ■ ボリンジャーバンド
//=========================
double bb_upper = iBands(NULL, PERIOD_M5, BB_Period, BB_Deviation, 0, PRICE_CLOSE, MODE_UPPER, 0);
double bb_lower = iBands(NULL, PERIOD_M5, BB_Period, BB_Deviation, 0, PRICE_CLOSE, MODE_LOWER, 0);

//=========================
// ■ 状態表示
//=========================
Comment(
"Multi TF MA EA\n",
"H1 slope:",DoubleToString(slope_h1,5),"\n",
"M5 slope:",DoubleToString(slope_m5,5),"\n",
"Last Exit:",TimeToString(lastExitTime),"\n",
"Exit MA:",DoubleToString(M5_LastN_Close_Avg(ExitMAPeriod),5),"\n",
"BB Upper:",DoubleToString(bb_upper,5),"\n",
"BB Lower:",DoubleToString(bb_lower,5)
);

bool priceInsideBB = (Bid <= bb_upper && Ask >= bb_lower);

//=========================
// ■ エントリー
//=========================
if(slope_h1 > H1_AngleThreshold && slope_m5 > M5_AngleThreshold && priceInsideBB)
OrderSend(Symbol(),OP_BUY,Lots,ask,Slippage,ask-StopLoss,0,"BUY",0,0,clrBlue);

if(slope_h1 < -H1_AngleThreshold && slope_m5 < -M5_AngleThreshold && priceInsideBB)
OrderSend(Symbol(),OP_SELL,Lots,bid,Slippage,bid+StopLoss,0,"SELL",0,0,clrRed);
}
