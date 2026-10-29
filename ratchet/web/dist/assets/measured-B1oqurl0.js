import{h as e,m as t,o as n,v as r}from"./utils-CnmWD783.js";import{p as i}from"./turnstile-model-NHmWWtpN.js";import{b as a,v as o}from"./who-xzxEpNLx.js";import{ot as s}from"./index-ChEdDKdj.js";var c={name:`copy`,size:24,node:[[`rect`,{width:`14`,height:`14`,x:`8`,y:`8`,rx:`2`,ry:`2`,key:`17jyea`}],[`path`,{d:`M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2`,key:`zix9uf`}]]};c.node;var l=i(c),u={name:`eye`,size:24,node:[[`path`,{d:`M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0`,key:`1nclc0`}],[`circle`,{cx:`12`,cy:`12`,r:`3`,key:`1v7zrd`}]]};u.node;var d=i(u),f={name:`gauge`,size:24,node:[[`path`,{d:`m12 14 4-4`,key:`9kzdfg`}],[`path`,{d:`M3.34 19a10 10 0 1 1 17.32 0`,key:`19p75a`}]]};f.node;var p=i(f),m={name:`video`,size:24,node:[[`path`,{d:`m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5`,key:`ftymec`}],[`rect`,{x:`2`,y:`6`,width:`14`,height:`12`,rx:`2`,key:`158x01`}]]};m.node;var h=i(m),g=r(e(),1),_=`https://github.com/harshbhx/ratchet/tree/main/`;function v(e){let t=/^(\d{4})-(\d{2})-(\d{2})/.exec(e);if(!t)return e;let n=[`Jan`,`Feb`,`Mar`,`Apr`,`May`,`Jun`,`Jul`,`Aug`,`Sep`,`Oct`,`Nov`,`Dec`][Number(t[2])-1];return n?`${Number(t[3])} ${n} ${t[1]}`:e}function y(e,t){if(!t||t===e)return v(e);let n=v(e).split(` `),r=v(t).split(` `);return n[1]===r[1]&&n[2]===r[2]?`${n[0]} to ${v(t)}`:`${v(e)} to ${v(t)}`}function b(e,t){return`Measured · ${y(e,t)}`}var x=e=>`${_}${e}`,S=e=>Math.round(e).toLocaleString(`en-US`);function C(e){return e===0?`$0`:e<.1?`$${e.toFixed(4)}`:`$${e.toFixed(2)}`}var w=(e,t=1)=>`${(e*100).toFixed(t)}%`;function T(e){return e>=120?`${Math.floor(e/60)} min ${Math.round(e%60)} s`:`${e>=10?Math.round(e):e.toFixed(1)} s`}function E(e){try{let t=JSON.parse(e);for(let e of[`exams`,`toolcall`,`retrain`,`endpoint`,`scout`,`smoke`,`labelling`,`hosted_nano`,`nightschool`])if(typeof t[e]!=`object`||t[e]===null)return null;return t}catch{return null}}var D=(e,t,n)=>t.map(t=>({group:e,label:n[t.model]??t.label??t.model,value:t.rate,text:`${w(t.rate)} (${t.agree} of ${t.n})`}));function O(e){let t=[],n={base:`Untrained model`,teacher:`Teacher (Ultra)`,r1:`Adapter, round 1`,r2:`Adapter, round 2`,adapter:`Adapter`,ultra:`Teacher (Ultra)`},r=e.exams.exams.find(e=>e.id===`exam1`),i=e.exams.exams.find(e=>e.id===`exam2`);if(r&&i){let a=e=>e.scores.find(e=>e.model===`r2`),o=e=>e.scores.find(e=>e.model===`base`),s=a(r),c=o(r);t.push({id:`exams`,title:`Text-to-SQL exams`,headline:s&&c?`The round-2 adapter gets ${w(s.rate)} on exam 1, the untrained model ${w(c.rate)}`:e.exams.what,date:e.exams.date,source:e.exams.source,tag:`Measured`,facts:[{label:`Exam 1`,value:`${r.rows} questions`},{label:`Exam 2`,value:`${i.rows} questions`}],bars:[...D(`Exam 1`,r.scores,n),...D(`Exam 2`,i.scores,n)]})}e.toolcall.exams.length&&t.push({id:`toolcall`,title:`Tool calling`,headline:e.toolcall.what,date:e.toolcall.date,source:e.toolcall.source,tag:`Measured`,facts:e.toolcall.exams.map(e=>({label:e.label,value:`${e.rows} questions`})),bars:e.toolcall.exams.flatMap(e=>D(e.label,e.scores,n))});let a=e.retrain.exams.find(e=>e.id===`exam1`),o=e.retrain.exams.find(e=>e.id===`exam2`);a&&o&&t.push({id:`retrain`,title:`Round-2 retrain on Nebius`,headline:`Retrained on one preemptible L40S in ${e.retrain.machine_hours} machine-hours for about ${C(e.retrain.cost_usd)}: ${w(a.nebius.rate)} on exam 1, ${w(o.nebius.rate)} on exam 2`,date:e.retrain.date,source:e.retrain.source,tag:`Measured`,facts:[{label:`Machine time`,value:`${e.retrain.machine_hours} h across ${e.retrain.jobs} jobs`},{label:`Cost at list price`,value:C(e.retrain.cost_usd)}],bars:[{group:`Exam 1`,label:`Retrained on Nebius`,value:a.nebius.rate,text:`${w(a.nebius.rate)} (${a.nebius.agree} of ${a.nebius.n})`},{group:`Exam 1`,label:`Original adapter`,value:a.original.rate,text:`${w(a.original.rate)} (${a.original.agree} of ${a.original.n})`},{group:`Exam 2`,label:`Retrained on Nebius`,value:o.nebius.rate,text:`${w(o.nebius.rate)} (${o.nebius.agree} of ${o.nebius.n})`},{group:`Exam 2`,label:`Original adapter`,value:o.original.rate,text:`${w(o.original.rate)} (${o.original.agree} of ${o.original.n})`}]});let s=e.endpoint,c=s.bench.filter(e=>e.adapter===`sql-r2`),l=Math.max(1,...s.bench.map(e=>e.lat_p50_s));if(s.bench.length){let e=s.bench.find(e=>e.adapter===`toolcall`&&e.concurrency===1)??c[0];t.push({id:`endpoint`,title:`Student endpoint speed`,headline:`Served by hand on a Nebius endpoint: ${T(s.cold_start_s)} from a cold start, ${T(s.restart_s)} after a restart, about ${e?e.lat_p50_s.toFixed(1):`1`} s per answer`,date:s.date,dateEnd:s.date_end,source:s.source,tag:`Measured`,facts:[{label:`Cold start to first answer`,value:T(s.cold_start_s)},{label:`Restart to first answer`,value:T(s.restart_s)},{label:`Adapter load`,value:T(s.adapter_load_s)},{label:`GPU price`,value:`$${s.gpu_usd_per_hour.toFixed(2)} an hour`},...c.find(e=>e.concurrency===16)?[{label:`SQL, 16 at a time`,value:`${C(c.find(e=>e.concurrency===16).usd_per_1000)} per 1,000 requests`}]:[],...c.find(e=>e.concurrency===16)?[{label:`Throughput, 16 at a time`,value:`${Math.round(c.find(e=>e.concurrency===16).tokens_per_s_total)} tokens a second`}]:[]],bars:s.bench.map(e=>({group:`${e.adapter}, median latency`,label:`${e.concurrency} at a time`,value:e.lat_p50_s/l,text:`${e.lat_p50_s.toFixed(2)} s`}))})}if(e.scout.runs.length){let n=e.scout.runs,r=Math.max(...n.map(e=>e.cost_usd));t.push({id:`scout`,title:`Recorded Scout runs`,headline:`${n.length} recorded runs on Token Factory with live search; the teacher answered every model call`,date:e.scout.date,dateEnd:e.scout.date_end,source:e.scout.source,tag:`Recorded run`,facts:[{label:`Duration`,value:`${Math.min(...n.map(e=>e.duration_s))} to ${Math.max(...n.map(e=>e.duration_s))} s`},{label:`Cost per run`,value:`${C(Math.min(...n.map(e=>e.cost_usd)))} to ${C(r)}`},{label:`Model calls per run`,value:`${Math.min(...n.map(e=>e.llm_calls))} to ${Math.max(...n.map(e=>e.llm_calls))}`}],bars:n.map((e,t)=>({group:`Cost of each run`,label:`Run ${t+1}`,value:r?e.cost_usd/r:0,text:`${C(e.cost_usd)} · ${e.duration_s} s`}))})}t.push({id:`smoke`,title:`Live smoke run`,headline:`${e.smoke.requests} requests answered by the teacher through the live path in ${e.smoke.wall_s} s`,date:e.smoke.date,source:e.smoke.source,tag:`Recorded run`,facts:[{label:`Spend`,value:C(e.smoke.total_usd)},{label:`Teacher, median`,value:`${Math.round(e.smoke.teacher_p50_wall_ms)} ms`},{label:`Student share`,value:`${e.smoke.student_share_pct.toFixed(0)}% (no student endpoint)`}]});let u=e.labelling.rounds;if(u.length){let n=Math.max(...u.map(e=>e.usd));t.push({id:`labelling`,title:`What the labels cost`,headline:`The teacher wrote ${S(u.reduce((e,t)=>e+t.rows,0))} text-to-SQL rows for ${C(u.reduce((e,t)=>e+t.usd,0))} on Token Factory`,date:e.labelling.date,source:e.labelling.source,tag:`Measured`,facts:u.map(e=>({label:`Round ${e.id.replace(`round`,``)} (${v(e.date)})`,value:`${C(e.usd)} · ${e.calls} calls`})),bars:u.map(e=>({label:`Round ${e.id.replace(`round`,``)}`,value:n?e.usd/n:0,text:`${C(e.usd)}`}))})}let d=e.hosted_nano;return t.push({id:`hosted-nano`,title:`Hosted Nano as a stand-in student`,headline:`The untuned hosted Nano was scored against the teacher's labels on Scout's two distillable calls`,date:d.date,source:d.source,tag:`Measured`,facts:[...Object.entries(d.rows).map(([e,t])=>({label:`${e} rows`,value:String(t)})),{label:`Spend`,value:C(d.spend_usd)}]}),t}function k(e){let t=e.nightschool;if(!t.present||t.verdict!==`rejected`)return null;let n=/(v\d+)$/.exec(t.adapter_name??``);return n?n[1]:null}function A(e){let t=e.nightschool;if(!t.present)return null;let n=k(e),r=t.numbers??{},i=[];return n&&i.push({label:`Verdict`,value:`Rejected at the golden gate; ${n} was never made live`}),r.precision&&r.target&&i.push({label:`Precision at the gate's threshold`,value:`${r.precision} against the ${r.target} target`}),r.coverage&&i.push({label:`Coverage at that threshold`,value:r.coverage}),r.golden_agreement&&i.push({label:`Golden agreement`,value:r.golden_agreement}),r.training_rows&&r.golden_rows&&i.push({label:`Rows`,value:`${r.training_rows} training, ${r.golden_rows} golden`}),i.length===0&&i.push({label:`Evidence files`,value:String(t.files?.length??0)}),{id:`nightschool`,title:`Night School on Nebius`,headline:n?`Night School ran end to end once on Nebius, by hand: trained on a spot L40S, the adapter served from a Nebius endpoint, and the golden gate rejected it`:t.what??`A Night School run on Nebius; the evidence folder has the details`,date:t.date??``,source:t.source,tag:`Recorded run`,facts:i}}var j=[`requests`,`requests_teacher`,`requests_split`,`requests_student`,`student_tokens_in`,`student_tokens_out`,`cost_usd`,`errors`,`scout_runs`];function M(e){if(typeof e!=`object`||!e)return null;let t=e;if(t.recorded!==!0||typeof t.since!=`string`||typeof t.totals!=`object`||t.totals===null)return null;let n=t.totals,r={};for(let e of[`server_errors`,`baseline_usd`]){if(n[e]===void 0)continue;let t=n[e];if(typeof t!=`number`||!Number.isFinite(t)||t<0)return null;r[e]=t}for(let e of j){let t=n[e];if(typeof t!=`number`||!Number.isFinite(t)||t<0)return null;r[e]=t}return{recorded:!0,since:t.since,updated:typeof t.updated==`string`?t.updated:null,instances:typeof t.instances==`number`?t.instances:1,totals:r}}function N(e){return e!==null&&(e.totals.requests>0||e.totals.scout_runs>0)}function P(e,t){if(!N(e))return null;let n=[`${S(e.totals.requests)} live requests since ${v(e.since??``)}`],r=e.totals.requests,i=e.totals.cost_usd;t&&t.smoke.requests>0&&(n.push(`${S(t.smoke.requests)} in the ${v(t.smoke.date)} live smoke run`),r+=t.smoke.requests,i+=t.smoke.total_usd);let a=t?.scout.runs??[];if(t&&a.length>0){let e=a.reduce((e,t)=>e+t.llm_calls,0);n.push(`${S(e)} model calls in ${a.length} recorded Scout runs (${y(t.scout.date,t.scout.date_end)})`),r+=e,i+=a.reduce((e,t)=>e+t.cost_usd,0)}return{requests:r,usd:i,line:`All real traffic to date: ${S(r)} model requests and ${C(i)} on Token Factory = ${n.join(` + `)}.`}}function F(e){let t=e.totals,n=[{label:`Requests`,value:S(t.requests)},{label:`Teacher`,value:S(t.requests_teacher)},t.requests_split>0?{label:`Random split`,value:S(t.requests_split),hint:`sent to the hosted Nano 30B`}:{label:`Random split`,value:`none so far`,hint:`no requests sent to the hosted Nano 30B yet`},t.requests_student>0?{label:`Student`,value:S(t.requests_student)}:{label:`Student`,value:`none`,hint:`Ratchet's own adapter is not connected to the live site`},{label:`Token Factory cost`,value:C(t.cost_usd)},t.scout_runs>0?{label:`Scout runs`,value:S(t.scout_runs)}:{label:`Scout runs`,value:`none so far`}];t.server_errors!==void 0&&n.push(t.server_errors===0?{label:`Server errors`,value:`none`,hint:`no failed model calls`}:{label:`Server errors`,value:S(t.server_errors),hint:`failed model calls`});let r=t.student_tokens_in+t.student_tokens_out;return r>0&&n.splice(5,0,{label:`Student tokens`,value:S(r)}),{headline:`Live since ${v(e.since??``)}: ${S(t.requests)} requests, ${C(t.cost_usd)} Token Factory, ${S(t.scout_runs)} Scout runs`,items:n,note:e.instances>1?`Kept across ${e.instances-1} restart${e.instances===2?``:`s`} of the gateway`:null}}function I(e){let t=k(e);if(e.nightschool.present&&t){let n=`Night School ran end to end once on Nebius, by hand (${v(e.nightschool.date??``)}). The golden gate rejected ${t}, so it was never made live. The loop has not run nightly on live traffic.`;return{versions:`No version of Ratchet's own adapter has served on this live gateway; Ultra teaches, and a random 40% of eligible requests are answered by the hosted Nano 30B. ${n}`,pipeline:n}}return{versions:`No version on this live gateway: the nightly loop is built and tested but has not run on live traffic. Ratchet's own adapter is not connected to it; a random 40% of eligible requests are answered by the hosted Nano 30B.`,pipeline:`The nightly loop is built and tested but has not run on live traffic.`}}function L(e){let t=e.scout.runs;if(t.length===0)return null;let n=t.reduce((e,t)=>e+t.llm_calls,0),r=t.reduce((e,t)=>e+t.cost_usd,0);return{headline:`Recorded runs to date: ${S(n)} model calls across ${t.length} Scout runs, ${C(r)}; ${S(e.smoke.requests)} requests in the live smoke run, ${C(e.smoke.total_usd)}`,source:e.scout.source,date:e.scout.date,dateEnd:e.smoke.date>e.scout.date_end?e.smoke.date:e.scout.date_end}}var R=`{
 "generator": "scripts/measured_numbers.py",
 "exams": {
  "date": "2026-10-24",
  "source": "evidence/text2sql-round2",
  "what": "Text-to-SQL, strict scoring: a query counts only if it returns exactly the reference rows",
  "exams": [
   {
    "id": "exam1",
    "name": "Exam 1",
    "rows": 172,
    "built": "22 Oct 2026",
    "evidence": "evidence/text2sql-round1",
    "scores": [
     {
      "model": "base",
      "label": "Base model",
      "agree": 22,
      "n": 172,
      "rate": 0.1279
     },
     {
      "model": "teacher",
      "label": "Teacher",
      "agree": 44,
      "n": 172,
      "rate": 0.2558
     },
     {
      "model": "r1",
      "label": "Adapter, round 1",
      "agree": 57,
      "n": 172,
      "rate": 0.3314
     },
     {
      "model": "r2",
      "label": "Adapter, round 2",
      "agree": 67,
      "n": 172,
      "rate": 0.3895
     }
    ]
   },
   {
    "id": "exam2",
    "name": "Exam 2",
    "rows": 200,
    "built": "23 Oct 2026",
    "evidence": "evidence/text2sql-exam2",
    "scores": [
     {
      "model": "base",
      "label": "Base model",
      "agree": 25,
      "n": 200,
      "rate": 0.125
     },
     {
      "model": "teacher",
      "label": "Teacher",
      "agree": 30,
      "n": 200,
      "rate": 0.15
     },
     {
      "model": "r1",
      "label": "Adapter, round 1",
      "agree": 56,
      "n": 200,
      "rate": 0.28
     },
     {
      "model": "r2",
      "label": "Adapter, round 2",
      "agree": 66,
      "n": 200,
      "rate": 0.33
     }
    ]
   }
  ]
 },
 "toolcall": {
  "date": "2026-10-24",
  "source": "evidence/toolcall",
  "what": "Tool calling: the model's call agrees with the reference call, on two exams",
  "exams": [
   {
    "id": "hard",
    "label": "Hard exam",
    "rows": 150,
    "scores": [
     {
      "model": "base",
      "agree": 94,
      "n": 150,
      "rate": 0.6267
     },
     {
      "model": "adapter",
      "agree": 136,
      "n": 150,
      "rate": 0.9067
     },
     {
      "model": "ultra",
      "agree": 137,
      "n": 150,
      "rate": 0.9133
     }
    ]
   },
   {
    "id": "bfcl",
    "label": "BFCL exam",
    "rows": 300,
    "scores": [
     {
      "model": "base",
      "agree": 232,
      "n": 300,
      "rate": 0.7733
     },
     {
      "model": "adapter",
      "agree": 241,
      "n": 300,
      "rate": 0.8033
     },
     {
      "model": "ultra",
      "agree": 265,
      "n": 300,
      "rate": 0.8833
     }
    ]
   }
  ]
 },
 "retrain": {
  "date": "2026-10-27",
  "source": "evidence/text2sql-round2-nebius",
  "what": "the round-2 recipe retrained on Nebius AI Cloud (one preemptible L40S, Serverless Job); adapter answers only",
  "exams": [
   {
    "id": "exam1",
    "nebius": {
     "agree": 70,
     "n": 172,
     "rate": 0.407
    },
    "original": {
     "agree": 67,
     "n": 172,
     "rate": 0.3895
    }
   },
   {
    "id": "exam2",
    "nebius": {
     "agree": 69,
     "n": 200,
     "rate": 0.345
    },
    "original": {
     "agree": 66,
     "n": 200,
     "rate": 0.33
    }
   }
  ],
  "machine_hours": 2.73,
  "cost_usd": 2.16,
  "jobs": 10,
  "adapter_sha256": "68b9ed06d984a2ec38a32b58090c246a56ae12ba65f3277f66b5d74227ea25d5"
 },
 "endpoint": {
  "date": "2026-10-27",
  "date_end": "2026-10-28",
  "source": "evidence/student-endpoint-nebius",
  "what": "The student served by hand on a Nebius Serverless Endpoint for speed tests; no accuracy was scored and it serves no live traffic",
  "gpu_usd_per_hour": 0.74,
  "max_tokens": 256,
  "cold_start_s": 457.7,
  "cold_running_s": 275.9,
  "restart_s": 424.5,
  "restart_running_s": 275.9,
  "adapter_load_s": 0.74,
  "bench": [
   {
    "adapter": "sql-r2",
    "concurrency": 1,
    "requests": 20,
    "ok": 19,
    "errors": 1,
    "lat_p50_s": 0.9904,
    "lat_p95_s": 1.7393,
    "tokens_per_s_total": 11.4,
    "requests_per_s": 0.301,
    "usd_per_1000": 0.683
   },
   {
    "adapter": "sql-r2",
    "concurrency": 4,
    "requests": 40,
    "ok": 40,
    "errors": 0,
    "lat_p50_s": 1.2542,
    "lat_p95_s": 1.8919,
    "tokens_per_s_total": 121.3,
    "requests_per_s": 3.111,
    "usd_per_1000": 0.0661
   },
   {
    "adapter": "sql-r2",
    "concurrency": 16,
    "requests": 64,
    "ok": 64,
    "errors": 0,
    "lat_p50_s": 1.6334,
    "lat_p95_s": 3.9434,
    "tokens_per_s_total": 276.3,
    "requests_per_s": 7.059,
    "usd_per_1000": 0.0291
   },
   {
    "adapter": "toolcall",
    "concurrency": 1,
    "requests": 20,
    "ok": 20,
    "errors": 0,
    "lat_p50_s": 0.9479,
    "lat_p95_s": 1.2551,
    "tokens_per_s_total": 27.7,
    "requests_per_s": 0.974,
    "usd_per_1000": 0.2111
   },
   {
    "adapter": "toolcall",
    "concurrency": 4,
    "requests": 40,
    "ok": 40,
    "errors": 0,
    "lat_p50_s": 1.0489,
    "lat_p95_s": 1.306,
    "tokens_per_s_total": 100.7,
    "requests_per_s": 3.447,
    "usd_per_1000": 0.0596
   },
   {
    "adapter": "toolcall",
    "concurrency": 16,
    "requests": 64,
    "ok": 64,
    "errors": 0,
    "lat_p50_s": 1.2303,
    "lat_p95_s": 2.525,
    "tokens_per_s_total": 302.1,
    "requests_per_s": 10.628,
    "usd_per_1000": 0.0193
   }
  ]
 },
 "scout": {
  "date": "2026-10-23",
  "date_end": "2026-10-24",
  "source": "evidence/scout",
  "what": "Three recorded Scout runs on Token Factory with live search; the teacher answered every model call",
  "runs": [
   {
    "file": "evidence/scout/20261023T231345Z-what-is-lora-fine-tuning-and-why-is-it-c.json",
    "recorded_at": "2026-10-23T23:13:45Z",
    "question": "What is LoRA fine-tuning and why is it cheaper than full fine-tuning?",
    "duration_s": 16.2,
    "calls": 23,
    "llm_calls": 20,
    "teacher_calls": 20,
    "student_calls": 0,
    "cost_usd": 0.02767,
    "citations": 3
   },
   {
    "file": "evidence/scout/20261023T231400Z-what-is-model-distillation-and-when-does.json",
    "recorded_at": "2026-10-23T23:14:00Z",
    "question": "What is model distillation and when does it fail?",
    "duration_s": 13.2,
    "calls": 21,
    "llm_calls": 18,
    "teacher_calls": 18,
    "student_calls": 0,
    "cost_usd": 0.022347,
    "citations": 1
   },
   {
    "file": "evidence/scout/20261024T115646Z-what-is-retrieval-augmented-generation-a.json",
    "recorded_at": "2026-10-24T11:56:46Z",
    "question": "What is retrieval-augmented generation and when does it help?",
    "duration_s": 14.8,
    "calls": 22,
    "llm_calls": 19,
    "teacher_calls": 19,
    "student_calls": 0,
    "cost_usd": 0.022516,
    "citations": 5
   }
  ]
 },
 "smoke": {
  "date": "2026-10-23",
  "source": "evidence/live-smoke-2026-10-23",
  "what": "One live smoke run on Token Factory, no student endpoint: the teacher answered every request",
  "run_id": "20261023T211213Z",
  "requests": 20,
  "total_usd": 0.013255999999999999,
  "teacher_p50_wall_ms": 917.5443999993149,
  "ultra_p50_client_ms": 892.2662999975728,
  "wall_s": 8.1,
  "student_share_pct": 0.0
 },
 "labelling": {
  "date": "2026-10-23",
  "source": "evidence/text2sql-round2/data/teacher_manifest_round2.json",
  "what": "What the teacher's text-to-SQL labels cost on Token Factory, at list prices",
  "rounds": [
   {
    "id": "round1",
    "date": "2026-10-22",
    "usd": 2.8701,
    "calls": 273,
    "rows": 993,
    "source": "evidence/text2sql-round1/data/teacher_manifest.json"
   },
   {
    "id": "round2",
    "date": "2026-10-23",
    "usd": 9.1845,
    "calls": 914,
    "rows": 3507,
    "source": "evidence/text2sql-round2/data/teacher_manifest_round2.json"
   }
  ]
 },
 "hosted_nano": {
  "date": "2026-10-26",
  "source": "evidence/hosted-nano",
  "what": "The untuned hosted Nano scored as a stand-in student on Scout's two distillable calls, against the teacher's labels",
  "rows": {
   "source-triage": 99,
   "page-read": 33
  },
  "spend_usd": 0.157,
  "student_model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
  "teacher_model": "nvidia/Nemotron-3-Ultra-550b-a55b"
 },
 "nightschool": {
  "present": true,
  "source": "evidence/nightschool-nebius",
  "date": "2026-10-28",
  "what": "the first end-to-end Night School run on Nebius, by hand: gateway on a CPU VM, a spot L40S training job, the adapter served from a Nebius Serverless Endpoint, the golden gate (rejected, never live); sanitised",
  "verdict": "rejected",
  "adapter_name": "student-v1",
  "has_readme": true,
  "numbers": {
   "golden_agreement": "17.9%",
   "coverage": "4.8%",
   "precision": "81.8%",
   "target": "95%",
   "training_rows": "3,864",
   "golden_rows": "458",
   "rows_imported": "4,322",
   "steps": "966",
   "loss_start": "0.15",
   "loss_end": "0.08",
   "gate_right": "9",
   "gate_answered": "11",
   "gate_questions": "229"
  },
  "files": [
   "evidence/nightschool-nebius/MANIFEST.json",
   "evidence/nightschool-nebius/NOTICE.md",
   "evidence/nightschool-nebius/README.md",
   "evidence/nightschool-nebius/data/final_inventory.txt",
   "evidence/nightschool-nebius/data/gpu_intervals_and_spend.txt",
   "evidence/nightschool-nebius/data/ledger_dump.txt",
   "evidence/nightschool-nebius/fixes/train_job_vm_local_fixes.diff",
   "evidence/nightschool-nebius/logs/night_eval_rerun.log",
   "evidence/nightschool-nebius/logs/night_run1.log",
   "evidence/nightschool-nebius/logs/seed_import.txt",
   "evidence/nightschool-nebius/logs/training_job2_FAILED_SAVE.log",
   "evidence/nightschool-nebius/logs/training_job3_SAVED.log",
   "evidence/nightschool-nebius/ratchet_bom.json",
   "evidence/nightschool-nebius/ratchet_bom.signed.json",
   "evidence/nightschool-nebius/scripts/reuse_night.py"
  ]
 }
}
`,z=t(),B=E(R);function V(){let[e,t]=(0,g.useState)(void 0);return(0,g.useEffect)(()=>{let e=!1;return(async()=>{try{let n=await fetch(`/ratchet/stats/persistent`,{cache:`no-store`}),r=n.ok&&(n.headers.get(`content-type`)??``).includes(`json`)?M(await n.json()):null;e||t(r)}catch{e||t(null)}})(),()=>{e=!0}},[]),e}var H=`inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] transition-colors hover:decoration-ink`;function U({date:e,dateEnd:t,source:r,tag:i=`Measured`,className:a}){let c=i===`Recorded run`?h:p,l=i===`Recorded run`?`Recorded run${e?` · ${b(e,t).replace(`Measured · `,``)}`:``}`:b(e,t);return(0,z.jsxs)(`span`,{className:n(`inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px]`,a),children:[(0,z.jsxs)(o,{tone:`owned`,className:`gap-1`,children:[(0,z.jsx)(c,{"aria-hidden":!0}),` `,l]}),(0,z.jsxs)(`a`,{href:x(r),target:`_blank`,rel:`noopener noreferrer`,className:n(H,`min-w-0 font-mono text-[11.5px] text-muted [overflow-wrap:anywhere]`),children:[r,(0,z.jsx)(s,{"aria-hidden":!0,className:`size-3 shrink-0`}),(0,z.jsx)(`span`,{className:`sr-only`,children:` (opens in a new tab)`})]})]})}function W({bars:e}){let t=[];for(let n of e){let e=t.find(e=>e.name===n.group);e?e.rows.push(n):t.push({name:n.group,rows:[n]})}return(0,z.jsx)(`div`,{className:`space-y-3`,children:t.map(e=>(0,z.jsxs)(`div`,{children:[e.name&&(0,z.jsx)(`p`,{className:`mb-1 text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted`,children:e.name}),(0,z.jsx)(`ul`,{className:`space-y-1.5`,children:e.rows.map(e=>(0,z.jsxs)(`li`,{className:`grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 text-[12.5px] sm:grid-cols-[9.5rem_minmax(0,1fr)]`,children:[(0,z.jsx)(`span`,{className:`truncate text-ink-2`,title:e.label,children:e.label}),(0,z.jsxs)(`span`,{className:`flex min-w-0 items-center gap-2`,children:[(0,z.jsx)(`span`,{className:`h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-line`,"aria-hidden":!0,children:(0,z.jsx)(`span`,{className:`block h-full rounded-full bg-owned`,style:{width:`${Math.max(2,Math.min(1,e.value)*100)}%`}})}),(0,z.jsx)(`span`,{className:`shrink-0 font-mono text-[11.5px] tabular-nums text-ink`,children:e.text})]})]},e.label))})]},e.name??`all`))})}function G({tile:e,className:t}){return(0,z.jsxs)(`article`,{className:n(`flex min-w-0 flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,t),"aria-labelledby":`m-${e.id}`,children:[(0,z.jsxs)(`div`,{className:`min-w-0`,children:[(0,z.jsx)(`h3`,{id:`m-${e.id}`,className:`text-[15px] font-semibold tracking-[-0.01em] text-ink`,children:e.title}),(0,z.jsx)(`p`,{className:`mt-1 text-pretty text-[13.5px] leading-relaxed text-ink-2`,children:e.headline})]}),e.bars&&e.bars.length>0&&(0,z.jsx)(W,{bars:e.bars}),e.facts.length>0&&(0,z.jsx)(`dl`,{className:`grid grid-cols-2 gap-2`,children:e.facts.map((t,r)=>(0,z.jsxs)(`div`,{className:n(`min-w-0 rounded-xl border border-line bg-surface px-3 py-2`,e.facts.length%2==1&&r===e.facts.length-1&&`col-span-2`),children:[(0,z.jsx)(`dt`,{className:`text-[11.5px] leading-snug text-muted`,children:t.label}),(0,z.jsx)(`dd`,{className:`mt-0.5 text-[13.5px] font-semibold tabular-nums text-ink [overflow-wrap:anywhere]`,children:t.value})]},t.label))}),(0,z.jsx)(U,{date:e.date,dateEnd:e.dateEnd,source:e.source,tag:e.tag,className:`mt-auto pt-1`})]})}function K({ids:e,className:t}){if(!B)return null;let r=[...O(B)],i=A(B);i&&r.push(i);let a=e?e.map(e=>r.find(t=>t.id===e)).filter(e=>!!e):r;return a.length===0?null:(0,z.jsx)(`div`,{className:n(`grid gap-4 md:grid-cols-2 xl:grid-cols-3`,t),children:a.map(e=>(0,z.jsx)(G,{tile:e},e.id))})}function q({className:e}){let t=B?L(B):null;return t?(0,z.jsxs)(`div`,{className:n(`flex min-w-0 flex-col gap-1.5`,e),children:[(0,z.jsx)(`p`,{className:`text-[13.5px] leading-relaxed text-ink-2`,children:t.headline}),(0,z.jsx)(U,{date:t.date,dateEnd:t.dateEnd,source:t.source,tag:`Recorded run`})]}):null}function J({className:e}){let t=V();if(t===void 0)return(0,z.jsx)(`div`,{className:n(`h-[72px] rounded-2xl border border-line bg-surface/60`,e),"aria-hidden":!0});if(N(t)){let r=F(t),i=P(t,B);return(0,z.jsxs)(`section`,{"aria-label":`Live totals since the first record`,className:n(`rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,e),children:[(0,z.jsxs)(`div`,{className:`flex flex-wrap items-center gap-2`,children:[(0,z.jsxs)(o,{tone:`good`,className:`gap-1`,children:[(0,z.jsx)(a,{"aria-hidden":!0}),` Live since `,v(t.since??``)]}),r.note&&(0,z.jsx)(`span`,{className:`text-[12px] text-muted`,children:r.note})]}),(0,z.jsx)(`p`,{className:`mt-2 text-[15px] font-medium text-ink`,children:r.headline}),(0,z.jsx)(`dl`,{className:`mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4`,children:r.items.map(e=>(0,z.jsxs)(`div`,{className:`min-w-0 rounded-xl border border-line bg-surface px-3 py-2`,children:[(0,z.jsx)(`dt`,{className:`text-[11.5px] leading-snug text-muted`,children:e.label}),(0,z.jsx)(`dd`,{className:`mt-0.5 text-[15px] font-semibold tabular-nums text-ink`,children:e.value}),e.hint&&(0,z.jsx)(`p`,{className:`text-[11.5px] leading-snug text-muted`,children:e.hint})]},e.label))}),i&&(0,z.jsx)(`p`,{className:`mt-3 text-[13px] leading-snug text-ink-2`,children:i.line}),!i&&t.totals.requests<100&&B&&(0,z.jsx)(q,{className:`mt-3`}),(0,z.jsx)(`p`,{className:`mt-2 text-[12px] leading-snug text-muted`,children:`Counts and dollars only, kept outside the container so a restart does not reset them. Counted since the first record. No trained adapter serves live traffic yet.`})]})}return(0,z.jsxs)(`section`,{"aria-label":`Live totals`,className:n(`rounded-2xl border border-line bg-bg-sunken/50 px-4 py-3.5 sm:px-5`,e),children:[(0,z.jsxs)(`p`,{className:`flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] leading-relaxed text-ink-2`,children:[(0,z.jsxs)(o,{tone:`outline`,className:`gap-1`,children:[(0,z.jsx)(a,{"aria-hidden":!0}),` Live totals`]}),`The live counters start with the first request and are kept across restarts. Until then this page shows what was measured, each value dated and linked to its evidence.`]}),B&&(0,z.jsx)(q,{className:`mt-2`})]})}function Y({ids:e,className:t}){return(0,z.jsxs)(`div`,{className:n(`space-y-3`,t),children:[(0,z.jsx)(`p`,{className:`text-[13.5px] leading-relaxed text-ink-2`,children:`This gateway has not answered a request since it last started, and its own ledger starts empty after every restart. What was measured on real runs is shown instead, each value with its date and the evidence it was read from. The totals the live gateway keeps across restarts are in the strip at the top of the page.`}),(0,z.jsx)(K,{ids:e})]})}function X({requests:e,ids:t,children:n}){return e>0?(0,z.jsx)(z.Fragment,{children:n}):(0,z.jsx)(Y,{ids:t})}export{k as _,G as a,l as b,P as c,y as d,C as f,I as g,A as h,K as i,v as l,F as m,B as n,X as o,N as p,U as r,V as s,J as t,S as u,p as v,d as y};