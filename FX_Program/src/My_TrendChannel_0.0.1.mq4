//+------------------------------------------------------------------+
//|  My_TrendChannel_0.0.1.mq4                                        |
//|  My_Make_Range.mq4 のスイング検出ロジックを流用し、               |
//|  長期間意識されているトレンドライン＋平行チャネルラインを描画      |
//|  終値がN本連続でラインの外側になったら破壊とみなし非表示にする     |
//+------------------------------------------------------------------+
#property indicator_chart_window

// ===== D1 設定 =====
input int   D1_LookbackBars   = 60;
input int   D1_SwingStrength  = 3;
input color D1_TrendColor     = clrYellow;
input color D1_ChannelColor   = clrLightYellow;
input int   D1_TrendWidth     = 3;
input int   D1_ChannelWidth   = 1;

// ===== H4 設定 =====
input int   H4_LookbackBars   = 100;
input int   H4_SwingStrength  = 3;
input color H4_TrendColor     = clrYellow;
input color H4_ChannelColor   = clrLightYellow;
input int   H4_TrendWidth     = 2;
input int   H4_ChannelWidth   = 1;

// ===== H1 設定 =====
input int   H1_LookbackBars   = 200;
input int   H1_SwingStrength  = 3;
input color H1_TrendColor     = clrYellow;
input color H1_ChannelColor   = clrLightYellow;
input int   H1_TrendWidth     = 1;
input int   H1_ChannelWidth   = 1;

// ===== 共通設定 =====
input int    AtrPeriod             = 14;    // タッチ許容誤差算出用ATR期間
input double AtrMultiplier         = 0.2;   // 許容誤差 = ATR * 倍率
input int    TL_MinTouches         = 3;     // 優先採用する最低タッチ数（長期トレンド重視）
input int    TL_FallbackMinTouches = 2;     // 3点で見つからない場合に許容する最低タッチ数
input int    TL_ConfirmBars        = 3;     // 終値が連続でラインの外側になったら破壊とみなす本数
input double TL_BigBreakMultiplier = 3.0;   // 破壊方向へ許容誤差のこの倍数以上終値が離れたら、確認本数を待たず1本で即破壊
input double TL_StaleDistanceMultiplier = 8.0; // 方向を問わず許容誤差のこの倍数以上現在値から離れたら、意味を失ったラインとして削除
input double TL_MinSlopeMultiplier = 2.0;   // ライン端点間の値幅が許容誤差のこの倍数未満ならほぼ水平とみなし不採用（水平レンジはMy_Make_Range側でカバー）
input int    TL_ExtendProbeMultiplier = 10; // ライン確定後、LookbackBarsのこの倍率分だけ過去へ延長し、同じ傾き上に乗る古いタッチを探す（履歴が足りない分は自動的に縮む）

// ===== タッチマーカー設定 =====
input color  TouchMarkerColor      = clrRed; // タッチ箇所の丸印の色
input int    TouchMarkerCode       = 159;    // 丸印のWingdingsコード
input int    TouchMarkerSize       = 1;      // 丸印のサイズ

// ===== スロット定義（時間足×方向） =====
#define SLOT_D1_UP   0
#define SLOT_D1_DOWN 1
#define SLOT_H4_UP   2
#define SLOT_H4_DOWN 3
#define SLOT_H1_UP   4
#define SLOT_H1_DOWN 5
#define SLOT_COUNT   6
#define MAX_EXCLUDED 200  // 破壊済みラインの端点を再利用させないための除外リスト上限

// ===== スロットごとの永続状態 =====
bool     g_tlActive[SLOT_COUNT];
datetime g_tlOldTime[SLOT_COUNT], g_tlNewTime[SLOT_COUNT];
double   g_tlOldPrice[SLOT_COUNT], g_tlNewPrice[SLOT_COUNT];

int      g_tlTouches[SLOT_COUNT];   // 採用したトレンドラインのタッチ数（ツールチップ表示用）

