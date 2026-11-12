# Rücktest

Skripte, mit denen die Regeln des Bots ausgewählt wurden. Brauchen `numpy` und `pandas`. Die Kursdaten liegen nicht im Repository, die `dl*.py`-Skripte laden sie von KuCoin.

| Skript | Inhalt |
|---|---|
| `dl.py`, `dl2.py` | Stundenkerzen und Funding für 45 Perpetuals |
| `dl_fut.py`, `dl_spot.py` | Tageskerzen aller seit 08/2024 gelisteten Perpetuals und aller Spot-USDT-Paare |
| `bt.py` | Rücktest-Kern für die Futures-Strategien |
| `grid.py`, `grid2.py`, `final.py`, `uni.py`, `ideas.py` | Strategievergleich, Nachbarschaft, Universum, Mindestgröße (Futures-Teil) |
| `tv.py` | Nachbau bekannter TradingView-Strategien |
| `listings.py` | Kursverlauf nach neuen Listings (`python3 listings.py fut` bzw. `spot`) |
| `spotmom.py`, `spotmom2.py`, `spotmom3.py` | Spot-Momentum über den ganzen Markt, Robustheit, gewählte Variante |
| `spot.py` | verworfene Spot-Trendfolge auf 16 Coins |

Kosten im Test: Futures 0,06 % Gebühr und 0,04 % Slippage je Seite plus echte Funding-Raten. Spot 0,1 % Gebühr und 0,3 % Slippage je Seite.

Ehrliche Einordnung: KuCoin liefert Stundenkerzen nur bis August 2024 zurück, das sind 27 Monate und im Kern ein Marktzyklus. Die Spot-Daten enthalten nur heute noch handelbare Coins. Mehrere Stellschrauben wurden nach einem ersten Durchlauf geändert, die Auswahl ist also nicht frei von Rückschau.
