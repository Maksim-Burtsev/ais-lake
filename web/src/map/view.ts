/** The picker's handle on the one map in the app (F6), and the bbox shape both
 *  sides speak. Its own module so importing it does not drag maplibre-gl (627 KB)
 *  into the top bar's chunk.
 *
 *  ponytail: a module-level singleton, not context. There is exactly one
 *  MapCanvas; the day a second one exists, this becomes a context provider.
 */

/** minLon,minLat,maxLon,maxLat — the api's bbox order, everywhere. */
export type Bbox = [number, number, number, number];

export const mapView: { goto: ((bbox: Bbox) => void) | null } = { goto: null };