bool     g_chActive[SLOT_COUNT];
bool     g_chRetired[SLOT_COUNT];   // 破壊済みチャネルを再計算させないためのフラグ
double   g_chOffset[SLOT_COUNT];

datetime g_exclTime[SLOT_COUNT][MAX_EXCLUDED];
int      g_exclCount[SLOT_COUNT];

datetime lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { DeleteAllObjects(); }

int OnCalculate(const int rates_total, const int prev_calculated,
                 const datetime &time[], const double &open[],
                 const double &high[], const double &low[], const double &close[],
                 const long &tick_volume[], const long &volume[], const int &spread[])
{
   if(prev_calculated > 0 && Time[0] == lastBarTime) return(rates_total);
   lastBarTime = Time[0];

   DrawTrendChannel(PERIOD_D1, D1_LookbackBars, D1_SwingStrength, D1_TrendColor, D1_TrendWidth, D1_ChannelColor, D1_ChannelWidth, "D1", SLOT_D1_UP, SLOT_D1_DOWN);
   DrawTrendChannel(PERIOD_H4, H4_LookbackBars, H4_SwingStrength, H4_TrendColor, H4_TrendWidth, H4_ChannelColor, H4_ChannelWidth, "H4", SLOT_H4_UP, SLOT_H4_DOWN);
   DrawTrendChannel(PERIOD_H1, H1_LookbackBars, H1_SwingStrength, H1_TrendColor, H1_TrendWidth, H1_ChannelColor, H1_ChannelWidth, "H1", SLOT_H1_UP, SLOT_H1_DOWN);

   return(rates_total);
}

//+------------------------------------------------------------------+
//| 指定タイムフレームでトレンドライン＋チャネルラインを管理する        |
//+------------------------------------------------------------------+
void DrawTrendChannel(int tf, int lookback, int strength, color trendColor, int trendWidth,
                       color channelColor, int channelWidth, string prefix, int slotUp, int slotDown)
{
   int tfBars = iBars(NULL, tf);
   if(tfBars < lookback + strength + 1) return;

   double tolerance = iATR(NULL, tf, AtrPeriod, strength + 1) * AtrMultiplier;
   if(tolerance <= 0) return;

   int    highBars[],   lowBars[];
   double highPrices[], lowPrices[];
   int    highCount = 0, lowCount = 0;
   int    bar;
   double h, l;

   ArrayResize(highBars,   lookback);
   ArrayResize(highPrices, lookback);
   ArrayResize(lowBars,    lookback);
   ArrayResize(lowPrices,  lookback);

   for(bar = strength + 1; bar < lookback; bar++)
   {
      h = iHigh(NULL, tf, bar);
      if(IsSwingHigh(tf, bar, strength, h)) { highBars[highCount] = bar; highPrices[highCount] = h; highCount++; }

      l = iLow(NULL, tf, bar);
      if(IsSwingLow(tf, bar, strength, l))  { lowBars[lowCount] = bar; lowPrices[lowCount] = l; lowCount++; }
   }

   bool   tlActive;
   int    barOld, barNew;
   double priceOld, priceNew;

   // 上昇トレンド: 安値同士を結ぶサポートライン ＋ 高値側チャネル
   ManageTrendLine(slotUp, tf, lookback, strength, tolerance, lowBars, lowPrices, lowCount, true,
                    trendColor, trendWidth, prefix + "_UP_TL",
                    tlActive, barOld, priceOld, barNew, priceNew);
   ManageChannel(slotUp, tf, lookback, strength, tolerance, tlActive, barOld, priceOld, barNew, priceNew,
                 true, channelColor, channelWidth, prefix + "_UP_CH");

   // 下降トレンド: 高値同士を結ぶレジスタンスライン ＋ 安値側チャネル
   ManageTrendLine(slotDown, tf, lookback, strength, tolerance, highBars, highPrices, highCount, false,
                    trendColor, trendWidth, prefix + "_DOWN_TL",
                    tlActive, barOld, priceOld, barNew, priceNew);
   ManageChannel(slotDown, tf, lookback, strength, tolerance, tlActive, barOld, priceOld, barNew, priceNew,
                 false, channelColor, channelWidth, prefix + "_DOWN_CH");
}

