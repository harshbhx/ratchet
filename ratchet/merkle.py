"""Merkle trees for the evidence features (ADR 0024): a sparse tree over a key set, and RFC 9162 log trees. Standard
library only (`hashlib`).

**`KeySet`: a set commitment with membership and non-membership proofs.** A collapsed binary sparse Merkle tree over
32-byte keys, each mapped to a 32-byte value. A node at depth d holds the keys that share their first d bits; an empty
node hashes to `EMPTY` (32 zero bytes), a node holding one key collapses to that key's leaf
(`leaf_hash = sha256(0x00 || key || value)`), and any other node is `node_hash = sha256(0x01 || left || right)`, split
on bit d (most significant bit first). The root therefore depends on the set alone, never on insertion order. A proof
is the list of sibling hashes along the key's path (depth 0 first) plus what the path ends at: a leaf or an empty node.

Why non-membership is sound, even against whoever built the tree: for one root and key, the fold path is fixed by the
key's bits. If an inclusion proof and a non-inclusion proof for the same key both verified, their intermediate hashes
would agree at every depth they share, or there is a `node_hash` collision; so at the shallower terminal a leaf hash
(or `EMPTY`) would equal an internal node hash or a different leaf. That needs a SHA-256 collision across the 0x00 and
0x01 domains (both preimages are 65 bytes) or a preimage of 32 zero bytes. A Merkle tree over a sorted list proves
absence only if the leaves really are sorted, which a verifier cannot check, so it is not used for that.

**RFC 9162 log trees** (`mth`, `inclusion_path`, `verify_inclusion`, `LogTree`): the Certificate Transparency tree over
an ordered list of byte strings (leaf `sha256(0x00 || d)`, node `sha256(0x01 || l || r)`, split at the largest power of
two below n, the empty tree `sha256(b'')`), for append-only records such as savings receipts.

Verifiers take untrusted input (a proof from a JSON file, a root from a BOM): they return 'invalid' or False for
anything malformed and never raise.
"""

from __future__ import annotations

import bisect
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SMT_ALG = "ratchet.smt/v1"  # the `alg` of a KeySet root in a BOM
LOG_ALG = "rfc9162-sha256"  # the `alg` of a LogTree root
EMPTY = bytes(32)
KEY_BITS = 256
INCLUDED, ABSENT, INVALID = "included", "absent", "invalid"


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _as32(value: bytes | str, what: str) -> bytes:
    """32 bytes from bytes or a 64-character hex string; ValueError otherwise."""
    if isinstance(value, str):
        try:
            value = bytes.fromhex(value)
        except ValueError:
            raise ValueError(f"{what} is not hex") from None
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError(f"{what} must be 32 bytes (64 hex characters)")
    return bytes(value)


def leaf_hash(key: bytes, value: bytes) -> bytes:
    """A KeySet leaf: sha256(0x00 || key || value), both exactly 32 bytes."""
    if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError("value must be 32 bytes")
    return _sha256(b"\x00" + bytes(key) + bytes(value))


def node_hash(left: bytes, right: bytes) -> bytes:
    """An inner node of either tree: sha256(0x01 || left || right)."""
    return _sha256(b"\x01" + left + right)


