// Decimales de precio por símbolo. Los cripto (BTCUSD/ETHUSD) se ven bien con 2;
// EURUSD (forex) necesita más precisión (4) para que el precio no se aplaste.
export function priceDecimals(symbol: string | null | undefined): number {
  return (symbol || '').toUpperCase() === 'EURUSD' ? 4 : 2
}
