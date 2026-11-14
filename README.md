# paper-bot

Paper-Trading-Bot für KuCoin, ohne Konto und ohne echte Orders. Drei Scheinkonten mit je 1.000 USDT laufen nebeneinander. Übersicht: `docs/index.html`

- **Strategie 1:** Trendfolge mit Hebel (300 USDT Futures) plus Momentum-Scanner (700 USDT Spot). Regeln an 27 Monaten KuCoin-Daten gewählt.
- **Strategie 2:** Bitcoin-Kern plus Rotation, nur Spot. Entwickelt und geprüft auf getrennten Datensätzen.
- **Strategie 3:** neu gelistete Krypto-Futures leerverkaufen, abgesichert mit einem Bitcoin-Long. Ebenfalls in drei Stufen geprüft.

## Strategie 2: Bitcoin-Kern plus Rotation (`kucoin/rotation_bot.py`)

- **Marktlage:** Liegt Bitcoin über seinem Durchschnitt der letzten 50, 100 und 200 Tage? Das ergibt 0 bis 3 Punkte.
- **Kern (500 USDT):** Bitcoin, investiert zu 0, 33, 67 oder 100 % je nach Punkten.
- **Rotation (500 USDT):** bei 1, 2 oder 3 Punkten bis zu 2, 3 oder 5 Coins. Gewählt werden die stärksten (Mittel aus 30- und 90-Tage-Rendite) unter den 20 meistgehandelten, die über ihrem 50-Tage-Schnitt liegen. Neuauswahl alle 7 Tage.
- **Ausstieg:** täglich, sobald ein Coin unter seinen 50-Tage-Schnitt fällt oder seine Stärke negativ wird. Bei 0 Punkten wird alles verkauft.
- **Größe:** 20 % des Rotations-Kontos, bei stark schwankenden Coins weniger (Ziel 80 % Jahresschwankung), mindestens 10 %.
- **Kosten:** 0,1 % Gebühr, 0,1 % Slippage. Gehandelt wird einmal am Tag kurz nach Mitternacht UTC.

Entwicklung in drei getrennten Stufen (`kucoin/research/rot.py`, `rot_core.py`, `rot_short.py`, `rot_eval.py`):

| Stufe | Daten | Strategie pro Jahr | Rückgang | Sharpe | Bitcoin halten | Rückgang |
|---|---|---|---|---|---|---|
| Entwicklung | Binance 2019–2022, inkl. gelöschter Coins | +73 % | −37 % | 1,48 | +45 % | −77 % |
| Prüfung (zweimal angesehen) | Binance 2023–08/2024 | +58 % | −29 % | 1,24 | +120 % | −24 % |
| Schlusskontrolle (einmal) | KuCoin 08/2024–11/2026 | +10 % | −28 % | 0,47 | +10 % | −53 % |

Lesart: Das Ergebnis wird mit jeder Stufe schwächer, der ehrlichste Wert ist die Schlusskontrolle. Die Rotation allein fiel in der Prüfung durch (−45 % Rückgang, weit hinter Bitcoin) und wurde deshalb mit dem Bitcoin-Kern kombiniert. Ein Short-Baustein für schlechte Marktlagen brachte in der Entwicklung nur 2022 Gewinn und verlor in der Prüfung, er ist nicht enthalten. Die Rechenfunktionen des Bots wurden an 30 Stichtagen gegen den Rücktest abgeglichen, ohne Abweichung.

## Strategie 3: Neu-Listing-Short (`kucoin/listing_bot.py`)

Neu gelistete Coins verlieren in den ersten Wochen meist deutlich, einzelne vervielfachen sich aber.

- **Einstieg:** Short am 4. Tag nach dem Listing eines Krypto-Futures (tokenisierte Aktien und ETFs ausgeschlossen).
- **Ausstieg:** nach 30 Tagen oder bei +50 % gegen die Position.
- **Größe:** 5 % des Kontos je Coin, höchstens 10 gleichzeitig.
- **Absicherung:** ein Bitcoin-Long in Höhe aller offenen Shorts.

