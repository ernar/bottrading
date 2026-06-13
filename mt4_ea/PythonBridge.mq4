//+------------------------------------------------------------------+
//|  PythonBridge.mq4                                                |
//|  File-based bridge para comunicacion con Python bot              |
//|  Compatible con todas las versiones de MT4                       |
//+------------------------------------------------------------------+
#property strict

#define CMD_FILE    "pb_cmd.txt"    // Python escribe aqui el comando
#define RESP_FILE   "pb_resp.txt"   // EA escribe aqui la respuesta
#define LOCK_FILE   "pb_lock.txt"   // mutex simple

datetime last_check = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   // Limpiar archivos previos
   FileDelete(CMD_FILE);
   FileDelete(RESP_FILE);
   FileDelete(LOCK_FILE);
   // Procesar comandos por timer (100ms), independiente de los ticks del simbolo.
   // Sin esto, los comandos solo se procesaban al llegar un tick al grafico, lo que
   // causaba timeouts en simbolos quietos, fin de semana o en otro simbolo.
   EventSetMillisecondTimer(100);
   Print("PythonBridge iniciado (file bridge, timer 100ms). Esperando comandos...");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   FileDelete(CMD_FILE);
   FileDelete(RESP_FILE);
   FileDelete(LOCK_FILE);
   Print("PythonBridge detenido");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   ProcessCommand();
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Respaldo: tambien se procesa al llegar ticks (OnTimer es el driver principal).
   ProcessCommand();
}

