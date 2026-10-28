import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { AnimatePresence, animate, motion, useReducedMotion } from "motion/react";
import { useLocation, useNavigate } from "react-router";
import examsText from "@/data/exams.json?raw";
import { parseExams } from "@/lib/exams-model";
import { useTheme } from "@/lib/hooks";
import { banterLines, createPicker, factsFromExams, nextDelayMs, readMs, type Line } from "./banter";
import { Bubble } from "./bubble";
import { Character } from "./character";
import { Companion } from "./companion";
import { COMPANION_EVENT, signal, type CompanionSignal } from "./companion-bus";
import { baseLook, isNight, isPhone, isTyping, motionPlan, type Look, type Who } from "./companion-model";
import { readSession, writeSession, type Session } from "./companion-storage";
import type { GamePhase } from "./game";
import { LESSON_TOTAL, earn, lessonsComplete, lessonsForSection, type LessonId } from "./lessons";
import { createBurst } from "./physics";
import type { Move } from "./perch";
import { useRoamer, useRoaming } from "./roaming";
import { AVOID_SELECTOR, chooseSlot, type Slot } from "./placement";
import { DUO_LINES, DUO_MOVES, EASTER_EGG, WATCHED_SECTIONS, liveReaction, pokeReaction, sectionReactions, shouldReact, type DuoMove, type Poke } from "./reactions";
import { ProgressRing, Sheet } from "./sheet";
import { Sparkles } from "./sparkles";
import { STORY_IDLE, storyReducer, storySteps } from "./story";
import { StoryBar } from "./story-bar";
import { useActor } from "./actor";

const FACTS = factsFromExams(parseExams(examsText));
const DOCK = { w: 64, h: 64 };
const RING = { w: 44, h: 44 };
/** The character rectangles (the SVG is 120 by 130): Nano 84 wide on a desktop, 48 on a phone; Ultra 108 and 60. */
const SIZES = {
  desktop: { nano: { w: 84, h: 91 }, ultra: { w: 108, h: 117 } },
  phone: { nano: { w: 48, h: 52 }, ultra: { w: 60, h: 65 } },
};
const THROWN: Record<Who, string[]> = {
  nano: ["Wheee!", "Again, again!", "I'm a student, not luggage!"],
  ultra: ["Gently, please.", "Put me down. Dignity first.", "Noted. Do not repeat."],
};
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/**
 * Nano and Ultra: the living layer. One instance in the Layout, loaded after first paint. It owns the two characters' looks and
 * bodies, the banter, the pokes and throws, the reactions to sections and to the theme, the lessons, the sheet and the story.
 * Under reduced motion they are static portraits (no moves, no physics, no auto-scroll) and everything is still available as text.
 */
