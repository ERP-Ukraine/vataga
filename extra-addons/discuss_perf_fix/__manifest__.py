{
    "name": "Discuss Performance Fix",
    "version": "17.0.1.0.0",
    "summary": "Prevent chat presence write storms from blocking HTTP workers",
    "description": """
Discuss chat presence writes ("last seen" / "fetched" markers) hammer the same
discuss_channel_member row from many concurrent clients. Under PostgreSQL
REPEATABLE READ this raises "could not serialize access due to concurrent
update"; Odoo's service.model.retrying() then retries the whole RPC up to 5
times with time.sleep() backoff, blocking the single-threaded prefork HTTP
worker for seconds and starving business requests.

These markers are best-effort: losing a race is harmless because the next
fetched/seen event advances them again. This module wraps the presence writes
in a SAVEPOINT and swallows the serialization failure so it never triggers a
retry storm.
""",
    "author": "ERP Ukraine",
    "website": "https://erp.co.ua",
    "category": "Discuss",
    "license": "LGPL-3",
    "depends": ["mail"],
    "installable": True,
    "auto_install": False,
    "application": False,
}
