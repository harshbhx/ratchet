# Recorded live Scout runs

Two real Scout runs, recorded on 24 Oct 2026 (UTC 23 Oct, 23:13 and 23:14) through a gateway in live mode on one laptop. The
gateway plays them back read-only at `GET /scout/recorded` when `RATCHET_SCOUT_RECORDINGS_DIR` points here (the Docker image sets it),
so a visitor can watch a real run at its real pace without a model call or a search credit. The Scout page labels them
"Recorded live run". See [ADR 0140](../../docs/adr/0140-recorded-scout-run.md).

| Question | Time | Model calls | Cost | Searches |
|---|---|---|---|---|
| What is LoRA fine-tuning and why is it cheaper than full fine-tuning? | 16.2 s | 20 | $0.0277 | 3 (Tavily) |
| What is model distillation and when does it fail? | 13.2 s | 18 | $0.0223 | 3 (Tavily) |

## What is real, and what is not

- **Real:** every model call went to Nemotron 3 Ultra on Nebius Token Factory, and every search went to Tavily. The cost is the gateway's
  own count of the tokens used at list prices. The answers and the sources they cite are what came back.
- **Not a distillation result:** all 38 model calls were answered by the teacher. There was no trained student in this gateway, so
  `student_calls` is 0 and the savings are 0%. These runs show Scout working end to end on live services. They do not show savings; the
  trained adapter's measured scores are in `evidence/text2sql-round2/` and `evidence/text2sql-exam2/`.
- **Host name:** the file holds `127.0.0.1` (the laptop). No key, header or cookie is in the files (INV-13).

## Record another

```bash
python -c "import sys; from ratchet.scout.__main__ import main; sys.exit(main(['record', 'Your question', '--base-url', 'http://127.0.0.1:8000']))"
```

(`python -m ratchet.scout record ...` does not reach the `record` branch yet: `main` only reads an argument list passed to it.)
