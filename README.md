# paper-bot

Paper-Trading-Bot für KuCoin-Futures, ohne Konto und ohne echte Orders. Dashboard: `docs/futures.html`

## KuCoin-Futures-Bot

Trendfolge auf 16 USDT-Perpetuals mit 1.000 USDT Scheinkapital, long und short. Regeln (Einstellungen oben in `kucoin/futures_bot.py`):

- **Einstieg:** Schlusskurs der Stundenkerze über dem 40-Tage-Hoch (long) oder unter dem 40-Tage-Tief (short).
- **Ausstieg:** Gegenkanal der letzten 20 Tage oder Stop bei 12 × Stunden-ATR (rund 2,5 Tagesspannen). Der Stop wird im Gewinn nachgezogen und zwischen den Läufen mit Minutenkerzen nachgeprüft.
- **Größe:** Mindestens 100 USDT je Position. Ziel ist, dass ein Stop 0,5 % des Kontos kostet. Würde schon die Mindestgröße mehr als 1 % riskieren, wird der Trade ausgelassen.
- **Hebel:** Eine Position höchstens 1,5 × Konto, alle zusammen höchstens 3 ×. Erlaubt wären 10 ×.
- **Kosten:** Kauf am Briefkurs, Verkauf am Geldkurs, 0,06 % Taker-Gebühr, 0,02 % Slippage, echte Funding-Raten.

Rücktest (`kucoin/research/`), August 2024 bis November 2026: etwa +14 % pro Jahr bei −10,5 % größtem Rückgang, 131 Trades, 41 % Gewinner, 13 von 27 Monaten im Plus. Die ersten 15 Monate brachten nur +2 % pro Jahr, die letzten 12 Monate +25 %. Der Zeitraum ist kurz und die Regeln wurden an denselben Daten gewählt, die Zahlen sind also eher zu günstig.

Was sonst getestet wurde (`grid.py`, `grid2.py`, `uni.py`, `spot.py`, `ideas.py`): Gegentrend-Strategien und schnelle Trendfolge verloren Geld, Stark-gegen-Schwach lag um null, 29 zusätzliche Coins erhöhten vor allem den Rückgang, ein Bitcoin-Trendfilter brachte keinen messbaren Vorteil, und das Einsammeln der Funding-Rate lohnt bei den Raten von 2025/26 (im Schnitt 0 bis 2 % pro Jahr) nicht.

Grenzen: Der Bot läuft etwa alle fünf Minuten. Stops werden zum Stop-Kurs abgerechnet (bei Kurslücken schlechter), in einem Absturz kann ein echter Stop weiter durchrutschen. Viele Coins fallen und steigen gemeinsam, mehrere Positionen sind dann praktisch eine große.

## Polymarket-Bot (beendet)

Der Test der stündlichen Up/Down-Märkte wurde am 12.11.2026 beendet, weil Polymarket aus Deutschland nicht handelbar ist. Code und letzter Stand bleiben zur Ansicht (`bot/`, `docs/polymarket.html`).


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
| `docs/` | Dashboards (GitHub Pages) |
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
