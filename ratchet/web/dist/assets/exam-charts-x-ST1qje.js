import{a as e,p as t}from"./utils-CjFVJpAU.js";import{h as n,v as r}from"./who-BzMhZQfE.js";import{M as i,O as a}from"./index-DnpcD33n.js";var o={name:`activity`,size:24,node:[[`path`,{d:`M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2`,key:`169zse`}]]};o.node;var s=i(o),c={name:`chevron-right`,size:24,node:[[`path`,{d:`m9 18 6-6-6-6`,key:`mthhwq`}]]};c.node;var l=i(c),u={name:`circle-dashed`,size:24,node:[[`path`,{d:`M10.1 2.182a10 10 0 0 1 3.8 0`,key:`5ilxe3`}],[`path`,{d:`M13.9 21.818a10 10 0 0 1-3.8 0`,key:`11zvb9`}],[`path`,{d:`M17.609 3.721a10 10 0 0 1 2.69 2.7`,key:`1iw5b2`}],[`path`,{d:`M2.182 13.9a10 10 0 0 1 0-3.8`,key:`c0bmvh`}],[`path`,{d:`M20.279 17.609a10 10 0 0 1-2.7 2.69`,key:`1ruxm7`}],[`path`,{d:`M21.818 10.1a10 10 0 0 1 0 3.8`,key:`qkgqxc`}],[`path`,{d:`M3.721 6.391a10 10 0 0 1 2.7-2.69`,key:`1mcia2`}],[`path`,{d:`M6.391 20.279a10 10 0 0 1-2.69-2.7`,key:`1fvljs`}]]};u.node;var d=i(u),f={name:`copy`,size:24,node:[[`rect`,{width:`14`,height:`14`,x:`8`,y:`8`,rx:`2`,ry:`2`,key:`17jyea`}],[`path`,{d:`M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2`,key:`zix9uf`}]]};f.node;var p=i(f),m={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};m.node;var h=i(m),g={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};g.node;var _=i(g),v={name:`shield-alert`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`M12 8v4`,key:`1got3b`}],[`path`,{d:`M12 16h.01`,key:`1drbdi`}]]};v.node;var y=i(v),b={name:`shield-check`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`m9 12 2 2 4-4`,key:`dzmm74`}]]};b.node;var x=i(b),S=[`real`,`measured`,`simulation`,`pending`,`not-run`],C={real:{label:`Real`,meaning:`Happened or runs as described, and you can check it`},measured:{label:`Measured`,meaning:`A number measured on a real model or a live gateway`},simulation:{label:`Simulation`,meaning:`Mock models; no real model was measured`},pending:{label:`Pending`,meaning:`Being measured now; no number yet`},"not-run":{label:`Not run`,meaning:`Built, but not yet run for real`}},w={real:`good`,measured:`owned`,simulation:`outline`,pending:`warn`,"not-run":`neutral`};function T(e){return typeof e==`string`&&S.includes(e)}function E(e){return typeof e==`string`?e.trim():``}function D(e){let t;try{t=JSON.parse(e)}catch{t={}}let n=t??{},r=n.links??{},i=[];for(let e of Array.isArray(n.entries)?n.entries:[]){let t=e??{},n=t.check??{},r={label:E(n.label)};E(n.command)&&(r.command=E(n.command)),E(n.href)&&(r.href=E(n.href));let a={id:E(t.id),claim:E(t.claim),status:T(t.status)?t.status:`pending`,evidence:E(t.evidence),check:r,paths:Array.isArray(t.paths)?t.paths.map(E).filter(Boolean):[],date:E(t.date),facts:(Array.isArray(t.facts)?t.facts:[]).map(e=>({label:E(e?.label),value:E(e?.value)})).filter(e=>e.label&&e.value),metrics:(Array.isArray(t.metrics)?t.metrics:[]).map(e=>{let t=e??{};return{id:E(t.id),label:E(t.label),value:E(t.value)||null}})};a.id&&a.claim&&a.evidence&&a.check.label&&(a.check.command||a.check.href)&&i.push(O(a))}return{updated:E(n.updated),links:{repo:E(r.repo),docs:E(r.docs),demo:E(r.demo),video:E(r.video)||null},entries:i}}function O(e){let t=e.metrics.filter(e=>e.value!=null).length;return e.status===`measured`&&(e.metrics.length===0||t<e.metrics.length)?{...e,status:`pending`,metrics:e.metrics.map(e=>({...e,value:null}))}:e.status!==`measured`&&t>0?{...e,metrics:e.metrics.map(e=>({...e,value:null}))}:e}function k(e,t){return e.entries.find(e=>e.id===t)}function A(e){return S.map(t=>({status:t,n:e.entries.filter(e=>e.status===t).length})).filter(e=>e.n>0)}function j(e){return`${C[e].label}: ${C[e].meaning.charAt(0).toLowerCase()}${C[e].meaning.slice(1)}`}function M(e){return e===`mock`?`simulation`:e===`live`?`measured`:null}function N(e){let t=/^(\d{4})-(\d{2})-(\d{2})$/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`],r=Number(t[2])-1;return r<0||r>11?e:`${Number(t[3])} ${n[r]} ${t[1]}`}var P=t(),ee={real:r,measured:h,simulation:a,pending:_,"not-run":d};function F({status:e,className:t,label:r}){let i=ee[e],a=C[e];return(0,P.jsxs)(n,{tone:w[e],className:t,title:a.meaning,"aria-label":j(e),role:`img`,children:[(0,P.jsx)(i,{"aria-hidden":!0}),` `,r??a.label]})}var I=`{
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
   "industries": "14 industries; 57 of the 172 questions come from five industries that were never in training.",
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
`,L=[`base`,`teacher`,`r1`,`r2`],R=[`simple`,`join`,`aggregate`,`hard`];function z(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of L){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function B(e){return`${(e*100).toFixed(1)}%`}function V(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function H(e){return`${e.agree} of ${e.n} · ${B(e.rate)}`}function U(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function W(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var G={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},K=z(I),q=124,J=130,Y=168;function X({ticks:e,max:t,top:n,bottom:r}){return(0,P.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=J+U(e,t,Y);return(0,P.jsxs)(`g`,{children:[(0,P.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,P.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function Z({exam:t,className:n}){let r=K.exams.find(e=>e.id===t),i=.6,a=6+L.length*40+22,o=Object.fromEntries(K.models.map(e=>[e.id,e.short]));return(0,P.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,P.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,P.jsx)(X,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+L.length*40}),L.map((e,n)=>{let r=K.scores[t][e],a=6+n*40,s=G[e];return(0,P.jsxs)(`g`,{children:[(0,P.jsx)(`text`,{x:q,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:e===`r2`?700:500,fill:`var(--ink)`,children:o[e]}),(0,P.jsx)(`rect`,{x:J,y:a+8,width:Math.max(2,U(r.rate,i,Y)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,P.jsx)(`line`,{x1:J+U(r.lo,i,Y),x2:J+U(r.hi,i,Y),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,P.jsx)(`line`,{x1:J+U(r.lo,i,Y),x2:J+U(r.lo,i,Y),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,P.jsx)(`line`,{x1:J+U(r.hi,i,Y),x2:J+U(r.hi,i,Y),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,P.jsx)(`text`,{x:J+U(r.hi,i,Y)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:B(r.rate)})]},e)})]}),(0,P.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,P.jsxs)(`table`,{className:`sr-only`,children:[(0,P.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,P.jsx)(`tbody`,{children:L.map(e=>(0,P.jsxs)(`tr`,{children:[(0,P.jsx)(`th`,{scope:`row`,children:o[e]}),(0,P.jsx)(`td`,{children:H(K.scores[t][e])}),(0,P.jsx)(`td`,{children:V(K.scores[t][e])})]},e))})]})]})}function Q({exam:t,className:n}){let r=K.exams.find(e=>e.id===t),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+R.length*74+20;return(0,P.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,P.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,P.jsx)(X,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+R.length*74-8}),R.map((e,n)=>{let r=K.difficulty[t][e],s=6+n*74;return(0,P.jsxs)(`g`,{children:[(0,P.jsx)(`text`,{x:q,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e}),(0,P.jsxs)(`text`,{x:q,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=G[e];return(0,P.jsxs)(`g`,{children:[(0,P.jsx)(`rect`,{x:J,y:i,width:Math.max(2,U(n,o,Y)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,P.jsxs)(`text`,{x:J+U(n,o,Y)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},e)})]}),(0,P.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function te({className:t}){let n=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,P.jsxs)(`figure`,{className:e(`min-w-0`,t),children:[(0,P.jsxs)(`svg`,{viewBox:`0 0 368 ${n.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,P.jsx)(`line`,{x1:214,x2:214,y1:2,y2:n.length*44+2,stroke:`var(--line-strong)`}),n.map((e,t)=>{let n=K.r1_to_r2[e.exam],r=6+t*44;return(0,P.jsxs)(`g`,{children:[(0,P.jsx)(`text`,{x:q,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,P.jsx)(`rect`,{x:214-U(n.right_to_wrong,26,84),y:r+8,width:U(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,P.jsxs)(`text`,{x:214-U(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,P.jsx)(`rect`,{x:214,y:r+8,width:U(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,P.jsxs)(`text`,{x:214+U(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,P.jsx)(`text`,{x:208,y:n.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,P.jsx)(`text`,{x:220,y:n.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,P.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,K.r1_to_r2.exam1.p,` and `,K.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function $({label:e,children:t}){return(0,P.jsxs)(`div`,{className:`min-w-0`,children:[(0,P.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,P.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function ne({className:t}){let n={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2, written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,P.jsxs)(`div`,{className:e(`grid gap-3 md:grid-cols-3`,t),children:[(0,P.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,P.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,P.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:n.name}),(0,P.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,P.jsx)($,{label:`Written by`,children:n.writer}),(0,P.jsx)($,{label:`What`,children:n.what}),(0,P.jsx)($,{label:`Kept apart from the exams`,children:n.never})]})]}),K.exams.map(e=>(0,P.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,P.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,P.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,P.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,P.jsx)($,{label:`Written by`,children:e.writer}),(0,P.jsx)($,{label:`Why that matters`,children:e.writer_note}),(0,P.jsx)($,{label:`Industries`,children:e.industries}),(0,P.jsx)($,{label:`How it was checked`,children:e.checked}),(0,P.jsx)($,{label:`Fingerprint (SHA-256)`,children:(0,P.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}export{p as C,s as E,h as S,l as T,D as _,Z as a,y as b,V as c,F as d,S as f,M as g,N as h,te as i,H as l,k as m,K as n,L as o,C as p,ne as r,B as s,Q as t,W as u,A as v,d as w,_ as x,x as y};