"""Tinybird resources, defined with the Python SDK.

stock_checks and storm_updates are the time-series memory.
stock_timeline and depletion_by_item_zip are the published endpoints.
"""

from tinybird_sdk import define_datasource, define_endpoint, define_token, engine, node, p, t

scout_append = define_token("scout_append")
scout_read = define_token("scout_read")

stock_checks = define_datasource(
    "stock_checks",
    {
        "description": "One row per stock check.",
        "schema": {
            "ts_utc": t.date_time64(3),
            "store_id": t.string(),
            "zip": t.string(),
            "essential_key": t.string(),
            "status": t.string().low_cardinality(),
            "price": t.float32(),
            "confidence": t.float32(),
            "level": t.string().low_cardinality(),
            "source_url": t.string(),
        },
        "engine": engine.merge_tree({"sorting_key": ["zip", "essential_key", "ts_utc"]}),
        "tokens": [{"token": scout_append, "scope": "APPEND"}],
    },
)

storm_updates = define_datasource(
    "storm_updates",
    {
        "description": "One row per storm advisory the agent records.",
        "schema": {
            "ts_utc": t.date_time64(3),
            "source_url": t.string(),
            "summary": t.string(),
            "phase_proposed": t.string().low_cardinality(),
            "advisory_json": t.string(),
        },
        "engine": engine.merge_tree({"sorting_key": ["ts_utc"]}),
        "tokens": [{"token": scout_append, "scope": "APPEND"}],
    },
)

# Phase 0 test rows used zip 96720 with fake store ids; keep them out of the numbers.
_REAL = "NOT startsWith(store_id, 'phase0') AND store_id != 'debug-store' AND zip != '00000'"

stock_timeline = define_endpoint(
    "stock_timeline",
    {
        "description": "Counts of in_stock / low / out per 15-minute bucket, for one item in one ZIP.",
        "params": {
            "essential_key": p.string().optional("flashlight"),
            "zip": p.string().optional("96720"),
            "hours": p.int32().optional(12),
        },
        "nodes": [
            node(
                {
                    "name": "timeline",
                    "sql": f"""
                        SELECT
                            toStartOfFifteenMinutes(ts_utc) AS bucket,
                            countIf(status = 'in_stock') AS in_stock,
                            countIf(status = 'low') AS low,
                            countIf(status = 'out') AS out,
                            countIf(status = 'unknown') AS unknown,
                            round(avgIf(price, price > 0), 2) AS avg_price
                        FROM stock_checks
                        WHERE essential_key = {{{{String(essential_key, 'flashlight')}}}}
                          AND zip = {{{{String(zip, '96720')}}}}
                          AND ts_utc > now() - INTERVAL {{{{Int32(hours, 12)}}}} HOUR
                          AND {_REAL}
                        GROUP BY bucket
                        ORDER BY bucket
                    """,
                }
            )
        ],
        "output": {
            "bucket": t.date_time(),
            "in_stock": t.uint64(),
            "low": t.uint64(),
            "out": t.uint64(),
            "unknown": t.uint64(),
            "avg_price": t.float64(),
        },
        "tokens": [{"token": scout_read, "scope": "READ"}],
    },
)

depletion_by_item_zip = define_endpoint(
    "depletion_by_item_zip",
    {
        "description": (
            "Stockout risk per essential and ZIP: 0.6 x share of stores whose latest status is "
            "low/out + 0.4 x share that flipped from in_stock to low/out in the window."
        ),
        "params": {
            "hours": p.int32().optional(3),
        },
        "nodes": [
            node(
                {
                    "name": "per_store",
                    "sql": f"""
                        SELECT
                            zip,
                            essential_key,
                            if(level = 'zip', concat('zip:', zip), store_id) AS store_key,
                            argMax(status, ts_utc) AS latest,
                            argMin(status, ts_utc) AS earliest,
                            max(ts_utc) AS last_seen
                        FROM stock_checks
                        WHERE ts_utc > now() - INTERVAL {{{{Int32(hours, 3)}}}} HOUR
                          AND level IN ('store', 'zip')
                          AND status != 'unknown'
                          AND {_REAL}
                        GROUP BY zip, essential_key, store_key
                    """,
                }
            ),
            node(
                {
                    "name": "depletion",
                    "sql": """
                        SELECT
                            zip,
                            essential_key,
                            count() AS stores_checked,
                            countIf(latest IN ('low', 'out')) AS stores_low_out,
                            countIf(earliest = 'in_stock' AND latest IN ('low', 'out')) AS stores_flipped,
                            round(0.6 * stores_low_out / stores_checked + 0.4 * stores_flipped / stores_checked, 3) AS risk,
                            max(last_seen) AS last_seen
                        FROM per_store
                        GROUP BY zip, essential_key
                        ORDER BY risk DESC
                    """,
                }
            ),
        ],
        "output": {
            "zip": t.string(),
            "essential_key": t.string(),
            "stores_checked": t.uint64(),
            "stores_low_out": t.uint64(),
            "stores_flipped": t.uint64(),
            "risk": t.float64(),
            "last_seen": t.date_time64(3),
        },
        "tokens": [{"token": scout_read, "scope": "READ"}],
    },
)
