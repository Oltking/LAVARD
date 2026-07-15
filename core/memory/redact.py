"""Redact secrets/credentials/PII AT CAPTURE (§4.5) so the memory store is never a leak vector.

Runs before anything is written to Portable Memory. Returns (clean_text, kinds_redacted).
Conservative by design: better to redact a near-miss than to persist a secret.
"""

from __future__ import annotations

import re

# BIP-39 mnemonics are exactly 12/15/18/21/24 words — restrict to those lengths so ordinary
# prose (any long run of short lowercase words) is not falsely scrubbed (audit finding LOW-1).
_BIP39_LENS = {12, 15, 18, 21, 24}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"\b0x[a-fA-F0-9]{64}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),          # AWS access key id
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.I)),
    ("okx_secret", re.compile(r"\bOKX_(?:SECRET|API|PASSPHRASE)[A-Z_]*\s*[:=]\s*\S+", re.I)),
    # a labeled secret with ':', '=', or the word 'is' after it (conservative — a near-miss is
    # better redacted than a persisted secret).
    ("password", re.compile(
        r"\b(?:password|passwd|pwd|secret|passphrase)\s*(?:[:=]|\bis\b)\s*\S+", re.I)),
    ("generic_key", re.compile(
        r"\b[A-Za-z0-9_]*(?:api[_-]?key|token|access[_-]?key|secret[_-]?key)\s*[:=]\s*\S+", re.I)),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]

# Seed phrases are ambiguous with prose, so only fire when the mnemonic is (a) the WHOLE field or
# (b) introduced by a seed/mnemonic/recovery label — and only at a valid BIP-39 word count. This
# catches real leaks without scrubbing a 12-word run that merely happens inside a sentence.
_WORDS = r"(?:[a-z]{3,10}\s+){11,23}[a-z]{3,10}"
_SEED_STANDALONE = re.compile(rf"^\s*({_WORDS})\s*$", re.I | re.M)
_SEED_LABELED = re.compile(
    rf"\b(?:seed|mnemonic|recovery|wallet|private[\s_-]?key)(?:\s+phrase|\s+words)?"
    rf"\s*[:=]?\s*({_WORDS})", re.I)


def _redact_seed(text: str, kinds: list[str]) -> str:
    def _sub(m: re.Match) -> str:
        phrase = m.group(1)
        if len(phrase.split()) not in _BIP39_LENS:
            return m.group(0)
        if "seed_phrase" not in kinds:
            kinds.append("seed_phrase")
        return m.group(0).replace(phrase, "[REDACTED:seed_phrase]")

    text = _SEED_LABELED.sub(_sub, text)
    text = _SEED_STANDALONE.sub(_sub, text)
    return text


def redact(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    out = text or ""
    for kind, pat in _PATTERNS:
        if pat.search(out):
            kinds.append(kind)
            out = pat.sub(f"[REDACTED:{kind}]", out)
    out = _redact_seed(out, kinds)
    return out, kinds
