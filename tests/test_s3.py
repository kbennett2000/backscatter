"""S3 client config — the connection-pool sizing guard.

The chunks active-dir scan fans out across ``chunks._DIR_SCAN_WORKERS`` threads
sharing the one client; if the urllib3 pool is smaller than that, connections are
discarded+reopened every scan and the live path starves (botocore's default pool
is 10). These assert the pool covers that concurrency and is actually wired into
the built client (not silently back to the default).
"""

from __future__ import annotations

from backscatter.ingest import chunks, s3


def test_pool_covers_dir_scan_concurrency() -> None:
    """The pool must never drop below the active-dir scan's worker count."""
    assert s3.MAX_POOL_CONNECTIONS >= chunks._DIR_SCAN_WORKERS


def test_make_client_uses_sized_pool() -> None:
    """A freshly built client carries the sized pool, not botocore's default 10."""
    client = s3.make_client()
    assert client.meta.config.max_pool_connections == s3.MAX_POOL_CONNECTIONS
