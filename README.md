# paper-bot

Zwei Paper-Trading-Tests, beide ohne Konto, ohne Wallet und ohne echte Orders:

- **KuCoin Futures** (`kucoin/`): Trendfolge auf 16 USDT-Perpetuals mit 1.000 $ Scheinkapital. Dashboard: `docs/futures.html`
- **Polymarket Up/Down** (`bot/`): stündliche Kryptomärkte. Dashboard: `docs/index.html`

## KuCoin-Futures-Bot

Regeln (Einstellungen oben in `kucoin/futures_bot.py`):

- **Einstieg:** Schlusskurs der Stundenkerze über dem 40-Tage-Hoch (long) oder unter dem 40-Tage-Tief (short).
- **Ausstieg:** Gegenkanal der letzten 20 Tage oder Stop bei 12 × Stunden-ATR (rund 2,5 Tagesspannen). Der Stop wird im Gewinn nachgezogen und zwischen den Läufen mit Minutenkerzen nachgeprüft.
- **Größe:** Ein Stop kostet 0,5 % des Kontos. Eine Position höchstens 1,5 × Konto, alle zusammen höchstens 3 ×.
- **Kosten:** Kauf am Briefkurs, Verkauf am Geldkurs, 0,06 % Taker-Gebühr, 0,02 % Slippage, echte Funding-Raten.

Rücktest (Skripte in `kucoin/research/`), August 2024 bis November 2026: etwa +15 % pro Jahr bei −19 % größtem Rückgang, 40 % Gewinntrades, 12 von 27 Monaten im Plus. Die fünf besten Trades brachten 60 % des Gewinns. Der Zeitraum ist kurz und die Stop-Weite wurde nach einem ersten Durchlauf angepasst, die Zahlen sind also eher zu günstig.

Grenzen: Der Bot läuft etwa alle fünf Minuten, nicht im Sekundentakt. Stops werden zum Stop-Kurs abgerechnet (bei Kurslücken schlechter), ein echter Stop kann in einem Absturz deutlich weiter durchrutschen. Viele Coins fallen und steigen gemeinsam, mehrere Positionen sind dann praktisch eine große.

## Polymarket-Bot

Paper-Trading-Test für die stündlichen **Up or Down**-Kryptomärkte auf Polymarket (BTC, ETH, SOL, XRP, DOGE, BNB).
Kein Konto, kein Wallet, keine Orders: Der Bot liest nur öffentliche Daten und loggt Scheintrades.

## Was er macht

1. Holt je Coin den Binance-Kurs gegen USDT (die Abrechnungsquelle des Marktes): Eröffnungskurs der laufenden Stunde, aktueller Kurs, Volatilität der letzten 4 Stunden.
2. Berechnet daraus die faire Wahrscheinlichkeit für "Up" (Schlusskurs ≥ Eröffnung) als digitale Option.
3. Liest das Polymarket-Orderbuch für "Up" und "Down".
4. Loggt einen Scheintrade über 10 $, wenn `Modell − Briefkurs − Gebühr ≥ 5 Cent`. Höchstens ein Trade je Coin und Stunde.
5. Rechnet beendete Runden mit der fertigen Binance-Kerze ab.

## Dateien

| Pfad | Inhalt |
|---|---|
| `bot/paper_bot.py` | Der Bot (nur Python-Standardbibliothek), Einstellungen stehen oben in der Datei |
| `data/observations.csv` | Jede Messung: Kurs, Modellwert, Marktpreise |
| `data/trades.json` | Alle Scheintrades mit Begründung und Ergebnis |
| `data/rounds.json` | Ergebnis jeder Runde |
| `docs/` | Dashboard (GitHub Pages) |
| `kucoin/futures_bot.py` | Futures-Scheinkonto, Daten in `data/futures/` |
| `.github/workflows/bot.yml` | Je Lauf 5 Messungen im Minutentakt, danach startet der Lauf seinen Nachfolger |

## Annahmen und Grenzen

- Kauf zum besten Briefkurs, nur wenn dort genug Stückzahl liegt. Kein Verkauf vor Rundenende.
- Gebühr laut Marktdaten (`feeSchedule`): `rate × (p × (1 − p))^exponent` je Anteil, derzeit bis 1,75 Cent bei 50 Cent.
- Das Modell kennt keine Drift, keine Sprünge und keine Orderbuch-Signale. Es ist ein Prüfstein, kein Geheimrezept.
- GitHubs Zeitplan ist unzuverlässig, deshalb stößt jeder Lauf den nächsten an. Zum Stoppen den laufenden Lauf unter Actions abbrechen, zum Starten dort "Run workflow" drücken.
- HYPE fehlt, weil dieser Markt über den Binance-Futures-Kurs abgerechnet wird.
- Bei DOGE und BNB liegen oft nur wenige hundert Dollar im Orderbuch. Ein Scheingewinn dort ließe sich mit echtem Geld kaum vergrößern.
- Aussagekräftig wird es erst ab etwa 100 abgerechneten Trades. Das Dashboard sagt bis dahin ausdrücklich "keine Aussage".

## Lokal ausführen

```
python3 -m unittest tests.test_model
python3 bot/paper_bot.py --samples 1
```
