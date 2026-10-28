import{l as e}from"./waking-store-C6YeQV1O.js";var t={name:`chevron-right`,size:24,node:[[`path`,{d:`m9 18 6-6-6-6`,key:`mthhwq`}]]};t.node;var n=e(t),r=`{
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
`,i=[`base`,`teacher`,`r1`,`r2`],a=[`simple`,`join`,`aggregate`,`hard`];function o(e){let t=JSON.parse(e);for(let e of[`exam1`,`exam2`])for(let n of i){let r=t.scores[e][n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`exams.json: ${e} ${n} is inconsistent`)}return t}function s(e){return`${(e*100).toFixed(1)}%`}function c(e){return`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`}function l(e){return`${e.agree} of ${e.n} · ${s(e.rate)}`}function u(e,t,n){return t>0?Math.max(0,Math.min(1,e/t))*n:0}function d(e,t){let n=e.scores[t];return n.r2.agree/n.base.agree}var f={base:{color:`var(--muted)`,opacity:.55},teacher:{color:`var(--rented)`,opacity:1},r1:{color:`var(--owned)`,opacity:.55},r2:{color:`var(--owned)`,opacity:1}};export{s as a,d as c,n as d,o as i,u as l,i as n,c as o,f as r,l as s,a as t,r as u};