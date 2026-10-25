import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import sso  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK["kid"] = "k1"
JWKS = {"keys": [JWK]}
PROV = sso.clean_provider({"id": "kc", "issuer": "https://sso.exemplo.net/realms/noc", "client_id": "bastion", "allowed_domains": ["exemplo.net"],
                           "admin_groups": ["noc-admin"], "viewer_groups": ["noc-view"]})


def tok(**over):
    c = {"iss": "https://sso.exemplo.net/realms/noc", "aud": "bastion", "sub": "u-1", "iat": int(time.time()), "exp": int(time.time()) + 300,
         "nonce": "n1", "email": "Gabriel@Exemplo.net", "email_verified": True, "name": "Gabriel", "groups": ["noc-admin"], **over}
    return jwt.encode(c, KEY, algorithm="RS256", headers={"kid": "k1"})


def test_verify():
    c = sso.verify_id_token(tok(), JWKS, PROV, PROV["issuer"], "n1")
    who = sso.identity(c)
    assert who["email"] == "gabriel@exemplo.net" and who["verified"]
    assert sso.role_from_groups(PROV, sso.groups_of(c, "groups")) == "admin"
    assert sso.domain_ok(PROV, who["email"]) and not sso.domain_ok(PROV, "x@outro.com")
    for bad in (tok(nonce="n2"), tok(aud="outro"), tok(iss="https://mal.exemplo"), tok(exp=int(time.time()) - 600)):
        with pytest.raises(sso.SsoError):
            sso.verify_id_token(bad, JWKS, PROV, PROV["issuer"], "n1")
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(jwt.decode(tok(), options={"verify_signature": False}), other, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(sso.SsoError):
        sso.verify_id_token(forged, JWKS, PROV, PROV["issuer"], "n1")


def test_misc():
    v, ch = sso.pkce()
    assert len(v) >= 43 and len(ch) == 43
    u = sso.auth_url({"authorization_endpoint": "https://sso.exemplo.net/auth"}, PROV, "https://bastion.exemplo.net/api/auth/sso/kc/callback", "s", "n", ch)
    assert "code_challenge_method=S256" in u and "redirect_uri=https%3A%2F%2Fbastion" in u
    assert sso.identity({"preferred_username": "ana@exemplo.net"})["email"] == "ana@exemplo.net"
    with pytest.raises(sso.SsoError):
        sso.clean_provider({"id": "ms", "issuer": "https://login.microsoftonline.com/<TENANT_ID>/v2.0", "client_id": "x"})


def test_modes():
    allc, sel = {"sso_users": "all"}, {"sso_users": "selected", "password_login": "admins"}
    op = {"role": "operator"}
    assert sso.sso_allowed(op, allc) and not sso.sso_allowed(op, sel)
    assert sso.sso_allowed({**op, "login_mode": "both"}, sel) and sso.sso_allowed({**op, "login_mode": "sso"}, sel)
    assert not sso.sso_allowed({**op, "login_mode": "password"}, allc)
    assert sso.password_allowed(op, allc) and not sso.password_allowed(op, sel)
    assert sso.password_allowed({**op, "login_mode": "password"}, sel) and not sso.password_allowed({**op, "login_mode": "sso"}, allc)
    assert sso.password_allowed({"role": "admin"}, sel)
