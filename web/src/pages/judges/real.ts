import realText from "@/data/real.json?raw";
import { parseReal } from "@/lib/real-model";

/** The "What's real" data (src/data/real.json), parsed and checked once. */
export const REAL = parseReal(realText);