//+------------------------------------------------------------------+
//| トレンドラインの状態管理：破壊判定 → 破壊なら削除、未検出なら探索  |
//+------------------------------------------------------------------+
void ManageTrendLine(int slot, int tf, int lookback, int strength, double tolerance,
                      const int &bars[], const double &prices[], int count,
                      bool isSupport, color clr, int width, string objSuffix,
                      bool &activeOut, int &barOldOut, double &priceOldOut, int &barNewOut, double &priceNewOut)
{
   if(g_tlActive[slot] &&
      IsLineBroken(tf, g_tlOldTime[slot], g_tlNewTime[slot], g_tlOldPrice[slot], g_tlNewPrice[slot], !isSupport, TL_ConfirmBars, tolerance))
   {
      DeleteLineAndMarkers(objSuffix); // ライン本体＋タッチ丸印マーカーをまとめて削除
      AddExclusion(slot, g_tlOldTime[slot]);
      AddExclusion(slot, g_tlNewTime[slot]);
      g_tlActive[slot]  = false;
      g_chActive[slot]  = false;
      g_chRetired[slot] = true; // トレンドライン破壊時はチャネルも連動して非表示のまま
   }

   if(!g_tlActive[slot])
   {
      int    barOld, barNew, touches;
      double priceOld, priceNew;
      int    attempts = 0;

      // 見つかった候補が「発見した時点で既に終値でN本破られている(＝過去のスイングだけを
      // 見て選んだら既に価格が突き抜けた後だった)」場合は採用せず、その端点を除外して
      // 次候補を探す。破壊直後に同種の古い線が即座に再描画され続ける不具合を防ぐ。
      while(attempts < MAX_EXCLUDED &&
            FindTrendCandidate(bars, prices, count, tolerance, TL_MinTouches, TL_FallbackMinTouches, tf, slot,
                                barOld, priceOld, barNew, priceNew, touches))
      {
         datetime tOld = iTime(NULL, tf, barOld);
         datetime tNew = iTime(NULL, tf, barNew);

         if(!IsLineBroken(tf, tOld, tNew, priceOld, priceNew, !isSupport, TL_ConfirmBars, tolerance))
         {
            // 見つけた傾きをそのまま延長し、探索窓の外（より過去）にも同じライン上に
            // 乗るタッチがないか確認する。全期間を組み合わせ探索する必要がないので軽い。
            datetime extTimes[];
            double   extPrices[];
            int      extCount;
            int      extBarOld   = barOld;
            double   extPriceOld = priceOld;
            ExtendToOlderTouch(tf, strength, tolerance, lookback * TL_ExtendProbeMultiplier, isSupport,
                                barOld, priceOld, barNew, priceNew,
                                extBarOld, extPriceOld, extTimes, extPrices, extCount);

            // 延長すると傾きが変わるため、延長後のライン自体が現在価格から見て
            // 既に破壊済みでないか必ず再検証する。破壊済みなら延長を採用せず、
            // 検証済みの延長前の2点ラインのままにする。
            datetime extOldTime = iTime(NULL, tf, extBarOld);
            if(extCount > 0 &&
               IsLineBroken(tf, extOldTime, tNew, extPriceOld, priceNew, !isSupport, TL_ConfirmBars, tolerance))
            {
               extBarOld   = barOld;
               extPriceOld = priceOld;
               extOldTime  = tOld;
               extCount    = 0;
               ArrayResize(extTimes,  0);
               ArrayResize(extPrices, 0);
            }

            g_tlActive[slot]   = true;
            g_tlOldTime[slot]  = extOldTime;
            g_tlNewTime[slot]  = tNew;
            g_tlOldPrice[slot] = extPriceOld;
            g_tlNewPrice[slot] = priceNew;
            g_tlTouches[slot]  = touches + extCount;
            g_chActive[slot]   = false;
            g_chRetired[slot]  = false; // 新しいトレンドラインなのでチャネルを再計算可能にする

            datetime touchTimes[];
            double   touchPrices[];
            int      touchCount;
            CollectTouchPoints(tf, bars, prices, count, tolerance, barOld, priceOld, barNew, priceNew,
                                touchTimes, touchPrices, touchCount);

            int allCount = touchCount + extCount;
            datetime allTimes[];
            double   allPrices[];
            ArrayResize(allTimes,  allCount);
            ArrayResize(allPrices, allCount);
            int mi;
            for(mi = 0; mi < touchCount; mi++) { allTimes[mi] = touchTimes[mi]; allPrices[mi] = touchPrices[mi]; }
            for(mi = 0; mi < extCount;   mi++) { allTimes[touchCount + mi] = extTimes[mi]; allPrices[touchCount + mi] = extPrices[mi]; }
            DrawTouchMarkers(objSuffix, allTimes, allPrices, allCount);
            break;
         }

         AddExclusion(slot, tOld);
         AddExclusion(slot, tNew);
         attempts++;
      }
   }

   activeOut = g_tlActive[slot];
   if(!activeOut) return;

   int bo = iBarShift(NULL, tf, g_tlOldTime[slot], false);
   int bn = iBarShift(NULL, tf, g_tlNewTime[slot], false);
   if(bo < 0 || bn < 0)
   {
      DeleteLineAndMarkers(objSuffix);
      g_tlActive[slot] = false;
      activeOut = false;
      return;
   }

   // 診断用: 現在値がラインから許容誤差の何倍離れているか（符号は isSupport 側から見て
   // 正=無事な側、負=破壊方向）をツールチップに出す。破壊されない理由の確認に使う。
   double diagSlope   = (g_tlNewPrice[slot] - g_tlOldPrice[slot]) / (double)(bn - bo);
   double diagLineVal = g_tlOldPrice[slot] + diagSlope * (1 - bo);
   double diagClose1  = iClose(NULL, tf, 1);
   double diagSigned  = isSupport ? (diagClose1 - diagLineVal) : (diagLineVal - diagClose1);
   double diagDistTol = tolerance > 0 ? diagSigned / tolerance : 0;

   string tooltip = objSuffix + " touches=" + IntegerToString(g_tlTouches[slot])
                     + " dist=" + DoubleToString(diagDistTol, 2) + "xTol"
                     + " tol=" + DoubleToString(tolerance, Digits);
   DrawTrendLine(objSuffix, tf, bo, g_tlOldPrice[slot], bn, g_tlNewPrice[slot], clr, width, tooltip);
   CheckRetouch(objSuffix, tf, isSupport, tolerance, bo, g_tlOldPrice[slot], bn, g_tlNewPrice[slot]);
   barOldOut = bo; priceOldOut = g_tlOldPrice[slot];
   barNewOut = bn; priceNewOut = g_tlNewPrice[slot];
}

