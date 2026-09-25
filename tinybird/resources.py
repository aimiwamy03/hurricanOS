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

stock_timeline = define_endpoint(
    "stock_timeline",
    {
        "description": "Stock checks for one item in one ZIP, newest first.",
        "params": {
            "essential_key": p.string().optional("flashlight"),
            "zip": p.string().optional("96720"),
            "limit": p.int32().optional(50),
        },
        "nodes": [
            node(
                {
                    "name": "timeline",
                    "sql": """
                        SELECT
                            ts_utc,
                            store_id,
                            zip,
                            essential_key,
                            status,
                            price,
                            confidence,
                            level
                        FROM stock_checks
                        WHERE essential_key = {{String(essential_key, 'flashlight')}}
                          AND zip = {{String(zip, '96720')}}
                        ORDER BY ts_utc DESC
                        LIMIT {{Int32(limit, 50)}}
                    """,
                }
            )
        ],
        "output": {
            "ts_utc": t.date_time64(3),
            "store_id": t.string(),
            "zip": t.string(),
            "essential_key": t.string(),
            "status": t.string(),
            "price": t.float32(),
            "confidence": t.float32(),
            "level": t.string(),
        },
        "tokens": [{"token": scout_read, "scope": "READ"}],
    },
)

depletion_by_item_zip = define_endpoint(
    "depletion_by_item_zip",
    {
        "description": "Recency-weighted share of checks that are low or out, last 2 hours.",
        "params": {
            "zip": p.string().optional("96720"),
        },
        "nodes": [
            node(
                {
                    "name": "depletion",
                    "sql": """
                        SELECT
                            essential_key,
                            zip,
                            count() AS checks,
                            if(
                                sum(exp(-dateDiff('second', ts_utc, now()) / 1800.)) = 0,
                                0,
                                sumIf(
                                    exp(-dateDiff('second', ts_utc, now()) / 1800.),
                                    status IN ('low', 'out')
                                ) / sum(exp(-dateDiff('second', ts_utc, now()) / 1800.))
                            ) AS depletion_share
                        FROM stock_checks
                        WHERE ts_utc >= now() - INTERVAL 2 HOUR
                          AND zip = {{String(zip, '96720')}}
                        GROUP BY essential_key, zip
                        ORDER BY depletion_share DESC
                    """,
                }
            )
        ],
        "output": {
            "essential_key": t.string(),
            "zip": t.string(),
            "checks": t.uint64(),
            "depletion_share": t.float64(),
        },
        "tokens": [{"token": scout_read, "scope": "READ"}],
    },
)
