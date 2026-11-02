-- Made-up rows for the CI job `backup-restore`: the column types production uses that a dump
-- and restore could get wrong (pgvector, exact decimals, JSON, non-ASCII text, timestamps).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE made_up_accounts (
    id serial PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE made_up_costs (
    id serial PRIMARY KEY,
    account_id integer NOT NULL REFERENCES made_up_accounts (id),
    amount_usd numeric(12, 6) NOT NULL,
    details jsonb NOT NULL
);

CREATE TABLE made_up_chunks (
    id serial PRIMARY KEY,
    body text NOT NULL,
    embedding vector(3) NOT NULL
);

INSERT INTO made_up_accounts (name) VALUES ('Made-up Clinic'), ('Exemple Santé'), ('例');

INSERT INTO made_up_costs (account_id, amount_usd, details) VALUES
    (1, 0.001617, '{"model": "made-up"}'),
    (2, 12.5, '{"tags": ["a", "b"]}');

INSERT INTO made_up_chunks (body, embedding) VALUES
    ('A made-up policy line.', '[0.1, 0.2, 0.3]'),
    ('Another made-up line.', '[-1, 0, 1]'),
    ('Third.', '[0, 0, 0]'),
    ('Fourth.', '[3.5, 2.25, 1]');
