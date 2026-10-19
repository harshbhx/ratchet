# 3-minute demo video script

Path through the product: landing page → Scout run → dashboard. Total running time 2:55.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Landing page (`/`), hero | "Every AI startup rents its intelligence. Ratchet lets you own it, legally." |
| 0:12 | Landing page: the one-line `base_url` change | "One line. Your app now talks to Ratchet instead of the model. A small model you own answers first; Nemotron 3 Ultra on Nebius Token Factory answers when it is unsure." |
| 0:27 | Click **Try the Scout demo**. `/scout`, pick "What is LoRA fine-tuning?" | "This is Scout, a team of five research agents. Every call they make goes through Ratchet." |
| 0:37 | Planner lane fills ("Pinned to Ultra"); searcher lane shows results ("Simulated search results" in mock mode) | "The planner writes search queries. Open-ended work like this is pinned to Ultra and never trained on. The searcher uses Tavily." |
| 0:52 | Triage and reader cards fill in parallel, most with the "Nano · yours" badge, one with "Ultra" + "escalated" | "Triage and reading are many short, repetitive calls. Ratchet learned them from Ultra's answers, so our own Nemotron Nano answers them. When it is unsure, like this one, it escalates." |
| 1:12 | Writer lane: answer with citations. Totals row and savings bar | "The writer is pinned to Ultra too. For the whole run: most calls answered by our model, and the run cost a fraction of all-Ultra." (Simulation label visible on screen.) |
| 1:27 | Click **Dashboard**. **By task** table | "The dashboard shows it per task: triage and reading moved to our model, planning and writing stay on Ultra by design." |
| 1:45 | **Model versions** table and release log: shadow → canary 10% → 50% → live | "Every night, a Nebius Serverless Job fine-tunes a new student and grades it on a held-out golden set. It then earns traffic step by step: shadow, 10%, 50%, 100%." |
| 2:05 | Click **Break next deploy**, then **Run 1 day + night**; rollback event appears | "What if a bad model ships? Ultra audits a sample of every answer. Ratchet sees precision collapse and rolls back automatically." |
| 2:28 | Provenance panel and `/ratchet/bom` | "Every training example is traceable to an open, license-clean teacher. Distilling GPT or Claude breaks their terms. This doesn't." |
| 2:42 | Numbers slide (live run) | "Real numbers from our live run: [cost cut], [precision], [latency]." |
| 2:50 | "Rent today. Own tomorrow." | |
| 2:55 | End | |

## Before recording

- Record the mock demo with `RATCHET_MOCK_WARMUP_DAYS=4`, so Scout opens on a trained student and the release log already has history. Keep the "Simulation" and "Simulated search results" labels in frame.
- The rollback at 2:05 can take 2–3 clicks of **Run 1 day + night** (the corrupted version must be trained and reach canary first). Record the clicks and cut the waiting in the edit. Use the **Traffic** selector's default (Tickets + Scout).
- Do a full Reset before the take, then wait 30 seconds: a second Reset within 30 seconds is refused.
- Replace the 2:42 numbers with the live-mode run before recording. Keep the simulation clearly labelled as a simulation.
