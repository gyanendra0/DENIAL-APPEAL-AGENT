-- The exact row count of every table in `public`, one "table|count" line each, sorted by name.
-- Run it on the source and on the restored copy and compare the two outputs:
--   psql --no-psqlrc --tuples-only --no-align --file row-counts.sql
-- query_to_xml runs one count(*) per table inside a single query, so no script is needed.
SELECT
    table_name,
    (xpath(
        '/row/n/text()',
        query_to_xml(format('SELECT count(*) AS n FROM %I.%I', table_schema, table_name), false, true, '')
    ))[1]::text::bigint AS row_count
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
