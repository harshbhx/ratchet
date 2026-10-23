import{f as e,i as t}from"./utils-YUdTo7nM.js";import{h as n,v as r}from"./who-CLuXFI3r.js";import{M as i,O as a}from"./index-DTJP-swj.js";var o={name:`circle-dashed`,size:24,node:[[`path`,{d:`M10.1 2.182a10 10 0 0 1 3.8 0`,key:`5ilxe3`}],[`path`,{d:`M13.9 21.818a10 10 0 0 1-3.8 0`,key:`11zvb9`}],[`path`,{d:`M17.609 3.721a10 10 0 0 1 2.69 2.7`,key:`1iw5b2`}],[`path`,{d:`M2.182 13.9a10 10 0 0 1 0-3.8`,key:`c0bmvh`}],[`path`,{d:`M20.279 17.609a10 10 0 0 1-2.7 2.69`,key:`1ruxm7`}],[`path`,{d:`M21.818 10.1a10 10 0 0 1 0 3.8`,key:`qkgqxc`}],[`path`,{d:`M3.721 6.391a10 10 0 0 1 2.7-2.69`,key:`1mcia2`}],[`path`,{d:`M6.391 20.279a10 10 0 0 1-2.69-2.7`,key:`1fvljs`}]]};o.node;var s=i(o),c={name:`copy`,size:24,node:[[`rect`,{width:`14`,height:`14`,x:`8`,y:`8`,rx:`2`,ry:`2`,key:`17jyea`}],[`path`,{d:`M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2`,key:`zix9uf`}]]};c.node;var l=i(c),u={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};u.node;var d=i(u),f={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};f.node;var p=i(f),m={name:`shield-alert`,size:24,node:[[`path`,{d:`M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z`,key:`oel41y`}],[`path`,{d:`M12 8v4`,key:`1got3b`}],[`path`,{d:`M12 16h.01`,key:`1drbdi`}]]};m.node;var h=i(m),g=[`real`,`measured`,`simulation`,`pending`,`not-run`],_={real:{label:`Real`,meaning:`Happened or runs as described, and you can check it`},measured:{label:`Measured`,meaning:`A number measured on a real model or a live gateway`},simulation:{label:`Simulation`,meaning:`Mock models; no real model was measured`},pending:{label:`Pending`,meaning:`Being measured now; no number yet`},"not-run":{label:`Not run`,meaning:`Built, but not yet run for real`}},v={real:`good`,measured:`owned`,simulation:`outline`,pending:`warn`,"not-run":`neutral`};function y(e){return typeof e==`string`&&g.includes(e)}function b(e){return typeof e==`string`?e.trim():``}function x(e){let t;try{t=JSON.parse(e)}catch{t={}}let n=t??{},r=n.links??{},i=[];for(let e of Array.isArray(n.entries)?n.entries:[]){let t=e??{},n=t.check??{},r={label:b(n.label)};b(n.command)&&(r.command=b(n.command)),b(n.href)&&(r.href=b(n.href));let a={id:b(t.id),claim:b(t.claim),status:y(t.status)?t.status:`pending`,evidence:b(t.evidence),check:r,paths:Array.isArray(t.paths)?t.paths.map(b).filter(Boolean):[],date:b(t.date),facts:(Array.isArray(t.facts)?t.facts:[]).map(e=>({label:b(e?.label),value:b(e?.value)})).filter(e=>e.label&&e.value),metrics:(Array.isArray(t.metrics)?t.metrics:[]).map(e=>{let t=e??{};return{id:b(t.id),label:b(t.label),value:b(t.value)||null}})};a.id&&a.claim&&a.evidence&&a.check.label&&(a.check.command||a.check.href)&&i.push(S(a))}return{updated:b(n.updated),links:{repo:b(r.repo),docs:b(r.docs),demo:b(r.demo),video:b(r.video)||null},entries:i}}function S(e){let t=e.metrics.filter(e=>e.value!=null).length;return e.status===`measured`&&(e.metrics.length===0||t<e.metrics.length)?{...e,status:`pending`,metrics:e.metrics.map(e=>({...e,value:null}))}:e.status!==`measured`&&t>0?{...e,metrics:e.metrics.map(e=>({...e,value:null}))}:e}function C(e,t){return e.entries.find(e=>e.id===t)}function w(e){return g.map(t=>({status:t,n:e.entries.filter(e=>e.status===t).length})).filter(e=>e.n>0)}function T(e){return`${_[e].label}: ${_[e].meaning.charAt(0).toLowerCase()}${_[e].meaning.slice(1)}`}function E(e){return e===`mock`?`simulation`:e===`live`?`measured`:null}function D(e){let t=/^(\d{4})-(\d{2})-(\d{2})$/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`],r=Number(t[2])-1;return r<0||r>11?e:`${Number(t[3])} ${n[r]} ${t[1]}`}var O=e(),k={real:r,measured:d,simulation:a,pending:p,"not-run":s};function A({status:e,className:t,label:r}){let i=k[e],a=_[e];return(0,O.jsxs)(n,{tone:v[e],className:t,title:a.meaning,"aria-label":T(e),role:`img`,children:[(0,O.jsx)(i,{"aria-hidden":!0}),` `,r??a.label]})}var j=`{
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
`,M=[`base`,`teacher`,`r1`,`r2`],N=[`simple`,`join`,`aggregate`,`hard`];function P(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of M){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function F(e){return`${(e*100).toFixed(1)}%`}function I(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function L(e){return`${e.agree} of ${e.n} · ${F(e.rate)}`}function R(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function z(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var B={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},V=P(j),H=124,U=130,W=168;function G({ticks:e,max:t,top:n,bottom:r}){return(0,O.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=U+R(e,t,W);return(0,O.jsxs)(`g`,{children:[(0,O.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,O.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function K({exam:e,className:n}){let r=V.exams.find(t=>t.id===e),i=.6,a=6+M.length*40+22,o=Object.fromEntries(V.models.map(e=>[e.id,e.short]));return(0,O.jsxs)(`figure`,{className:t(`min-w-0`,n),children:[(0,O.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,O.jsx)(G,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+M.length*40}),M.map((t,n)=>{let r=V.scores[e][t],a=6+n*40,s=B[t];return(0,O.jsxs)(`g`,{children:[(0,O.jsx)(`text`,{x:H,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:t===`r2`?700:500,fill:`var(--ink)`,children:o[t]}),(0,O.jsx)(`rect`,{x:U,y:a+8,width:Math.max(2,R(r.rate,i,W)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,O.jsx)(`line`,{x1:U+R(r.lo,i,W),x2:U+R(r.hi,i,W),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,O.jsx)(`line`,{x1:U+R(r.lo,i,W),x2:U+R(r.lo,i,W),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,O.jsx)(`line`,{x1:U+R(r.hi,i,W),x2:U+R(r.hi,i,W),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,O.jsx)(`text`,{x:U+R(r.hi,i,W)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:F(r.rate)})]},t)})]}),(0,O.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,O.jsxs)(`table`,{className:`sr-only`,children:[(0,O.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,O.jsx)(`tbody`,{children:M.map(t=>(0,O.jsxs)(`tr`,{children:[(0,O.jsx)(`th`,{scope:`row`,children:o[t]}),(0,O.jsx)(`td`,{children:L(V.scores[e][t])}),(0,O.jsx)(`td`,{children:I(V.scores[e][t])})]},t))})]})]})}function q({exam:e,className:n}){let r=V.exams.find(t=>t.id===e),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+N.length*74+20;return(0,O.jsxs)(`figure`,{className:t(`min-w-0`,n),children:[(0,O.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,O.jsx)(G,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+N.length*74-8}),N.map((t,n)=>{let r=V.difficulty[e][t],s=6+n*74;return(0,O.jsxs)(`g`,{children:[(0,O.jsx)(`text`,{x:H,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:t}),(0,O.jsxs)(`text`,{x:H,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=B[e];return(0,O.jsxs)(`g`,{children:[(0,O.jsx)(`rect`,{x:U,y:i,width:Math.max(2,R(n,o,W)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,O.jsxs)(`text`,{x:U+R(n,o,W)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},t)})]}),(0,O.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function J({className:e}){let n=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,O.jsxs)(`figure`,{className:t(`min-w-0`,e),children:[(0,O.jsxs)(`svg`,{viewBox:`0 0 368 ${n.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,O.jsx)(`line`,{x1:214,x2:214,y1:2,y2:n.length*44+2,stroke:`var(--line-strong)`}),n.map((e,t)=>{let n=V.r1_to_r2[e.exam],r=6+t*44;return(0,O.jsxs)(`g`,{children:[(0,O.jsx)(`text`,{x:H,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,O.jsx)(`rect`,{x:214-R(n.right_to_wrong,26,84),y:r+8,width:R(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,O.jsxs)(`text`,{x:214-R(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,O.jsx)(`rect`,{x:214,y:r+8,width:R(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,O.jsxs)(`text`,{x:214+R(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,O.jsx)(`text`,{x:208,y:n.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,O.jsx)(`text`,{x:220,y:n.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,O.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,V.r1_to_r2.exam1.p,` and `,V.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function Y({label:e,children:t}){return(0,O.jsxs)(`div`,{className:`min-w-0`,children:[(0,O.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,O.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function X({className:e}){let n={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2, written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,O.jsxs)(`div`,{className:t(`grid gap-3 md:grid-cols-3`,e),children:[(0,O.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,O.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,O.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:n.name}),(0,O.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,O.jsx)(Y,{label:`Written by`,children:n.writer}),(0,O.jsx)(Y,{label:`What`,children:n.what}),(0,O.jsx)(Y,{label:`Kept apart from the exams`,children:n.never})]})]}),V.exams.map(e=>(0,O.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,O.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,O.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,O.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,O.jsx)(Y,{label:`Written by`,children:e.writer}),(0,O.jsx)(Y,{label:`Why that matters`,children:e.writer_note}),(0,O.jsx)(Y,{label:`Industries`,children:e.industries}),(0,O.jsx)(Y,{label:`How it was checked`,children:e.checked}),(0,O.jsx)(Y,{label:`Fingerprint (SHA-256)`,children:(0,O.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}export{s as C,l as S,x as _,K as a,p as b,I as c,A as d,g as f,E as g,D as h,J as i,L as l,C as m,V as n,M as o,_ as p,X as r,F as s,q as t,z as u,w as v,d as x,h as y};