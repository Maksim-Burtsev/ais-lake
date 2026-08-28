# The 12 launch ports — candidates from our own data

Generated 2026-08-29 by `ops/geo/rank.py` against the dev lake (13.6k ships in the
24 h window, 21k static rows). Re-run it once the polygons exist to check them.

M3-H1 asks for 12 ports drawn by hand. This is the evidence for choosing which 12.

**How to read this.** Positions and ship counts are facts out of `vessel_latest`.
The port *names* are mine, read off the coordinates — `destination` cannot name a
mooring, because it says where a ship is **going** (the 88-ship cluster below
declares "NLVLI" and sits in Terneuzen, 25 km away). Check each name on your own
chart before you draw; that is the ten minutes only you can do.

---

## Recommended 12

| # | locode | port | centre — start drawing here | moored+anchored | declared 14 d |
|---|--------|------|------|------|------|
| 1 | NLRTM | Rotterdam | 51.8822, 4.2768 | **510** | 672 |
| 2 | BEANR | Antwerp | 51.2646, 4.3382 | 327 | 330 |
| 3 | NLAMS | Amsterdam | 52.3787, 4.8942 | 236 | 407 |
| 4 | DEHAM | Hamburg | 53.5443, 9.9681 | 100 | 239 |
| 5 | NLTNZ | Terneuzen | 51.3331, 3.8213 | 88 | 30 |
| 6 | NLIJM | IJmuiden | 52.4602, 4.5893 | 75 | 50 |
| 7 | NLDZL | Delfzijl | 53.3180, 6.9350 | 62 | 29 |
| 8 | BEGNE | Ghent | 51.1509, 3.7864 | 48 | 71 |
| 9 | DEBRV | Bremerhaven | 53.5168, 8.5785 | 47 | 74 |
| 10 | NLHAR | Harlingen | 53.1760, 5.4141 | 42 | 90 |
| 11 | NLDHR | Den Helder | 52.9585, 4.7810 | 39 | 40 |
| 12 | NLVLI | Vlissingen | 51.4451, 3.5937 | 25 | 75 |

**Rotterdam is three clusters, one port.** 51.8822,4.2768 (408 — the city and
Waalhaven), 51.9578,4.0058 (82 — Europoort and Maasvlakte) and 51.9339,3.7311
(20 — the Maasgeul approach anchorage). They sit more than 18 km apart so the
script lists them separately; one polygon with several anchorages covers all three,
and that offshore cluster is the anchorage worth having.

Nine of twelve are Dutch or Belgian. That is not a bias in the method — it is where
the North Sea's traffic is, and both signals say so independently.

## Swaps, if you want a different shape

| locode | port | centre | moored | why you might |
|--------|------|--------|--------|---------------|
| NLDOR | Dordrecht | 51.8128, 4.6537 | **207** | 4th busiest water we can see — but Rhine barge moorings, not a seaport. Best queue story, least representative port. |
| BEZEE | Zeebrugge | 51.3292, 3.1991 | 26 | A real seaport, ferry and LNG |
| DECUX | Cuxhaven | 53.8658, 8.7114 | 24 | Elbe mouth; overlaps Hamburg's approach |
| GBSOU | Southampton | 50.8368, -1.3261 | 23 | **the only British port we can see at all** |
| DEWVN | Wilhelmshaven | 53.5160, 8.1482 | 22 | Germany's only deepwater container terminal |
| DEEME | Emden | 53.3583, 7.2023 | 20 | car terminal, Ems mouth |
| NLMOE | Moerdijk | 51.6831, 4.3972 | 56 | position reads as Willemstad / Hollands Diep, not Moerdijk itself — check before trusting |

## The coverage asymmetry — the finding worth keeping

Last hour of `positions`:

| | messages / h | distinct ships |
|---|---|---|
| continent, east of 1.5°E | 128 923 | 9 770 |
| UK side, west of 1.5°E | 14 128 | 1 036 |

**The UK side carries ~11% of the continental volume.** aisstream's receiver density
is continental. London (47 ships declared), Felixstowe and Dover appear in the
declared ranking and have essentially no observable berths, so no British port except
Southampton can be hand-verified against our own data — and none is in the 12.

This reaches past M3. **F35's Dover Strait** is one of the two launch straits, and its
acceptance criterion is "N/S-bound counters with median speed… recompute from live
positions ≤1 min and **match the map**". On the English side they will not. Worth
deciding before M5 whether that page says so out loud, counts only what it can see, or
waits for a second feed. Cheaper to know now than to discover it on the page.

## What the two signals disagree about

- **Observed favours inland barge moorings** — Dordrecht (207), Gorinchem (58),
  Zwolle (55), Wageningen (47), Gouda (28). Real ships, really moored, on the Rhine
  and the Dutch canals. Not seaports, and they would dominate a list ranked by count.
- **Declared favours seaports at the edge of coverage** — London, Esbjerg, Cuxhaven,
  Eemshaven, Ostend.
- They agree without argument on the big four: Rotterdam, Antwerp, Amsterdam, Hamburg.
- Clusters outside the launch bbox turn up too (Kiel, Rostock, Bergen, Stavanger,
  Gothenburg, Oslo, Frankfurt, Köln). The ingestor's bbox is the North Sea, so these
  are ships whose last fix reached us before they left it, plus Rhine traffic inland
  of the box. Ignore them; they are not ours to draw.

## Before you open QGIS

`anchored` is nav_status-only until M3-T2 — 314 ships out of 13.6k live carry it, and
the state machine that will make it real does not exist yet. **Draw the anchorage
multipolygons from charts and local knowledge, not from our data**, and expect to
adjust them once the detector runs. The port polygons are safe to draw against the
centres above; the anchorages are the part our data cannot yet check.

Output: `ops/geo/ports.geojson`, one feature per polygon, properties `locode` and
`name`, anchorages as a `MultiPolygon` feature per port.
