from __future__ import annotations

import os
import sys
import psycopg2

PG_HOST = "platformuser.cluster-custom-csteuf9lw8dv.ap-northeast-1.rds.amazonaws.com"
PG_PORT = 5432
PG_DB = "replication_report"
PG_USER = "platform_exchanges_user"
PG_PASS = "Gdafl(j;390HJDL"
TABLE = "platform_exchanges_setting_min"

DDL = f"""
BEGIN;

CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGSERIAL PRIMARY KEY,
  symbol VARCHAR(32) NOT NULL,
  exchange VARCHAR(16) NOT NULL,
  max_leverage INT NULL,
  max_size NUMERIC(36,12) NULL,
  mmr NUMERIC(18,10) NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 兼容已有旧表：补充 tier_order 列并设置为 NOT NULL
ALTER TABLE {TABLE}
  ADD COLUMN IF NOT EXISTS tier_order INT;

-- 将空值填充默认序号 1（首次落表无需此步，已存在旧表时保障非空）
UPDATE {TABLE} SET tier_order = 1 WHERE tier_order IS NULL;

-- 设为 NOT NULL（若已是 NOT NULL 不会报错）
ALTER TABLE {TABLE}
  ALTER COLUMN tier_order SET NOT NULL;

-- 索引
-- 若存在旧唯一索引 (symbol, exchange)，需要移除以避免与新索引冲突
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE schemaname = 'public' AND tablename = '{TABLE}' AND indexname = 'uq_{TABLE}_symbol_exchange'
  ) THEN
    EXECUTE 'DROP INDEX IF EXISTS uq_{TABLE}_symbol_exchange';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_symbol_exchange_tier
  ON {TABLE} (symbol, exchange, tier_order);

COMMIT;
"""


def main() -> None:
    print("[info] connecting postgres ...")
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        connect_timeout=10,
        sslmode=os.environ.get("PG_SSLMODE", "prefer"),
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print(f"[ok] schema prepared for table: {TABLE}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[err] ", e)
        sys.exit(1)
