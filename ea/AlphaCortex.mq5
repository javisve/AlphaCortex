//+------------------------------------------------------------------+
//|                                          AlphaCortex.mq5         |
//|                                              Javier Sobrino Vega |
//|                                                                  |
//|  Multi-symbol AI-driven portfolio manager.                       |
//|  Polls a backend AI service every N minutes via WebRequest.      |
//|  Executes trades across multiple symbols from a single chart.    |
//+------------------------------------------------------------------+
#property copyright "Javier Sobrino Vega"
#property version "1.00"
#property strict

#include <Trade\SymbolInfo.mqh>
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Backend Connection ===" input string InpBackendUrl =
    "http://127.0.0.1:8000";    // Backend URL
input string InpApiKey = "";    // API Key
input int InpPollMinutes = 240; // Poll interval (minutes)
input int InpTimeoutMs = 15000; // Request timeout (ms)

input group "=== Portfolio Risk ===" input int InpMaxPositions =
    8;                                 // Max simultaneous positions
input double InpMaxMarginPct = 50.0;   // Max % of free margin to use
input double InpMaxPerAssetPct = 15.0; // Max % allocation per asset
input double InpMaxDailyDDPct = 5.0;   // Max daily drawdown % before halt

input group "=== Order Management ===" input double InpDefaultSLPct =
    5.0;                             // Default SL % from entry (if AI omits)
input double InpDefaultTPPct = 10.0; // Default TP % from entry (if AI omits)
input int InpMagicNumber = 777001;

//+------------------------------------------------------------------+
//| Structs                                                          |
//+------------------------------------------------------------------+
struct AssetDecision {
  string symbol;
  string action; // BUY, SELL, HOLD, CLOSE
  double weight;
  double sl_pct;
  double tp_pct;
};

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade g_trade;
CSymbolInfo g_symInfo;

int g_pollSeconds;
datetime g_lastPollTime = 0;
datetime g_nextPollTime = 0;
double g_startDayEquity = 0;
bool g_haltedDueToDD = false;
string g_lastRegime = "NEUTRAL";
double g_cashTarget = 0.5;
int g_lastEvalId = 0;
string g_statusMsg = "Initializing...";

//+------------------------------------------------------------------+
//| EA Initialization                                                |
//+------------------------------------------------------------------+
int OnInit() {
  g_trade.SetExpertMagicNumber(InpMagicNumber);
  g_trade.SetDeviationInPoints(20);
  // Do not hardcode IOC, let CTrade auto-detect the allowed filling mode

  g_pollSeconds = InpPollMinutes * 60;
  g_startDayEquity = AccountInfoDouble(ACCOUNT_EQUITY);

  // Start timer — fires every 5s for dashboard updates; we handle poll interval
  // internally
  EventSetTimer(5);

  PrintFormat("[AlphaCortex] Started. Poll every %d min. Backend: %s",
              InpPollMinutes, InpBackendUrl);
  g_statusMsg = "Waiting for first poll...";

  return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| EA Deinitialization                                              |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
  EventKillTimer();
  Comment("");
}

