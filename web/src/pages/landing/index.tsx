import { useMemo } from "react";
import { WhoProvider } from "@/components/ui/who";
import { useStats } from "@/lib/hooks";
import { whoContext } from "@/lib/who-model";
import { HonestBill } from "./sections/bill";
import { Catch } from "./sections/catch";
import { Checked } from "./sections/checked";
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

/**
 * The story page: a hero, then ten chapters told in scroll order. One stats poll feeds every live number on the page
 * (the live strip, the honest bill, the trust chapter); without a gateway they fall back to labelled simulation or hide.
 * The same poll tells the badges which student answers now (FR-7.40).
 */
export default function LandingPage() {
  const { stats } = useStats(15000);
  const who = useMemo(() => whoContext(stats), [stats]);
  return (
    <WhoProvider value={who}>
      <div className="overflow-x-clip">
        <ChapterRail />
        <Hero />
        <LiveStrip stats={stats} />
        <OneLineChange />
        <Rent />
        <Catch />
        <How />
        <Safety />
        <Learn />
        <HonestBill stats={stats} />
        <Checked stats={stats} />
        <Scout />
        <Stack />
        <OpenSource />
      </div>
    </WhoProvider>
  );
}
