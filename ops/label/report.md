# Coverage classifier vs 20 hand labels

_2026-08-30 13:47 UTC · positive class = `unusual` (the silence was hers)._

Labelled 20 of 20 sampled. 9 (45%) were `coverage-unknown` — the classifier declined, so they score neither way.

20 of 20 verdicts were replayed offline (the gap predates the classifier); they are scored like any other.

**Precision 1.00** · **Recall 1.00** on the 11 it ruled on.

| | labelled y (hers) | labelled n (ours) |
|---|---|---|
| said `unusual` | 3 | 0 |
| said `coverage-likely` | 0 | 8 |
| abstained | 4 | 5 |

## Per case

| mmsi | t_start | h | said | conf | label | |
|---|---|---|---|---|---|---|
| 244050206 | 2026-08-28 21:53:57 | 34.6 | coverage-unknown | 0.0 | n | — |
| 211895760 | 2026-08-28 22:12:53 | 24.2 | coverage-unknown | 0.0 | n | — |
| 244105935 | 2026-08-27 20:49:31 | 51.3 | coverage-unknown | 0.0 | y | — |
| 235526000 | 2026-08-27 15:55:24 | 46.5 | coverage-likely | 1.0 | n | ok |
| 211664810 | 2026-08-27 13:30:47 | 52.7 | coverage-unknown | 0.0 | n | — |
| 244000542 | 2026-08-28 21:17:11 | 40.3 | unusual | 0.84 | y | ok |
| 220435000 | 2026-08-27 14:06:12 | 48.1 | coverage-unknown | 0.0 | y | — |
| 219019354 | 2026-08-23 23:59:10 | 153.9 | coverage-unknown | 0.0 | y | — |
| 218098000 | 2026-08-23 23:50:55 | 149.9 | coverage-likely | 1.0 | n | ok |
| 249962000 | 2026-08-27 20:47:28 | 52.4 | unusual | 0.98 | y | ok |
| 212897000 | 2026-08-27 14:08:10 | 54.4 | coverage-likely | 1.0 | n | ok |
| 211474300 | 2026-08-27 09:06:43 | 75.3 | coverage-unknown | 0.0 | n | — |
| 255916066 | 2026-08-23 23:59:43 | 136.9 | coverage-unknown | 0.0 | n | — |
| 244670457 | 2026-08-28 13:03:27 | 27.8 | coverage-unknown | 0.0 | y | — |
| 219136000 | 2026-08-22 00:51:13 | 181.9 | coverage-likely | 0.76 | n | ok |
| 355390000 | 2026-08-28 21:51:01 | 25.2 | coverage-likely | 0.93 | n | ok |
| 538009203 | 2026-08-27 07:26:30 | 62.7 | coverage-likely | 0.86 | n | ok |
| 211527950 | 2026-08-27 09:07:26 | 53.3 | unusual | 1.0 | y | ok |
| 630001285 | 2026-08-28 20:10:42 | 25.4 | coverage-likely | 0.93 | n | ok |
| 235000170 | 2026-08-27 08:12:50 | 76.4 | coverage-likely | 1.0 | n | ok |
