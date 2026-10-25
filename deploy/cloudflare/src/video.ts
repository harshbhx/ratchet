// The /video short link on the simulation demo: a plain redirect to the demo video's counting link, tagged with where the click came from.
// No imports and no external files: the Worker answers it before anything else (no container start, no gateway call, nothing counted
// here), and web/tests/video-links.test.mjs runs it under Node.

/** video.ratchet.dualmindlab.tech counts the `from` tag and sends the visitor on to the video on YouTube. */
export const VIDEO_URL = "https://video.ratchet.dualmindlab.tech/?from=demo";

/** The answer for /video (and /video/): a 302 that is never cached, so each visit reaches the counter. Null for any other path. */
export function videoRedirect(pathname: string): Response | null {
  if (pathname !== "/video" && pathname !== "/video/") return null;
  return new Response(null, { status: 302, headers: { location: VIDEO_URL, "cache-control": "no-store" } });
}
