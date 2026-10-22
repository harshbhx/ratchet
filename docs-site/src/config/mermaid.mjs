// @ts-check
/*
  Mermaid's configuration for the docs site, passed to astro-mermaid in astro.config.mjs.

  How light and dark work. astro-mermaid's `autoTheme` swaps Mermaid between its own `default` and `dark` themes and
  re-renders every diagram when `data-theme` changes; that would throw away the `base` theme and its brand variables.
  So `autoTheme` is off, every diagram renders once with the `base` theme, and the colours come from `themeCSS`: rules
  written in the site's CSS variables (src/styles/theme.css), which follow `data-theme` without a re-render.
  Mermaid prefixes each `themeCSS` rule with the diagram's own id, so these rules win over its built-in ones and over a
  diagram's own `classDef` colours (the kit's selectors carry one more class than a classDef's).
  The `themeVariables` below are the light palette as plain hex: Mermaid derives some colours from them (and cannot read
  CSS variables), and they are what a diagram looks like if `themeCSS` is ever missing.

  The style kit. Every diagram can tag nodes with these classes (`A:::student`, or `class A,B student`):
    student  owned blue: the student, Nano 4B with the adapter you own
    teacher  rented orange: the teacher you rent per token (Nemotron 3 Ultra)
    store    the ledger, a bucket, a file: anything that keeps data
    gate     a check or a decision that can stop the flow
    ok       a good outcome: live, served, passed
    danger   a bad outcome: rejected, rolled back, refused
    pending  written but not run yet (amber, dashed)
    outline  optional, off by default, or the untuned hosted fallback (dashed outline)
    neutral  the default look, for when a class must be named
  and five for subgraphs, which colour only the border: studentZone, teacherZone, okZone, pendingZone, outlineZone.
  The rules below style these names on their own (`A:::student` needs no classDef). The copy-paste `classDef` block
  in docs-site/README.md ("Diagrams") does the same with the same CSS variables; Mermaid writes a classDef's styles
  inline with !important, so a classDef must use the variables too, never a fixed colour, or dark mode breaks.

  Typography lives here too, not in diagrams.css: Mermaid measures every label with these rules before it sizes the
  boxes, so a font size or weight set only in the page's CSS would make text overflow its box.
*/

const FONT = '"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
const MONO = '"JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace';

/** The site's tokens (theme.css), light values: Mermaid's derived colours and the no-CSS fallback. */
const light = {
  bg: '#fbfaf7',
  sunken: '#f3f1ec',
  surface: '#ffffff',
  surface2: '#f7f6f2',
  ink: '#121212',
  ink2: '#3d3c39',
  muted: '#64625d',
  line: '#e6e3dc',
  lineStrong: '#d3cfc6',
  owned: '#2a78d6',
  ownedSoft: '#e3eefc',
  rented: '#eb6834',
  rentedSoft: '#fdeae1',
  good: '#1f8f3a',
  goodSoft: '#e3f4e7',
  warn: '#a86400',
  warnSoft: '#fbf0dc',
  bad: '#c92a2a',
  badSoft: '#fbe4e4',
};

/** A CSS variable from theme.css, with its light value as the fallback. */
const v = (name, fallback) => `var(--rt-${name}, ${fallback})`;
const T = {
  bg: v('bg', light.bg),
  sunken: v('bg-sunken', light.sunken),
  surface: v('surface', light.surface),
  surface2: v('surface-2', light.surface2),
  ink: v('ink', light.ink),
  ink2: v('ink-2', light.ink2),
  muted: v('muted', light.muted),
  line: v('line', light.line),
  lineStrong: v('line-strong', light.lineStrong),
  owned: v('owned', light.owned),
  ownedSoft: v('owned-soft', light.ownedSoft),
  rented: v('rented', light.rented),
  rentedSoft: v('rented-soft', light.rentedSoft),
  good: v('good', light.good),
  goodSoft: v('good-soft', light.goodSoft),
  warn: v('warn', light.warn),
  warnSoft: v('warn-soft', light.warnSoft),
  bad: v('bad', light.bad),
  badSoft: v('bad-soft', light.badSoft),
};

