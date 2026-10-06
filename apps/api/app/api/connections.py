import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.security.network import connection_allowed

router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionTest(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    database: str
    username: str
    password: str = Field(repr=False)


def try_connect(*, host: str, port: int, database: str, username: str, password: str) -> tuple[bool, str]:
    """Open and close a connection. The message never echoes credentials."""
    try:
        with psycopg.connect(host=host, port=port, dbname=database, user=username, password=password, connect_timeout=3):
            return True, "ok"
    except psycopg.Error:
        return False, "could not connect with the supplied details"


@router.post("/test")
def test_connection(body: ConnectionTest, request: Request):
    if not connection_allowed(body.host, body.port):
        raise HTTPException(400, detail="This release only connects to QueryDoctor's sample databases.")
    tester = getattr(request.app.state, "connection_tester", try_connect)
    ok, message = tester(
        host=body.host, port=body.port, database=body.database, username=body.username, password=body.password
    )
    return {"ok": ok, "message": message}
