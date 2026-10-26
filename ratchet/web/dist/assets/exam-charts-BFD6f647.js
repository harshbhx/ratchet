import{a as e,p as t}from"./utils-CjFVJpAU.js";import{y as n}from"./section-BuN0E1f-.js";import{h as r,x as i,y as a}from"./who-BS7bbyw3.js";var o={name:`activity`,size:24,node:[[`path`,{d:`M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2`,key:`169zse`}]]};o.node;var s=n(o),c={name:`chevron-right`,size:24,node:[[`path`,{d:`m9 18 6-6-6-6`,key:`mthhwq`}]]};c.node;var l=n(c),u={name:`circle-dashed`,size:24,node:[[`path`,{d:`M10.1 2.182a10 10 0 0 1 3.8 0`,key:`5ilxe3`}],[`path`,{d:`M13.9 21.818a10 10 0 0 1-3.8 0`,key:`11zvb9`}],[`path`,{d:`M17.609 3.721a10 10 0 0 1 2.69 2.7`,key:`1iw5b2`}],[`path`,{d:`M2.182 13.9a10 10 0 0 1 0-3.8`,key:`c0bmvh`}],[`path`,{d:`M20.279 17.609a10 10 0 0 1-2.7 2.69`,key:`1ruxm7`}],[`path`,{d:`M21.818 10.1a10 10 0 0 1 0 3.8`,key:`qkgqxc`}],[`path`,{d:`M3.721 6.391a10 10 0 0 1 2.7-2.69`,key:`1mcia2`}],[`path`,{d:`M6.391 20.279a10 10 0 0 1-2.69-2.7`,key:`1fvljs`}]]};u.node;var d=n(u),f={name:`copy`,size:24,node:[[`rect`,{width:`14`,height:`14`,x:`8`,y:`8`,rx:`2`,ry:`2`,key:`17jyea`}],[`path`,{d:`M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2`,key:`zix9uf`}]]};f.node;var p=n(f),m={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};m.node;var h=n(m),g={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};g.node;var _=n(g),v={name:`server`,size:24,node:[[`rect`,{width:`20`,height:`8`,x:`2`,y:`2`,rx:`2`,ry:`2`,key:`ngkwjq`}],[`rect`,{width:`20`,height:`8`,x:`2`,y:`14`,rx:`2`,ry:`2`,key:`iecqi9`}],[`line`,{x1:`6`,x2:`6.01`,y1:`6`,y2:`6`,key:`16zg32`}],[`line`,{x1:`6`,x2:`6.01`,y1:`18`,y2:`18`,key:`nzw8ys`}]]};v.node;var y=n(v),b={name:`shield-alert`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`M12 8v4`,key:`1got3b`}],[`path`,{d:`M12 16h.01`,key:`1drbdi`}]]};b.node;var x=n(b),S=[`real`,`measured`,`simulation`,`pending`,`not-run`];function C(e,t){if(!e.video)return null;if(!e.videoShort)return e.video;try{let n=new URL(e.videoShort);return n.protocol===`https:`?(n.searchParams.set(`from`,t),n.href):e.video}catch{return e.video}}var w={real:{label:`Real`,meaning:`Happened or runs as described, and you can check it`},measured:{label:`Measured`,meaning:`A number measured on a real model or a live gateway`},simulation:{label:`Simulation`,meaning:`Mock models; no real model was measured`},pending:{label:`Pending`,meaning:`Planned or in progress; not available yet`},"not-run":{label:`Ready to run`,meaning:`Built and tested offline, ready to run for real`}},T={real:`good`,measured:`owned`,simulation:`outline`,pending:`warn`,"not-run":`neutral`};function E(e){return typeof e==`string`&&S.includes(e)}function D(e){return typeof e==`string`?e.trim():``}function O(e){let t;try{t=JSON.parse(e)}catch{t={}}let n=t??{},r=n.links??{},i=[];for(let e of Array.isArray(n.entries)?n.entries:[]){let t=e??{},n=t.check??{},r={label:D(n.label)};D(n.command)&&(r.command=D(n.command)),D(n.href)&&(r.href=D(n.href));let a={id:D(t.id),claim:D(t.claim),status:E(t.status)?t.status:`pending`,evidence:D(t.evidence),check:r,paths:Array.isArray(t.paths)?t.paths.map(D).filter(Boolean):[],date:D(t.date),facts:(Array.isArray(t.facts)?t.facts:[]).map(e=>({label:D(e?.label),value:D(e?.value)})).filter(e=>e.label&&e.value),metrics:(Array.isArray(t.metrics)?t.metrics:[]).map(e=>{let t=e??{};return{id:D(t.id),label:D(t.label),value:D(t.value)||null}})};a.id&&a.claim&&a.evidence&&a.check.label&&(a.check.command||a.check.href)&&i.push(k(a))}return{updated:D(n.updated),links:{repo:D(r.repo),docs:D(r.docs),demo:D(r.demo),video:D(r.video)||null,videoShort:D(r.videoShort)||null},entries:i}}function k(e){let t=e.metrics.filter(e=>e.value!=null).length;return e.status===`measured`&&(e.metrics.length===0||t<e.metrics.length)?{...e,status:`pending`,metrics:e.metrics.map(e=>({...e,value:null}))}:e.status!==`measured`&&t>0?{...e,metrics:e.metrics.map(e=>({...e,value:null}))}:e}function A(e,t){return e.entries.find(e=>e.id===t)}function j(e){return S.map(t=>({status:t,n:e.entries.filter(e=>e.status===t).length})).filter(e=>e.n>0)}function M(e){return`${w[e].label}: ${w[e].meaning.charAt(0).toLowerCase()}${w[e].meaning.slice(1)}`}function N(e){return e===`mock`?`simulation`:e===`live`?`measured`:null}function P(e){let t=/^(\d{4})-(\d{2})-(\d{2})$/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`],r=Number(t[2])-1;return r<0||r>11?e:`${Number(t[3])} ${n[r]} ${t[1]}`}var F=t(),ee={real:i,measured:h,simulation:a,pending:_,"not-run":d};function I({status:e,className:t,label:n}){let i=ee[e],a=w[e];return(0,F.jsxs)(r,{tone:T[e],className:t,title:a.meaning,"aria-label":M(e),role:`img`,children:[(0,F.jsx)(i,{"aria-hidden":!0}),` `,n??a.label]})}var L=`{
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
`,R=[`base`,`teacher`,`r1`,`r2`],z=[`simple`,`join`,`aggregate`,`hard`];function B(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of R){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function V(e){return`${(e*100).toFixed(1)}%`}function H(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function U(e){return`${e.agree} of ${e.n} · ${V(e.rate)}`}function W(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function G(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var K={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},q=B(L),J=124,Y=130,X=168;function Z({ticks:e,max:t,top:n,bottom:r}){return(0,F.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=Y+W(e,t,X);return(0,F.jsxs)(`g`,{children:[(0,F.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,F.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function Q({exam:t,className:n}){let r=q.exams.find(e=>e.id===t),i=.6,a=6+R.length*40+22,o=Object.fromEntries(q.models.map(e=>[e.id,e.short]));return(0,F.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,F.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,F.jsx)(Z,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+R.length*40}),R.map((e,n)=>{let r=q.scores[t][e],a=6+n*40,s=K[e];return(0,F.jsxs)(`g`,{children:[(0,F.jsx)(`text`,{x:J,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:e===`r2`?700:500,fill:`var(--ink)`,children:o[e]}),(0,F.jsx)(`rect`,{x:Y,y:a+8,width:Math.max(2,W(r.rate,i,X)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,F.jsx)(`line`,{x1:Y+W(r.lo,i,X),x2:Y+W(r.hi,i,X),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,F.jsx)(`line`,{x1:Y+W(r.lo,i,X),x2:Y+W(r.lo,i,X),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,F.jsx)(`line`,{x1:Y+W(r.hi,i,X),x2:Y+W(r.hi,i,X),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,F.jsx)(`text`,{x:Y+W(r.hi,i,X)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:V(r.rate)})]},e)})]}),(0,F.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,F.jsx)(`div`,{className:`sr-only`,children:(0,F.jsxs)(`table`,{children:[(0,F.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,F.jsx)(`tbody`,{children:R.map(e=>(0,F.jsxs)(`tr`,{children:[(0,F.jsx)(`th`,{scope:`row`,children:o[e]}),(0,F.jsx)(`td`,{children:U(q.scores[t][e])}),(0,F.jsx)(`td`,{children:H(q.scores[t][e])})]},e))})]})})]})}function te({exam:t,className:n}){let r=q.exams.find(e=>e.id===t),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+z.length*74+20;return(0,F.jsxs)(`figure`,{className:e(`min-w-0`,n),children:[(0,F.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,F.jsx)(Z,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+z.length*74-8}),z.map((e,n)=>{let r=q.difficulty[t][e],s=6+n*74;return(0,F.jsxs)(`g`,{children:[(0,F.jsx)(`text`,{x:J,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e}),(0,F.jsxs)(`text`,{x:J,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=K[e];return(0,F.jsxs)(`g`,{children:[(0,F.jsx)(`rect`,{x:Y,y:i,width:Math.max(2,W(n,o,X)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,F.jsxs)(`text`,{x:Y+W(n,o,X)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},e)})]}),(0,F.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function ne({className:t}){let n=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,F.jsxs)(`figure`,{className:e(`min-w-0`,t),children:[(0,F.jsxs)(`svg`,{viewBox:`0 0 368 ${n.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,F.jsx)(`line`,{x1:214,x2:214,y1:2,y2:n.length*44+2,stroke:`var(--line-strong)`}),n.map((e,t)=>{let n=q.r1_to_r2[e.exam],r=6+t*44;return(0,F.jsxs)(`g`,{children:[(0,F.jsx)(`text`,{x:J,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,F.jsx)(`rect`,{x:214-W(n.right_to_wrong,26,84),y:r+8,width:W(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,F.jsxs)(`text`,{x:214-W(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,F.jsx)(`rect`,{x:214,y:r+8,width:W(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,F.jsxs)(`text`,{x:214+W(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,F.jsx)(`text`,{x:208,y:n.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,F.jsx)(`text`,{x:220,y:n.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,F.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,q.r1_to_r2.exam1.p,` and `,q.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function $({label:e,children:t}){return(0,F.jsxs)(`div`,{className:`min-w-0`,children:[(0,F.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,F.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function re({className:t}){let n={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2 (3,507 written, 6 dropped for overlapping the exam), written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,F.jsxs)(`div`,{className:e(`grid gap-3 md:grid-cols-3`,t),children:[(0,F.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,F.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,F.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:n.name}),(0,F.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,F.jsx)($,{label:`Written by`,children:n.writer}),(0,F.jsx)($,{label:`What`,children:n.what}),(0,F.jsx)($,{label:`Kept apart from the exams`,children:n.never})]})]}),q.exams.map(e=>(0,F.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,F.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,F.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,F.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,F.jsx)($,{label:`Written by`,children:e.writer}),(0,F.jsx)($,{label:`Why that matters`,children:e.writer_note}),(0,F.jsx)($,{label:`Industries`,children:e.industries}),(0,F.jsx)($,{label:`How it was checked`,children:e.checked}),(0,F.jsx)($,{label:`Fingerprint (SHA-256)`,children:(0,F.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}export{h as C,s as D,l as E,_ as S,d as T,O as _,Q as a,x as b,H as c,I as d,S as f,N as g,P as h,ne as i,U as l,A as m,q as n,R as o,w as p,re as r,V as s,te as t,G as u,j as v,p as w,y as x,C as y};