//+------------------------------------------------------------------+
void ProcessCommand()
{
   if(!FileIsExist(CMD_FILE)) return;
   if(FileIsExist(LOCK_FILE)) return;

   // Leer comando
   int fh = FileOpen(CMD_FILE, FILE_READ | FILE_TXT | FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   string cmd = FileReadString(fh);
   FileClose(fh);

   if(StringLen(cmd) == 0) return;

   // Poner lock
   int lh = FileOpen(LOCK_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(lh != INVALID_HANDLE) FileClose(lh);

   // Procesar y escribir respuesta
   string response = HandleCommand(cmd);
   int rh = FileOpen(RESP_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(rh != INVALID_HANDLE) {
      FileWriteString(rh, response);
      FileClose(rh);
   }

   // Borrar comando y lock
   FileDelete(CMD_FILE);
   FileDelete(LOCK_FILE);
}

//+------------------------------------------------------------------+
string HandleCommand(string cmd)
{
   string parts[];
   int count = StringSplit(cmd, '|', parts);
   if(count == 0) return "ERROR|empty command";

   string op = parts[0];

   if(op == "PING")           return "PONG";
   if(op == "ACCOUNT_INFO")   return GetAccountInfo();
   if(op == "SYMBOLS")        return GetSymbols();
   if(op == "SYMBOL_INFO" && count >= 2)  return GetSymbolInfo(parts[1]);
   if(op == "TICK"        && count >= 2)  return GetTick(parts[1]);
   if(op == "OHLCV"       && count >= 3)  return GetOHLCV(parts[1], (int)StringToInteger(parts[2]));
   if(op == "POSITIONS")  return GetPositions(count >= 2 ? parts[1] : "");
   if(op == "ORDERS")     return GetOrders();
   if(op == "PLACE_ORDER" && count >= 5)  return PlaceOrder(parts);
   if(op == "CLOSE_POSITION" && count >= 2) return ClosePosition(parts[1]);

   return "ERROR|unknown command: " + op;
}

//+------------------------------------------------------------------+
string GetAccountInfo()
{
   double margin_level = 0;
   if(AccountMargin() > 0)
      margin_level = AccountEquity() / AccountMargin() * 100.0;

   return StringFormat(
      "OK|login=%d|balance=%.2f|equity=%.2f|margin=%.2f|free_margin=%.2f|margin_level=%.2f|profit=%.2f|leverage=%d|currency=%s|broker=%s",
      AccountNumber(), AccountBalance(), AccountEquity(),
      AccountMargin(), AccountFreeMargin(), margin_level,
      AccountProfit(), AccountLeverage(), AccountCurrency(), AccountCompany()
   );
}

//+------------------------------------------------------------------+
string GetSymbols()
{
   string result = "OK|";
   int total = SymbolsTotal(true);
   for(int i = 0; i < total; i++) {
      if(i > 0) result += ",";
      result += SymbolName(i, true);
   }
   return result;
}

//+------------------------------------------------------------------+
string GetSymbolInfo(string symbol)
{
   if(!SymbolSelect(symbol, true))
      return "ERROR|symbol not found: " + symbol;

   return StringFormat(
      "OK|symbol=%s|point=%.10f|digits=%d|spread=%.1f|tick_value=%.5f|lot_min=%.2f|lot_max=%.2f|lot_step=%.2f",
      symbol,
      MarketInfo(symbol, MODE_POINT),
      (int)MarketInfo(symbol, MODE_DIGITS),
      MarketInfo(symbol, MODE_SPREAD),
      MarketInfo(symbol, MODE_TICKVALUE),
      MarketInfo(symbol, MODE_MINLOT),
      MarketInfo(symbol, MODE_MAXLOT),
      MarketInfo(symbol, MODE_LOTSTEP)
   );
}

//+------------------------------------------------------------------+
string GetTick(string symbol)
{
   if(!SymbolSelect(symbol, true))
      return "ERROR|symbol not found: " + symbol;

   return StringFormat("OK|symbol=%s|bid=%.10f|ask=%.10f|time=%d",
      symbol,
      MarketInfo(symbol, MODE_BID),
      MarketInfo(symbol, MODE_ASK),
      (int)MarketInfo(symbol, MODE_TIME)
   );
}

//+------------------------------------------------------------------+
string GetOHLCV(string symbol, int bars)
{
   if(!SymbolSelect(symbol, true))
      return "ERROR|symbol not found: " + symbol;
   if(bars <= 0 || bars > 500) bars = 20;

   string result = "OK|";
   for(int i = bars - 1; i >= 0; i--) {
      if(i < bars - 1) result += ";";
      result += StringFormat("%d,%.10f,%.10f,%.10f,%.10f,%d",
         (int)Time[i], Open[i], High[i], Low[i], Close[i], (long)Volume[i]);
   }
   return result;
}

//+------------------------------------------------------------------+
string GetPositions(string symbol)
{
   string result = "OK|";
   bool first = true;
   for(int i = 0; i < OrdersTotal(); i++) {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderType() > 1) continue;
      if(symbol != "" && OrderSymbol() != symbol) continue;
      if(!first) result += ";";
      first = false;
      result += StringFormat(
         "ticket=%d,symbol=%s,type=%d,volume=%.2f,open_price=%.10f,sl=%.10f,tp=%.10f,profit=%.2f,open_time=%d,comment=%s",
         OrderTicket(), OrderSymbol(), OrderType(), OrderLots(),
         OrderOpenPrice(), OrderStopLoss(), OrderTakeProfit(),
         OrderProfit(), (int)OrderOpenTime(), OrderComment()
      );
   }
   return result;
}

//+------------------------------------------------------------------+
string GetOrders()
{
   string result = "OK|";
   bool first = true;
   for(int i = 0; i < OrdersTotal(); i++) {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderType() < 2) continue;
      if(!first) result += ";";
      first = false;
      result += StringFormat(
         "ticket=%d,symbol=%s,type=%d,volume=%.2f,price=%.10f,sl=%.10f,tp=%.10f,open_time=%d,comment=%s",
         OrderTicket(), OrderSymbol(), OrderType(), OrderLots(),
         OrderOpenPrice(), OrderStopLoss(), OrderTakeProfit(),
         (int)OrderOpenTime(), OrderComment()
      );
   }
   return result;
}

//+------------------------------------------------------------------+
string PlaceOrder(string &parts[])
{
   string symbol  = parts[1];
   string type_s  = parts[2];
   double volume  = StringToDouble(parts[3]);
   double price   = StringToDouble(parts[4]);
   double sl      = (ArraySize(parts) > 5) ? StringToDouble(parts[5]) : 0;
   double tp      = (ArraySize(parts) > 6) ? StringToDouble(parts[6]) : 0;
   string comment = (ArraySize(parts) > 7) ? parts[7] : "PythonBot";

   int order_type;
   if(type_s == "BUY")             order_type = OP_BUY;
   else if(type_s == "SELL")       order_type = OP_SELL;
   else if(type_s == "BUY_LIMIT")  order_type = OP_BUYLIMIT;
   else if(type_s == "SELL_LIMIT") order_type = OP_SELLLIMIT;
   else if(type_s == "BUY_STOP")   order_type = OP_BUYSTOP;
   else if(type_s == "SELL_STOP")  order_type = OP_SELLSTOP;
   else return "ERROR|invalid order type: " + type_s;

   if(!SymbolSelect(symbol, true))
      return "ERROR|symbol not found: " + symbol;

   if(price == 0) {
      if(order_type == OP_BUY)  price = MarketInfo(symbol, MODE_ASK);
      if(order_type == OP_SELL) price = MarketInfo(symbol, MODE_BID);
   }

   int ticket = OrderSend(symbol, order_type, volume, price, 3, sl, tp, comment, 234000, 0, clrNONE);
   if(ticket < 0)
      return StringFormat("ERROR|OrderSend failed, error=%d", GetLastError());

   return StringFormat("OK|ticket=%d|price=%.10f", ticket, price);
}

//+------------------------------------------------------------------+
string ClosePosition(string symbol)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--) {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol || OrderType() > 1) continue;

      double close_price = (OrderType() == OP_BUY)
         ? MarketInfo(symbol, MODE_BID)
         : MarketInfo(symbol, MODE_ASK);

      bool ok = OrderClose(OrderTicket(), OrderLots(), close_price, 3, clrNONE);
      if(!ok)
         return StringFormat("ERROR|OrderClose failed, error=%d", GetLastError());
      return StringFormat("OK|closed ticket=%d", OrderTicket());
   }
   return "ERROR|no open position for " + symbol;
}
