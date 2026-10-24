"""BFCL exam scorer (single call). Rules are listed in README.md. Pure functions, stdlib only."""
import re


def std(s):
    """BFCL's string normalisation: drop space , . / - _ * ^, lowercase, apostrophe -> double quote."""
    return re.sub(r"[ ,./\-_*^]", "", s).lower().replace("'", '"')


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def eq(ans, acc):
    """ans equals one concrete acceptable value acc (acc may be a dict of acceptable-lists, or a list)."""
    if isinstance(acc, dict):
        if not isinstance(ans, dict):
            return False
        if set(ans) - set(acc):
            return False
        for k, alts in acc.items():
            alts = alts if isinstance(alts, list) else [alts]
            if k not in ans:
                if "" in alts:
                    continue
                return False
            if not any_match(ans[k], alts):
                return False
        return True
    if isinstance(acc, list):
        if not isinstance(ans, list) or len(ans) != len(acc):
            return False
        return all(eq(x, y) for x, y in zip(ans, acc))
    if isinstance(acc, bool):
        return isinstance(ans, bool) and ans == acc
    if _num(acc):
        return _num(ans) and ans == acc
    if isinstance(acc, str):
        return isinstance(ans, str) and std(ans) == std(acc)
    return ans == acc


def any_match(ans, alts):
    return any(eq(ans, a) for a in alts if not (isinstance(a, str) and a == ""))


def type_ok(t, v):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t in ("float", "number"):
        return _num(v)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t in ("array", "tuple"):
        return isinstance(v, list)
    if t in ("dict", "object"):
        return isinstance(v, dict)
    return True  # any / unknown


def score(exp, ans):
    """exp: row['expected'] {name, ground_truth, required, types}; ans: parsed model JSON. -> (bool, verdict)"""
    if not isinstance(ans, dict) or not isinstance(ans.get("name"), str) or not isinstance(ans.get("arguments"), dict):
        return False, "bad_shape"
    if exp["name"] == "none":
        return (True, "ok") if ans["name"] == "none" and not ans["arguments"] else (False, "none_missed")
    if ans["name"] == "none":
        return False, "none_wrongly_answered"
    if ans["name"] != exp["name"]:
        return False, "wrong_tool"
    gt, args = exp["ground_truth"], ans["arguments"]
    for p in exp["required"]:
        if p not in args:
            return False, "missing_required"
    for p in args:
        if p not in gt:
            return False, "unexpected_argument"
    for p, alts in gt.items():
        if p not in args:
            if "" in alts:
                continue
            return False, "missing_argument"
        if args[p] is None and None in alts:
            continue  # null listed as acceptable: skips the type check
        if not type_ok(exp["types"].get(p), args[p]):
            return False, "wrong_type"
        if not any_match(args[p], alts):
            return False, "wrong_value"
    return True, "ok"
