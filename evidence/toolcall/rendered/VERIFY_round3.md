## Tool-calling round 3

Every claim below recomputes from files in `evidence/toolcall/`. Nothing here needs a GPU, a key or a network connection.

| Claim | One command |
|---|---|
| The scores, ranges and wording verdicts follow from the saved answers, and every saved verdict follows from its raw answer | `python scripts/toolcall_results.py --check` |
| The hard exam is the frozen one | `python -c "import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_hard.jsonl','rb').read()).hexdigest())"` prints `e26b6d020caedf2ce050b48641d5c2a55a96202826f51ca66ad81be9d04dada2` |
| The BFCL exam is the frozen one | `python -c "import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_bfcl.jsonl','rb').read()).hexdigest())"` prints `c6b2e9dd3abd6bb2a5e7b33105e0de372612be51e82e2117087e6c36faeccb37` |
| The copied evidence files are unchanged | `python scripts/toolcall_results.py --check` (compares each file's SHA-256 with `evidence/toolcall/MANIFEST.json`) |
| The drafts and the page data are the ones the numbers give | `python scripts/toolcall_results.py --render --web --check-render` |

**It shows:** the published numbers are the saved answers' numbers, under the exam's own strict scorer. **It does not show:** that the adapter produced those answers on the Modal T4 as described: that rests on the training and generation logs kept with the run. A 95% range on 150 or 300 questions is wide, which is why the wording says 'about the same' only when the ranges overlap and the gap is under 5 points, and 'lower' or 'higher' with a chance caveat when the ranges overlap and the gap is larger.
