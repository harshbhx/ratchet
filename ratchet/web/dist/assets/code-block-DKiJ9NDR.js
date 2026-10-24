import{_ as e,a as t,m as n,p as r}from"./utils-CjFVJpAU.js";import{C as i,_ as a}from"./exam-charts-BGoCexrc.js";import{y as o}from"./who-i-Gtvbsk.js";import{M as s}from"./index-jrgTnyWe.js";var c={name:`arrow-down`,size:24,node:[[`path`,{d:`M12 5v14`,key:`s699le`}],[`path`,{d:`m19 12-7 7-7-7`,key:`1idqje`}]]};c.node;var l=s(c),u={name:`book-open`,size:24,node:[[`path`,{d:`M12 5v16`,key:`1f6ucr`}],[`path`,{d:`M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z`,key:`1fyvmf`}]]};u.node;var d=s(u),f={name:`code`,size:24,node:[[`path`,{d:`m16 18 6-6-6-6`,key:`eg8j8`}],[`path`,{d:`m8 6-6 6 6 6`,key:`ppft3o`}]]};f.node;var p=s(f),m={name:`layout-dashboard`,size:24,node:[[`rect`,{width:`7`,height:`9`,x:`3`,y:`3`,rx:`1`,key:`10lvy0`}],[`rect`,{width:`7`,height:`5`,x:`14`,y:`3`,rx:`1`,key:`16une8`}],[`rect`,{width:`7`,height:`9`,x:`14`,y:`12`,rx:`1`,key:`1hutg5`}],[`rect`,{width:`7`,height:`5`,x:`3`,y:`16`,rx:`1`,key:`ldoo1y`}]]};m.node;var h=s(m),g={name:`list-checks`,size:24,node:[[`path`,{d:`M13 5h8`,key:`a7qcls`}],[`path`,{d:`M13 12h8`,key:`h98zly`}],[`path`,{d:`M13 19h8`,key:`c3s6r1`}],[`path`,{d:`m3 17 2 2 4-4`,key:`1jhpwq`}],[`path`,{d:`m3 7 2 2 4-4`,key:`1obspn`}]]};g.node;var _=s(g),v={name:`receipt`,size:24,node:[[`path`,{d:`M12 17V7`,key:`pyj7ub`}],[`path`,{d:`M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8`,key:`1elt7d`}],[`path`,{d:`M4 3a1 1 0 0 1 1-1 1.3 1.3 0 0 1 .7.2l.933.6a1.3 1.3 0 0 0 1.4 0l.934-.6a1.3 1.3 0 0 1 1.4 0l.933.6a1.3 1.3 0 0 0 1.4 0l.933-.6a1.3 1.3 0 0 1 1.4 0l.934.6a1.3 1.3 0 0 0 1.4 0l.933-.6A1.3 1.3 0 0 1 19 2a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1 1.3 1.3 0 0 1-.7-.2l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.934.6a1.3 1.3 0 0 1-1.4 0l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-1.4 0l-.934-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-.7.2 1 1 0 0 1-1-1z`,key:`ycz6yz`}]]};v.node;var y=s(v),b={name:`route`,size:24,node:[[`circle`,{cx:`6`,cy:`19`,r:`3`,key:`1kj8tv`}],[`path`,{d:`M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15`,key:`1d8sl`}],[`circle`,{cx:`18`,cy:`5`,r:`3`,key:`gq8acd`}]]};b.node;var x=s(b),S=a(`{
  "updated": "2026-10-24",
  "links": {
    "repo": "https://github.com/harshbhx/ratchet",
    "docs": "https://docs.ratchet.dualmindlab.tech/",
    "demo": "https://demo.ratchet.dualmindlab.tech",
    "video": null
  },
  "entries": [
    {
      "id": "teacher-labels",
      "claim": "A real teacher wrote the text-to-SQL training data",
      "status": "real",
      "evidence": "Nemotron 3 Ultra on Nebius Token Factory wrote the databases, questions and SQL. Every SQL query ran in a sandbox and was kept only if it ran and returned rows. 821 training rows; 172 exam rows from held-out database domains.",
      "check": {
        "label": "Read the generator: a query is kept only if it runs and returns rows",
        "href": "https://github.com/harshbhx/ratchet/blob/main/scripts/text2sql/gen_sql_data.py"
      },
      "paths": ["scripts/text2sql/gen_sql_data.py", "scripts/text2sql/README.md"],
      "date": "2026-10-22",
      "facts": [
        { "label": "Training rows", "value": "821" },
        { "label": "Exam rows (held-out domains)", "value": "172" },
        { "label": "Teacher spend", "value": "$2.87 of a $5 cap" }
      ],
      "metrics": []
    },
    {
      "id": "adapter-training",
      "claim": "A LoRA adapter was trained on that data",
      "status": "real",
      "evidence": "Trained on a free Google Colab T4 GPU, not on Nebius GPUs: a 4-bit LoRA (r=16) on Nemotron 3 Nano 4B, 206 steps over 2 epochs in 66 minutes. Training loss went from 0.334 to 0.075; a training loss is not a quality result.",
      "check": {
        "label": "Read the training script that ran on the T4",
        "href": "https://github.com/harshbhx/ratchet/blob/main/scripts/text2sql/train_t4.py"
      },
      "paths": ["scripts/text2sql/train_t4.py"],
      "date": "2026-10-22",
      "facts": [
        { "label": "Hardware", "value": "Free Colab T4" },
        { "label": "Adapter", "value": "4-bit LoRA, r=16" },
        { "label": "Steps", "value": "206 over 2 epochs in 66 minutes" },
        { "label": "Training loss", "value": "0.334 to 0.075" }
      ],
      "metrics": []
    },
    {
      "id": "exam-result",
      "claim": "How well the adapter does on two held-out exams",
      "status": "measured",
      "evidence": "Two exams of text-to-SQL questions on databases the models never saw: 172 written by the teacher and 200 on new industries written by a different model (Claude, for evaluation only, never training). An answer is right when running it returns the same rows as the stored reference query. Strict, exam 1 / exam 2: base 22 of 172 / 25 of 200, teacher 44 / 30, adapter round 1 (821 rows) 57 / 56, adapter round 2 (4,322 rows) 67 / 66. Every number is recomputed from the raw answers by one script. Round 2 beat round 1 on both exams, but the gain could partly be chance (p = 0.12 and 0.13); the adapter does not yet replace the teacher at a 95 percent precision target.",
      "check": {
        "label": "Recompute the numbers from the raw answers (evidence/text2sql-round1, text2sql-round2 and text2sql-exam2)",
        "command": "python scripts/measure_adapter.py verify --raw raw.jsonl --results results.json --golden golden.jsonl --dbs dbs.json"
      },
      "paths": [
        "scripts/measure_adapter.py"
      ],
      "date": "2026-10-24",
      "facts": [
        {
          "label": "Exam 1",
          "value": "172 rows written by the teacher; 40% from five industries never trained on"
        },
        {
          "label": "Exam 2",
          "value": "200 rows on 10 new industries, written by Claude Sonnet 5.5 (evaluation only)"
        },
        {
          "label": "Base model",
          "value": "12.8% on exam 1, 12.5% on exam 2"
        },
        {
          "label": "Teacher, same prompts",
          "value": "25.6% on exam 1, 15.0% on exam 2"
        },
        {
          "label": "Adapter, round 1 (821 rows)",
          "value": "33.1% on exam 1, 28.0% on exam 2"
        },
        {
          "label": "Adapter, round 2 (4,322 rows)",
          "value": "39.0% on exam 1 (range 32.0 to 46.4), 33.0% on exam 2 (range 26.9 to 39.8)"
        }
      ],
      "metrics": [
        {
          "id": "accuracy",
          "label": "Accuracy",
          "value": "39.0% on exam 1 and 33.0% on exam 2 (base model 12.8% and 12.5%)"
        },
        {
          "id": "coverage",
          "label": "Coverage (answered by the adapter)",
          "value": "1 of 86 questions at the 95% precision target on exam 1; none on exam 2"
        },
        {
          "id": "precision",
          "label": "Precision when answered",
          "value": "too few answered questions to trust"
        },
        {
          "id": "cost",
          "label": "Cost per 1,000 requests",
          "value": "about 1% cheaper than all-teacher at that coverage on exam 1; none on exam 2"
        }
      ]
    },
    {
      "id": "hf-publish",
      "claim": "The adapter, its training data and the exams on Hugging Face",
      "status": "real",
      "evidence": "Published and public under the harshbhx account on 24 Oct 2026: the round 2 and round 1 adapters (with NVIDIA's license, a NOTICE and a manifest), all 4,322 training examples with their provenance, and both exams with every model's answers and scores. The adapter weights downloaded from the public repository match the recorded SHA-256 hashes.",
      "check": {
        "label": "Open the adapter's page",
        "href": "https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql"
      },
      "paths": [
        "scripts/text2sql/README.md"
      ],
      "date": "2026-10-24",
      "facts": [
        {
          "label": "Adapter",
          "value": "harshbhx/ratchet-nemotron-nano-4b-text2sql"
        },
        {
          "label": "Training data",
          "value": "harshbhx/ratchet-text2sql-synthetic"
        },
        {
          "label": "Exams and answers",
          "value": "harshbhx/ratchet-text2sql-exams"
        },
        {
          "label": "Tool-calling adapter (round 3)",
          "value": "harshbhx/ratchet-nemotron-nano-4b-toolcall"
        },
        {
          "label": "Tool-calling data and exams (round 3)",
          "value": "harshbhx/ratchet-toolcall-train, ratchet-toolcall-exam-hard, ratchet-toolcall-exam-bfcl"
        }
      ],
      "metrics": []
    },
    {
      "id": "gateway-live",
      "claim": "The student and its training on Nebius GPUs (Serverless Jobs and Endpoints)",
      "status": "not-run",
      "evidence": "The tuned student has not been served on a Nebius endpoint and no training job has run on Nebius GPUs: round 1 trained on a free Colab T4 and round 2 on a rented Modal T4. The scripts exist and are tested offline; running them needs GPU access this project did not have.",
      "check": {
        "label": "The day-1 checks, with a Token Factory key",
        "command": "make day1"
      },
      "paths": [
        "scripts/day1_check.py"
      ],
      "date": "2026-10-24",
      "facts": [],
      "metrics": []
    },
    {
      "id": "live-demo",
      "claim": "A public live demo that calls Nebius Token Factory",
      "status": "real",
      "evidence": "On 24 Oct 2026 the gateway was deployed in live mode on Cloudflare Containers. A request to it is answered by Nemotron 3 Ultra on Nebius Token Factory under daily spend and rate limits, and an admin key and the mock-only demo controls are refused. No trained student is served in it yet, so the teacher answers every request.",
      "check": {
        "label": "Open it and send a request: the reply carries a ratchet block with who answered",
        "href": "https://live.ratchet.dualmindlab.tech"
      },
      "paths": [
        "deploy/cloudflare-live/src/index.ts",
        "deploy/cloudflare-live/wrangler.json"
      ],
      "date": "2026-10-24",
      "facts": [
        {
          "label": "Answered by",
          "value": "the teacher, Nemotron 3 Ultra on Token Factory"
        },
        {
          "label": "Limits",
          "value": "about $2.50 a day for the gateway, $1.50 for the demo key"
        }
      ],
      "metrics": []
    },
    {
      "id": "live-gateway-smoke",
      "claim": "The gateway in live mode, answering through real Token Factory calls",
      "status": "real",
      "evidence": "On 23 Oct 2026 the gateway ran in live mode and served 20 real requests through its OpenAI-compatible API. Every one was answered by Nemotron 3 Ultra on Nebius Token Factory, for about $0.01. No trained student exists yet in live mode, so the student's share was 0%. The per-request records are published and the harness recomputes its client-side numbers from them.",
      "check": {
        "label": "Recompute the run's client-side numbers from its records",
        "command": "python -m ratchet.liverun verify evidence/live-smoke-2026-10-23"
      },
      "paths": [
        "ratchet/liverun.py",
        "evidence/live-smoke-2026-10-23/results.json"
      ],
      "date": "2026-10-24",
      "facts": [
        {
          "label": "Requests",
          "value": "20, all answered by the teacher"
        },
        {
          "label": "Spend",
          "value": "about $0.01"
        },
        {
          "label": "Teacher",
          "value": "nvidia/Nemotron-3-Ultra-550b-a55b on Nebius Token Factory"
        }
      ],
      "metrics": []
    },
    {
      "id": "scout-tavily",
      "claim": "Scout with real Tavily searches",
      "status": "not-run",
      "evidence": "Scout runs on a mock research world in the demo. Recorded live runs with real Tavily searches are planned.",
      "check": {
        "label": "How Scout is built",
        "href": "https://github.com/harshbhx/ratchet/blob/main/docs/SCOUT.md"
      },
      "paths": ["docs/SCOUT.md", "ratchet/scout/credits.py"],
      "date": "2026-10-23",
      "facts": [],
      "metrics": []
    },
    {
      "id": "demo-numbers",
      "claim": "The demo and the dashboard numbers",
      "status": "simulation",
      "evidence": "The demo at demo.ratchet.dualmindlab.tech runs in mock mode on a Cloudflare Container: mock models answer every request, and the story page replays the reference run. None of these numbers measures a real model.",
      "check": {
        "label": "Run the reference simulation yourself",
        "command": "python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3"
      },
      "paths": ["sim/simulate.py"],
      "date": "2026-10-23",
      "facts": [],
      "metrics": []
    },
    {
      "id": "machinery",
      "claim": "The cascade, release gate, signed BOM, certificates and receipts",
      "status": "real",
      "evidence": "Real as code and tests (more than 1,900 automated tests, no network and no GPU), shown here in simulation.",
      "check": {
        "label": "Run the test suite",
        "command": "make test"
      },
      "paths": ["ratchet/router.py", "ratchet/release.py", "ratchet/bom.py", "ratchet/certificate.py", "ratchet/receipts.py"],
      "date": "2026-10-23",
      "facts": [],
      "metrics": []
    },
    {
      "id": "license-rule",
      "claim": "Only open teachers: the license-clean rule",
      "status": "real",
      "evidence": "Enforced in code: every teacher passes the license registry, and closed models are refused as teachers.",
      "check": {
        "label": "Run the license tests",
        "command": "python -m pytest -q tests/test_licensing.py"
      },
      "paths": ["ratchet/licenses.py", "tests/test_licensing.py"],
      "date": "2026-10-23",
      "facts": [],
      "metrics": []
    },
    {
      "id": "toolcall-round3",
      "claim": "How well a tool-calling adapter does on two held-out exams",
      "status": "measured",
      "evidence": "On the hard exam the adapter is above the untrained model: 90.7% against 62.7%, and the 95% ranges do not overlap. It is about the same as the teacher: 90.7% against 91.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). On the BFCL exam the adapter is about the same as the untrained model: 80.3% against 77.3% (higher by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). It is about the same as the teacher: 80.3% against 88.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). Strict scoring. Free-text arguments are a known weak spot in the training data: the strict scorer cannot agree on free text, so tools such as create_ticket (15 training rows), search_files, send_payment and search_inbox are thin. The hard exam uses 20 tools that were never in training. The adapter was trained on a rented Modal T4, not on Nebius GPUs, on synthetic data. The hard exam (150 questions, 20 new tools) was written by Claude Sonnet 5.5 for evaluation only and is never training data; the second exam (300 questions) is drawn from the Berkeley Function Calling Leaderboard (Apache-2.0).",
      "check": {
        "label": "Recompute the scores from the saved answers",
        "command": "python scripts/toolcall_results.py --check"
      },
      "paths": [
        "scripts/toolcall_results.py",
        "evidence/toolcall/results.json"
      ],
      "date": "2026-10-24",
      "facts": [
        {
          "label": "Hard exam, untrained nano 4b",
          "value": "94 of 150"
        },
        {
          "label": "Hard exam, teacher (nemotron 3 ultra)",
          "value": "137 of 150"
        },
        {
          "label": "Hard exam, tool-calling adapter",
          "value": "136 of 150"
        },
        {
          "label": "BFCL exam, untrained nano 4b",
          "value": "232 of 300"
        },
        {
          "label": "BFCL exam, teacher (nemotron 3 ultra)",
          "value": "265 of 300"
        },
        {
          "label": "BFCL exam, tool-calling adapter",
          "value": "241 of 300"
        }
      ],
      "metrics": [
        {
          "id": "accuracy",
          "label": "Accuracy (strict)",
          "value": "90.7% on the hard exam and 80.3% on BFCL (untrained model 62.7% and 77.3%)"
        }
      ]
    }
  ]
}
`),C=`{
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
    "adapter_vs_ultra": "about_same"
   },
   "sentence": "On the BFCL exam the adapter is about the same as the untrained model: 80.3% against 77.3% (higher by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). It is about the same as the teacher: 80.3% against 88.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart).",
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
 "setting": "The adapter was trained on a rented Modal T4, not on Nebius GPUs, on synthetic data. The hard exam (150 questions, 20 new tools) was written by Claude Sonnet 5.5 for evaluation only and is never training data; the second exam (300 questions) is drawn from the Berkeley Function Calling Leaderboard (Apache-2.0)."
}
`,w=[`hard`,`bfcl`],T=[`base`,`ultra`,`adapter`],E={base:`Untrained Nano 4B`,ultra:`Teacher (Nemotron 3 Ultra)`,adapter:`Tool-calling adapter`};function D(e){return w.every(t=>e.exams[t].scores.adapter.lo>e.exams[t].scores.base.hi)?`B`:`A`}function O(e){if(!e)return null;let t=JSON.parse(e);for(let e of w)for(let n of T){let r=t.exams[e].scores[n];if(!(r.n>0)||Math.abs(r.agree/r.n-r.rate)>1e-4||!(r.lo<=r.rate&&r.rate<=r.hi))throw Error(`toolcall.json: ${e} ${n} is inconsistent`)}if(t.variant!==D(t))throw Error(`toolcall.json: the variant does not follow from the ranges`);return t}function k(e){return e?e.variant===`B`?`top`:`bottom`:`none`}var A=e=>`${(e*100).toFixed(1)}%`,j=e=>`${(e.lo*100).toFixed(1)} to ${(e.hi*100).toFixed(1)}%`,M=e=>`${e.agree} of ${e.n} · ${A(e.rate)}`,N=r(),P=O(Object.values(Object.assign({"../../data/toolcall.json":C}))[0]);function F({where:e}){let t=P;return!t||k(t)!==e?null:(0,N.jsxs)(`section`,{id:`toolcall`,"aria-labelledby":`toolcall-title`,className:`mt-9 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5`,children:[(0,N.jsxs)(`p`,{className:`text-[12.5px] font-medium text-muted`,children:[`Measured on real models`,t.date?`, ${t.date}`:``,`. Not the simulation.`]}),(0,N.jsx)(`h3`,{id:`toolcall-title`,className:`mt-1 text-[17px] font-semibold tracking-[-0.01em] text-ink`,children:`Tool calling: a second adapter, scored strictly on two exams`}),(0,N.jsx)(`div`,{className:`mt-3 overflow-x-auto`,children:(0,N.jsxs)(`table`,{className:`w-full min-w-[30rem] text-left text-[13.5px]`,children:[(0,N.jsx)(`caption`,{className:`sr-only`,children:`Strict tool-calling scores and 95% ranges on two exams`}),(0,N.jsx)(`thead`,{className:`border-b border-line text-[12px] text-muted`,children:(0,N.jsxs)(`tr`,{children:[(0,N.jsx)(`th`,{scope:`col`,className:`py-2 pr-3 font-medium`,children:`Model`}),w.map(e=>(0,N.jsxs)(`th`,{scope:`col`,className:`py-2 pr-3 font-medium`,children:[t.exams[e].label,` (`,t.exams[e].rows,`)`]},e))]})}),(0,N.jsx)(`tbody`,{className:`divide-y divide-line`,children:T.map(e=>(0,N.jsxs)(`tr`,{children:[(0,N.jsx)(`th`,{scope:`row`,className:`py-2 pr-3 font-medium ${e===`adapter`?`text-ink`:`text-ink-2`}`,children:E[e]}),w.map(n=>(0,N.jsxs)(`td`,{className:`py-2 pr-3 tabular-nums text-ink`,children:[M(t.exams[n].scores[e]),` `,(0,N.jsxs)(`span`,{className:`text-muted`,children:[`(`,j(t.exams[n].scores[e]),`)`]})]},n))]},e))})]})}),(0,N.jsx)(`ul`,{className:`mt-3 grid gap-2 text-[14px] leading-relaxed text-ink-2`,children:w.map(e=>(0,N.jsx)(`li`,{children:t.exams[e].sentence},e))}),(0,N.jsxs)(`p`,{className:`mt-3 max-w-[78ch] text-[13px] leading-relaxed text-muted`,children:[t.caveat,` `,t.setting]})]})}var I=e(n(),1);function L({title:e,copy:n,children:r,className:a}){let[s,c]=(0,I.useState)(!1);return(0,N.jsxs)(`div`,{className:t(`overflow-hidden rounded-2xl border border-line bg-bg-sunken`,a),children:[(0,N.jsxs)(`div`,{className:`flex items-center justify-between border-b border-line px-4 py-2`,children:[(0,N.jsx)(`span`,{className:`font-mono text-[12px] text-muted`,children:e}),(0,N.jsxs)(`button`,{type:`button`,onClick:async()=>{try{await navigator.clipboard.writeText(n),c(!0),window.setTimeout(()=>c(!1),1600)}catch{}},className:`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink`,"aria-label":`Copy code`,children:[s?(0,N.jsx)(o,{className:`size-3.5 text-good`}):(0,N.jsx)(i,{className:`size-3.5`}),s?`Copied`:`Copy`]})]}),(0,N.jsx)(`pre`,{className:`overflow-x-auto px-4 py-4 font-mono text-[13px] leading-6 text-ink-2`,children:(0,N.jsx)(`code`,{children:r})})]})}var R={k:e=>(0,N.jsx)(`span`,{className:`text-owned-ink`,children:e}),s:e=>(0,N.jsx)(`span`,{className:`text-good-ink`,children:e}),c:e=>(0,N.jsx)(`span`,{className:`text-muted`,children:e}),h:e=>(0,N.jsx)(`span`,{className:`rounded bg-owned-soft px-1 text-owned-ink`,children:e})};export{S as a,_ as c,d,l as f,F as i,h as l,R as n,x as o,P as r,y as s,L as t,p as u};