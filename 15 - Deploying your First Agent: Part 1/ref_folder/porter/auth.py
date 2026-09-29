"""Who is calling — decided by a signed token, never by a field the caller typed.

The first version of the contract took `customer_id` in the request body. That is a
service where anybody can be anybody: change one number and you are reading someone else's
orders, and continuing someone else's conversation. The fix is not a check on the number;
it is to stop asking the caller who they are.

    a customer    signs in somewhere else (the shop's login page) and arrives with a token
                  whose subject is their customer id. The service checks the signature and
                  believes nothing else.
    a colleague   has a staff token. Staff approve refunds; they do not chat as customers.

Tokens are HS256 JWTs signed with `PORTER_AUTH_SECRET`. A real shop would verify tokens its
identity provider issued (Cognito, Auth0, its own login service) with a public key instead
of a shared secret — the check in `decode_token` changes, and nothing downstream of it does.

    python -m porter.auth customer 12381      # prints a token, signed with the configured secret
    python -m porter.auth staff alice
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from porter.settings import Settings, get_settings

ALGORITHM = "HS256"


@dataclass(frozen=True)
class Identity:
    subject: str                   # "customer:12381" or "staff:alice"
    role: str                      # "customer" or "staff"
    customer_id: float | None = None

    @property
    def namespace(self) -> str:
        """The prefix every conversation this caller owns is stored under."""
        if self.customer_id is not None:
            return f"c{int(self.customer_id)}"
        return self.subject.replace(":", "-")


def issue_token(*, customer_id: float | None = None, staff: str | None = None,
                secret: str | None = None, ttl_s: int | None = None) -> str:
    """Sign a token. In production this is the identity provider's job, not the service's."""
    settings = get_settings()
    now = int(time.time())
    if staff:
        claims = {"sub": f"staff:{staff}", "role": "staff"}
    elif customer_id is not None:
        claims = {"sub": f"customer:{int(customer_id)}", "role": "customer"}
    else:
        raise ValueError("a token is for a customer or for a member of staff")
    claims.update({"iat": now, "exp": now + (ttl_s or settings.token_ttl_s)})
    return jwt.encode(claims, secret or settings.auth_secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> Identity:
    """Check the signature and the expiry, then believe the claims. Raises on anything else."""
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    role = claims.get("role", "customer")
    subject = str(claims["sub"])
    if role == "customer":
        _, _, number = subject.partition(":")
        return Identity(subject=subject, role="customer", customer_id=float(number))
    return Identity(subject=subject, role="staff")


def peek_identity(headers: dict[bytes, bytes], settings: Settings) -> Identity | None:
    """The caller, if the request carries a valid token — for the operations layer, which
    needs a name to rate-limit against before the request has reached a route. It never
    refuses anything; refusing is the route's job."""
    raw = headers.get(b"authorization", b"").decode()
    if not raw.lower().startswith("bearer "):
        return None
    try:
        return decode_token(raw[7:].strip(), settings.auth_secret)
    except jwt.PyJWTError:
        return None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def identify(request: Request, credentials: HTTPAuthorizationCredentials | None,
             claimed_customer: float | None) -> Identity:
    """The customer behind a chat request.

    With `auth_required` off, the service trusts `customer_id` in the body — which is only
    ever acceptable when the caller is another service that has already signed the customer
    in. With it on, the token decides, and a body that names somebody else is refused
    rather than quietly ignored.
    """
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        if claimed_customer is None:
            raise HTTPException(422, "customer_id is required when the service is not checking tokens.")
        identity = Identity(subject=f"customer:{int(claimed_customer)}", role="customer",
                            customer_id=float(claimed_customer))
    else:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _unauthorized("Sign in first: this desk needs a bearer token.")
        try:
            identity = decode_token(credentials.credentials, settings.auth_secret)
        except jwt.ExpiredSignatureError:
            raise _unauthorized("That sign-in has expired.")
        except jwt.PyJWTError:
            raise _unauthorized("That token is not one this desk issued.")
        if identity.role != "customer":
            raise HTTPException(403, "Staff tokens approve refunds; they do not chat as a customer.")
        if claimed_customer is not None and float(claimed_customer) != identity.customer_id:
            raise HTTPException(403, "customer_id does not match the signed-in customer.")

    request.state.identity = identity
    return identity


def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None) -> Identity:
    """Whoever the token says, customer or staff, for a route that only needs to know who is
    asking (GET /v1/me). The signature and the expiry are checked as everywhere else."""
    settings: Settings = request.app.state.settings
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Sign in first: this desk needs a bearer token.")
    try:
        identity = decode_token(credentials.credentials, settings.auth_secret)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("That sign-in has expired.")
    except jwt.PyJWTError:
        raise _unauthorized("That token is not one this desk issued.")
    request.state.identity = identity
    return identity


def require_staff(request: Request, credentials: HTTPAuthorizationCredentials | None) -> Identity:
    """A colleague, or a refusal. Checked on every staff route, whatever `auth_required` says:
    turning customer tokens off for a trusted front end must not turn refunds into a free-for-all."""
    settings: Settings = request.app.state.settings
    if credentials is None:
        raise _unauthorized("Staff routes need a staff token.")
    try:
        identity = decode_token(credentials.credentials, settings.auth_secret)
    except jwt.PyJWTError:
        raise _unauthorized("That token is not one this desk issued.")
    if identity.role != "staff":
        raise HTTPException(403, "Only a member of staff can do that.")
    request.state.identity = identity
    return identity


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("customer", "staff"):
        sys.exit("usage: python -m porter.auth customer 12381 | staff alice")
    if sys.argv[1] == "customer":
        print(issue_token(customer_id=float(sys.argv[2])))
    else:
        print(issue_token(staff=sys.argv[2]))
