import{a as e,p as t}from"./utils-CjFVJpAU.js";import{h as n,v as r}from"./who-cOcelTpL.js";import{M as i,O as a}from"./index-BYP2m7Gt.js";var o={name:`activity`,size:24,node:[[`path`,{d:`M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2`,key:`169zse`}]]};o.node;var s=i(o),c={name:`chevron-right`,size:24,node:[[`path`,{d:`m9 18 6-6-6-6`,key:`mthhwq`}]]};c.node;var l=i(c),u={name:`circle-dashed`,size:24,node:[[`path`,{d:`M10.1 2.182a10 10 0 0 1 3.8 0`,key:`5ilxe3`}],[`path`,{d:`M13.9 21.818a10 10 0 0 1-3.8 0`,key:`11zvb9`}],[`path`,{d:`M17.609 3.721a10 10 0 0 1 2.69 2.7`,key:`1iw5b2`}],[`path`,{d:`M2.182 13.9a10 10 0 0 1 0-3.8`,key:`c0bmvh`}],[`path`,{d:`M20.279 17.609a10 10 0 0 1-2.7 2.69`,key:`1ruxm7`}],[`path`,{d:`M21.818 10.1a10 10 0 0 1 0 3.8`,key:`qkgqxc`}],[`path`,{d:`M3.721 6.391a10 10 0 0 1 2.7-2.69`,key:`1mcia2`}],[`path`,{d:`M6.391 20.279a10 10 0 0 1-2.69-2.7`,key:`1fvljs`}]]};u.node;var d=i(u),f={name:`copy`,size:24,node:[[`rect`,{width:`14`,height:`14`,x:`8`,y:`8`,rx:`2`,ry:`2`,key:`17jyea`}],[`path`,{d:`M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2`,key:`zix9uf`}]]};f.node;var p=i(f),m={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};m.node;var h=i(m),g={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};g.node;var _=i(g),v={name:`server`,size:24,node:[[`rect`,{width:`20`,height:`8`,x:`2`,y:`2`,rx:`2`,ry:`2`,key:`ngkwjq`}],[`rect`,{width:`20`,height:`8`,x:`2`,y:`14`,rx:`2`,ry:`2`,key:`iecqi9`}],[`line`,{x1:`6`,x2:`6.01`,y1:`6`,y2:`6`,key:`16zg32`}],[`line`,{x1:`6`,x2:`6.01`,y1:`18`,y2:`18`,key:`nzw8ys`}]]};v.node;var y=i(v),b={name:`shield-alert`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`M12 8v4`,key:`1got3b`}],[`path`,{d:`M12 16h.01`,key:`1drbdi`}]]};b.node;var x=i(b),S={name:`shield-check`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`m9 12 2 2 4-4`,key:`dzmm74`}]]};S.node;var C=i(S),w=[`real`,`measured`,`simulation`,`pending`,`not-run`],T={real:{label:`Real`,meaning:`Happened or runs as described, and you can check it`},measured:{label:`Measured`,meaning:`A number measured on a real model or a live gateway`},simulation:{label:`Simulation`,meaning:`Mock models; no real model was measured`},pending:{label:`Pending`,meaning:`Planned or in progress; not available yet`},"not-run":{label:`Not run`,meaning:`Built, but not yet run for real`}},E={real:`good`,measured:`owned`,simulation:`outline`,pending:`warn`,"not-run":`neutral`};function D(e){return typeof e==`string`&&w.includes(e)}function O(e){return typeof e==`string`?e.trim():``}function k(e){let t;try{t=JSON.parse(e)}catch{t={}}let n=t??{},r=n.links??{},i=[];for(let e of Array.isArray(n.entries)?n.entries:[]){let t=e??{},n=t.check??{},r={label:O(n.label)};O(n.command)&&(r.command=O(n.command)),O(n.href)&&(r.href=O(n.href));let a={id:O(t.id),claim:O(t.claim),status:D(t.status)?t.status:`pending`,evidence:O(t.evidence),check:r,paths:Array.isArray(t.paths)?t.paths.map(O).filter(Boolean):[],date:O(t.date),facts:(Array.isArray(t.facts)?t.facts:[]).map(e=>({label:O(e?.label),value:O(e?.value)})).filter(e=>e.label&&e.value),metrics:(Array.isArray(t.metrics)?t.metrics:[]).map(e=>{let t=e??{};return{id:O(t.id),label:O(t.label),value:O(t.value)||null}})};a.id&&a.claim&&a.evidence&&a.check.label&&(a.check.command||a.check.href)&&i.push(A(a))}return{updated:O(n.updated),links:{repo:O(r.repo),docs:O(r.docs),demo:O(r.demo),video:O(r.video)||null},entries:i}}function A(e){let t=e.metrics.filter(e=>e.value!=null).length;return e.status===`measured`&&(e.metrics.length===0||t<e.metrics.length)?{...e,status:`pending`,metrics:e.metrics.map(e=>({...e,value:null}))}:e.status!==`measured`&&t>0?{...e,metrics:e.metrics.map(e=>({...e,value:null}))}:e}function j(e,t){return e.entries.find(e=>e.id===t)}function M(e){return w.map(t=>({status:t,n:e.entries.filter(e=>e.status===t).length})).filter(e=>e.n>0)}function N(e){return`${T[e].label}: ${T[e].meaning.charAt(0).toLowerCase()}${T[e].meaning.slice(1)}`}function P(e){return e===`mock`?`simulation`:e===`live`?`measured`:null}function F(e){let t=/^(\d{4})-(\d{2})-(\d{2})$/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`],r=Number(t[2])-1;return r<0||r>11?e:`${Number(t[3])} ${n[r]} ${t[1]}`}var I=t(),ee={real:r,measured:h,simulation:a,pending:_,"not-run":d};function L({status:e,className:t,label:r}){let i=ee[e],a=T[e];return(0,I.jsxs)(n,{tone:E[e],className:t,title:a.meaning,"aria-label":N(e),role:`img`,children:[(0,I.jsx)(i,{"aria-hidden":!0}),` `,r??a.label]})}var R=`{
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
`,z=[`base`,`teacher`,`r1`,`r2`],B=[`simple`,`join`,`aggregate`,`hard`];function V(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of z){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function H(e){return`${(e*100).toFixed(1)}%`}function U(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function W(e){return`${e.agree} of ${e.n} · ${H(e.rate)}`}function G(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function K(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var q={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},J=V(R),Y=124,X=130,Z=168;function Q({ticks:e,max:t,top:n,bottom:r}){return(0,I.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=X+G(e,t,Z);return(0,I.jsxs)(`g`,{children:[(0,I.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,I.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function te({exam:t,className:n}){let r=J.exams.find(e=>e.id===t),i=.6,a=6+z.length*40+22,o=Object.fromEntries(J.models.map(e=>[e.id,e.short]));return(0,I.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,I.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,I.jsx)(Q,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+z.length*40}),z.map((e,n)=>{let r=J.scores[t][e],a=6+n*40,s=q[e];return(0,I.jsxs)(`g`,{children:[(0,I.jsx)(`text`,{x:Y,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:e===`r2`?700:500,fill:`var(--ink)`,children:o[e]}),(0,I.jsx)(`rect`,{x:X,y:a+8,width:Math.max(2,G(r.rate,i,Z)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,I.jsx)(`line`,{x1:X+G(r.lo,i,Z),x2:X+G(r.hi,i,Z),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,I.jsx)(`line`,{x1:X+G(r.lo,i,Z),x2:X+G(r.lo,i,Z),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,I.jsx)(`line`,{x1:X+G(r.hi,i,Z),x2:X+G(r.hi,i,Z),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,I.jsx)(`text`,{x:X+G(r.hi,i,Z)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:H(r.rate)})]},e)})]}),(0,I.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,I.jsxs)(`table`,{className:`sr-only`,children:[(0,I.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,I.jsx)(`tbody`,{children:z.map(e=>(0,I.jsxs)(`tr`,{children:[(0,I.jsx)(`th`,{scope:`row`,children:o[e]}),(0,I.jsx)(`td`,{children:W(J.scores[t][e])}),(0,I.jsx)(`td`,{children:U(J.scores[t][e])})]},e))})]})]})}function ne({exam:t,className:n}){let r=J.exams.find(e=>e.id===t),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+B.length*74+20;return(0,I.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,I.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,I.jsx)(Q,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+B.length*74-8}),B.map((e,n)=>{let r=J.difficulty[t][e],s=6+n*74;return(0,I.jsxs)(`g`,{children:[(0,I.jsx)(`text`,{x:Y,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e}),(0,I.jsxs)(`text`,{x:Y,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=q[e];return(0,I.jsxs)(`g`,{children:[(0,I.jsx)(`rect`,{x:X,y:i,width:Math.max(2,G(n,o,Z)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,I.jsxs)(`text`,{x:X+G(n,o,Z)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},e)})]}),(0,I.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function re({className:t}){let n=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,I.jsxs)(`figure`,{className:e(`min-w-0`,t),children:[(0,I.jsxs)(`svg`,{viewBox:`0 0 368 ${n.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,I.jsx)(`line`,{x1:214,x2:214,y1:2,y2:n.length*44+2,stroke:`var(--line-strong)`}),n.map((e,t)=>{let n=J.r1_to_r2[e.exam],r=6+t*44;return(0,I.jsxs)(`g`,{children:[(0,I.jsx)(`text`,{x:Y,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,I.jsx)(`rect`,{x:214-G(n.right_to_wrong,26,84),y:r+8,width:G(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,I.jsxs)(`text`,{x:214-G(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,I.jsx)(`rect`,{x:214,y:r+8,width:G(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,I.jsxs)(`text`,{x:214+G(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,I.jsx)(`text`,{x:208,y:n.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,I.jsx)(`text`,{x:220,y:n.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,I.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,J.r1_to_r2.exam1.p,` and `,J.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function $({label:e,children:t}){return(0,I.jsxs)(`div`,{className:`min-w-0`,children:[(0,I.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,I.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function ie({className:t}){let n={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2 (3,507 written, 6 dropped for overlapping the exam), written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,I.jsxs)(`div`,{className:e(`grid gap-3 md:grid-cols-3`,t),children:[(0,I.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,I.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,I.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:n.name}),(0,I.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,I.jsx)($,{label:`Written by`,children:n.writer}),(0,I.jsx)($,{label:`What`,children:n.what}),(0,I.jsx)($,{label:`Kept apart from the exams`,children:n.never})]})]}),J.exams.map(e=>(0,I.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,I.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,I.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,I.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,I.jsx)($,{label:`Written by`,children:e.writer}),(0,I.jsx)($,{label:`Why that matters`,children:e.writer_note}),(0,I.jsx)($,{label:`Industries`,children:e.industries}),(0,I.jsx)($,{label:`How it was checked`,children:e.checked}),(0,I.jsx)($,{label:`Fingerprint (SHA-256)`,children:(0,I.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}export{h as C,s as D,l as E,_ as S,d as T,k as _,te as a,x as b,U as c,L as d,w as f,P as g,F as h,re as i,W as l,j as m,J as n,z as o,T as p,ie as r,H as s,ne as t,K as u,M as v,p as w,y as x,C as y};