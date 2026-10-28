import{m as e}from"./utils-CnmWD783.js";import{v as t}from"./section-Cs8fnTiB.js";import{i as n}from"./live-request-Z-6qR3cv.js";import{l as r}from"./index-CclA6-fa.js";var i={name:`list-checks`,size:24,node:[[`path`,{d:`M13 5h8`,key:`a7qcls`}],[`path`,{d:`M13 12h8`,key:`h98zly`}],[`path`,{d:`M13 19h8`,key:`c3s6r1`}],[`path`,{d:`m3 17 2 2 4-4`,key:`1jhpwq`}],[`path`,{d:`m3 7 2 2 4-4`,key:`1obspn`}]]};i.node;var a=t(i),o={name:`receipt`,size:24,node:[[`path`,{d:`M12 17V7`,key:`pyj7ub`}],[`path`,{d:`M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8`,key:`1elt7d`}],[`path`,{d:`M4 3a1 1 0 0 1 1-1 1.3 1.3 0 0 1 .7.2l.933.6a1.3 1.3 0 0 0 1.4 0l.934-.6a1.3 1.3 0 0 1 1.4 0l.933.6a1.3 1.3 0 0 0 1.4 0l.933-.6a1.3 1.3 0 0 1 1.4 0l.934.6a1.3 1.3 0 0 0 1.4 0l.933-.6A1.3 1.3 0 0 1 19 2a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1 1.3 1.3 0 0 1-.7-.2l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.934.6a1.3 1.3 0 0 1-1.4 0l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-1.4 0l-.934-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-.7.2 1 1 0 0 1-1-1z`,key:`ycz6yz`}]]};o.node;var s=t(o),c={name:`route`,size:24,node:[[`circle`,{cx:`6`,cy:`19`,r:`3`,key:`1kj8tv`}],[`path`,{d:`M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15`,key:`1d8sl`}],[`circle`,{cx:`18`,cy:`5`,r:`3`,key:`gq8acd`}]]};c.node;var l=t(c),u=`{
 "variant": "A",
 "variant_reason": "adapter not above the untrained model on both exams (hard: above, bfcl: about_same)",
 "date": "2026-10-24",
 "exams": {
  "hard": {
   "label": "Hard exam",
   "rows": 150,
   "scores": {
    "base": {
     "agree": 94,
     "n": 150,
     "rate": 0.6267,
     "lo": 0.547,
     "hi": 0.7
    },
    "adapter": {
     "agree": 136,
     "n": 150,
     "rate": 0.9067,
     "lo": 0.8494,
     "hi": 0.9436
    },
    "ultra": {
     "agree": 137,
     "n": 150,
     "rate": 0.9133,
     "lo": 0.8574,
     "hi": 0.9487
    }
   },
   "verdicts": {
    "adapter_vs_base": "above",
    "adapter_vs_ultra": "about_same"
   },
   "sentence": "On the hard exam the adapter is above the untrained model: 90.7% against 62.7%, and the 95% ranges do not overlap. It is about the same as the teacher: 90.7% against 91.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart).",
   "paired": {
    "fixed": 42,
    "broke": 0,
    "both_right": 94,
    "both_wrong": 14,
    "p": 0.0
   }
  },
  "bfcl": {
   "label": "BFCL exam",
   "rows": 300,
   "scores": {
    "base": {
     "agree": 232,
     "n": 300,
     "rate": 0.7733,
     "lo": 0.7227,
     "hi": 0.8171
    },
    "adapter": {
     "agree": 241,
     "n": 300,
     "rate": 0.8033,
     "lo": 0.7546,
     "hi": 0.8444
    },
    "ultra": {
     "agree": 265,
     "n": 300,
     "rate": 0.8833,
     "lo": 0.8421,
     "hi": 0.9149
    }
   },
   "verdicts": {
    "adapter_vs_base": "about_same",
    "adapter_vs_ultra": "lower"
   },
   "sentence": "On the BFCL exam the adapter is about the same as the untrained model: 80.3% against 77.3% (higher by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). It is lower than the teacher: 80.3% against 88.3% (241/300 against 265/300; the 95% ranges overlap only slightly, so part of the gap could be chance).",
   "paired": {
    "fixed": 15,
    "broke": 6,
    "both_right": 226,
    "both_wrong": 53,
    "p": 0.0784
   }
  }
 },
 "caveat": "Free-text arguments are a known weak spot in the training data: the strict scorer cannot agree on free text, so tools such as create_ticket (15 training rows), search_files, send_payment and search_inbox are thin. The hard exam uses 20 tools that were never in training.",
 "setting": "The adapter was trained on a borrowed GPU, on synthetic data. The hard exam (150 questions, 20 new tools) was written by Claude Sonnet 5.5 for evaluation only and is never training data; the second exam (300 questions) is drawn from the Berkeley Function Calling Leaderboard (Apache-2.0)."
}
`,d=[`hard`,`bfcl`],f=[`base`,`ultra`,`adapter`],p={base:`Untrained Nano 4B`,ultra:`Teacher (Nemotron 3 Ultra)`,adapter:`Tool-calling adapter`};function m(e){return d.every(t=>e.exams[t].scores.adapter.lo>e.exams[t].scores.base.hi)?`B`:`A`}function h(e){if(!e)return null;let t=JSON.parse(e);for(let e of d)for(let n of f){let r=t.exams[e].scores[n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`toolcall.json: ${e} ${n} is inconsistent`)}if(t.variant!==m(t))throw Error(`toolcall.json: the variant does not follow from the ranges`);return t}function g(e){return e?e.variant===`B`?`top`:`bottom`:`none`}var _=e=>`${(e*100).toFixed(1)}%`,v=e=>`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`,y=e=>`${e.agree} of ${e.n} · ${_(e.rate)}`,b=e(),x=h(Object.values(Object.assign({"../../data/toolcall.json":u}))[0]);function S({where:e}){let t=x;return!t||g(t)!==e?null:(0,b.jsxs)(`section`,{id:`toolcall`,"aria-labelledby":`toolcall-title`,className:`mt-9 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,b.jsxs)(`p`,{className:`text-[12.5px] font-medium text-muted`,children:[`Measured on real models`,t.date?`, ${r(t.date)}`:``,`.`]}),(0,b.jsx)(`h3`,{id:`toolcall-title`,className:`mt-1 text-[17px] font-semibold tracking-[-0.01em] text-ink`,children:`Tool calling: a second adapter, scored strictly on two exams`}),(0,b.jsx)(n,{wrapClassName:`mt-3`,children:(0,b.jsxs)(`table`,{className:`w-full min-w-[30rem] text-left text-[13.5px]`,children:[(0,b.jsx)(`caption`,{className:`sr-only`,children:`Strict tool-calling scores and 95% ranges on two exams`}),(0,b.jsx)(`thead`,{className:`border-b border-line text-[12px] text-muted`,children:(0,b.jsxs)(`tr`,{children:[(0,b.jsx)(`th`,{scope:`col`,className:`py-2 pr-3 font-medium`,children:`Model`}),d.map(e=>(0,b.jsxs)(`th`,{scope:`col`,className:`py-2 pr-3 font-medium`,children:[t.exams[e].label,` (`,t.exams[e].rows,`)`]},e))]})}),(0,b.jsx)(`tbody`,{className:`divide-y divide-line`,children:f.map(e=>(0,b.jsxs)(`tr`,{children:[(0,b.jsx)(`th`,{scope:`row`,className:`py-2 pr-3 font-medium ${e===`adapter`?`text-ink`:`text-ink-2`}`,children:p[e]}),d.map(n=>(0,b.jsxs)(`td`,{className:`py-2 pr-3 tabular-nums text-ink`,children:[y(t.exams[n].scores[e]),` `,(0,b.jsxs)(`span`,{className:`text-muted`,children:[`(`,v(t.exams[n].scores[e]),`)`]})]},n))]},e))})]})}),(0,b.jsx)(`ul`,{className:`mt-3 grid gap-2 text-[14px] leading-relaxed text-ink-2`,children:d.map(e=>(0,b.jsx)(`li`,{children:t.exams[e].sentence},e))}),(0,b.jsxs)(`p`,{className:`mt-3 max-w-[78ch] text-[13px] leading-relaxed text-muted`,children:[t.caveat,` `,t.setting]})]})}export{_ as a,s as c,p as i,a as l,S as n,y as o,d as r,l as s,x as t};