//+------------------------------------------------------------------+
//| チャネルラインの状態管理：トレンドラインに追従し、破壊されたら     |
//| そのトレンドラインが有効な間は再計算せず非表示のままにする         |
//+------------------------------------------------------------------+
void ManageChannel(int slot, int tf, int lookback, int strength, double tolerance, bool trendActive,
                    int barOld, double priceOld, int barNew, double priceNew,
                    bool useHigh, color clr, int width, string objSuffix)
{
   string name = "MTC_" + objSuffix;

   if(!trendActive)
   {
      if(g_chActive[slot]) ObjectDelete(name);
      g_chActive[slot] = false;
      return;
   }

   if(g_chActive[slot])
   {
      datetime tOld = iTime(NULL, tf, barOld);
      datetime tNew = iTime(NULL, tf, barNew);
      double   chOldPrice = priceOld + g_chOffset[slot];
      double   chNewPrice = priceNew + g_chOffset[slot];

      if(IsLineBroken(tf, tOld, tNew, chOldPrice, chNewPrice, useHigh, TL_ConfirmBars, tolerance))
      {
         ObjectDelete(name);
         g_chActive[slot]  = false;
         g_chRetired[slot] = true;
      }
   }

   if(!g_chActive[slot] && !g_chRetired[slot])
   {
      g_chOffset[slot] = ComputeChannelOffset(tf, lookback, strength, barOld, priceOld, barNew, priceNew, useHigh);
      g_chActive[slot] = true;
   }

   if(!g_chActive[slot]) return;

   string tooltip = objSuffix + " offset=" + DoubleToString(g_chOffset[slot], Digits);
   DrawTrendLine(objSuffix, tf, barOld, priceOld + g_chOffset[slot], barNew, priceNew + g_chOffset[slot], clr, width, tooltip);
}

