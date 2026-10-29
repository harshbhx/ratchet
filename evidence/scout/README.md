# Recorded live Scout runs

Three real Scout runs. Two were recorded on 23 Oct 2026 (UTC, 23:13 and 23:14) through a gateway in live mode on one laptop; the third on 24 Oct 2026 (11:56 UTC) through the deployed live gateway at `live.ratchet.dualmindlab.tech`, which shows the live gateway's own Tavily and Token Factory keys work. The
gateway no longer serves them (the `/scout/recorded` routes were removed); they stay here as evidence. See [ADR 0140](../../docs/adr/0140-recorded-scout-run.md).

| Question | Time | Model calls | Cost | Searches | Recorded through |
|---|---|---|---|---|---|
| What is LoRA fine-tuning and why is it cheaper than full fine-tuning? | 16.2 s | 20 | $0.0277 | 3 (Tavily) | a gateway on one laptop |
| What is model distillation and when does it fail? | 13.2 s | 18 | $0.0223 | 3 (Tavily) | a gateway on one laptop |
| What is retrieval-augmented generation and when does it help? | 14.8 s | 19 | $0.0225 | 3 (Tavily) | the deployed live gateway |

## What is real, and what is not

- **Real:** every model call went to Nemotron 3 Ultra on Nebius Token Factory, and every search went to Tavily. The cost is the gateway's
  own count of the tokens used at list prices. The answers and the sources they cite are what came back.
- **Not a distillation result:** all 57 model calls were answered by the teacher. There was no trained student in this gateway, so
  `student_calls` is 0 and the savings are 0%. These runs show Scout working end to end on live services. They do not show savings; the
  trained adapter's measured scores are in `evidence/text2sql-round2/` and `evidence/text2sql-exam2/`.
- **Host name:** the files hold `127.0.0.1` (the laptop) or `live.ratchet.dualmindlab.tech`. No key, header or cookie is in the files (INV-13).

## Record another

```bash
python -c "import sys; from ratchet.scout.__main__ import main; sys.exit(main(['record', 'Your question', '--base-url', 'http://127.0.0.1:8000']))"
```

The shorter form works too: `python -m ratchet.scout record "Your question" --base-url http://127.0.0.1:8000`.
