/**
 * What the story's "Built to be checked" chapter claims, as data: the files it names, the redaction labels, the release
 * gate's numbers and the commands it prints. tests/test_contract.py::test_story_trust_claims_match_code checks each one
 * against the code (files exist, labels are `ratchet/redact.py` PATTERNS, numbers are `ReleasePolicy`'s defaults, the BOM
 * verifier prints these lines, CI runs the traceability check), so the page cannot claim more than the code does.
 * Keep each constant the test reads (the `TRUST_*`, `BOM_*`, `REQTRACE_CHECK` and `GATE_TESTS` lines) on one line.
 */

export const TRUST_FILES = ["ratchet/redact.py", "ratchet/bom.py", "scripts/reqtrace.py", "docs/REQUIREMENTS.md", ".github/workflows/ci.yml", "ratchet/release.py", "ratchet/stats.py", "ratchet/merkle.py", "tests/test_release.py"];

/** Every redaction rule, in `redact.PATTERNS` order. */
export const TRUST_REDACTS = ["SECRET", "EMAIL", "IBAN", "CARD", "AADHAAR", "SSN", "PAN", "IP", "PHONE"];

/** `ReleasePolicy` defaults the chapter prints. */
export const TRUST_GATE = { "min_precision": 0.95, "tolerance": 0.02, "min_audits": 20, "live_floor": 0.92 };

/** The BOM check: fetch a copy from anywhere, then verify it offline against the key the operator published. */
export const BOM_FETCH = "curl -s localhost:8000/ratchet/versions/1/bom > bom.json";
export const BOM_VERIFY = "python -m ratchet.bom verify bom.json --public-key <published key>";
export const BOM_OK = "OK: signature valid (Ed25519, key <key id>, trusted key)";
export const BOM_TAMPERED = "FAIL: sha256 does not match the manifest (edited after it was written)";

export const REQTRACE_CHECK = "python scripts/reqtrace.py --check";
export const GATE_TESTS = "python -m pytest -q tests/test_release.py";

/** Plain-English names for the redaction labels, in the order above. */
export const REDACT_WORDS: Record<string, string> = {
  SECRET: "API secrets",
  EMAIL: "emails",
  IBAN: "IBANs",
  CARD: "card numbers",
  AADHAAR: "Aadhaar numbers",
  SSN: "US Social Security numbers",
  PAN: "Indian PAN numbers",
  IP: "IPv4 addresses",
  PHONE: "phone numbers",
};

/** "emails, phone numbers and IP addresses": a list of labels in words, in their order. */
export function listWords(labels: readonly string[], words: Record<string, string> = REDACT_WORDS): string {
  const w = labels.map((l) => words[l] ?? l.toLowerCase());
  if (w.length <= 1) return w.join("");
  return `${w.slice(0, -1).join(", ")} and ${w[w.length - 1]}`;
}

/** A gateway event's detail for the "latest rollback here" line: one line, at most `max` characters. */
export function clip(text: string, max = 160): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).trimEnd()}…`;
}
