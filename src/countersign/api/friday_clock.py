"""C62: read-only demo projections use the staged clock, never the build date."""

from datetime import datetime, timedelta
from uuid import UUID


async def project(source, resource, documents):
    if source.runtime.mode != "demo" or not source.runtime.paypal.is_mock or not documents:
        return
    from countersign.api.dashboard import _demo_clock

    now = source.clock() if source.clock else await _demo_clock(source.runtime)
    async with source.pool.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        if resource == "cash":
            row = await (await conn.execute(
                "SELECT coalesce((SELECT (value #>> '{}')::int FROM policies "
                "WHERE key='cash_stale_seconds'),10800) AS seconds"
            )).fetchone()
            for doc in documents:
                paypal = doc["paypal"]
                age = max(0, (now - datetime.fromisoformat(paypal["last_refresh_time"])).total_seconds())
                paypal.update(age_seconds=int(age), stale=age > row["seconds"])
        elif resource == "makers":
            rows = await (await conn.execute(
                "SELECT maker_id,coalesce(sum(amount_minor),0)::bigint AS amount "
                "FROM obligations WHERE maker_id=ANY(%s) AND currency='USD' "
                "AND state='SETTLED' AND updated_at>=%s GROUP BY maker_id",
                ([UUID(d["id"]) for d in documents], now - timedelta(days=30)),
            )).fetchall()
            amounts = {str(row["maker_id"]):row["amount"] for row in rows}
            for doc in documents:
                doc["settled_last_30d"]["minor"] = amounts.get(doc["id"], 0)
