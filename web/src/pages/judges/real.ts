import realText from "@/data/real.json?raw";
import { parseReal, videoHref } from "@/lib/real-model";

/** The "What's real" data (src/data/real.json), parsed and checked once. */
export const REAL = parseReal(realText);

/** The judges page's links to the video: its counting link, tagged from=judges; null while there is no video. */
export const JUDGES_VIDEO = videoHref(REAL.links, "judges");
