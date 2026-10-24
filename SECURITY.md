# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue. Use [GitHub's private advisory form](https://github.com/harshbhx/ratchet/security/advisories/new) or email **harshbhx@gmail.com** with:

- what you found and how to reproduce it,
- the impact you expect,
- any suggested fix.

You will get an answer within 3 working days. Fixes are released as soon as they are ready, with credit if you want it.

## Scope

In scope: the gateway (`ratchet/`), Night School, deploy scripts, and the dashboard.
Out of scope: Nebius, NVIDIA, or other third-party services themselves (report those to the vendor).

## Handling data

Ratchet logs prompts and teacher answers to build training data. Before running it on real traffic, read [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): redact PII, protect the ledger file, and use Token Factory's zero-retention option where needed.
