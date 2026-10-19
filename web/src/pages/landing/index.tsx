import { Catch } from "./sections/catch";
import { Hero, OneLineChange } from "./sections/hero";
import { How } from "./sections/how";
import { Learn } from "./sections/learn";
import { LiveStrip } from "./sections/live-strip";
import { OpenSource } from "./sections/open-source";
import { Rent } from "./sections/rent";
import { Safety } from "./sections/safety";
import { Scout } from "./sections/scout";
import { Stack } from "./sections/stack";
import { ChapterRail } from "./rail";

/** The story page: a hero, then eight chapters told in scroll order. */
export default function LandingPage() {
  return (
    <div className="overflow-x-clip">
      <ChapterRail />
      <Hero />
      <LiveStrip />
      <OneLineChange />
      <Rent />
      <Catch />
      <How />
      <Safety />
      <Learn />
      <Scout />
      <Stack />
      <OpenSource />
    </div>
  );
}
