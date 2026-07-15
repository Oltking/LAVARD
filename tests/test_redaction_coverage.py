"""Audit pass 4: redaction must cover the secret shapes that protect Portable Memory."""

import pytest

from core.memory.redact import redact

SECRETS = {
    "openai": ("call with sk-proj-abcd1234EFGH5678ijkl9012", "api_key"),
    "eth_privkey": ("key " + "0x" + "0123456789abcdef" * 4, "private_key"),
    "aws_key": ("creds AKIAIOSFODNN7EXAMPLE here", "aws_key"),
    "aws_temp": ("creds ASIAIOSFODNN7EXAMPLE here", "aws_key"),
    "email": ("reach alice.secret@example.com now", "email"),
    "bearer": ("Authorization: Bearer eyJhbGciOiJI.abc.def", "bearer"),
    "seed_labeled": ("mnemonic: legal winner thank year wave sausage worth useful legal winner thank yellow",
                     "seed_phrase"),
    "seed_wallet": ("wallet: legal winner thank year wave sausage worth useful legal winner thank yellow",
                    "seed_phrase"),
    "password_colon": ("db password: Hunter2Pass!", "password"),
    "password_is": ("the db password is Hunter2Pass!", "password"),
    "okx": ("OKX_SECRET_KEY=abcdef123456", "okx_secret"),
}


@pytest.mark.parametrize("name", list(SECRETS))
def test_secret_shape_is_redacted(name):
    text, kind = SECRETS[name]
    clean, kinds = redact(text)
    assert clean != text, f"{name} left unredacted: {clean}"
    assert kind in kinds


def test_normal_prose_is_not_scrubbed():
    prose = "please research the rollup market and write a short investor brief about it"
    clean, kinds = redact(prose)
    assert clean == prose and kinds == []      # no false positives on ordinary goals


def test_twelve_word_prose_run_is_not_a_false_seed():
    # 12 ordinary short words inside a sentence must NOT be scrubbed as a mnemonic
    prose = "we will plan and then build and test and ship the small new app today"
    clean, kinds = redact(prose)
    assert "seed_phrase" not in kinds
