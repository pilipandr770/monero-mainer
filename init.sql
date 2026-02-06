CREATE TABLE IF NOT EXISTS stats (
    id SERIAL PRIMARY KEY,
    total_hashrate FLOAT DEFAULT 0,
    total_shares INTEGER DEFAULT 0,
    estimated_xmr FLOAT DEFAULT 0
);