| Stufe | Daten | Trades | je Trade nach Kosten | Gewinner |
|---|---|---|---|---|
| Entwicklung | Binance-Listings 2019–2022 (Spotkurse, inkl. gelöschter Coins) | 333 | +5,7 % | 64 % |
| Prüfung | Binance-Listings 2023–08/2024 | 85 | +9,2 % | 71 % |
| Schlusskontrolle | KuCoin-Futures 08/2024–11/2026, echtes Funding, nur Krypto | 340 | +3,4 % | 59 % |

Auf Kontoebene in der Schlusskontrolle: +10 % pro Jahr bei −32 % größtem Rückgang. Ohne Absicherung lag der Schnitt in der Entwicklung nur bei +2 % je Trade, und der Short verlor in guten Marktphasen. Einschränkungen: Die Trennung von Krypto und Aktien-Token wurde erst nach dem ersten Blick auf die KuCoin-Daten eingeführt (mit Aktien-Token +1,5 % je Trade), und im letzten Jahr blieben nur noch rund +1 % je Trade. Skripte: `kucoin/research/newlist.py`, `dl_futf.py`.

### Geprüft und verworfen: Fibonacci 0,882 und Liquidity Sweep + Order Block (`kucoin/research/smc.py`)

Ergebnis je Trade in R (Vielfache des Anfangsrisikos) nach Kosten, 24 große Coins.

| Regel | Entwicklung 2019–2022 | Prüfung 2023–08/2024 | Schlusskontrolle KuCoin |
|---|---|---|---|
| Fibonacci 0,882, Stop unter dem Ausgangstief, Ziel altes Hoch (1h) | −0,38 R, auch ohne Kosten −0,15 R | nicht weiter geprüft | |
| Sweep + Order Block, 1h, Ziel 3R | +0,03 R | −0,17 R | |
| Sweep + Order Block, 4h, Ziel 3R, Trendfilter (Kandidat) | +0,23 R (205 Trades) | +0,14 R (108) | 0,00 R (70) |
| Sweep + Order Block, 4h, Ziel 3R ohne Filter | 0,00 R | +0,23 R | +0,22 R |

Der tiefe Fibonacci-Rücklauf hat einen klar negativen Erwartungswert: Nach einem bestätigten Hoch läuft der Kurs häufiger weiter, als dass er am 0,882-Level dreht. Beim Sweep-Muster gibt es vor Kosten einen kleinen Vorteil, aber welche Variante vorn liegt, wechselt von Zeitraum zu Zeitraum. Der vorab gewählte Kandidat landete in der Schlusskontrolle bei null.

# Strategie 1

## Futures-Teil (`kucoin/futures_bot.py`)

Trendfolge auf 16 großen USDT-Perpetuals, long und short, 300 USDT Futures-Konto.

- **Einstieg:** Schlusskurs der Stundenkerze über dem 40-Tage-Hoch (long) oder unter dem 40-Tage-Tief (short).
- **Teilgewinn:** Bei +1 R (Gewinn so groß wie das anfängliche Risiko) wird die Hälfte verkauft und der Stop knapp über den Einstand gezogen.
- **Ausstieg:** Gegenkanal der letzten 20 Tage oder Stop bei 12 × Stunden-ATR (rund 2,5 Tagesspannen), im Gewinn nachgezogen.
- **Größe:** Mindestens 100 USDT je Position. Ziel sind 5 USDT Verlust am Stop. Würde die Mindestgröße mehr als 10 USDT (1 % des Gesamtkapitals) riskieren, wird der Trade ausgelassen.
- **Hebel:** Alle Positionen zusammen höchstens 1.500 USDT, also 5 × Futures-Konto. Erlaubt wären 10 ×, im Rücktest wurde nie mehr als rund 1.000 USDT gebraucht.
- **Kosten:** 0,06 % Taker-Gebühr, 0,02 % Slippage, echte Funding-Raten.

## Spot-Teil (`kucoin/spot_bot.py`)

Momentum-Scanner über den ganzen KuCoin-Spotmarkt, 700 USDT Spot-Konto, kein Hebel.