/** Shapes Mermaid draws for a node: a plain rect or polygon, or a group of paths for rounded and special shapes. */
const SHAPES = ':is(rect, polygon, path, circle, ellipse)';
const shape = (cls) => `.node.${cls} > ${SHAPES}, .node.${cls} > g:not(.label) > ${SHAPES}`;
const text = (cls) => `.node.${cls} :is(.nodeLabel, .nodeLabel p, span, text)`;

/** One kit class for nodes and states: fill, stroke and text. */
function kit(cls, { fill, stroke, width = '1.5px', dash = 'none', color = T.ink }) {
  return `
${shape(cls)} { fill: ${fill} !important; stroke: ${stroke} !important; stroke-width: ${width} !important; stroke-dasharray: ${dash} !important; }
${text(cls)} { color: ${color} !important; fill: ${color} !important; }`;
}

/** One zone class for subgraphs: the border only, so the nodes inside keep their own colours. */
function zone(cls, stroke, width = '1.25px') {
  return `\n.cluster.${cls} > rect { stroke: ${stroke} !important; stroke-width: ${width} !important; }`;
}

const themeCSS = `
/* ---------- Type: measured before layout, so it lives here ---------- */
:is(.label, .nodeLabel, .edgeLabel, .cluster-label, .stateLabel, .messageText, .noteText, .loopText, .labelText, text) {
  font-family: ${FONT};
}
/* The page's prose rules (paragraph size, line height, margins, text-wrap: pretty) reach the <p> inside a label once the
   diagram sits in the page, but not while Mermaid measures it: pin them here so both agree. */
:is(.nodeLabel, .edgeLabel, .cluster-label, .label) :is(p, span, div) {
  margin: 0; padding: 0; font-size: inherit; line-height: inherit; letter-spacing: inherit; white-space: inherit;
  text-wrap: inherit; text-indent: 0; max-width: none;
}
.nodeLabel, .nodeLabel p { font-size: 14px; line-height: 1.4; }
.nodeLabel code, .edgeLabel code, .cluster-label code { font-family: ${MONO}; font-size: 0.88em; }
.edgeLabel, .edgeLabel p, .edgeLabel span { font-size: 12.5px; line-height: 1.35; }
.cluster-label .nodeLabel, .cluster-label span, .cluster-label p { font-size: 12.5px; font-weight: 600; letter-spacing: 0.01em; }

/* ---------- Default nodes ---------- */
.node > ${SHAPES}, .node > g:not(.label) > ${SHAPES} {
  fill: ${T.surface2}; stroke: ${T.lineStrong}; stroke-width: 1px;
}
.node :is(.nodeLabel, .nodeLabel p, span) { color: ${T.ink}; }
.node text { fill: ${T.ink}; }

/* ---------- Subgraphs ---------- */
.cluster > rect { fill: color-mix(in srgb, ${T.ink} 3%, transparent) !important; stroke: ${T.lineStrong} !important; stroke-width: 1px !important; stroke-dasharray: 5 4; rx: 12px; ry: 12px; }
.cluster :is(.cluster-label, .nodeLabel, span, p, text) { color: ${T.ink2} !important; fill: ${T.ink2} !important; }

/* ---------- Edges and their labels ---------- */
.flowchart-link { stroke: ${T.muted} !important; stroke-width: 1.4px; }
.flowchart-link.edge-thickness-thick { stroke: ${T.ink2} !important; stroke-width: 2.4px !important; }
.flowchart-link.edge-pattern-dotted { stroke-dasharray: 3 4 !important; }
marker path, .marker, .arrowheadPath { fill: ${T.muted} !important; stroke: ${T.muted} !important; }
.edgeLabel, .edgeLabel p, .edgeLabel span { color: ${T.ink2} !important; }
.edgeLabel .labelBkg, .edgeLabel p, span.edgeLabel { background-color: ${T.surface} !important; }
.labelBkg { background-color: ${T.surface} !important; border-radius: 4px; }
.edgeLabel rect { fill: ${T.surface} !important; opacity: 1 !important; }

/* ---------- The kit ---------- */
${kit('student', { fill: T.ownedSoft, stroke: T.owned })}
${kit('teacher', { fill: T.rentedSoft, stroke: T.rented })}
${kit('store', { fill: T.surface2, stroke: T.muted, width: '1.25px' })}
${kit('gate', { fill: T.surface, stroke: T.ink2, width: '1.5px' })}
${kit('ok', { fill: T.goodSoft, stroke: T.good })}
${kit('danger', { fill: T.badSoft, stroke: T.bad })}
${kit('pending', { fill: T.warnSoft, stroke: T.warn, dash: '5 4' })}
${kit('outline', { fill: T.surface, stroke: T.muted, width: '1.25px', dash: '4 3', color: T.ink2 })}
${kit('neutral', { fill: T.surface2, stroke: T.lineStrong, width: '1px' })}
${zone('studentZone', T.owned)}
${zone('teacherZone', T.rented)}
${zone('okZone', T.good)}
${zone('pendingZone', T.warn)}
${zone('outlineZone', T.muted, '1px')}

/* ---------- Sequence diagrams ---------- */
rect.actor, .actor-box, rect.actor-top, rect.actor-bottom { fill: ${T.surface2} !important; stroke: ${T.lineStrong} !important; stroke-width: 1px !important; }
text.actor, text.actor > tspan { fill: ${T.ink} !important; font-weight: 600; }
/* The kit by participant id: \`participant Student as ...\`, \`participant Teacher as ...\`, \`participant Ledger as ...\`. */
rect.actor:is([name='Student'], [name='Endpoint']) { fill: ${T.ownedSoft} !important; stroke: ${T.owned} !important; stroke-width: 1.5px !important; }
rect.actor[name='Teacher'] { fill: ${T.rentedSoft} !important; stroke: ${T.rented} !important; stroke-width: 1.5px !important; }
rect.actor:is([name='Ledger'], [name='Bucket']) { stroke: ${T.muted} !important; stroke-width: 1.25px !important; }
.actor-line, line.actor-line { stroke: ${T.lineStrong} !important; stroke-dasharray: 3 4; }
.messageLine0, .messageLine1 { stroke: ${T.muted} !important; stroke-width: 1.4px !important; }
#arrowhead path, #crosshead path, #filled-head path, #sequencenumber { fill: ${T.muted} !important; stroke: ${T.muted} !important; }
.messageText, .messageText > tspan { fill: ${T.ink2} !important; stroke: none !important; }
.note, rect.note { fill: ${T.ownedSoft} !important; stroke: ${T.owned} !important; stroke-width: 1px !important; }
.noteText, .noteText > tspan { fill: ${T.ink} !important; stroke: none !important; }
.labelBox { fill: ${T.surface2} !important; stroke: ${T.lineStrong} !important; }
.labelText, .labelText > tspan { fill: ${T.ink2} !important; font-weight: 600; font-size: 12px; }
.loopText, .loopText > tspan, .sectionTitle { fill: ${T.ink2} !important; font-style: italic; font-size: 12.5px !important; }
.loopLine { stroke: ${T.lineStrong} !important; stroke-width: 1.25px !important; stroke-dasharray: 4 3; }
.activation0, .activation1, .activation2 { fill: ${T.surface} !important; stroke: ${T.muted} !important; }
.sequenceNumber { fill: ${T.surface} !important; font-family: ${FONT}; font-weight: 700; font-size: 11px; }

/* ---------- State diagrams ---------- */
.statediagram-state > ${SHAPES}, .statediagram-state rect.basic, .statediagram-state .label-container { stroke-width: 1px; }
.stateLabel .box, .statediagram-note rect { fill: ${T.surface2} !important; stroke: ${T.lineStrong} !important; }
.transition { stroke: ${T.muted} !important; stroke-width: 1.4px !important; }
.state-start, .start-state, circle.state-start { fill: ${T.ink} !important; stroke: ${T.ink} !important; }
.state-end { fill: ${T.ink} !important; stroke: ${T.surface} !important; }
.end-state-outer { fill: ${T.surface} !important; stroke: ${T.ink} !important; }
.end-state-inner { fill: ${T.ink} !important; }
.statediagram-cluster rect, .statediagram-cluster .inner { fill: ${T.bg} !important; stroke: ${T.lineStrong} !important; }
.statediagram-cluster .outer { fill: none !important; stroke: ${T.lineStrong} !important; }
`;

