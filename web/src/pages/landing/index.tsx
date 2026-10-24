import { useMemo } from "react";
import { WhoProvider } from "@/components/ui/who";
import { useStats } from "@/lib/hooks";
import { whoContext } from "@/lib/who-model";
import { HonestBill } from "./sections/bill";
import { Catch } from "./sections/catch";
import { Checked } from "./sections/checked";
import { Hero } from "./sections/hero";
import { How } from "./sections/how";
import { Integrate } from "./sections/integrate";
import { Lifecycle } from "./sections/lifecycle";
import { Learn } from "./sections/learn";
import { LiveStrip } from "./sections/live-strip";
import { RunOnNebius } from "./sections/nebius";
import { OpenSource } from "./sections/open-source";
import { RealRun } from "./sections/real-run";
import { WhatsIn } from "./sections/whats-in";
import { Rent } from "./sections/rent";
import { Safety } from "./sections/safety";
import { Scout } from "./sections/scout";
import { Stack } from "./sections/stack";
import { TryIt } from "./sections/try-it";
import { Why } from "./sections/why";
import { ChapterRail } from "./rail";

/**
 * The story page: a hero, then ten chapters told in scroll order, with three interludes that are not chapters (why it
 * is different, the request explorer, the integration tabs). One stats poll feeds every live number on the page (the
 * live strip, the honest bill, the trust chapter); without a gateway they fall back to labelled simulation or hide.
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
        <RealRun />
        <TryIt />
        <WhatsIn />
        <LiveStrip stats={stats} />
        <Rent />
        <Catch />
        <Why />
        <How />
        <Lifecycle />
        <Safety />
        <Learn />
        <HonestBill stats={stats} />
        <Checked stats={stats} />
        <Scout />
        <Stack />
        <Integrate />
        <RunOnNebius />
        <OpenSource />
      </div>
    </WhoProvider>
  );
}