def bit(key: bytes, d: int) -> int:
    """Bit `d` of `key`, most significant bit of the first byte first."""
    return (key[d // 8] >> (7 - d % 8)) & 1


class KeySet:
    """A sparse Merkle tree over (key, value) pairs of 32 bytes each (bytes or hex). Duplicate keys raise ValueError.
    `root` and `size` describe the set; `prove(key)` gives a membership or non-membership proof in O(log n), from the
    node hashes kept while the root was built (one per node that holds two keys or more)."""

    def __init__(self, pairs: Iterable[tuple[bytes | str, bytes | str]]):
        items = sorted((_as32(k, "key"), _as32(v, "value")) for k, v in pairs)
        for i in range(1, len(items)):
            if items[i][0] == items[i - 1][0]:
                raise ValueError(f"duplicate key {items[i][0].hex()}")
        self._keys = [k for k, _ in items]
        self._values = [v for _, v in items]
        self._ints = [int.from_bytes(k, "big") for k in self._keys]
        self._index = {k: i for i, k in enumerate(self._keys)}
        self._memo: dict[int, bytes] = {}  # by _slot(lo, hi, d): one entry per node that holds two keys or more
        self.size = len(items)
        self.root = self._hash(0, self.size, 0)

    def _slot(self, lo: int, hi: int, d: int) -> int:
        """One int per (lo, hi, d), a cheaper dict key than a tuple. d is needed: where every key of a range shares bit
        d, the same range is a node again one level down, with another hash."""
        return (lo * (self.size + 1) + hi) * (KEY_BITS + 1) + d

    def _split(self, lo: int, hi: int, d: int) -> int:
        """The first index in [lo, hi) whose key has bit d set. Every key there shares its first d bits, so in sorted
        order the keys with bit d clear come first."""
        prefix = (self._ints[lo] >> (KEY_BITS - d)) << (KEY_BITS - d) if d else 0
        return bisect.bisect_left(self._ints, prefix | (1 << (KEY_BITS - 1 - d)), lo, hi)

    def _hash(self, lo: int, hi: int, d: int) -> bytes:
        if hi - lo == 0:
            return EMPTY
        if hi - lo == 1:
            return _sha256(b"\x00" + self._keys[lo] + self._values[lo])  # leaf_hash; checked in __init__
        slot = self._slot(lo, hi, d)
        memo = self._memo.get(slot)
        if memo is None:
            mid = self._split(lo, hi, d)
            memo = _sha256(b"\x01" + self._hash(lo, mid, d + 1) + self._hash(mid, hi, d + 1))  # node_hash
            self._memo[slot] = memo
        return memo

    @property
    def root_hex(self) -> str:
        return self.root.hex()

    def value(self, key: bytes | str) -> bytes | None:
        """The value stored under `key`, or None when it is not in the set."""
        i = self._index.get(_as32(key, "key"))
        return None if i is None else self._values[i]

    def __len__(self) -> int:
        return self.size

    def __contains__(self, key: object) -> bool:
        try:
            return self.value(key) is not None  # type: ignore[arg-type]
        except ValueError:
            return False

    def prove(self, key: bytes | str) -> dict[str, Any]:
        """{'siblings': [hex, ...] (depth 0 first), 'terminal': {'leaf': {'key': hex, 'value': hex}} | {'empty': True}}:
        walk from the root along the key's bits, recording the other half's hash wherever the current node holds two
        keys or more, and stop at a node with one key (a leaf: the key itself when it is a member) or none."""
        k = _as32(key, "key")
        lo, hi, d = 0, self.size, 0
        siblings = []
        while hi - lo >= 2:
            mid = self._split(lo, hi, d)
            if bit(k, d):
                siblings.append(self._hash(lo, mid, d + 1))
                lo = mid
            else:
                siblings.append(self._hash(mid, hi, d + 1))
                hi = mid
            d += 1
        if hi - lo == 1:
            terminal: dict[str, Any] = {"leaf": {"key": self._keys[lo].hex(), "value": self._values[lo].hex()}}
        else:
            terminal = {"empty": True}
        return {"siblings": [s.hex() for s in siblings], "terminal": terminal}


def verify(root: bytes | str, key: bytes | str, proof: Mapping[str, Any]) -> str:
    """'included', 'absent' or 'invalid': fold the proof's terminal (its leaf hash, or EMPTY) with the siblings from
    the deepest up, on the side `key`'s bits say, and compare with `root`. At most 256 siblings. 'included' only when
    the terminal leaf is `key` itself; a terminal leaf for another key must share `key`'s first len(siblings) bits (it
    is where `key` would be), else the proof is 'invalid'. Malformed input of any kind is 'invalid', never an error."""
    try:
        r, k = _as32(root, "root"), _as32(key, "key")
        siblings = [_as32(s, "sibling") for s in proof["siblings"]]
        if len(siblings) > KEY_BITS:
            return INVALID
        terminal = proof["terminal"]
        leaf = terminal.get("leaf") if isinstance(terminal, Mapping) else None
        if leaf is not None:
            lk, lv = _as32(leaf["key"], "leaf key"), _as32(leaf["value"], "leaf value")
            x = leaf_hash(lk, lv)
            if lk != k and any(bit(lk, d) != bit(k, d) for d in range(len(siblings))):
                return INVALID
        elif isinstance(terminal, Mapping) and terminal.get("empty") is True:
            lk, x = None, EMPTY
        else:
            return INVALID
    except (KeyError, TypeError, ValueError, AttributeError):
        return INVALID
    for d in range(len(siblings) - 1, -1, -1):
        x = node_hash(siblings[d], x) if bit(k, d) else node_hash(x, siblings[d])
    if x != r:
        return INVALID
    return INCLUDED if lk == k else ABSENT


# ---------------------------------------------------------------------------------------------------------------------
# RFC 9162 log trees
# ---------------------------------------------------------------------------------------------------------------------
def _log_leaf(data: bytes) -> bytes:
    return _sha256(b"\x00" + data)


def _largest_power_of_two_below(n: int) -> int:
    return 1 << ((n - 1).bit_length() - 1)


class LogTree:
    """The RFC 9162 Merkle tree over `leaves` (byte strings, in order). `root` is the Merkle Tree Hash; `path(index)`
    is the audit path of one leaf (RFC 9162 section 2.1.3.1), O(log n) from the subtree hashes kept while the root was
    built."""

    def __init__(self, leaves: Sequence[bytes]):
        for i, d in enumerate(leaves):
            if not isinstance(d, (bytes, bytearray)):
                raise ValueError(f"leaf {i} must be bytes, not {type(d).__name__}")
        self._hashes = [_log_leaf(bytes(d)) for d in leaves]
        self._memo: dict[tuple[int, int], bytes] = {}
        self.size = len(self._hashes)
        self.root = self._mth(0, self.size) if self.size else _sha256(b"")

    def _mth(self, lo: int, hi: int) -> bytes:
        if hi - lo == 1:
            return self._hashes[lo]
        memo = self._memo.get((lo, hi))
        if memo is None:
            k = _largest_power_of_two_below(hi - lo)
            memo = node_hash(self._mth(lo, lo + k), self._mth(lo + k, hi))
            self._memo[(lo, hi)] = memo
        return memo

    def path(self, index: int) -> list[bytes]:
        """The audit path of leaf `index`, deepest sibling first; ValueError outside 0..size-1."""
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.size:
            raise ValueError(f"leaf index must be from 0 to {self.size - 1}, not {index!r}")
        out: list[bytes] = []
        lo, hi, m = 0, self.size, index
        while hi - lo > 1:
            k = _largest_power_of_two_below(hi - lo)
            if m < k:
                out.append(self._mth(lo + k, hi))
                hi = lo + k
            else:
                out.append(self._mth(lo, lo + k))
                lo, m = lo + k, m - k
        return out[::-1]


def mth(leaves: Sequence[bytes]) -> bytes:
    """The RFC 9162 Merkle Tree Hash of `leaves` (byte strings, in order); sha256(b'') for none."""
    return LogTree(leaves).root


def inclusion_path(leaves: Sequence[bytes], index: int) -> list[bytes]:
    """The RFC 9162 inclusion proof (audit path) of `leaves[index]`."""
    return LogTree(leaves).path(index)


def verify_inclusion(leaf_data: bytes, index: int, size: int, path: Sequence[bytes | str], root: bytes | str) -> bool:
    """RFC 9162 section 2.1.3.2: whether `path` proves that `leaf_data` is leaf `index` of the tree of `size` leaves
    whose root is `root`. Path entries and the root may be bytes or hex. Malformed input is False, never an error.
    Take `size` from the same signed record as `root`: the size only shapes the right edge of the tree, so for some
    leaves a path also verifies against the same root under a nearby size (leaf 0 of 3 leaves, as of 4)."""
    try:
        if not isinstance(leaf_data, (bytes, bytearray)):
            return False
        for v in (index, size):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                return False
        if index >= size:
            return False
        want = _as32(root, "root")
        nodes = [_as32(p, "path entry") for p in path]
    except (TypeError, ValueError):
        return False
    fn, sn, r = index, size - 1, _log_leaf(bytes(leaf_data))
    for p in nodes:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == want
