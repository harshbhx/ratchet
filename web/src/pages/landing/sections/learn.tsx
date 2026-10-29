import { Container } from "@/components/ui/section";
import { Chapter, ChapterHead, Lead, Reveal } from "../shared";
import { RealCurve } from "./real-curve";
import { MeasuredOnNebius } from "./measured-on-nebius";

export function Learn() {
  return (
    <Chapter id="learn" className="border-t border-line bg-bg-sunken/60">
      <Container className="relative">
        <ChapterHead
          id="learn"
          title="Watch it learn."
          lead={
            <Lead>
              The measured curve: the same small model, untrained, after one round of teacher labels and after two, on two exams it never saw.
            </Lead>
          }
        />
        <Reveal className="mt-12">
          <RealCurve />
        </Reveal>
        <Reveal className="mt-8">
          <MeasuredOnNebius />
        </Reveal>
      </Container>
    </Chapter>
  );
}
