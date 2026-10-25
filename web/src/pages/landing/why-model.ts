/**
 * "Why it is different": six things the code does that a plain router or a distillation API does not, as data. Each
 * names the files that implement it and the invariant in AGENTS.md that states it, so web/tests/why-model.test.mjs
 * can hold the page to the repo: every file exists and every cited invariant says what the card says it says.
 * No JSX here, so the test can import it under Node.
 */

export type WhyId = "licenses" | "bom" | "golden" | "release" | "redaction" | "receipts";

export interface WhyItem {
  id: WhyId;
  title: string;
  body: string;
  /** Repo paths that implement it; each must exist. */
  files: string[];
  /** The invariant's number in AGENTS.md and the words its first sentence must contain. */
  invariant: { n: number; says: string } | null;
  /** Two thirds of a row on desktop. */
  wide: boolean;
}

export const WHY: WhyItem[] = [
  {
    id: "licenses",
    title: "Only teachers whose license allows it",
    body: "A closed model (GPT, Claude, Gemini) stops the gateway at startup. Nemotron 3 Ultra is under OpenMDW-1.1, which places no restrictions on its outputs, so training on its answers is allowed in writing.",
    files: ["ratchet/licenses.py"],
    invariant: { n: 1, says: "Only distillable teachers" },
    wide: true,
  },
  {
    id: "bom",
    title: "A signed bill of materials per adapter",
    body: "Written once, at training time, from the exact rows it trained and graded on: teachers, licenses, dataset hashes, redaction rules and the evaluation it passed. Ed25519-signed; anyone with the public key can check it offline.",
    files: ["ratchet/bom.py", "ratchet/nightschool/notices.py"],
    invariant: { n: 14, says: "A BOM is written once, at training time" },
    wide: false,
  },
  {
    id: "golden",
    title: "Precision measured on held-out data",
    body: "Golden examples are never trained on. A candidate is calibrated on one half of them and measured on the other, and compared with the live version re-scored on the same rows the same night.",
    files: ["ratchet/nightschool/evaluate.py", "ratchet/nightschool/dataset.py"],
    invariant: { n: 6, says: "Precision is measured on held-out data" },
    wide: false,
  },
  {
    id: "release",
    title: "Shadow, canary, automatic rollback",
    body: "A new adapter answers silently first, then serves 10%, 50% and 100% of traffic with teacher audits at every stage. If the upper bound of its audited precision falls below the floor, traffic returns to the last good version on its own. No training while a release is in flight.",
    files: ["ratchet/release.py", "ratchet/stats.py"],
    invariant: { n: 5, says: "No training while a release is in flight" },
    wide: true,
  },
  {
    id: "redaction",
    title: "Redaction before anything is stored",
    body: "Emails, phone numbers, cards, IBANs, Aadhaar, PAN, GSTIN, UPI ids, IPs and API secrets are replaced with placeholders before a request and its teacher answer become a training example. The request log keeps metadata only, never the text.",
    files: ["ratchet/redact.py", "ratchet/privacy.py"],
    invariant: { n: 12, says: "Redact before storing" },
    wide: false,
  },
  {
    id: "receipts",
    title: "Savings you can check, not just read",
    body: "One signed receipt per ledger day and API client, with the savings on a one-sided 95% lower bound from audited teacher bills, chained day to day and verifiable offline. Below break-even the cost report says self-hosting does not pay.",
    files: ["ratchet/receipts.py", "ratchet/receipt.py", "ratchet/cost.py"],
    invariant: { n: 17, says: "Committed leaves and sealed receipts are append-only" },
    wide: false,
  },
];
