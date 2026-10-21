// Small geometry helpers for the hand-drawn diagrams: orthogonal connectors with rounded corners, and arrowheads
// drawn as polygons (no <marker> ids, so several diagrams can share a page and both themes colour them by class).

export type Pt = [number, number];

/** A path through the points with corners rounded to radius r (clamped to half of each segment). */
export function connector(points: Pt[], r = 10): string {
  if (points.length < 2) return '';
  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1];
    const [cx, cy] = points[i];
    const [nx, ny] = points[i + 1];
    const inLen = Math.hypot(cx - px, cy - py);
    const outLen = Math.hypot(nx - cx, ny - cy);
    const k = Math.min(r, inLen / 2, outLen / 2);
    const ax = cx - ((cx - px) / inLen) * k;
    const ay = cy - ((cy - py) / inLen) * k;
    const bx = cx + ((nx - cx) / outLen) * k;
    const by = cy + ((ny - cy) / outLen) * k;
    d += ` L ${ax} ${ay} Q ${cx} ${cy} ${bx} ${by}`;
  }
  const [lx, ly] = points[points.length - 1];
  // Stop the line where the arrowhead starts, so the head's tip is the end point.
  const [sx, sy] = points[points.length - 2];
  const len = Math.hypot(lx - sx, ly - sy) || 1;
  const back = 6;
  d += ` L ${lx - ((lx - sx) / len) * back} ${ly - ((ly - sy) / len) * back}`;
  return d;
}

/** An arrowhead polygon whose tip is the last point, pointing along the last segment. */
export function head(points: Pt[], size = 8): string {
  const [tx, ty] = points[points.length - 1];
  const [sx, sy] = points[points.length - 2];
  const len = Math.hypot(tx - sx, ty - sy) || 1;
  const ux = (tx - sx) / len;
  const uy = (ty - sy) / len;
  const bx = tx - ux * size;
  const by = ty - uy * size;
  const w = size * 0.55;
  return `${tx},${ty} ${bx - uy * w},${by + ux * w} ${bx + uy * w},${by - ux * w}`;
}
