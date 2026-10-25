### Tool calling (round 3)

A tool-calling adapter on Nemotron 3 Nano 4B (2,940 training rows, about $2.33 in all), scored strictly on two exams it never saw.

| | Hard exam (150) | BFCL exam (300) |
|---|---|---|
| Untrained Nano 4B | 94 of 150 (62.7%, 95% range 54.7 to 70.0%) | 232 of 300 (77.3%, 95% range 72.3 to 81.7%) |
| Teacher (Nemotron 3 Ultra) | 137 of 150 (91.3%, 95% range 85.7 to 94.9%) | 265 of 300 (88.3%, 95% range 84.2 to 91.5%) |
| Tool-calling adapter | 136 of 150 (90.7%, 95% range 84.9 to 94.4%) | 241 of 300 (80.3%, 95% range 75.5 to 84.4%) |

On the hard exam the adapter is above the untrained model: 90.7% against 62.7%, and the 95% ranges do not overlap. It is about the same as the teacher: 90.7% against 91.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart).

On the BFCL exam the adapter is about the same as the untrained model: 80.3% against 77.3% (higher by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). It is lower than the teacher: 80.3% against 88.3% (241/300 against 265/300; the 95% ranges overlap only slightly, so part of the gap could be chance).

Hard exam: question by question against the untrained model, the adapter fixed 42 and broke 0 (exact sign test p < 0.0001). BFCL exam: question by question against the untrained model, the adapter fixed 15 and broke 6 (exact sign test p = 0.0784).

Strict scoring: an answer counts only if it is one JSON object with the right tool and arguments; a reply with text around the JSON is wrong. Free-text arguments are a known weak spot in the training data: the strict scorer cannot agree on free text, so tools such as create_ticket (15 training rows), search_files, send_payment and search_inbox are thin. The hard exam uses 20 tools that were never in training. The adapter was trained on a borrowed GPU, on synthetic data. The hard exam (150 questions, 20 new tools) was written by Claude Sonnet 5.5 for evaluation only and is never training data; the second exam (300 questions) is drawn from the Berkeley Function Calling Leaderboard (Apache-2.0).

Every score recomputes from the saved answers: see [Check it yourself](docs/VERIFY.md#tool-calling-round-3).
