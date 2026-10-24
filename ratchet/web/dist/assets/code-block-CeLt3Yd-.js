import{_ as e,a as t,m as n,p as r}from"./utils-CjFVJpAU.js";import{C as i,_ as a}from"./exam-charts-C4rJRpxK.js";import{y as o}from"./who-Cj2xBl3a.js";import{M as s}from"./index-C7CrShCB.js";var c={name:`arrow-down`,size:24,node:[[`path`,{d:`M12 5v14`,key:`s699le`}],[`path`,{d:`m19 12-7 7-7-7`,key:`1idqje`}]]};c.node;var l=s(c),u={name:`book-open`,size:24,node:[[`path`,{d:`M12 5v16`,key:`1f6ucr`}],[`path`,{d:`M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z`,key:`1fyvmf`}]]};u.node;var d=s(u),f={name:`code`,size:24,node:[[`path`,{d:`m16 18 6-6-6-6`,key:`eg8j8`}],[`path`,{d:`m8 6-6 6 6 6`,key:`ppft3o`}]]};f.node;var p=s(f),m={name:`layout-dashboard`,size:24,node:[[`rect`,{width:`7`,height:`9`,x:`3`,y:`3`,rx:`1`,key:`10lvy0`}],[`rect`,{width:`7`,height:`5`,x:`14`,y:`3`,rx:`1`,key:`16une8`}],[`rect`,{width:`7`,height:`9`,x:`14`,y:`12`,rx:`1`,key:`1hutg5`}],[`rect`,{width:`7`,height:`5`,x:`3`,y:`16`,rx:`1`,key:`ldoo1y`}]]};m.node;var h=s(m),g={name:`list-checks`,size:24,node:[[`path`,{d:`M13 5h8`,key:`a7qcls`}],[`path`,{d:`M13 12h8`,key:`h98zly`}],[`path`,{d:`M13 19h8`,key:`c3s6r1`}],[`path`,{d:`m3 17 2 2 4-4`,key:`1jhpwq`}],[`path`,{d:`m3 7 2 2 4-4`,key:`1obspn`}]]};g.node;var _=s(g),v={name:`receipt`,size:24,node:[[`path`,{d:`M12 17V7`,key:`pyj7ub`}],[`path`,{d:`M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8`,key:`1elt7d`}],[`path`,{d:`M4 3a1 1 0 0 1 1-1 1.3 1.3 0 0 1 .7.2l.933.6a1.3 1.3 0 0 0 1.4 0l.934-.6a1.3 1.3 0 0 1 1.4 0l.933.6a1.3 1.3 0 0 0 1.4 0l.933-.6a1.3 1.3 0 0 1 1.4 0l.934.6a1.3 1.3 0 0 0 1.4 0l.933-.6A1.3 1.3 0 0 1 19 2a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1 1.3 1.3 0 0 1-.7-.2l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.934.6a1.3 1.3 0 0 1-1.4 0l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-1.4 0l-.934-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-.7.2 1 1 0 0 1-1-1z`,key:`ycz6yz`}]]};v.node;var y=s(v),b={name:`route`,size:24,node:[[`circle`,{cx:`6`,cy:`19`,r:`3`,key:`1kj8tv`}],[`path`,{d:`M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15`,key:`1d8sl`}],[`circle`,{cx:`18`,cy:`5`,r:`3`,key:`gq8acd`}]]};b.node;var x=s(b),S=a(`{
  "updated": "2026-10-23",
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
    }
  ]
}
`),C=e(n(),1),w=r();function T({title:e,copy:n,children:r,className:a}){let[s,c]=(0,C.useState)(!1);return(0,w.jsxs)(`div`,{className:t(`overflow-hidden rounded-2xl border border-line bg-bg-sunken`,a),children:[(0,w.jsxs)(`div`,{className:`flex items-center justify-between border-b border-line px-4 py-2`,children:[(0,w.jsx)(`span`,{className:`font-mono text-[12px] text-muted`,children:e}),(0,w.jsxs)(`button`,{type:`button`,onClick:async()=>{try{await navigator.clipboard.writeText(n),c(!0),window.setTimeout(()=>c(!1),1600)}catch{}},className:`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink`,"aria-label":`Copy code`,children:[s?(0,w.jsx)(o,{className:`size-3.5 text-good`}):(0,w.jsx)(i,{className:`size-3.5`}),s?`Copied`:`Copy`]})]}),(0,w.jsx)(`pre`,{className:`overflow-x-auto px-4 py-4 font-mono text-[13px] leading-6 text-ink-2`,children:(0,w.jsx)(`code`,{children:r})})]})}var E={k:e=>(0,w.jsx)(`span`,{className:`text-owned-ink`,children:e}),s:e=>(0,w.jsx)(`span`,{className:`text-good-ink`,children:e}),c:e=>(0,w.jsx)(`span`,{className:`text-muted`,children:e}),h:e=>(0,w.jsx)(`span`,{className:`rounded bg-owned-soft px-1 text-owned-ink`,children:e})};export{y as a,p as c,x as i,d as l,E as n,_ as o,S as r,h as s,T as t,l as u};