/** The full Mermaid configuration (astro-mermaid's `mermaidConfig`). */
export const mermaidConfig = {
  theme: 'base',
  fontFamily: FONT,
  // Mermaid's default is 16; sequence diagrams take their message, note and actor sizes from this one.
  fontSize: 14,
  themeCSS,
  themeVariables: {
    darkMode: false,
    fontFamily: FONT,
    fontSize: '14px',
    background: light.surface,
    // Nodes
    primaryColor: light.surface2,
    primaryBorderColor: light.lineStrong,
    primaryTextColor: light.ink,
    secondaryColor: light.ownedSoft,
    secondaryBorderColor: light.owned,
    secondaryTextColor: light.ink,
    tertiaryColor: light.bg,
    tertiaryBorderColor: light.lineStrong,
    tertiaryTextColor: light.ink,
    mainBkg: light.surface2,
    nodeBorder: light.lineStrong,
    nodeTextColor: light.ink,
    textColor: light.ink2,
    titleColor: light.ink2,
    // Subgraphs
    clusterBkg: light.bg,
    clusterBorder: light.lineStrong,
    // Edges
    lineColor: light.muted,
    defaultLinkColor: light.muted,
    edgeLabelBackground: light.surface,
    arrowheadColor: light.muted,
    // Notes
    noteBkgColor: light.ownedSoft,
    noteBorderColor: light.owned,
    noteTextColor: light.ink,
    // Sequence diagrams
    actorBkg: light.surface2,
    actorBorder: light.lineStrong,
    actorTextColor: light.ink,
    actorLineColor: light.lineStrong,
    signalColor: light.muted,
    signalTextColor: light.ink2,
    labelBoxBkgColor: light.surface2,
    labelBoxBorderColor: light.lineStrong,
    labelTextColor: light.ink2,
    loopTextColor: light.ink2,
    activationBkgColor: light.surface,
    activationBorderColor: light.muted,
    sequenceNumberColor: light.surface,
    // State diagrams
    stateBkg: light.surface2,
    stateLabelColor: light.ink,
    compositeBackground: light.bg,
    compositeTitleBackground: light.surface2,
    compositeBorder: light.lineStrong,
    altBackground: light.bg,
    transitionColor: light.muted,
    transitionLabelColor: light.ink2,
    specialStateColor: light.ink,
    innerEndBackground: light.ink,
    // Status colours, for diagram types that read them
    errorBkgColor: light.badSoft,
    errorTextColor: light.bad,
  },
  // Natural size, scrolling sideways when wider than the column, instead of shrinking the text.
  flowchart: {
    curve: 'basis',
    htmlLabels: true,
    useMaxWidth: false,
    padding: 11,
    nodeSpacing: 28,
    rankSpacing: 44,
    diagramPadding: 6,
    wrappingWidth: 220,
  },
  // Message, note and actor text take the top-level fontFamily and fontSize (Mermaid copies them over these).
  sequence: {
    useMaxWidth: false,
    // Mermaid's default 50 px on each side would push a five-participant diagram past the column.
    diagramMarginX: 8,
    diagramMarginY: 8,
    mirrorActors: false,
    showSequenceNumbers: false,
    width: 104,
    height: 54,
    actorMargin: 24,
    boxMargin: 12,
    boxTextMargin: 6,
    noteMargin: 10,
    messageMargin: 34,
    wrap: false,
  },
  state: {
    useMaxWidth: false,
    padding: 10,
    nodeSpacing: 40,
    rankSpacing: 46,
    titleShift: -12,
  },
  er: { useMaxWidth: false },
  class: { useMaxWidth: false },
};

/** The light palette, exported for docs and tests that want the kit's plain colours. */
export const diagramPalette = light;