- **Universum:** Alle USDT-Paare mit mindestens 1 Mio. USDT Tagesumsatz (Median 20 Tage) und 51 Tagen Historie. Neue Coins kommen von selbst dazu, sobald sie beides haben. Stablecoins und Hebel-Token sind ausgeschlossen.
- **Kauf:** Tagesschluss über dem 20-Tage-Hoch, mindestens dreifacher Umsatz gegen den 20-Tage-Schnitt, über dem 50-Tage-Durchschnitt. Ausführung kurz nach Mitternacht UTC.
- **Teilgewinn:** Bei +20 % wird die Hälfte verkauft und der Stop auf den Einstand gezogen, der Rest läuft weiter.
- **Verkauf:** Stop 12 % unter dem Kaufkurs, nachgezogen auf 15 % unter dem höchsten Tagesschluss.
- **Größe:** 100 USDT je Coin, höchstens 7 gleichzeitig.
- **Kosten:** 0,1 % Taker-Gebühr, 0,1 % Slippage.

## Was der Rücktest sagt (`kucoin/research/`)

August 2024 bis November 2026, beide Teile zusammen: aus 1.000 wurden rund 1.570 USDT, etwa +22 % pro Jahr bei −19 % größtem Rückgang, 11 von 27 Monaten im Plus.

- Futures-Teil allein: +248 USDT auf 300, −24 % Rückgang, 140 Trades, 49 % Gewinner. Ohne Teilgewinn: +414 USDT, −34 %, 40 %.
- Spot-Teil allein: +18 % pro Jahr auf 700 USDT, −20 % Rückgang, 135 Trades, 42 % Gewinner. Bei doppelter Slippage noch +13 %.

Zwei vorab festgelegte Tests (`split_short.py`), Übernahme nur bei Verbesserung in Bullen- und Bärenphase getrennt:

- **Aufteilung 700 Spot / 300 Futures statt 500/500:** übernommen. Bullenphase 22 % statt 11 % pro Jahr, Bärenphase 17 % statt 16 %, Rückgang je Phase nicht schlechter.
- **Short-Scanner** (liquide Coins leerverkaufen, die mit dreifachem Umsatz unter ihr 20-Tage-Tief fallen): verworfen. Verlust in beiden Phasen, insgesamt −147 USDT auf 500.

Ausstiegsvarianten (`exits_fut.py`, `exits_spot.py`): Teilgewinn und Einstand-Stop schaffen keinen zusätzlichen Vorteil. Im Futures-Teil kosten sie Ertrag und senken den Rückgang, im Spot-Teil ändern sie wenig. Gewinner ohne Teilverkauf mit weitem Abstand (20 bis 30 %) laufen zu lassen, hat im Spot-Test fast alle Gewinne wieder abgegeben.

### Fremdtest: Binance 2019 bis 08/2024, unveränderte Regeln (`oos_binance.py`, `oos_spot2.py`)

Diese Daten lagen bei der Auswahl der Regeln nicht vor.

| | Rendite pro Jahr | Größter Rückgang | Anmerkung |
|---|---|---|---|
| Futures-Teil wie live | +7 % | −31 % | 2019 bis 2022 leicht im Minus, Gewinn erst 2023/24. 2021 kein einziger Trade, weil die Mindestgröße bei hoher Schwankung alles ausließ |
| Trendregel pur, Coins aus Sicht von 2019 | +5 % | −30 % | Longs +7,5 %, Shorts −2,4 % pro Jahr |
| Spot-Regel wörtlich (ab 1 Mio. Umsatz) | −30 % | −96 % | auf Binance lässt die Schwelle 200 bis 340 Coins zu, darunter viele später gelöschte |
| Spot-Regel, Schwelle an Binance angepasst (10 bis 20 Mio.) | +11 bis +14 % | −29 bis −41 % | verdient 2020/21, sonst um null oder im Minus |
| Bitcoin halten | +64 % | −77 % | zum Vergleich |

Folgerungen: Die Ergebnisse aus den 27 Entwicklungsmonaten waren deutlich zu günstig. Gelöschte Coins machen bei kleinen Coins den Unterschied zwischen −5 % und −30 % pro Jahr. Die Spot-Regel taugt nur für die meistgehandelten Coins.

Die Zahlen des 27-Monats-Tests sind zu günstig: Der Zeitraum ist kurz, die Regeln wurden an denselben Daten gewählt, und der Spot-Test kennt keine inzwischen gelöschten Coins.

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
