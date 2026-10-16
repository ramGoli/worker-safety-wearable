"""psycopg pool for Lakebase. The App's own OAuth token is the Postgres password, minted per new connection."""
import os

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg_pool import ConnectionPool

_w = WorkspaceClient()


class OAuthConnection(psycopg.Connection):
    @classmethod
    def connect(cls, conninfo="", **kwargs):
        kwargs["password"] = _w.config.oauth_token().access_token
        return super().connect(conninfo, **kwargs)


def make_pool():
    conninfo = (f"host={os.environ['PGHOST']} port={os.environ.get('PGPORT', '5432')} "
                f"dbname={os.environ['PGDATABASE']} user={os.environ['PGUSER']} sslmode=require")
    return ConnectionPool(conninfo, connection_class=OAuthConnection, min_size=1, max_size=5,
                          max_lifetime=2700, kwargs={"autocommit": True}, open=True)