//+------------------------------------------------------------------+
//| ラインの新しい側のタッチ(newTime)が確定してから現在(1本目)まで、    |
//| 一度でも破壊条件を満たした瞬間がなかったか全区間を走査する。        |
//| 直近の終値だけを見ると、過去に一度割れてその後戻ってきたケースを    |
//| 見逃してしまうため、必ず全区間をチェックする。                     |
//| ・終値がconfirmBars本連続でラインの外側にあれば破壊                |
//|   （ヒゲのみの一時的な超過や、すぐ戻っただましは対象外）           |
//| ・破壊方向へ許容誤差のTL_BigBreakMultiplier倍以上離れた瞬間が       |
//|   1本でもあれば、確認本数を待たず破壊とみなす（明確な逸脱）        |
//| ・方向を問わず許容誤差のTL_StaleDistanceMultiplier倍以上離れた     |
//|   瞬間が1本でもあれば、意味を失った古いラインとして破壊扱いにする   |
//+------------------------------------------------------------------+
bool IsLineBroken(int tf, datetime oldTime, datetime newTime, double oldPrice, double newPrice,
                   bool aboveIsBreak, int confirmBars, double tolerance)
{
   int barOld = iBarShift(NULL, tf, oldTime, false);
   int barNew = iBarShift(NULL, tf, newTime, false);
   if(barOld < 0 || barNew < 0 || barOld == barNew) return true;

   double slope        = (newPrice - oldPrice) / (double)(barNew - barOld);
   double bigBreakDist = tolerance * TL_BigBreakMultiplier;
   double staleDist    = tolerance * TL_StaleDistanceMultiplier;
   int    consecutive  = 0;
   int    bar;
   double lineVal, c, dev;

   for(bar = barNew; bar >= 1; bar--)
   {
      lineVal = oldPrice + slope * (bar - barOld);
      c       = iClose(NULL, tf, bar);

      if(MathAbs(c - lineVal) >= staleDist) return true;

      dev = aboveIsBreak ? (c - lineVal) : (lineVal - c);
      if(dev >= bigBreakDist) return true;

      if(dev > 0)
      {
         consecutive++;
         if(consecutive >= confirmBars) return true;
      }
      else
      {
         consecutive = 0;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| 同種のスイング点集合から、最もタッチ数が多く／長期間意識された     |
//| トレンドラインを探索する（3点優先、なければ2点にフォールバック）   |
//| 破壊済みラインの端点（除外リスト）はアンカー候補から除外する       |
//+------------------------------------------------------------------+
bool FindTrendCandidate(const int &bars[], const double &prices[], int count, double tolerance,
                         int minTouches, int fallbackMinTouches, int tf, int slot,
                         int &outBarOld, double &outPriceOld, int &outBarNew, double &outPriceNew, int &outTouches)
{
   int    bestTouches = 0, bestDuration = -1;
   int    bestBarOld = -1, bestBarNew = -1;
   double bestPriceOld = 0.0, bestPriceNew = 0.0;

   int    fbTouches = 0, fbDuration = -1;
   int    fbBarOld = -1, fbBarNew = -1;
   double fbPriceOld = 0.0, fbPriceNew = 0.0;

   int i, j, k, touches, barMin, barMax, duration;
   double slope, lineVal, priceAtMin, priceAtMax;

   for(i = 0; i < count; i++)
   {
      if(IsExcludedTime(slot, iTime(NULL, tf, bars[i]))) continue;

      for(j = i + 1; j < count; j++)
      {
         if(bars[i] == bars[j]) continue;
         if(IsExcludedTime(slot, iTime(NULL, tf, bars[j]))) continue;

         slope      = (prices[j] - prices[i]) / (double)(bars[j] - bars[i]);
         touches    = 0;
         barMin     = -1;
         barMax     = -1;
         priceAtMin = 0.0;
         priceAtMax = 0.0;

         for(k = 0; k < count; k++)
         {
            lineVal = prices[i] + slope * (bars[k] - bars[i]);
            if(MathAbs(prices[k] - lineVal) <= tolerance)
            {
               touches++;
               if(barMax < 0 || bars[k] > barMax) { barMax = bars[k]; priceAtMax = lineVal; }
               if(barMin < 0 || bars[k] < barMin) { barMin = bars[k]; priceAtMin = lineVal; }
            }
         }

         if(touches < fallbackMinTouches || barMax <= barMin) continue;
         if(MathAbs(priceAtMax - priceAtMin) < tolerance * TL_MinSlopeMultiplier) continue; // ほぼ水平＝レンジなので不採用
         duration = barMax - barMin;

         if(touches >= minTouches &&
            (touches > bestTouches || (touches == bestTouches && duration > bestDuration)))
         {
            bestTouches  = touches;
            bestDuration = duration;
            bestBarOld   = barMax; bestPriceOld = priceAtMax;
            bestBarNew   = barMin; bestPriceNew = priceAtMin;
         }

         if(touches > fbTouches || (touches == fbTouches && duration > fbDuration))
         {
            fbTouches  = touches;
            fbDuration = duration;
            fbBarOld   = barMax; fbPriceOld = priceAtMax;
            fbBarNew   = barMin; fbPriceNew = priceAtMin;
         }
      }
   }

   if(bestBarOld >= 0)
   {
      outBarOld = bestBarOld; outPriceOld = bestPriceOld;
      outBarNew = bestBarNew; outPriceNew = bestPriceNew;
      outTouches = bestTouches;
      return true;
   }
   if(fbBarOld >= 0)
   {
      outBarOld = fbBarOld; outPriceOld = fbPriceOld;
      outBarNew = fbBarNew; outPriceNew = fbPriceNew;
      outTouches = fbTouches;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| 破壊済みラインの端点を記録し、以後のアンカー候補から除外する       |
//+------------------------------------------------------------------+
void AddExclusion(int slot, datetime t)
{
   if(g_exclCount[slot] >= MAX_EXCLUDED) return;
   g_exclTime[slot][g_exclCount[slot]] = t;
   g_exclCount[slot]++;
}

bool IsExcludedTime(int slot, datetime t)
{
   int k;
   for(k = 0; k < g_exclCount[slot]; k++)
      if(g_exclTime[slot][k] == t) return true;
   return false;
}

//+------------------------------------------------------------------+
//| トレンドラインを基準に、反対側の最大乖離点までのオフセットを算出   |
//+------------------------------------------------------------------+
double ComputeChannelOffset(int tf, int lookback, int strength, int barOld, double priceOld,
                             int barNew, double priceNew, bool useHigh)
{
   double slope  = (priceNew - priceOld) / (double)(barNew - barOld);
   double offset = 0.0;
   int    bar;
   double lineVal, dev;

   for(bar = strength + 1; bar < lookback; bar++)
   {
      lineVal = priceOld + slope * (bar - barOld);
      if(useHigh)
      {
         dev = iHigh(NULL, tf, bar) - lineVal;
         if(dev > offset) offset = dev;
      }
      else
      {
         dev = iLow(NULL, tf, bar) - lineVal;
         if(dev < offset) offset = dev;
      }
   }
   return offset;
}

//+------------------------------------------------------------------+
//| 採用したトレンドラインに実際に乗っているスイング点（＝タッチ点）を  |
//| 洗い出す。ラインの端点(barOld/barNew)自体の傾きを基準に判定する    |
//+------------------------------------------------------------------+
void CollectTouchPoints(int tf, const int &bars[], const double &prices[], int count, double tolerance,
                         int barOld, double priceOld, int barNew, double priceNew,
                         datetime &outTimes[], double &outPrices[], int &outCount)
{
   double slope = (priceNew - priceOld) / (double)(barNew - barOld);
   double lineVal;
   int    k;

   ArrayResize(outTimes,  count);
   ArrayResize(outPrices, count);
   outCount = 0;

   for(k = 0; k < count; k++)
   {
      lineVal = priceOld + slope * (bars[k] - barOld);
      if(MathAbs(prices[k] - lineVal) <= tolerance)
      {
         outTimes[outCount]  = iTime(NULL, tf, bars[k]);
         outPrices[outCount] = prices[k];
         outCount++;
      }
   }

   ArrayResize(outTimes,  outCount);
   ArrayResize(outPrices, outCount);
}

//+------------------------------------------------------------------+
//| 採用したラインの傾きをそのまま延長し、探索窓(lookback)の外側      |
//| （より過去）に同じライン上へ乗るスイング高値/安値がないか確認する。 |
//| 組み合わせ探索ではなく1本ずつの走査なので、探索範囲を広げても軽い。  |
//| 見つかった最も古い一致をラインの新しい古い側アンカーとして返す。   |
//| 履歴がprobeBarsに満たない場合は自動的に探索範囲が縮む              |
//+------------------------------------------------------------------+
void ExtendToOlderTouch(int tf, int strength, double tolerance, int probeBars, bool isSupport,
                         int barOld, double priceOld, int barNew, double priceNew,
                         int &outBarOld, double &outPriceOld,
                         datetime &outTimes[], double &outPrices[], int &outCount)
{
   double slope  = (priceNew - priceOld) / (double)(barNew - barOld);
   int    tfBars = iBars(NULL, tf);
   int    maxBar = (int)MathMin((double)probeBars, (double)(tfBars - strength - 1));

   outBarOld   = barOld;
   outPriceOld = priceOld;
   outCount    = 0;
   ArrayResize(outTimes,  0);
   ArrayResize(outPrices, 0);

   if(maxBar <= barOld) return;

   ArrayResize(outTimes,  maxBar - barOld);
   ArrayResize(outPrices, maxBar - barOld);

   int    bar;
   double px, lineVal;
   bool   isSwing;

   for(bar = barOld + 1; bar <= maxBar; bar++)
   {
      lineVal = priceOld + slope * (bar - barOld);
      px      = isSupport ? iLow(NULL, tf, bar) : iHigh(NULL, tf, bar);
      isSwing = isSupport ? IsSwingLow(tf, bar, strength, px) : IsSwingHigh(tf, bar, strength, px);

      if(isSwing && MathAbs(px - lineVal) <= tolerance)
      {
         outTimes[outCount]  = iTime(NULL, tf, bar);
         outPrices[outCount] = px;
         outCount++;
         outBarOld   = bar;     // より過去の一致が見つかるたびに更新し、最終的に最遠を採用
         outPriceOld = lineVal; // 傾きを保つため、ライン上の値をアンカー価格にする
      }
   }

   ArrayResize(outTimes,  outCount);
   ArrayResize(outPrices, outCount);
}

//+------------------------------------------------------------------+
//| タッチ点に赤丸マーカーを作成する                                   |
//+------------------------------------------------------------------+
void DrawTouchMarkers(string objSuffix, const datetime &touchTimes[], const double &touchPrices[], int touchCount)
{
   int    i;
   string name;

   for(i = 0; i < touchCount; i++)
   {
      name = "MTC_" + objSuffix + "_T" + IntegerToString(i);
      if(ObjectFind(name) < 0)
         ObjectCreate(name, OBJ_ARROW, 0, touchTimes[i], touchPrices[i]);
      else
         ObjectMove(name, 0, touchTimes[i], touchPrices[i]);

      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, TouchMarkerCode);
      ObjectSetInteger(0, name, OBJPROP_COLOR, TouchMarkerColor);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, TouchMarkerSize);
   }
}

//+------------------------------------------------------------------+
//| ライン確定後、直近確定足が再びライン付近まで来ていたら              |
//| （＝再タッチ）新たに赤丸マーカーを追加する                         |
//+------------------------------------------------------------------+
void CheckRetouch(string objSuffix, int tf, bool isSupport, double tolerance,
                   int barOld, double priceOld, int barNew, double priceNew)
{
   double slope   = (priceNew - priceOld) / (double)(barNew - barOld);
   double lineVal = priceOld + slope * (1 - barOld);
   double px      = isSupport ? iLow(NULL, tf, 1) : iHigh(NULL, tf, 1);

   if(MathAbs(px - lineVal) > tolerance) return;

   datetime t    = iTime(NULL, tf, 1);
   string   name = "MTC_" + objSuffix + "_RT_" + TimeToString(t, TIME_DATE | TIME_SECONDS);
   if(ObjectFind(name) >= 0) return; // 既にマーク済み

   ObjectCreate(name, OBJ_ARROW, 0, t, px);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, TouchMarkerCode);
   ObjectSetInteger(0, name, OBJPROP_COLOR, TouchMarkerColor);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, TouchMarkerSize);
}

//+------------------------------------------------------------------+
//| トレンドラインと、それに紐づく全タッチマーカーをまとめて削除する    |
//+------------------------------------------------------------------+
void DeleteLineAndMarkers(string objSuffix)
{
   string base = "MTC_" + objSuffix;
   int    idx;
   string objName;

   for(idx = ObjectsTotal() - 1; idx >= 0; idx--)
   {
      objName = ObjectName(idx);
      if(StringFind(objName, base) == 0)
         ObjectDelete(objName);
   }
}

//+------------------------------------------------------------------+
//| トレンド／チャネルラインオブジェクトを作成・更新（未来方向へレイ延長）|
//+------------------------------------------------------------------+
void DrawTrendLine(string objSuffix, int tf, int barOld, double priceOld, int barNew, double priceNew,
                    color clr, int width, string tooltip)
{
   string   name    = "MTC_" + objSuffix;
   datetime timeOld = iTime(NULL, tf, barOld);
   datetime timeNew = iTime(NULL, tf, barNew);

   if(ObjectFind(name) < 0)
      ObjectCreate(name, OBJ_TREND, 0, timeOld, priceOld, timeNew, priceNew);
   else
   {
      ObjectMove(name, 0, timeOld, priceOld);
      ObjectMove(name, 1, timeNew, priceNew);
   }

   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, true);
   ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);
}

//+------------------------------------------------------------------+
bool IsSwingHigh(int tf, int bar, int strength, double h)
{
   int k;
   for(k = 1; k <= strength; k++)
   {
      if(bar - k < 0 || iHigh(NULL, tf, bar - k) >= h) return false;
      if(iHigh(NULL, tf, bar + k) >= h) return false;
   }
   return true;
}

bool IsSwingLow(int tf, int bar, int strength, double l)
{
   int k;
   for(k = 1; k <= strength; k++)
   {
      if(bar - k < 0 || iLow(NULL, tf, bar - k) <= l) return false;
      if(iLow(NULL, tf, bar + k) <= l) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
void DeleteAllObjects()
{
   int idx;
   for(idx = ObjectsTotal() - 1; idx >= 0; idx--)
      if(StringFind(ObjectName(idx), "MTC_") == 0)
         ObjectDelete(ObjectName(idx));
}
//+------------------------------------------------------------------+
