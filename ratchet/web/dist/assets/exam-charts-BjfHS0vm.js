import{m as e,o as t}from"./utils-CnmWD783.js";import{v as n}from"./section-Cs8fnTiB.js";import{v as r}from"./who-Ci-jgprf.js";import{n as i}from"./shield-check-Bhu86p57.js";import{f as a,o,s}from"./index-xBgCIggu.js";var c={name:`chevron-right`,size:24,node:[[`path`,{d:`m9 18 6-6-6-6`,key:`mthhwq`}]]};c.node;var l=n(c),u={name:`circle-dashed`,size:24,node:[[`path`,{d:`M10.1 2.182a10 10 0 0 1 3.8 0`,key:`5ilxe3`}],[`path`,{d:`M13.9 21.818a10 10 0 0 1-3.8 0`,key:`11zvb9`}],[`path`,{d:`M17.609 3.721a10 10 0 0 1 2.69 2.7`,key:`1iw5b2`}],[`path`,{d:`M2.182 13.9a10 10 0 0 1 0-3.8`,key:`c0bmvh`}],[`path`,{d:`M20.279 17.609a10 10 0 0 1-2.7 2.69`,key:`1ruxm7`}],[`path`,{d:`M21.818 10.1a10 10 0 0 1 0 3.8`,key:`qkgqxc`}],[`path`,{d:`M3.721 6.391a10 10 0 0 1 2.7-2.69`,key:`1mcia2`}],[`path`,{d:`M6.391 20.279a10 10 0 0 1-2.69-2.7`,key:`1fvljs`}]]};u.node;var d=n(u),f={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};f.node;var p=n(f),m={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};m.node;var h=n(m),g={name:`server`,size:24,node:[[`rect`,{width:`20`,height:`8`,x:`2`,y:`2`,rx:`2`,ry:`2`,key:`ngkwjq`}],[`rect`,{width:`20`,height:`8`,x:`2`,y:`14`,rx:`2`,ry:`2`,key:`iecqi9`}],[`line`,{x1:`6`,x2:`6.01`,y1:`6`,y2:`6`,key:`16zg32`}],[`line`,{x1:`6`,x2:`6.01`,y1:`18`,y2:`18`,key:`nzw8ys`}]]};g.node;var _=n(g),v={name:`shield-alert`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`M12 8v4`,key:`1got3b`}],[`path`,{d:`M12 16h.01`,key:`1drbdi`}]]};v.node;var y=n(v),b=e(),x={real:i,measured:p,pending:h,"not-run":d};function S({status:e,className:t,label:n}){let i=x[e],c=o[e];return(0,b.jsxs)(r,{tone:s[e],className:t,title:c.meaning,"aria-label":a(e),role:`img`,children:[(0,b.jsx)(i,{"aria-hidden":!0}),` `,n??c.label]})}var C=`{
 "updated": "2026-10-24",
 "models": [
  {
   "id": "base",
   "label": "Base model, no training",
   "short": "Base model"
  },
  {
   "id": "teacher",
   "label": "Teacher (Nemotron 3 Ultra), same prompts",
   "short": "Teacher"
  },
  {
   "id": "r1",
   "label": "Adapter, round 1 (821 rows)",
   "short": "Adapter, round 1"
  },
  {
   "id": "r2",
   "label": "Adapter, round 2 (4,322 rows)",
   "short": "Adapter, round 2"
  }
 ],
 "exams": [
  {
   "id": "exam1",
   "name": "Exam 1",
   "rows": 172,
   "built": "22 Oct 2026",
   "writer": "Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)",
   "writer_note": "The same model that wrote the training labels, so it favours the teacher and anything trained to copy it.",
   "industries": "14 industries; 57 of the 172 questions come from two industries (airline operations, pharmacy inventory) that were never in training. The other 115 are on databases that were not in training, in industries that were.",
   "checked": "Every reference query was run in the sandbox and kept only if it was one read-only SELECT returning 1 to 100 rows.",
   "fingerprint": "6b9c1db49bbe2aecf4157ed27845fe024d1b4fcb540f93d81b6987b982b22864",
   "evidence": "evidence/text2sql-round1"
  },
  {
   "id": "exam2",
   "name": "Exam 2",
   "rows": 200,
   "built": "23 Oct 2026",
   "writer": "Claude Sonnet 5.5, a different lab's model, used for evaluation only",
   "writer_note": "Written after training by a different author, so it does not depend on the teacher's style. No closed model's output is training data anywhere in this project.",
   "industries": "10 industries in neither the training data nor exam 1: museum collections, veterinary clinic, wine distribution, film production, wind farm maintenance, court cases, recycling plant, podcast network, cinema chain, fishing fleet.",
   "checked": "The same sandbox check, with the same mix as exam 1 (60 simple, 60 join, 40 aggregate, 40 hard). No row shares a question, a query or a set of table names with the training data.",
   "fingerprint": "b7634971cf6b0d79e64e30e4c16d1253fad65ab9a501a06edc6c9f8e70d69058",
   "evidence": "evidence/text2sql-exam2"
  }
 ],
 "scores": {
  "exam1": {
   "base": {
    "agree": 22,
    "n": 172,
    "rate": 0.1279,
    "lo": 0.086,
    "hi": 0.1861
   },
   "teacher": {
    "agree": 44,
    "n": 172,
    "rate": 0.2558,
    "lo": 0.1964,
    "hi": 0.3259
   },
   "r1": {
    "agree": 57,
    "n": 172,
    "rate": 0.3314,
    "lo": 0.2654,
    "hi": 0.4048
   },
   "r2": {
    "agree": 67,
    "n": 172,
    "rate": 0.3895,
    "lo": 0.3198,
    "hi": 0.4641
   }
  },
  "exam2": {
   "base": {
    "agree": 25,
    "n": 200,
    "rate": 0.125,
    "lo": 0.0861,
    "hi": 0.178
   },
   "teacher": {
    "agree": 30,
    "n": 200,
    "rate": 0.15,
    "lo": 0.1071,
    "hi": 0.2061
   },
   "r1": {
    "agree": 56,
    "n": 200,
    "rate": 0.28,
    "lo": 0.2224,
    "hi": 0.3459
   },
   "r2": {
    "agree": 66,
    "n": 200,
    "rate": 0.33,
    "lo": 0.2686,
    "hi": 0.3978
   }
  }
 },
 "difficulty": {
  "exam1": {
   "simple": {
    "n": 51,
    "base": 7,
    "teacher": 11,
    "r1": 18,
    "r2": 16
   },
   "join": {
    "n": 53,
    "base": 11,
    "teacher": 20,
    "r1": 15,
    "r2": 26
   },
   "aggregate": {
    "n": 36,
    "base": 3,
    "teacher": 8,
    "r1": 17,
    "r2": 18
   },
   "hard": {
    "n": 32,
    "base": 1,
    "teacher": 5,
    "r1": 7,
    "r2": 7
   }
  },
  "exam2": {
   "simple": {
    "n": 60,
    "base": 15,
    "teacher": 10,
    "r1": 19,
    "r2": 16
   },
   "join": {
    "n": 60,
    "base": 3,
    "teacher": 7,
    "r1": 5,
    "r2": 12
   },
   "aggregate": {
    "n": 40,
    "base": 5,
    "teacher": 9,
    "r1": 22,
    "r2": 24
   },
   "hard": {
    "n": 40,
    "base": 2,
    "teacher": 4,
    "r1": 10,
    "r2": 14
   }
  }
 },
 "r1_to_r2": {
  "exam1": {
   "wrong_to_right": 22,
   "right_to_wrong": 12,
   "both_right": 45,
   "both_wrong": 93,
   "p": 0.121
  },
  "exam2": {
   "wrong_to_right": 23,
   "right_to_wrong": 13,
   "both_right": 43,
   "both_wrong": 121,
   "p": 0.132
  }
 },
 "teacher_shape": {
  "exam1": {
   "exact": 44,
   "same_rows_other_columns": 74,
   "same_count_other_values": 28,
   "other_row_count": 24,
   "does_not_run": 2
  },
  "exam2": {
   "exact": 30,
   "same_rows_other_columns": 102,
   "same_count_other_values": 48,
   "other_row_count": 20,
   "does_not_run": 0
  }
 }
}
`,w=[`base`,`teacher`,`r1`,`r2`],T=[`simple`,`join`,`aggregate`,`hard`];function E(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of w){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function D(e){return`${(e*100).toFixed(1)}%`}function O(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function k(e){return`${e.agree} of ${e.n} · ${D(e.rate)}`}function A(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function j(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var M={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},N=E(C),P=124,F=130,I=168;function L({ticks:e,max:t,top:n,bottom:r}){return(0,b.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=F+A(e,t,I);return(0,b.jsxs)(`g`,{children:[(0,b.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,b.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function R({exam:e,className:n}){let r=N.exams.find(t=>t.id===e),i=.6,a=6+w.length*40+22,o=Object.fromEntries(N.models.map(e=>[e.id,e.short]));return(0,b.jsxs)(`figure`,{className:t(`min-w-0`,n),children:[(0,b.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,b.jsx)(L,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+w.length*40}),w.map((t,n)=>{let r=N.scores[e][t],a=6+n*40,s=M[t];return(0,b.jsxs)(`g`,{children:[(0,b.jsx)(`text`,{x:P,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:t===`r2`?700:500,fill:`var(--ink)`,children:o[t]}),(0,b.jsx)(`rect`,{x:F,y:a+8,width:Math.max(2,A(r.rate,i,I)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,b.jsx)(`line`,{x1:F+A(r.lo,i,I),x2:F+A(r.hi,i,I),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,b.jsx)(`line`,{x1:F+A(r.lo,i,I),x2:F+A(r.lo,i,I),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,b.jsx)(`line`,{x1:F+A(r.hi,i,I),x2:F+A(r.hi,i,I),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,b.jsx)(`text`,{x:F+A(r.hi,i,I)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:D(r.rate)})]},t)})]}),(0,b.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,b.jsx)(`div`,{className:`sr-only`,children:(0,b.jsxs)(`table`,{children:[(0,b.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,b.jsx)(`tbody`,{children:w.map(t=>(0,b.jsxs)(`tr`,{children:[(0,b.jsx)(`th`,{scope:`row`,children:o[t]}),(0,b.jsx)(`td`,{children:k(N.scores[e][t])}),(0,b.jsx)(`td`,{children:O(N.scores[e][t])})]},t))})]})})]})}function z({exam:e,className:n}){let r=N.exams.find(t=>t.id===e),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+T.length*74+20;return(0,b.jsxs)(`figure`,{className:t(`min-w-0`,n),children:[(0,b.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,b.jsx)(L,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+T.length*74-8}),T.map((t,n)=>{let r=N.difficulty[e][t],s=6+n*74;return(0,b.jsxs)(`g`,{children:[(0,b.jsx)(`text`,{x:P,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:t}),(0,b.jsxs)(`text`,{x:P,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=M[e];return(0,b.jsxs)(`g`,{children:[(0,b.jsx)(`rect`,{x:F,y:i,width:Math.max(2,A(n,o,I)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,b.jsxs)(`text`,{x:F+A(n,o,I)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},t)})]}),(0,b.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function B({className:e}){let n=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,b.jsxs)(`figure`,{className:t(`min-w-0`,e),children:[(0,b.jsxs)(`svg`,{viewBox:`0 0 368 ${n.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,b.jsx)(`line`,{x1:214,x2:214,y1:2,y2:n.length*44+2,stroke:`var(--line-strong)`}),n.map((e,t)=>{let n=N.r1_to_r2[e.exam],r=6+t*44;return(0,b.jsxs)(`g`,{children:[(0,b.jsx)(`text`,{x:P,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,b.jsx)(`rect`,{x:214-A(n.right_to_wrong,26,84),y:r+8,width:A(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,b.jsxs)(`text`,{x:214-A(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,b.jsx)(`rect`,{x:214,y:r+8,width:A(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,b.jsxs)(`text`,{x:214+A(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,b.jsx)(`text`,{x:208,y:n.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,b.jsx)(`text`,{x:220,y:n.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,b.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,N.r1_to_r2.exam1.p,` and `,N.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function V({label:e,children:t}){return(0,b.jsxs)(`div`,{className:`min-w-0`,children:[(0,b.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,b.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function H({className:e}){let n={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2 (3,507 written, 6 dropped for overlapping the exam), written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,b.jsxs)(`div`,{className:t(`grid gap-3 md:grid-cols-3`,e),children:[(0,b.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,b.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,b.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:n.name}),(0,b.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,b.jsx)(V,{label:`Written by`,children:n.writer}),(0,b.jsx)(V,{label:`What`,children:n.what}),(0,b.jsx)(V,{label:`Kept apart from the exams`,children:n.never})]})]}),N.exams.map(e=>(0,b.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,b.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,b.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,b.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,b.jsx)(V,{label:`Written by`,children:e.writer}),(0,b.jsx)(V,{label:`Why that matters`,children:e.writer_note}),(0,b.jsx)(V,{label:`Industries`,children:e.industries}),(0,b.jsx)(V,{label:`How it was checked`,children:e.checked}),(0,b.jsx)(V,{label:`Fingerprint (SHA-256)`,children:(0,b.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}export{p as _,R as a,D as c,j as d,C as f,h as g,_ as h,B as i,O as l,y as m,N as n,w as o,S as p,H as r,E as s,z as t,k as u,d as v,l as y};