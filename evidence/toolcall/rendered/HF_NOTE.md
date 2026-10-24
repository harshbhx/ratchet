## Collection note

Tool calling: a second adapter, trained with the same recipe as the text-to-SQL one. On a 150-question hard exam it scores 90.7% (95% range 84.9 to 94.4%) against 62.7% for the untrained model; on 300 BFCL questions 80.3% (95% range 75.5 to 84.4%) against 77.3%. On the hard exam the adapter is above the untrained model: 90.7% against 62.7%, and the 95% ranges do not overlap. It is about the same as the teacher: 90.7% against 91.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). On the BFCL exam the adapter is about the same as the untrained model: 80.3% against 77.3% (higher by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). It is about the same as the teacher: 80.3% against 88.3% (lower by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart). Strict scoring, rented Modal T4, synthetic data.

## Model card link line

Scores, exams and every saved answer: https://github.com/harshbhx/ratchet/tree/main/evidence/toolcall (recompute with `python scripts/toolcall_results.py --check`).
