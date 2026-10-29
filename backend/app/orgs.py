"""Organizations and Members (GLOSSARY). `GET /me` and `POST /orgs` are the only routes a signed-in person
without an Organization may call; the Admin calls `POST /orgs` right after sign-up (or on first sign-in when
email confirmation came in between), and the caller becomes the new Organization's Admin.

One Organization per person for now: switching between several is #14."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import store
from app.auth import SignedIn


@dataclass(frozen=True)
class Membership:
    org_id: UUID
    org_name: str
    role: str


class AlreadyMember(Exception):
    pass


_MEMBERSHIPS = ("select m.org_id, o.name, m.role from members m join orgs o on o.id = m.org_id "
                "where m.user_id = %s order by m.created_at, m.org_id")


def memberships(user_id: UUID) -> list[Membership]:
    """This person's memberships, read as the API role: RLS shows only rows carrying their own user id."""
    with store.tenant(None, user_id=user_id) as c:
        return [Membership(*r) for r in c.execute(_MEMBERSHIPS, (user_id,)).fetchall()]


def membership(user_id: UUID) -> Membership | None:
    """The Organization this person acts for: their only (or oldest) one until #14 adds switching."""
    with store.tenant(None, user_id=user_id) as c:
        row = c.execute(_MEMBERSHIPS + " limit 1", (user_id,)).fetchone()
    return Membership(*row) if row else None


def create_org(user_id: UUID, name: str) -> Membership:
    """Create an Organization with `user_id` as its Admin, in one transaction. A per-person advisory lock makes
    a double-submitted sign-up create one Organization, not two. Runs as the connecting role (`store.system`):
    the new Organization has no Members yet, so there is no Organization for a policy to scope the writes to."""
    with store.system() as c:
        c.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"create-org:{user_id}",))
        if c.execute("select 1 from members where user_id = %s", (user_id,)).fetchone():
            raise AlreadyMember
        (org_id,) = c.execute("insert into orgs (name, created_by) values (%s, %s) returning id",
                              (name, user_id)).fetchone()
        c.execute("insert into members (org_id, user_id, role) values (%s, %s, 'admin')", (org_id, user_id))
    return Membership(org_id, name, "admin")


# ---------------------------------------------------------------- routes

class OrgOut(BaseModel):
    id: UUID
    name: str
    role: str


class UserOut(BaseModel):
    id: UUID
    email: str


class MeOut(BaseModel):
    user: UserOut
    org: OrgOut | None = Field(description="The Organization this request acts for; null until one is created")
    orgs: list[OrgOut]


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not (v := " ".join(v.split())):
            raise ValueError("name must not be blank")
        return v


router = APIRouter()


@router.get("/me", response_model=MeOut)
def me(ctx: SignedIn) -> MeOut:
    orgs = [OrgOut(id=m.org_id, name=m.org_name, role=m.role) for m in memberships(ctx.user.id)]
    current = next((o for o in orgs if o.id == ctx.org_id), None)
    return MeOut(user=UserOut(id=ctx.user.id, email=ctx.user.email), org=current, orgs=orgs)


@router.post("/orgs", response_model=OrgOut, status_code=201)
def create(req: CreateOrgRequest, ctx: SignedIn) -> OrgOut:
    try:
        m = create_org(ctx.user.id, req.name)
    except AlreadyMember:
        raise HTTPException(409, "you already belong to an Organization") from None
    return OrgOut(id=m.org_id, name=m.org_name, role=m.role)
