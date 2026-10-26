import recordedText from "@/data/recorded-tries.json?raw";
import { parseRecordedTries } from "./try-request-model";

/** The four answers recorded from the live demo (src/data/recorded-tries.json), parsed and checked once. */
export const RECORDED_TRIES = parseRecordedTries(recordedText);
