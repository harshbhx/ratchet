"""The judges' console (web/src/pages/judges/) claims only what the repository backs.

"What's real" (web/src/data/real.json) is one data file for every badge and the judges' table: each entry needs evidence
and a way to check it that exists here, a measured entry needs every number, and a pending one has none (invariant 21,
as test_story_trust_claims_match_code holds the story's chapter to the code). The API surface it prints
(web/src/data/api-surface.json) names served routes at the access level ratchet/access.py gives them, and its route
words cover every route the router records.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ratchet import access, router

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"
REAL = WEB / "data" / "real.json"
SURFACE = WEB / "data" / "api-surface.json"
REPO_BLOB = "https://github.com/harshbhx/ratchet/blob/main/"


def _statuses() -> list[str]:
    src = (WEB / "lib" / "real-model.ts").read_text(encoding="utf-8")
    m = re.search(r"^export const REAL_STATUSES = \[(.+)\] as const;$", src, re.M)
    assert m, "real-model.ts lost its one-line REAL_STATUSES"
    return re.findall(r'"([^"]+)"', m.group(1))


def _make_targets() -> set[str]:
    return set(re.findall(r"^([a-z][\w-]*):", (ROOT / "Makefile").read_text(encoding="utf-8"), re.M))


def _command_exists(cmd: str) -> bool:
    """A printed command names something in this repository: a make target, a module, a script or a test file."""
    parts = cmd.split()
    if parts[0] == "make":
        return parts[1] in _make_targets()
    if parts[:2] == ["python", "-m"] and parts[2] == "pytest":
        return all((ROOT / p).exists() for p in parts[3:] if p.startswith("tests/"))
    if parts[:2] == ["python", "-m"]:
        mod = ROOT.joinpath(*parts[2].split("."))
        return mod.with_suffix(".py").is_file() or (mod / "__main__.py").is_file()
    if parts[0] == "python":
        return (ROOT / parts[1]).is_file()
    return False


@pytest.mark.req("INV-21")
def test_whats_real_entries_have_evidence_and_a_way_to_check():
    data = json.loads(REAL.read_text(encoding="utf-8"))
    statuses = _statuses()
    assert statuses == ["real", "measured", "pending", "not-run"]
    for key in ("repo", "docs"):
        assert data["links"][key].startswith("https://"), key
    assert "video" in data["links"]  # the slot for the video, null until it is published

    ids = [e["id"] for e in data["entries"]]
    assert len(ids) == len(set(ids)) and ids
    for e in data["entries"]:
        where = e["id"]
        assert e["status"] in statuses, where
        assert e["claim"].strip() and len(e["evidence"].strip()) > 20, where
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]), where
        check = e["check"]
        assert check["label"].strip() and (check.get("command") or check.get("href")), where
        if check.get("command"):
            assert _command_exists(check["command"]), (where, check["command"])
        if check.get("href", "").startswith(REPO_BLOB):
            assert (ROOT / check["href"][len(REPO_BLOB) :]).is_file(), (where, check["href"])
        assert e["paths"] and [p for p in e["paths"] if not (ROOT / p).exists()] == [], where
        values = [m["value"] for m in e["metrics"]]
        if e["status"] == "measured":
            assert values and all(v not in (None, "") for v in values), f"{where}: a measured entry needs every value"
        else:
            assert all(v is None for v in values), f"{where}: only a measured entry carries numbers"
        for f in e["facts"]:
            assert f["label"].strip() and f["value"].strip(), where


@pytest.mark.req("INV-21")
def test_whats_real_keeps_the_seeded_facts():
    """The statuses the page was published with: no claim moves to real or measured without its evidence."""
    by_id = {e["id"]: e for e in json.loads(REAL.read_text(encoding="utf-8"))["entries"]}
    assert by_id["teacher-labels"]["status"] == "real" and by_id["adapter-training"]["status"] == "real"
    assert "borrowed GPU" in by_id["adapter-training"]["evidence"]
    assert by_id["exam-result"]["status"] in ("pending", "measured")
    assert by_id["gateway-live"]["status"] == "not-run" and by_id["scout-tavily"]["status"] == "real"
    assert "demo-numbers" not in by_id  # the simulation entry is gone: the site shows only real and live things

    def strings(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from strings(v, k)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v, key)
        elif isinstance(node, str) and key not in ("id", "entry", "path", "href"):
            yield node

    # What the judges page prints from its data files says nothing about a simulation or a demo (ids may keep `live-demo`).
    for f in (REAL, SURFACE):
        for text in strings(json.loads(f.read_text(encoding="utf-8"))):
            assert not re.search(r"simulat|\bdemo\b", text, re.I), (f.name, text[:80])


@pytest.mark.req("INV-21")
def test_judges_api_surface_matches_the_gateway(gw):
    surface = json.loads(SURFACE.read_text(encoding="utf-8"))
    assert sorted(lv["level"] for lv in surface["levels"]) == sorted(access.LEVELS)
    served = {(m, re.sub(r"\{[^}]+\}", "{}", r.path)) for r in gw.app.routes if isinstance(r, APIRoute) for m in r.methods}
    templates = {
        (m, re.sub(r"\{[^}]+\}", "{}", r.path)): r.path for r in gw.app.routes if isinstance(r, APIRoute) for m in r.methods
    }
    for group in surface["groups"]:
        for row in group["routes"]:
            key = (row["method"], re.sub(r"\{[^}]+\}", "{}", row["path"]))
            assert key in served, row
            assert access.route_access(row["method"], templates[key]) == row["level"], row
    assert json.loads(surface["error_example"].replace("req_…", "req_x"))["error"]["code"] == "invalid_value"


def test_judges_route_words_cover_every_route():
    src = (WEB / "pages" / "judges" / "judges-model.ts").read_text(encoding="utf-8")
    block = re.search(r"export const ROUTE_WORDS: Record<string, string> = \{(.*?)\n\};", src, re.S)
    assert block
    named = set(re.findall(r"^\s+(\w+): \"", block.group(1), re.M))
    assert set(router.ROUTES) | {router.PASSTHROUGH_ROUTE} <= named


def test_judges_page_is_served_and_public(gw):
    r = TestClient(gw.app).get("/judges")
    assert r.status_code == 200 and "<" in r.text
    assert access.route_access("GET", "/judges") == access.PUBLIC