//+------------------------------------------------------------------+
//| OnTick — only for managing existing positions (SL/TP already set)|
//+------------------------------------------------------------------+
void OnTick() {
  CheckDailyDrawdown();
  UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Check if US Market is open (16:30 - 23:00 Server Time, Mon-Fri)  |
//+------------------------------------------------------------------+
bool IsMarketOpen() {
  MqlDateTime dt;
  TimeToStruct(TimeCurrent(), dt);

  // 0 = Sunday, 6 = Saturday
  if (dt.day_of_week == 0 || dt.day_of_week == 6)
    return false;

  int currentMinutes = dt.hour * 60 + dt.min;
  int startMinutes = 16 * 60 + 30; // 16:30
  int endMinutes = 23 * 60;        // 23:00

  if (currentMinutes < startMinutes || currentMinutes >= endMinutes)
    return false;

  return true;
}

//+------------------------------------------------------------------+
//| OnTimer — main poll loop                                         |
//+------------------------------------------------------------------+
void OnTimer() {
  CheckDailyDrawdown();

  // Reset daily equity baseline at 00:00 server time
  MqlDateTime dt;
  TimeToStruct(TimeCurrent(), dt);
  if (dt.hour == 0 && dt.min < 2)
    g_startDayEquity = AccountInfoDouble(ACCOUNT_EQUITY);

  if (g_haltedDueToDD) {
    g_statusMsg = "⛔ HALTED — Max daily drawdown reached";
    UpdateDashboard();
    return;
  }

  // Check if it's time to poll
  if (TimeCurrent() < g_nextPollTime) {
    UpdateDashboard();
    return;
  }

  // Check market hours before spending AI quota
  if (!IsMarketOpen()) {
    g_statusMsg = "💤 Market Closed (Waiting 16:30-23:00)";
    UpdateDashboard();
    return;
  }

  g_statusMsg = "📡 Polling backend...";
  UpdateDashboard();

  string payload = BuildRequestPayload();
  string response = CallBackend(payload);

  if (response == "") {
    g_statusMsg = "⚠️ Backend unreachable — keeping current positions";
    UpdateDashboard();
    g_nextPollTime = TimeCurrent() + g_pollSeconds;
    return;
  }

  ProcessBackendResponse(response);
  g_lastPollTime = TimeCurrent();
  g_nextPollTime = TimeCurrent() + g_pollSeconds;

  UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Build JSON payload for the backend                               |
//+------------------------------------------------------------------+
string BuildRequestPayload() {
  double balance = AccountInfoDouble(ACCOUNT_BALANCE);
  double equity = AccountInfoDouble(ACCOUNT_EQUITY);
  double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
  double usedMargin = AccountInfoDouble(ACCOUNT_MARGIN);
  string currency = AccountInfoString(ACCOUNT_CURRENCY);

  string json = "{";
  json += "\"account\":{";
  json += "\"balance\":" + DoubleToString(balance, 2) + ",";
  json += "\"equity\":" + DoubleToString(equity, 2) + ",";
  json += "\"margin_free\":" + DoubleToString(freeMargin, 2) + ",";
  json += "\"margin_used\":" + DoubleToString(usedMargin, 2) + ",";
  json += "\"currency\":\"" + currency + "\"";
  json += "},";

  // Open positions
  json += "\"positions\":[";
  bool firstPos = true;
  for (int i = PositionsTotal() - 1; i >= 0; i--) {
    if (!PositionSelectByTicket(PositionGetTicket(i)))
      continue;
    if (PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
      continue;

    string sym = PositionGetString(POSITION_SYMBOL);
    int pType = (int)PositionGetInteger(POSITION_TYPE);
    double lots = PositionGetDouble(POSITION_VOLUME);
    double profit = PositionGetDouble(POSITION_PROFIT);
    double oprice = PositionGetDouble(POSITION_PRICE_OPEN);
    double margin = 0;
    if (!OrderCalcMargin(pType == POSITION_TYPE_BUY ? ORDER_TYPE_BUY
                                                    : ORDER_TYPE_SELL,
                         sym, lots, oprice, margin)) {
      margin = 0;
    }

    if (!firstPos)
      json += ",";
    firstPos = false;

    json += "{";
    json += "\"symbol\":\"" + sym + "\",";
    json +=
        "\"type\":\"" + (pType == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
    json += "\"lots\":" + DoubleToString(lots, 2) + ",";
    json += "\"profit\":" + DoubleToString(profit, 2) + ",";
    json += "\"open_price\":" + DoubleToString(oprice, 5) + ",";
    json += "\"margin\":" + DoubleToString(margin, 2);
    json += "}";
  }
  json += "],";

  // Config
  json += "\"config\":{";
  json += "\"max_positions\":" + IntegerToString(InpMaxPositions) + ",";
  json += "\"max_margin_pct\":" + DoubleToString(InpMaxMarginPct, 1) + ",";
  json += "\"max_per_asset_pct\":" + DoubleToString(InpMaxPerAssetPct, 1);
  json += "}";
  json += "}";

  return json;
}

//+------------------------------------------------------------------+
//| HTTP call to backend via WebRequest                              |
//+------------------------------------------------------------------+
string CallBackend(const string &payload) {
  string headers =
      "Content-Type: application/json\r\nX-Api-Key: " + InpApiKey + "\r\n";
  char postData[];
  char resultData[];
  string resultHeaders;

  StringToCharArray(payload, postData, 0, StringLen(payload));

  int statusCode =
      WebRequest("POST", InpBackendUrl + "/api/v1/portfolio/review", headers,
                 InpTimeoutMs, postData, resultData, resultHeaders);

  if (statusCode <= 0) {
    int err = GetLastError();
    PrintFormat(
        "[AlphaCortex] WebRequest error: code=%d, err=%d. Is URL whitelisted?",
        statusCode, err);
    return "";
  }

  if (statusCode != 200) {
    PrintFormat("[AlphaCortex] Backend returned HTTP %d", statusCode);
    return "";
  }

  return CharArrayToString(resultData);
}

//+------------------------------------------------------------------+
//| Parse backend JSON response and execute trades                   |
//+------------------------------------------------------------------+
void ProcessBackendResponse(const string &json) {
  // Parse regime and cash_target
  g_lastRegime = JsonGetString(json, "regime", "NEUTRAL");
  g_cashTarget = JsonGetDouble(json, "cash_target", 0.4);
  g_lastEvalId = (int)JsonGetDouble(json, "eval_id", 0);
  int nextH =
      (int)JsonGetDouble(json, "next_review_h", (double)InpPollMinutes / 60);
  g_nextPollTime = TimeCurrent() + nextH * 3600;

  // Extract portfolio array
  int portfolioStart = StringFind(json, "\"portfolio\":[");
  if (portfolioStart < 0) {
    Print("[AlphaCortex] No portfolio array in response");
    return;
  }

  // Parse each decision object from the portfolio array
  AssetDecision decisions[];
  int maxDecisions = 20;
  ArrayResize(decisions, 0);

  int searchFrom = portfolioStart + StringLen("\"portfolio\":[");
  int depth = 0;
  int objStart = -1;

  for (int i = searchFrom; i < StringLen(json) - 1; i++) {
    ushort ch = StringGetCharacter(json, i);
    if (ch == '{') {
      if (depth == 0)
        objStart = i;
      depth++;
    } else if (ch == '}') {
      depth--;
      if (depth == 0 && objStart >= 0) {
        string obj = StringSubstr(json, objStart, i - objStart + 1);
        AssetDecision d;
        d.symbol = JsonGetString(obj, "s", "");
        d.action = JsonGetString(obj, "a", "HOLD");
        d.weight = JsonGetDouble(obj, "w", 0.0);
        d.sl_pct = JsonGetDouble(obj, "sl_pct", InpDefaultSLPct);
        d.tp_pct = JsonGetDouble(obj, "tp_pct", InpDefaultTPPct);

        if (d.symbol != "" && d.action != "HOLD") {
          int sz = ArraySize(decisions);
          ArrayResize(decisions, sz + 1);
          decisions[sz] = d;
        }
        objStart = -1;
      }
    } else if (ch == ']' && depth == 0)
      break; // End of portfolio array
  }

  PrintFormat("[AlphaCortex] Parsed %d decisions. Regime=%s Cash=%.0f%%",
              ArraySize(decisions), g_lastRegime, g_cashTarget * 100);

  // ── Execute decisions ──────────────────────────────────────────
  // First: close positions marked CLOSE
  for (int i = 0; i < ArraySize(decisions); i++)
    if (decisions[i].action == "CLOSE")
      ClosePosition(decisions[i].symbol);

  // Then: open new BUY/SELL positions
  double balance = AccountInfoDouble(ACCOUNT_BALANCE);
  double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
  double maxMargin = freeMargin * InpMaxMarginPct / 100.0;

  for (int i = 0; i < ArraySize(decisions); i++) {
    AssetDecision d = decisions[i];
    if (d.action != "BUY" && d.action != "SELL")
      continue;
    if (HasOpenPosition(d.symbol))
      continue; // Already in this symbol
    if (CountOurPositions() >= InpMaxPositions)
      break;

    double targetValue = balance * d.weight;
    double targetMargin = targetValue * 0.20; // Darwinex 20% margin
    if (targetMargin >
        maxMargin * (InpMaxPerAssetPct / 100.0) * InpMaxPositions)
      targetMargin = maxMargin * (InpMaxPerAssetPct / 100.0);

    OpenPosition(d.symbol, d.action, targetMargin, d.sl_pct, d.tp_pct);
  }
}

//+------------------------------------------------------------------+
//| Open a position for a symbol                                     |
//+------------------------------------------------------------------+
void OpenPosition(const string symbol, const string action, double targetMargin,
                  double slPct, double tpPct) {
  // Ensure symbol is in Market Watch
  if (!SymbolSelect(symbol, true)) {
    PrintFormat("[AlphaCortex] Cannot select symbol: %s", symbol);
    return;
  }

  // Wait for price data synchronization
  bool synced = false;
  for (int i = 0; i < 20; i++) {
    if (g_symInfo.Name(symbol) && g_symInfo.RefreshRates() &&
        g_symInfo.Ask() > 0) {
      synced = true;
      break;
    }
    Sleep(100); // Wait 100ms for terminal to download tick data
  }

  if (!synced) {
    PrintFormat("[AlphaCortex] No price data for %s after waiting", symbol);
    return;
  }

  double ask = g_symInfo.Ask();
  double bid = g_symInfo.Bid();
  double minLot = g_symInfo.LotsMin();
  double maxLot = g_symInfo.LotsMax();
  double lotStep = g_symInfo.LotsStep();
  double marginPer1 = 0;

  bool isBuy = (action == "BUY");
  ENUM_ORDER_TYPE otype = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
  double price = isBuy ? ask : bid;

  // Calculate margin for 1 lot
  if (!OrderCalcMargin(otype, symbol, 1.0, price, marginPer1) ||
      marginPer1 <= 0) {
    PrintFormat("[AlphaCortex] Cannot calculate margin for %s", symbol);
    return;
  }

  // Calculate lot size from target margin
  double lots = MathFloor((targetMargin / marginPer1) / lotStep) * lotStep;
  lots = MathMax(minLot, MathMin(maxLot, lots));

  // Auto-detect the correct filling policy for this specific symbol
  g_trade.SetTypeFillingBySymbol(symbol);

  // Verify margin sufficiency
  double actualMargin = 0;
  if (!OrderCalcMargin(otype, symbol, lots, price, actualMargin)) {
    PrintFormat("[AlphaCortex] Cannot calculate margin verification for %s",
                symbol);
    return;
  }
  double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
  if (actualMargin > freeMargin * 0.95) {
    PrintFormat("[AlphaCortex] Not enough margin for %s: need=%.2f have=%.2f",
                symbol, actualMargin, freeMargin);
    return;
  }

  // Calculate SL and TP
  double sl, tp;
  int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
  if (isBuy) {
    sl = NormalizeDouble(price * (1.0 - slPct / 100.0), digits);
    tp = NormalizeDouble(price * (1.0 + tpPct / 100.0), digits);
  } else {
    sl = NormalizeDouble(price * (1.0 + slPct / 100.0), digits);
    tp = NormalizeDouble(price * (1.0 - tpPct / 100.0), digits);
  }

  // Validate SL distance
  long stopLevel = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
  double minDist = stopLevel * SymbolInfoDouble(symbol, SYMBOL_POINT);
  if (isBuy && sl > price - minDist)
    sl = NormalizeDouble(price - minDist, digits);
  if (!isBuy && sl < price + minDist)
    sl = NormalizeDouble(price + minDist, digits);

  bool ok = isBuy ? g_trade.Buy(lots, symbol, price, sl, tp, "AlphaCortex")
                  : g_trade.Sell(lots, symbol, price, sl, tp, "AlphaCortex");

  if (ok)
    PrintFormat("[AlphaCortex] %s %s: %.2f lots @ %.5f SL=%.5f TP=%.5f margin=%.2f",
                action, symbol, lots, price, sl, tp, actualMargin);
  else
    PrintFormat("[AlphaCortex] Order failed %s %s: %s", action, symbol,
                g_trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Close all positions for a symbol                                 |
//+------------------------------------------------------------------+
void ClosePosition(const string symbol) {
  for (int i = PositionsTotal() - 1; i >= 0; i--) {
    ulong ticket = PositionGetTicket(i);
    if (!PositionSelectByTicket(ticket))
      continue;
    if (PositionGetString(POSITION_SYMBOL) != symbol)
      continue;
    if (PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
      continue;

      PrintFormat("[AlphaCortex] Closed position %s (ticket=%llu)", symbol,
                  ticket);
      PrintFormat("[AlphaCortex] Close failed %s: %s", symbol,
                  g_trade.ResultRetcodeDescription());
  }
}

//+------------------------------------------------------------------+
//| Check if we have an open position in a symbol                    |
//+------------------------------------------------------------------+
bool HasOpenPosition(const string symbol) {
  for (int i = PositionsTotal() - 1; i >= 0; i--) {
    if (!PositionSelectByTicket(PositionGetTicket(i)))
      continue;
    if (PositionGetString(POSITION_SYMBOL) == symbol &&
        PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      return true;
  }
  return false;
}

//+------------------------------------------------------------------+
//| Count our open positions                                         |
//+------------------------------------------------------------------+
int CountOurPositions() {
  int count = 0;
  for (int i = PositionsTotal() - 1; i >= 0; i--) {
    if (!PositionSelectByTicket(PositionGetTicket(i)))
      continue;
    if (PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      count++;
  }
  return count;
}

//+------------------------------------------------------------------+
//| Daily drawdown protection                                        |
//+------------------------------------------------------------------+
void CheckDailyDrawdown() {
  if (g_haltedDueToDD)
    return;
  double equity = AccountInfoDouble(ACCOUNT_EQUITY);
  if (g_startDayEquity <= 0)
    return;
  double ddPct = (g_startDayEquity - equity) / g_startDayEquity * 100.0;
  if (ddPct >= InpMaxDailyDDPct) {
    g_haltedDueToDD = true;
    PrintFormat("[AlphaCortex] ⛔ Daily drawdown limit hit: %.2f%% (limit=%.2f%%)",
                ddPct, InpMaxDailyDDPct);
  }
}

//+------------------------------------------------------------------+
//| Dashboard — text overlay on chart                                |
//+------------------------------------------------------------------+
void UpdateDashboard() {
  double equity = AccountInfoDouble(ACCOUNT_EQUITY);
  double balance = AccountInfoDouble(ACCOUNT_BALANCE);
  double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
  int positions = CountOurPositions();

  datetime nextPoll = g_nextPollTime;
  int minsLeft = (int)MathMax(0, (nextPoll - TimeCurrent()) / 60);

  string ddStr = "";
  if (g_startDayEquity > 0) {
    double dd = (g_startDayEquity - equity) / g_startDayEquity * 100.0;
    ddStr = StringFormat("DD:%.2f%%", dd);
  }

  string info = "";
  info += "━━━ ALPHACORTEX AI ━━━\n";
  info += StringFormat("Balance:  $%.2f\n", balance);
  info += StringFormat("Equity:   $%.2f\n", equity);
  info += StringFormat("Free Mgn: $%.2f\n", freeMargin);
  info += StringFormat("Positions:%d / %d\n", positions, InpMaxPositions);
  info += StringFormat("Regime:   %s  Cash:%.0f%%\n", g_lastRegime,
                       g_cashTarget * 100);
  if (ddStr != "")
    info += ddStr + "\n";
  info += StringFormat("Eval ID:  #%d\n", g_lastEvalId);
  info += StringFormat("Next poll: %d min\n", minsLeft);
  info += "───────────────────────\n";
  info += g_statusMsg;

  Comment(info);
}

//+------------------------------------------------------------------+
//| Minimal JSON helpers (no external libraries)                     |
//+------------------------------------------------------------------+
string JsonGetString(const string &json, const string &key,
                     const string &defaultVal) {
  string searchKey = "\"" + key + "\":\"";
  int pos = StringFind(json, searchKey);
  if (pos < 0)
    return defaultVal;
  int start = pos + StringLen(searchKey);
  int end = StringFind(json, "\"", start);
  if (end < 0)
    return defaultVal;
  return StringSubstr(json, start, end - start);
}

double JsonGetDouble(const string &json, const string &key,
                     const double defaultVal) {
  string searchKey = "\"" + key + "\":";
  int pos = StringFind(json, searchKey);
  if (pos < 0)
    return defaultVal;
  int start = pos + StringLen(searchKey);
  // Skip whitespace
  while (start < StringLen(json) && (StringGetCharacter(json, start) == ' ' ||
                                     StringGetCharacter(json, start) == '\n'))
    start++;
  // Read until separator
  string numStr = "";
  for (int i = start; i < StringLen(json); i++) {
    ushort ch = StringGetCharacter(json, i);
    if (ch == ',' || ch == '}' || ch == ']')
      break;
    numStr += ShortToString(ch);
  }
  StringTrimLeft(numStr);
  StringTrimRight(numStr);
  if (numStr == "" || numStr == "null")
    return defaultVal;
  return StringToDouble(numStr);
}
