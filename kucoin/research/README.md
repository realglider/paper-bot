# Rücktest

Skripte, mit denen die Strategie des Futures-Bots ausgewählt wurde. Brauchen `numpy` und `pandas`.

```
python3 dl.py      # Stundenkerzen und Funding von KuCoin laden (ca. 40 Minuten)
python3 grid.py    # erster Vergleich, enge Stops: alles im Minus oder instabil
python3 grid2.py   # zweiter Vergleich, Stops in Tagesspannen
python3 final.py   # Nachbarschaft, Kosten, je Coin und Monatsrenditen der gewählten Variante
```

Breiteres Universum (`dl2.py`, `uni.py`): Mit 29 weiteren liquiden Coins stieg die Rendite im Test von 15 % auf 22 % pro Jahr, der größte Rückgang aber von −19 % auf −43 %. Die 29 neuen Coins allein kamen nur auf einen Profit-Faktor von 1,06 bis 1,13. Der Bot bleibt deshalb bei 16 Coins.

Kosten im Test: 0,06 % Taker-Gebühr und 0,04 % Slippage je Seite, dazu die echten Funding-Raten.

Ehrliche Einordnung: KuCoin liefert Stundenkerzen nur bis August 2024 zurück, das sind 27 Monate und im Kern ein Marktzyklus. Die Coins sind die heute großen Namen, die es damals schon gab. Die Stop-Weite wurde nach dem ersten Durchlauf geändert, die Auswahl ist also nicht frei von Rückschau.
