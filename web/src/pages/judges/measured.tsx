import { MeasuredTiles } from "@/components/measured/measured";
import { Chapter } from "./ui";

/**
 * The runs that are not exams: what each cost and how fast it was, every one dated and linked to the evidence folder it was read from
 * (src/data/measured.json, written by scripts/measured_numbers.py and checked against evidence/ by a test).
 */
export function MeasuredChapter() {
  return (
    <Chapter
      id="measured"
      status="measured"
      title="Measured runs, with dates"
      lead={
        <p>
          Everything here was run for real and saved: the number is read from the saved files at build time, never typed. A recorded run is a replay of a real run's
          saved events. The student endpoint was served by hand for speed tests and serves no live traffic; the live gateway's teacher answers every request.
        </p>
      }
    >
      <MeasuredTiles ids={["retrain", "endpoint", "scout", "smoke", "labelling", "hosted-nano", "nightschool"]} />
    </Chapter>
  );
}
