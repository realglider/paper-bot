# paper-bot

Paper-Trading-Bot für KuCoin, ohne Konto und ohne echte Orders. Ein Scheinkonto mit 1.000 USDT, je zur Hälfte auf Futures und Spot verteilt. Übersicht: `docs/index.html`

## Futures-Teil (`kucoin/futures_bot.py`)

Trendfolge auf 16 großen USDT-Perpetuals, long und short, 500 USDT Futures-Konto.

- **Einstieg:** Schlusskurs der Stundenkerze über dem 40-Tage-Hoch (long) oder unter dem 40-Tage-Tief (short).
- **Teilgewinn:** Bei +1 R (Gewinn so groß wie das anfängliche Risiko) wird die Hälfte verkauft und der Stop knapp über den Einstand gezogen.
- **Ausstieg:** Gegenkanal der letzten 20 Tage oder Stop bei 12 × Stunden-ATR (rund 2,5 Tagesspannen), im Gewinn nachgezogen.
- **Größe:** Mindestens 100 USDT je Position. Ziel sind 5 USDT Verlust am Stop. Würde die Mindestgröße mehr als 10 USDT (1 % des Gesamtkapitals) riskieren, wird der Trade ausgelassen.
- **Hebel:** Alle Positionen zusammen höchstens 3.000 USDT, also 3 × Gesamtkapital. Erlaubt wären 10 ×.
- **Kosten:** 0,06 % Taker-Gebühr, 0,02 % Slippage, echte Funding-Raten.

## Spot-Teil (`kucoin/spot_bot.py`)

Momentum-Scanner über den ganzen KuCoin-Spotmarkt, 500 USDT Spot-Konto, kein Hebel.

- **Universum:** Alle USDT-Paare mit mindestens 1 Mio. USDT Tagesumsatz (Median 20 Tage) und 51 Tagen Historie. Neue Coins kommen von selbst dazu, sobald sie beides haben. Stablecoins und Hebel-Token sind ausgeschlossen.
- **Kauf:** Tagesschluss über dem 20-Tage-Hoch, mindestens dreifacher Umsatz gegen den 20-Tage-Schnitt, über dem 50-Tage-Durchschnitt. Ausführung kurz nach Mitternacht UTC.
- **Teilgewinn:** Bei +20 % wird die Hälfte verkauft und der Stop auf den Einstand gezogen, der Rest läuft weiter.
- **Verkauf:** Stop 12 % unter dem Kaufkurs, nachgezogen auf 15 % unter dem höchsten Tagesschluss.
- **Größe:** 100 USDT je Coin, höchstens 5 gleichzeitig.
- **Kosten:** 0,1 % Taker-Gebühr, 0,1 % Slippage.

## Was der Rücktest sagt (`kucoin/research/`)

August 2024 bis November 2026, beide Teile zusammen: aus 1.000 wurden rund 1.420 USDT, etwa +17 % pro Jahr bei −16 % größtem Rückgang, 11 von 27 Monaten im Plus.

- Futures-Teil allein: +19 % pro Jahr auf 500 USDT, −14 % Rückgang, 132 Trades, 49 % Gewinner. Ohne Teilgewinn: +23 %, −24 %, 39 %.
- Spot-Teil allein: +14 % pro Jahr auf 500 USDT, −24 % Rückgang, 113 Trades, 40 % Gewinner. Bei doppelter Slippage noch +11 %.

Ausstiegsvarianten (`exits_fut.py`, `exits_spot.py`): Teilgewinn und Einstand-Stop schaffen keinen zusätzlichen Vorteil, senken aber den Rückgang. Gewinner ohne Teilverkauf mit weitem Abstand (20 bis 30 %) laufen zu lassen, hat im Spot-Test fast alle Gewinne wieder abgegeben.

Die Zahlen sind zu günstig: Der Zeitraum ist kurz, die Regeln wurden an denselben Daten gewählt, und der Spot-Test kennt keine inzwischen gelöschten Coins.

Was sonst getestet und verworfen wurde:

| Idee | Skript | Ergebnis |
|---|---|---|
| Gegentrend (RSI, Bollinger), schnelle Trendfolge | `grid.py` | Verlust |
| Stark gegen Schwach, breiteres Futures-Universum | `grid2.py`, `uni.py` | um null bzw. mehr Rückgang |
| TradingView-Klassiker (SuperTrend, UT Bot, Chandelier Exit, Squeeze Momentum, MACD) | `tv.py` | auf 4-Stunden-Kerzen instabil, auf Tageskerzen wie die Trendfolge |
| Neue Listings kaufen | `listings.py` | Spot: nach 90 Tagen im Median −50 %, nur 22 % im Plus. Futures: −28 % |
| Neue Futures-Listings leerverkaufen | `listings.py` | im Schnitt +3 % je Trade vor Funding, jeder vierte gestoppt, zu dünn |
| Spot-Momentum mit kleinen Coins (unter 1 Mio. Umsatz) | `spotmom.py` | klarer Verlust |
| Bitcoin-Trendfilter, Funding-Rate einsammeln | `ideas.py` | kein Vorteil bzw. 0 bis 2 % pro Jahr |

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
| `kucoin/futures_bot.py`, `kucoin/spot_bot.py` | Die beiden Teile, Daten in `data/futures/` und `data/spot/` |
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