export default function Stage({ pending, onPendingHandled }: { pending: CompanionSignal["type"] | null; onPendingHandled: () => void }) {
  const reduce = !!useReducedMotion();
  const plan = motionPlan(reduce);
  const { theme } = useTheme();
  const dark = theme === "dark";
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const [vw, setVw] = useState(() => window.innerWidth);
  const phone = isPhone(vw);
  const [night, setNight] = useState(() => isNight(new Date()));
  const [tabHidden, setTabHidden] = useState(() => document.hidden);
  const [session, setSession] = useState<Session>(readSession);
  const sessionRef = useRef(session);
  const [squinting, setSquinting] = useState(false);
  const [over, setOver] = useState<Record<Who, Partial<Look>>>({ nano: {}, ultra: {} });
  const [speech, setSpeech] = useState<{ who: Who; text: string } | null>(null);
  const [liveText, setLiveText] = useState("");
  const [sheet, setSheet] = useState(false);
  const [burst, setBurst] = useState(0);
  const [slot, setSlot] = useState<Slot>({ side: "right", lift: 0, clear: true });
  const [moving, setMoving] = useState<Record<Who, Move | null>>({ nano: null, ultra: null });
  const [visible, setVisible] = useState<Record<Who, boolean>>({ nano: true, ultra: true });
  const [mobileActive, setMobileActive] = useState<Who>("nano");
  const [story, dispatch] = useReducer((s: typeof STORY_IDLE, a: Parameters<typeof storyReducer>[1]) => storyReducer(s, a, 9), STORY_IDLE);
  const [wantStory, setWantStory] = useState(false);

  const nano = useActor();
  const ultra = useActor();
  const actors = useMemo(() => ({ nano, ultra }), [nano, ultra]);
  const rn = useRoamer();
  const ru = useRoamer();
  const roamers = useMemo(() => ({ nano: rn, ultra: ru }), [rn, ru]);
  const dockRef = useRef<HTMLDivElement | null>(null);
  const steps = useMemo(() => storySteps(FACTS), []);

  const overTimers = useRef<Record<Who, number>>({ nano: 0, ultra: 0 });
  const speechTimer = useRef(0);
  const scriptTimer = useRef(0);
  const lastScroll = useRef(0);
  const dragging = useRef(false);
  const duoBusy = useRef(false);
  const speaking = useRef(false);
  const lastPoke = useRef<Partial<Record<Who, Poke["kind"]>>>({});
  const pokes = useRef(createBurst(5, 3000));
  const hardPokes = useRef(createBurst(3, 1200));
  const savedScroll = useRef(0);
  const remaining = useRef({ index: -1, ms: 0 });
  const barRef = useRef<HTMLDivElement | null>(null);
  const live = useRef({ story: story.status, sheet });
  live.current = { story: story.status, sheet };

  const ctx = { night, dark, graduated: session.graduated, squinting: reduce ? !dark : squinting };
  const looks: Record<Who, Look> = { nano: { ...baseLook("nano", ctx), ...over.nano }, ultra: { ...baseLook("ultra", ctx), ...over.ultra } };

  // ---------- looks and speech ----------
  const act = useCallback((who: Who, patch: Partial<Look>, ms?: number) => {
    window.clearTimeout(overTimers.current[who]);
    setOver((o) => ({ ...o, [who]: patch }));
    if (ms) overTimers.current[who] = window.setTimeout(() => setOver((o) => ({ ...o, [who]: {} })), ms);
  }, []);

  const say = useCallback((who: Who, text: string, ms = readMs(text)) => {
    setSpeech({ who, text });
    setLiveText(text);
    window.clearTimeout(speechTimer.current);
    speaking.current = true;
    speechTimer.current = window.setTimeout(() => {
      speaking.current = false;
      setSpeech(null);
    }, ms);
  }, []);

  const runLines = useCallback(
    (lines: Line[], gap = 250) => {
      window.clearTimeout(scriptTimer.current);
      let i = 0;
      const step = () => {
        const l = lines[i++];
        if (!l) return;
        const ms = readMs(l.text);
        say(l.who, l.text, ms);
        if (l.expr) act(l.who, { expr: l.expr }, ms + 300);
        scriptTimer.current = window.setTimeout(step, ms + gap);
      };
      step();
    },
    [act, say],
  );

  const busy = useCallback(() => {
    const l = live.current;
    return l.story !== "idle" || l.sheet || dragging.current || document.hidden || isTyping(document.activeElement) || Date.now() - lastScroll.current < 1500;
  }, []);

  // ---------- lessons ----------
  const celebrate = useCallback(() => {
    setBurst((b) => b + 1);
    window.setTimeout(() => {
      runLines([
        { who: "nano", text: `${LESSON_TOTAL} for ${LESSON_TOTAL}. Cap, please!`, expr: "proud" },
        { who: "ultra", text: "Fine. Take the cap.", expr: "happy" },
      ]);
      if (!reduce) void ultra.nod();
    }, 500);
  }, [runLines, reduce, ultra]);

  const earnLesson = useCallback(
    (id: LessonId) => {
      const cur = sessionRef.current;
      const r = earn(cur.lessons, id);
      if (!r.newly) return;
      const done = lessonsComplete(r.state);
      const next: Session = { ...cur, lessons: r.state, graduated: cur.graduated || done };
      sessionRef.current = next;
      setSession(next);
      writeSession(next);
      if (done && !cur.graduated) celebrate();
    },
    [celebrate],
  );

  // ---------- moves between the two ----------
  const duo = useCallback(
    async (m: DuoMove) => {
      if (duoBusy.current) return;
      duoBusy.current = true;
      runLines(DUO_LINES[m]);
      const clear = () => {
        act("nano", {});
        act("ultra", {});
      };
      try {
        if (m === "highfive") {
          act("nano", { pose: "pointRight", expr: "happy" });
          act("ultra", { pose: "pointLeft", expr: "happy" });
          if (plan.physics) await Promise.all([nano.slide(18), ultra.slide(-18)]);
          setBurst((b) => b + 1);
          if (plan.physics) await Promise.all([nano.hop(14), ultra.hop(8)]);
          await sleep(900);
          if (plan.physics) await Promise.all([nano.home(), ultra.home()]);
        } else if (m === "pat") {
          act("ultra", { pose: "pat", expr: "happy" });
          act("nano", { expr: "proud", blush: true, pop: "heart" });
          if (plan.physics) {
            await ultra.slide(-34);
            await Promise.all([ultra.nod(), nano.squash(500)]);
          }
          await sleep(1500);
          if (plan.physics) await ultra.home();
        } else if (m === "pass") {
          act("ultra", { held: "card", pose: "carry", expr: "idle" });
          if (plan.physics) await ultra.slide(-16);
          await sleep(900);
          act("ultra", { held: "none", pose: "stand" });
          act("nano", { held: "card", pose: "carry", expr: "thinking", pop: "?" });
          if (plan.physics) await ultra.home();
          await sleep(2200);
        } else {
          act("nano", { expr: "surprised", sweat: true, pop: "!" });
          act("ultra", { expr: "determined", pose: "stop" });
          if (plan.physics) await nano.slide(64);
          await sleep(2600);
          if (plan.physics) await nano.home();
        }
      } finally {
        clear();
        duoBusy.current = false;
      }
    },
    [act, nano, ultra, plan.physics, runLines],
  );

  // ---------- play ----------
  const poke = useCallback(
    (who: Who) => {
      earnLesson("hello");
      const t = Date.now();
      const a = actors[who];
      if (pokes.current.push(t)) {
        void (plan.physics ? nano.spin() : Promise.resolve());
        act("nano", { expr: "laughing", pop: "sparkle" }, 2500);
        runLines(EASTER_EGG);
        return;
      }
      if (who === "nano" && hardPokes.current.push(t) && plan.physics) {
        void duo("hide");
        return;
      }
      const r = pokeReaction(who, Math.random, lastPoke.current[who]);
      lastPoke.current[who] = r.kind;
      act(who, r.look, 2200);
      say(who, r.say);
      if (plan.physics) {
        if (r.kind === "giggle" || r.kind === "startle") void a.shake();
        else if (r.kind === "blush") void a.squash(500);
      }
    },
    [act, actors, duo, earnLesson, nano, plan.physics, runLines, say],
  );

  const jump = useCallback(
    (who: Who) => {
      if (!plan.physics) return;
      act(who, { pose: "jump", expr: "happy" }, 750);
      void actors[who].hop(who === "nano" ? 48 : 36);
    },
    [act, actors, plan.physics],
  );

  const onGrab = useCallback(
    (who: Who) => {
      dragging.current = true;
      act(who, { expr: "surprised", pop: "!" });
    },
    [act],
  );
  const onThrow = useCallback(
    (who: Who) => {
      dragging.current = false;
      act(who, { expr: "laughing" }, 1800);
      if (Math.random() < 0.5) say(who, THROWN[who][Math.floor(Math.random() * THROWN[who].length)]!);
    },
    [act, say],
  );

  // ---------- the routing game (sheet) ----------
  const onPhase = useCallback(
    (p: GamePhase) => {
      const card = { held: "card", pose: "carry" } as const;
      if (p === "catch") {
        act("nano", { ...card, expr: "surprised", pop: "!" });
        act("ultra", {});
        if (plan.physics) void nano.hop(14);
      } else if (p === "try1" || p === "try2" || p === "try3") act("nano", { ...card, expr: "thinking", pop: "?" });
      else if (p === "unsure") act("nano", { ...card, expr: "sad", sweat: true });
      else if (p === "throw") {
        act("nano", { held: "none", pose: "pointRight", expr: "determined" });
        act("ultra", { expr: "surprised", pop: "!" });
      } else if (p === "answer") {
        act("nano", { expr: "happy" });
        act("ultra", { ...card, expr: "proud" });
        setBurst((b) => b + 1);
        earnLesson("cascade");
      }
    },
    [act, earnLesson, nano, plan.physics],
  );

  const closeSheet = useCallback(() => {
    setSheet(false);
    act("nano", {});
    act("ultra", {});
  }, [act]);

  const send = useCallback(
    (q: string) => {
      closeSheet();
      navigate("/live", { state: { companionPrefill: q } });
    },
    [closeSheet, navigate],
  );

  // ---------- the story ----------
  const scrollToTarget = useCallback((id: string | null) => {
    const el = id ? document.getElementById(id) : null;
    const top = el ? el.getBoundingClientRect().top + window.scrollY - 76 : 0;
    window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }, []);

  const startStory = useCallback(() => {
    setSheet(false);
    savedScroll.current = pathname === "/" ? window.scrollY : 0;
    if (pathname !== "/") navigate("/");
    setWantStory(true);
  }, [navigate, pathname]);

  useEffect(() => {
    if (!wantStory || pathname !== "/") return;
    let tries = 0;
    const id = window.setInterval(() => {
      if (document.getElementById("rent") || tries++ > 40) {
        window.clearInterval(id);
        setWantStory(false);
        dispatch({ type: "start" });
      }
    }, 100);
    return () => window.clearInterval(id);
  }, [wantStory, pathname]);

  const exitStory = useCallback(() => {
    dispatch({ type: "exit" });
    act("nano", {});
    act("ultra", {});
    window.scrollTo({ top: savedScroll.current, behavior: "instant" as ScrollBehavior });
  }, [act]);

  const storyKey = story.status === "idle" ? -1 : story.status === "done" ? 99 : story.index;
  useEffect(() => {
    if (storyKey === -1) return;
    window.clearTimeout(scriptTimer.current);
    setSpeech(null);
    if (storyKey === 99) {
      act("nano", { expr: "wave", pop: "sparkle" });
      act("ultra", { expr: "happy" });
      setBurst((b) => b + 1);
      earnLesson("adapter");
      return;
    }
    const step = steps[storyKey]!;
    act("nano", step.nano);
    act("ultra", step.ultra);
    if (step.lesson) earnLesson(step.lesson);
    if (plan.autoScroll) scrollToTarget(step.target);
  }, [storyKey, steps, act, earnLesson, plan.autoScroll, scrollToTarget]);

  useEffect(() => {
    if (story.status !== "playing" || reduce) return;
    const step = steps[story.index]!;
    const rem = remaining.current.index === story.index ? remaining.current.ms : step.ms;
    const t0 = Date.now();
    const id = window.setTimeout(() => dispatch({ type: "next" }), rem);
    return () => {
      window.clearTimeout(id);
      remaining.current = { index: story.index, ms: Math.max(400, rem - (Date.now() - t0)) };
    };
  }, [story.status, story.index, reduce, steps]);

  useEffect(() => {
    if (story.status === "idle") return;
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !live.current.sheet) exitStory();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [story.status, exitStory]);

  // ---------- what the page tells us ----------
  useEffect(() => {
    const on = (e: Event) => {
      const s = (e as CustomEvent<CompanionSignal>).detail;
      if (!s) return;
      if (s.type === "story") startStory();
      else if (s.type === "sheet") setSheet(true);
      else if (s.type === "live") {
        if (s.phase === "sent") earnLesson("adapter");
        if (!live.current.sheet && live.current.story === "idle") runLines(liveReaction(s.phase));
      }
      if (s.type === "story" || s.type === "sheet") window.setTimeout(onPendingHandled, 0);
    };
    window.addEventListener(COMPANION_EVENT, on);
    return () => window.removeEventListener(COMPANION_EVENT, on);
  }, [startStory, earnLesson, runLines, onPendingHandled]);

  useEffect(() => {
    if (pending === "story") startStory();
    else if (pending === "sheet") setSheet(true);
    if (pending) onPendingHandled();
    // once, at mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------- the page around them ----------
  useEffect(() => {
    const onResize = () => setVw(window.innerWidth);
    const onScroll = () => (lastScroll.current = Date.now());
    const onVis = () => setTabHidden(document.hidden);
    const clock = window.setInterval(() => setNight(isNight(new Date())), 60_000);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(clock);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  // Nano squints when the page turns light (and a little at first), and keeps the sunglasses for the dark theme.
  const firstTheme = useRef(true);
  useEffect(() => {
    if (reduce) return;
    if (firstTheme.current) {
      firstTheme.current = false;
      if (dark) return;
    }
    if (dark) return setSquinting(false);
    setSquinting(true);
    const id = window.setTimeout(() => setSquinting(false), 2400);
    return () => window.clearTimeout(id);
  }, [dark, reduce]);

  // The shelf stays clear of the primary calls to action.
  useEffect(() => {
    const size = phone || reduce ? DOCK : RING;
    let raf = 0;
    const run = () => {
      raf = 0;
      const zones = Array.from(document.querySelectorAll(AVOID_SELECTOR))
        .map((e) => e.getBoundingClientRect())
        .filter((r) => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight);
      setSlot((prev) => {
        const n = chooseSlot({ w: window.innerWidth, h: window.innerHeight }, size, zones, prev);
        return n.side === prev.side && n.lift === prev.lift ? prev : n;
      });
    };
    const req = () => {
      if (!raf) raf = requestAnimationFrame(run);
    };
    window.addEventListener("scroll", req, { passive: true });
    window.addEventListener("resize", req);
    const iv = window.setInterval(req, 800);
    req();
    return () => {
      cancelAnimationFrame(raf);
      window.clearInterval(iv);
      window.removeEventListener("scroll", req);
      window.removeEventListener("resize", req);
    };
  }, [phone, reduce, pathname]);

  // Reactions to the landing sections: lessons for every key section, a comment once per section per visit.
  useEffect(() => {
    if (pathname !== "/") return;
    const reactions = sectionReactions(FACTS);
    const visible = new Set<string>();
    const timers: number[] = [];
    let io: IntersectionObserver | undefined;
    let poll = 0;
    let tries = 0;
    const idOf = (el: Element) => (el.tagName === "FOOTER" ? "footer" : el.id);
    const tryReact = (id: string, attempt = 0) => {
      timers.push(
        window.setTimeout(() => {
          if (!visible.has(id)) return;
          const cur = sessionRef.current;
          if (!shouldReact(cur.seen, id)) return;
          if (busy()) {
            if (attempt < 4) tryReact(id, attempt + 1);
            return;
          }
          const r = reactions[id];
          if (!r) return;
          const next = { ...cur, seen: [...cur.seen, id] };
          sessionRef.current = next;
          setSession(next);
          writeSession(next);
          const total = r.lines.reduce((n, l) => n + readMs(l.text) + 250, 400);
          if (r.nano) act("nano", r.nano, total);
          if (r.ultra) act("ultra", r.ultra, total);
          runLines(r.lines);
        }, 900),
      );
    };
    const attach = () => {
      const els = [...WATCHED_SECTIONS.map((id) => document.getElementById(id)), document.querySelector("footer")].filter((e): e is HTMLElement => !!e);
      if (els.length < 6 && tries++ < 20) {
        poll = window.setTimeout(attach, 500);
        return;
      }
      io = new IntersectionObserver(
        (entries) => {
          for (const e of entries) {
            const id = idOf(e.target);
            if (e.isIntersecting) {
              visible.add(id);
              lessonsForSection(id).forEach(earnLesson);
              tryReact(id);
            } else visible.delete(id);
          }
        },
        { rootMargin: "-32% 0px -32% 0px" },
      );
      els.forEach((el) => io!.observe(el));
    };
    attach();
    return () => {
      window.clearTimeout(poll);
      timers.forEach((t) => window.clearTimeout(t));
      io?.disconnect();
    };
  }, [pathname, act, busy, earnLesson, runLines]);

  // ---------- roaming ----------
  const sizes = phone ? SIZES.phone : SIZES.desktop;
  const roam = useRoaming({
    enabled: plan.physics,
    phone,
    active: mobileActive,
    sizes,
    actors,
    roamers,
    extraZones: () => {
      const out: { left: number; top: number; right: number; bottom: number }[] = [];
      const bar = barRef.current?.getBoundingClientRect();
      if (bar) out.push({ left: bar.left, top: bar.top, right: bar.right, bottom: bar.bottom });
      const d = dockRef.current?.getBoundingClientRect();
      if (d && d.width) out.push({ left: d.left, top: d.top, right: d.right, bottom: d.bottom });
      return out;
    },
    onMove: (who, m) => setMoving((o) => (o[who] === m ? o : { ...o, [who]: m })),
    onLook: act,
    onVisible: (who, v) => setVisible((o) => (o[who] === v ? o : { ...o, [who]: v })),
    hold: () => live.current.sheet || dragging.current,
  });

  // On a phone one of them walks at a time, the other waits in the dock.
  useEffect(() => {
    if (!phone || !plan.physics) return;
    const id = window.setInterval(() => setMobileActive((w) => (w === "nano" ? "ultra" : "nano")), 30_000);
    return () => window.clearInterval(id);
  }, [phone, plan.physics]);
  useEffect(() => {
    roam.repick();
    const id = window.setTimeout(roam.repick, 700);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, mobileActive]);

  // Idle life: a greeting, then small moves every so often, and banter every 25 to 40 seconds (Ultra walks over to Nano to talk).
  useEffect(() => {
    if (plan.autonomous) {
      act("nano", { expr: "wave" }, 2600);
    }
    const pool = banterLines(FACTS).filter((b) => !b.when || b.when === (night ? "night" : "day"));
    const picker = createPicker(pool);
    let talk = 0;
    let move = 0;
    let rel = 0;
    const fireTalk = () => {
      if (busy() || duoBusy.current || speaking.current) {
        talk = window.setTimeout(fireTalk, 4000);
        return;
      }
      const b = picker.next();
      if (b) {
        const total = b.lines.reduce((n, l) => n + readMs(l.text) + 250, 0);
        const walk = plan.physics && !phone;
        if (walk) roam.meet();
        window.setTimeout(() => runLines(b.lines), walk ? 1100 : 0);
        window.clearTimeout(rel);
        rel = window.setTimeout(roam.release, total + 1800);
      }
      talk = window.setTimeout(fireTalk, nextDelayMs());
    };
    const fireMove = () => {
      move = window.setTimeout(fireMove, 6000 + Math.random() * 6000);
      if (!plan.autonomous || busy() || duoBusy.current || speaking.current) return;
      const r = Math.random();
      if (r < 0.14) void nano.hop(26);
      else if (r < 0.24) {
        // excited: three little bounces
        act("nano", { expr: "happy", pop: "sparkle" }, 2000);
        void nano.hop(20).then(() => nano.hop(16)).then(() => nano.hop(12));
      } else if (r < 0.31) void ultra.nod();
      else if (r < 0.4) {
        // a stretch
        act("nano", { pose: "jump", expr: "happy" }, 1100);
        void animate(nano.sy, [1, 1.1, 1], { duration: 1 });
      } else if (r < 0.5) act("ultra", { pose: "sip", held: "mug", expr: "happy" }, 2600);
      else if (r < 0.6) roam.wander("nano");
      else if (r < 0.66) roam.wander("ultra");
      else if (r < 0.72) act("nano", { expr: "thinking", pop: "?" }, 1800);
      else if (r < 0.78 && !dark) {
        setSquinting(true);
        window.setTimeout(() => setSquinting(false), 1300);
      } else if (r < 0.84) act("ultra", { adjust: true }, 900);
      else if (r < 0.92) act(Math.random() < 0.5 ? "nano" : "ultra", { micro: Math.random() < 0.5 ? "twitch" : "shake" }, 900);
      else void duo(DUO_MOVES[Math.floor(Math.random() * DUO_MOVES.length)]!);
    };
    talk = window.setTimeout(fireTalk, nextDelayMs());
    move = window.setTimeout(fireMove, 6000);
    return () => {
      window.clearTimeout(talk);
      window.clearTimeout(move);
      window.clearTimeout(rel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [night, plan.autonomous, plan.physics, phone, dark, act, busy, duo, nano, ultra, runLines]);

  useEffect(
    () => () => {
      window.clearTimeout(speechTimer.current);
      window.clearTimeout(scriptTimer.current);
      window.clearTimeout(overTimers.current.nano);
      window.clearTimeout(overTimers.current.ultra);
    },
    [],
  );

  // ---------- render ----------
  const n = session.lessons.ids.length;
  const storyOn = story.status !== "idle";
  const roaming = plan.physics;
  const walkers: Who[] = !roaming ? [] : phone ? [mobileActive] : ["nano", "ultra"];
  const shown = (w: Who) => walkers.includes(w) && visible[w];
  // Which face the dock shows: the one not out walking (Nano when both are out or on reduced motion).
  const dockWho: Who = phone && roaming ? (visible[mobileActive] ? (mobileActive === "nano" ? "ultra" : "nano") : mobileActive) : "nano";
  const ringOnly = roaming && !phone;
  const dockSize = ringOnly ? RING : DOCK;
  const shiftX = slot.side === "left" ? -(vw - 24 - dockSize.w) : 0;
  const dockMove = reduce ? { duration: 0 } : ({ type: "spring", stiffness: 140, damping: 20 } as const);
  const speakerShownOnRoamer = !!speech && shown(speech.who);

  const gazeFor = (who: Who) => {
    if (!speech || speech.who === who || !shown(speech.who) || !shown(who)) return null;
    const dx = roamers[speech.who].px.get() - roamers[who].px.get();
    const dy = roamers[speech.who].py.get() - roamers[who].py.get();
    const d = Math.hypot(dx, dy) || 1;
    return { x: dx / d, y: dy / d };
  };

  const roamer = (who: Who) => {
    const r = roamers[who];
    const size = sizes[who];
    const svgSize = phone ? (who === "nano" ? 48 : 60) : who === "nano" ? 84 : 108;
    const left = r.px.get() < vw / 2;
    return (
      <motion.div
        key={who}
        style={{ x: r.px, y: r.py, width: size.w, height: size.h, marginLeft: -size.w / 2, marginTop: -size.h }}
        className={`pointer-events-none fixed left-0 top-0 z-40 ${sheet || !visible[who] ? "invisible" : ""}`}
      >
        <AnimatePresence>
          {speech?.who === who && <Bubble key={speech.text} who={who} text={speech.text} reduce={reduce} align={left ? "left" : "right"} />}
        </AnimatePresence>
        {who === "nano" && <Sparkles burst={burst} />}
        <motion.div style={{ rotate: r.lean, originX: 0.5, originY: 1 }}>
          <Companion
            who={who}
            look={looks[who]}
            size={svgSize}
            actor={actors[who]}
            live={plan.ambient}
            draggable={plan.physics}
            moving={moving[who]}
            talking={speech?.who === who}
            gaze={gazeFor(who)}
            onPoke={() => poke(who)}
            onJump={() => jump(who)}
            onGrab={() => onGrab(who)}
            onThrow={() => onThrow(who)}
          />
        </motion.div>
      </motion.div>
    );
  };

  return (
    <div className="cp-root contents" data-paused={tabHidden}>
      <div role="status" aria-live="polite" className="sr-only">
        {liveText}
      </div>

      {walkers.map((w) => roamer(w))}

      {!(phone && storyOn) && (
        <motion.div
          ref={dockRef}
          animate={{ x: shiftX, y: -slot.lift }}
          transition={dockMove}
          style={{ width: dockSize.w, height: dockSize.h }}
          className={`pointer-events-none fixed bottom-3 right-3 z-40 ${sheet ? "invisible" : ""}`}
        >
          <AnimatePresence>
            {speech && !speakerShownOnRoamer && <Bubble key={speech.text} who={speech.who} text={speech.text} reduce={reduce} align="right" />}
          </AnimatePresence>
          <button
            type="button"
            onClick={() => setSheet(true)}
            aria-label={
              ringOnly
                ? `Nano learned ${n} of ${LESSON_TOTAL} lessons from your visit. Open Nano and Ultra.`
                : `Open Nano and Ultra. Nano learned ${n} of ${LESSON_TOTAL} lessons from your visit.`
            }
            className={
              ringOnly
                ? "pointer-events-auto absolute inset-0 flex items-center justify-center rounded-full border border-line-strong bg-surface shadow-card"
                : "pointer-events-auto relative flex size-16 items-end justify-center overflow-visible rounded-full border border-line-strong bg-surface shadow-card"
            }
          >
            {ringOnly ? (
              <ProgressRing n={n} size={34} />
            ) : (
              <>
                <Character who={dockWho} look={looks[dockWho]} size={50} live={plan.ambient} talking={speech?.who === dockWho} className="mb-1" />
                <ProgressRing n={n} size={26} className="absolute -right-1 -top-1" />
              </>
            )}
          </button>
        </motion.div>
      )}

      <AnimatePresence>
        {storyOn && (
          <StoryBar
            key="story"
            ref={barRef}
            steps={steps}
            state={story}
            reduce={reduce}
            phone={phone}
            looks={looks}
            onPause={() => dispatch({ type: "pause" })}
            onResume={() => dispatch({ type: "resume" })}
            onNext={() => dispatch({ type: "next" })}
            onPrev={() => dispatch({ type: "prev" })}
            onGo={() => scrollToTarget(steps[story.index]?.target ?? null)}
            onReplay={() => dispatch({ type: "start" })}
            onExit={exitStory}
          />
        )}
      </AnimatePresence>

      {sheet && (
        <Sheet
          phone={phone}
          reduce={reduce}
          live={plan.ambient}
          looks={looks}
          actors={actors}
          lessons={session.lessons}
          line={speech?.text ?? ""}
          celebrate={burst}
          onClose={closeSheet}
          onStory={startStory}
          onHide={() => signal({ type: "hide" })}
          onPoke={poke}
          onJump={jump}
          onPhase={onPhase}
          onSend={send}
        />
      )}
    </div>
  );
}
