import{f as e,g as t,i as n,p as r}from"./utils-YUdTo7nM.js";import{h as i,v as a,y as o}from"./who-DHM5lcYB.js";import{i as s,n as c,r as l}from"./shield-alert-DA2WHqN4.js";import{A as u,E as d}from"./index-D_Uiu7Wi.js";var f={name:`book-open`,size:24,node:[[`path`,{d:`M12 5v16`,key:`1f6ucr`}],[`path`,{d:`M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z`,key:`1fyvmf`}]]};f.node;var p=u(f),m={name:`hourglass`,size:24,node:[[`path`,{d:`M5 22h14`,key:`ehvnwv`}],[`path`,{d:`M5 2h14`,key:`pdyrp9`}],[`path`,{d:`M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22`,key:`1d314k`}],[`path`,{d:`M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2`,key:`1vvvr6`}]]};m.node;var h=u(m),g={name:`layout-dashboard`,size:24,node:[[`rect`,{width:`7`,height:`9`,x:`3`,y:`3`,rx:`1`,key:`10lvy0`}],[`rect`,{width:`7`,height:`5`,x:`14`,y:`3`,rx:`1`,key:`16une8`}],[`rect`,{width:`7`,height:`9`,x:`14`,y:`12`,rx:`1`,key:`1hutg5`}],[`rect`,{width:`7`,height:`5`,x:`3`,y:`16`,rx:`1`,key:`ldoo1y`}]]};g.node;var _=u(g),v=[`real`,`measured`,`simulation`,`pending`,`not-run`],y={real:{label:`Real`,meaning:`Happened or runs as described, and you can check it`},measured:{label:`Measured`,meaning:`A number measured on a real model or a live gateway`},simulation:{label:`Simulation`,meaning:`Mock models; no real model was measured`},pending:{label:`Pending`,meaning:`Being measured now; no number yet`},"not-run":{label:`Not run`,meaning:`Built, but not yet run for real`}},b={real:`good`,measured:`owned`,simulation:`outline`,pending:`warn`,"not-run":`neutral`};function x(e){return typeof e==`string`&&v.includes(e)}function S(e){return typeof e==`string`?e.trim():``}function C(e){let t;try{t=JSON.parse(e)}catch{t={}}let n=t??{},r=n.links??{},i=[];for(let e of Array.isArray(n.entries)?n.entries:[]){let t=e??{},n=t.check??{},r={label:S(n.label)};S(n.command)&&(r.command=S(n.command)),S(n.href)&&(r.href=S(n.href));let a={id:S(t.id),claim:S(t.claim),status:x(t.status)?t.status:`pending`,evidence:S(t.evidence),check:r,paths:Array.isArray(t.paths)?t.paths.map(S).filter(Boolean):[],date:S(t.date),facts:(Array.isArray(t.facts)?t.facts:[]).map(e=>({label:S(e?.label),value:S(e?.value)})).filter(e=>e.label&&e.value),metrics:(Array.isArray(t.metrics)?t.metrics:[]).map(e=>{let t=e??{};return{id:S(t.id),label:S(t.label),value:S(t.value)||null}})};a.id&&a.claim&&a.evidence&&a.check.label&&(a.check.command||a.check.href)&&i.push(w(a))}return{updated:S(n.updated),links:{repo:S(r.repo),docs:S(r.docs),demo:S(r.demo),video:S(r.video)||null},entries:i}}function w(e){let t=e.metrics.filter(e=>e.value!=null).length;return e.status===`measured`&&(e.metrics.length===0||t<e.metrics.length)?{...e,status:`pending`,metrics:e.metrics.map(e=>({...e,value:null}))}:e.status!==`measured`&&t>0?{...e,metrics:e.metrics.map(e=>({...e,value:null}))}:e}function T(e,t){return e.entries.find(e=>e.id===t)}function E(e){return v.map(t=>({status:t,n:e.entries.filter(e=>e.status===t).length})).filter(e=>e.n>0)}function D(e){return`${y[e].label}: ${y[e].meaning.charAt(0).toLowerCase()}${y[e].meaning.slice(1)}`}function O(e){return e===`mock`?`simulation`:e===`live`?`measured`:null}function k(e){let t=/^(\d{4})-(\d{2})-(\d{2})$/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`],r=Number(t[2])-1;return r<0||r>11?e:`${Number(t[3])} ${n[r]} ${t[1]}`}var A=e(),j={real:a,measured:c,simulation:d,pending:h,"not-run":s};function M({status:e,className:t,label:n}){let r=j[e],a=y[e];return(0,A.jsxs)(i,{tone:b[e],className:t,title:a.meaning,"aria-label":D(e),role:`img`,children:[(0,A.jsx)(r,{"aria-hidden":!0}),` `,n??a.label]})}var N=`{
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
`,P=[`base`,`teacher`,`r1`,`r2`],F=[`simple`,`join`,`aggregate`,`hard`];function I(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of P){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function L(e){return`${(e*100).toFixed(1)}%`}function R(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function z(e){return`${e.agree} of ${e.n} · ${L(e.rate)}`}function B(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function V(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var H={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}},U=I(N),W=124,G=130,K=168;function q({ticks:e,max:t,top:n,bottom:r}){return(0,A.jsx)(`g`,{"aria-hidden":!0,children:e.map(e=>{let i=G+B(e,t,K);return(0,A.jsxs)(`g`,{children:[(0,A.jsx)(`line`,{x1:i,x2:i,y1:n,y2:r,stroke:`var(--grid)`,strokeWidth:1}),(0,A.jsxs)(`text`,{x:i,y:r+13,textAnchor:`middle`,fontSize:10,fill:`var(--muted)`,children:[Math.round(e*100),`%`]})]},e)})})}function J({exam:e,className:t}){let r=U.exams.find(t=>t.id===e),i=.6,a=6+P.length*40+22,o=Object.fromEntries(U.models.map(e=>[e.id,e.short]));return(0,A.jsxs)(`figure`,{className:n(`min-w-0`,t),children:[(0,A.jsxs)(`svg`,{viewBox:`0 0 360 ${a}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share of ${r.rows} questions answered exactly right, by model`,children:[(0,A.jsx)(q,{ticks:[0,.2,.4,.6],max:i,top:6,bottom:6+P.length*40}),P.map((t,n)=>{let r=U.scores[e][t],a=6+n*40,s=H[t];return(0,A.jsxs)(`g`,{children:[(0,A.jsx)(`text`,{x:W,y:a+21,textAnchor:`end`,fontSize:12.5,fontWeight:t===`r2`?700:500,fill:`var(--ink)`,children:o[t]}),(0,A.jsx)(`rect`,{x:G,y:a+8,width:Math.max(2,B(r.rate,i,K)),height:20,rx:4,fill:s.color,opacity:s.opacity}),(0,A.jsx)(`line`,{x1:G+B(r.lo,i,K),x2:G+B(r.hi,i,K),y1:a+18,y2:a+18,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,A.jsx)(`line`,{x1:G+B(r.lo,i,K),x2:G+B(r.lo,i,K),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,A.jsx)(`line`,{x1:G+B(r.hi,i,K),x2:G+B(r.hi,i,K),y1:a+13,y2:a+23,stroke:`var(--ink)`,strokeWidth:1.4,opacity:.75}),(0,A.jsx)(`text`,{x:G+B(r.hi,i,K)+6,y:a+22,fontSize:12.5,fontWeight:700,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:L(r.rate)})]},t)})]}),(0,A.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`: `,r.rows,` questions, written by `,r.writer.split(`,`)[0],`. The line on each bar is the 95% range.`]}),(0,A.jsxs)(`table`,{className:`sr-only`,children:[(0,A.jsxs)(`caption`,{children:[r.name,`, strict scores`]}),(0,A.jsx)(`tbody`,{children:P.map(t=>(0,A.jsxs)(`tr`,{children:[(0,A.jsx)(`th`,{scope:`row`,children:o[t]}),(0,A.jsx)(`td`,{children:z(U.scores[e][t])}),(0,A.jsx)(`td`,{children:R(U.scores[e][t])})]},t))})]})]})}function Y({exam:e,className:t}){let r=U.exams.find(t=>t.id===e),i=[`base`,`r1`,`r2`],a={base:`base`,teacher:`teacher`,r1:`round 1`,r2:`round 2`},o=.8,s=6+F.length*74+20;return(0,A.jsxs)(`figure`,{className:n(`min-w-0`,t),children:[(0,A.jsxs)(`svg`,{viewBox:`0 0 360 ${s}`,role:`img`,className:`h-auto w-full`,"aria-label":`${r.name}: share right by question difficulty, base model and the two adapters`,children:[(0,A.jsx)(q,{ticks:[0,.2,.4,.6,.8],max:o,top:6,bottom:6+F.length*74-8}),F.map((t,n)=>{let r=U.difficulty[e][t],s=6+n*74;return(0,A.jsxs)(`g`,{children:[(0,A.jsx)(`text`,{x:W,y:s+28,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:t}),(0,A.jsxs)(`text`,{x:W,y:s+44,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:[r.n,` questions`]}),i.map((e,t)=>{let n=r[e]/r.n,i=s+8+t*20,c=H[e];return(0,A.jsxs)(`g`,{children:[(0,A.jsx)(`rect`,{x:G,y:i,width:Math.max(2,B(n,o,K)),height:16,rx:3,fill:c.color,opacity:c.opacity}),(0,A.jsxs)(`text`,{x:G+B(n,o,K)+5,y:i+12,fontSize:10.5,fill:`var(--ink)`,style:{fontVariantNumeric:`tabular-nums`},children:[a[e],` `,r[e],`/`,r.n]})]},e)})]},t)})]}),(0,A.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[r.name,`, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.`]})]})}function X({className:e}){let t=[{exam:`exam1`,name:`Exam 1`},{exam:`exam2`,name:`Exam 2`}];return(0,A.jsxs)(`figure`,{className:n(`min-w-0`,e),children:[(0,A.jsxs)(`svg`,{viewBox:`0 0 368 ${t.length*44+28}`,role:`img`,className:`h-auto w-full`,"aria-label":`Questions that changed between the round 1 and the round 2 adapter, on both exams`,children:[(0,A.jsx)(`line`,{x1:214,x2:214,y1:2,y2:t.length*44+2,stroke:`var(--line-strong)`}),t.map((e,t)=>{let n=U.r1_to_r2[e.exam],r=6+t*44;return(0,A.jsxs)(`g`,{children:[(0,A.jsx)(`text`,{x:W,y:r+24,textAnchor:`end`,fontSize:12.5,fontWeight:600,fill:`var(--ink)`,children:e.name}),(0,A.jsx)(`rect`,{x:214-B(n.right_to_wrong,26,84),y:r+8,width:B(n.right_to_wrong,26,84),height:20,rx:3,fill:`var(--bad)`,opacity:.85}),(0,A.jsxs)(`text`,{x:214-B(n.right_to_wrong,26,84)-5,y:r+23,textAnchor:`end`,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`−`,n.right_to_wrong]}),(0,A.jsx)(`rect`,{x:214,y:r+8,width:B(n.wrong_to_right,26,84),height:20,rx:3,fill:`var(--good)`,opacity:.9}),(0,A.jsxs)(`text`,{x:214+B(n.wrong_to_right,26,84)+5,y:r+23,fontSize:12,fontWeight:700,fill:`var(--ink)`,children:[`+`,n.wrong_to_right]})]},e.exam)}),(0,A.jsx)(`text`,{x:208,y:t.length*44+20,textAnchor:`end`,fontSize:10.5,fill:`var(--muted)`,children:`right in round 1 only`}),(0,A.jsx)(`text`,{x:220,y:t.length*44+20,fontSize:10.5,fill:`var(--muted)`,children:`right in round 2 only`})]}),(0,A.jsxs)(`figcaption`,{className:`mt-1 text-[12.5px] leading-snug text-muted`,children:[`Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = `,U.r1_to_r2.exam1.p,` and `,U.r1_to_r2.exam2.p,`), so the direction repeats, but neither exam alone settles it.`]})]})}function Z({label:e,children:t}){return(0,A.jsxs)(`div`,{className:`min-w-0`,children:[(0,A.jsx)(`dt`,{className:`text-[12px] font-medium text-muted`,children:e}),(0,A.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] leading-snug text-ink-2`,children:t})]})}function Q({className:e}){let t={name:`Training examples`,writer:`Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)`,what:`821 examples in round 1 and 3,501 more in round 2, written on Nebius Token Factory. Every query was run before it was kept.`,never:`Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).`};return(0,A.jsxs)(`div`,{className:n(`grid gap-3 md:grid-cols-3`,e),children:[(0,A.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,A.jsx)(`p`,{className:`font-mono text-[12px] text-muted`,children:`Teaches the adapter`}),(0,A.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:t.name}),(0,A.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,A.jsx)(Z,{label:`Written by`,children:t.writer}),(0,A.jsx)(Z,{label:`What`,children:t.what}),(0,A.jsx)(Z,{label:`Kept apart from the exams`,children:t.never})]})]}),U.exams.map(e=>(0,A.jsxs)(`article`,{className:`min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,A.jsxs)(`p`,{className:`font-mono text-[12px] text-muted`,children:[`Tests the adapter · `,e.rows,` questions · `,e.built]}),(0,A.jsx)(`h3`,{className:`mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink`,children:e.name}),(0,A.jsxs)(`dl`,{className:`mt-3 space-y-2.5`,children:[(0,A.jsx)(Z,{label:`Written by`,children:e.writer}),(0,A.jsx)(Z,{label:`Why that matters`,children:e.writer_note}),(0,A.jsx)(Z,{label:`Industries`,children:e.industries}),(0,A.jsx)(Z,{label:`How it was checked`,children:e.checked}),(0,A.jsx)(Z,{label:`Fingerprint (SHA-256)`,children:(0,A.jsx)(`span`,{className:`font-mono text-[11.5px] [overflow-wrap:anywhere]`,children:e.fingerprint})})]})]},e.id))]})}var $=t(r(),1);function ee({title:e,copy:t,children:r,className:i}){let[a,s]=(0,$.useState)(!1);return(0,A.jsxs)(`div`,{className:n(`overflow-hidden rounded-2xl border border-line bg-bg-sunken`,i),children:[(0,A.jsxs)(`div`,{className:`flex items-center justify-between border-b border-line px-4 py-2`,children:[(0,A.jsx)(`span`,{className:`font-mono text-[12px] text-muted`,children:e}),(0,A.jsxs)(`button`,{type:`button`,onClick:async()=>{try{await navigator.clipboard.writeText(t),s(!0),window.setTimeout(()=>s(!1),1600)}catch{}},className:`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink`,"aria-label":`Copy code`,children:[a?(0,A.jsx)(o,{className:`size-3.5 text-good`}):(0,A.jsx)(l,{className:`size-3.5`}),a?`Copied`:`Copy`]})]}),(0,A.jsx)(`pre`,{className:`overflow-x-auto px-4 py-4 font-mono text-[13px] leading-6 text-ink-2`,children:(0,A.jsx)(`code`,{children:r})})]})}var te={k:e=>(0,A.jsx)(`span`,{className:`text-owned-ink`,children:e}),s:e=>(0,A.jsx)(`span`,{className:`text-good-ink`,children:e}),c:e=>(0,A.jsx)(`span`,{className:`text-muted`,children:e}),h:e=>(0,A.jsx)(`span`,{className:`rounded bg-owned-soft px-1 text-owned-ink`,children:e})};export{p as C,h as S,k as _,Q as a,E as b,P as c,z as d,V as f,T as g,y as h,U as i,L as l,v as m,te as n,X as o,M as p,Y as r,J as s,ee as t,R as u,O as v,_ as x,C as y};