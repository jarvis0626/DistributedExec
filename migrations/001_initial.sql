CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, parent_id TEXT, status TEXT NOT NULL, created REAL NOT NULL,
 finished REAL, priority INTEGER NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
 code_hash TEXT NOT NULL, input_hash TEXT NOT NULL, idempotency_key TEXT UNIQUE,
 error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE workers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, credential_hash TEXT UNIQUE NOT NULL,
 created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 heartbeat REAL NOT NULL, mode TEXT NOT NULL, cpu REAL NOT NULL, memory_mb INTEGER NOT NULL,
 concurrency INTEGER NOT NULL, runtime_id TEXT
);
CREATE TABLE chunks (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), ordinal INTEGER NOT NULL,
 data TEXT NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL, current_attempt TEXT,
 attempt_count INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL, result TEXT,
 UNIQUE(job_id, ordinal)
);
CREATE TABLE attempts (
 id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL REFERENCES chunks(id), worker_id TEXT NOT NULL REFERENCES workers(id),
 token_hash TEXT NOT NULL, state TEXT NOT NULL, started REAL NOT NULL, finished REAL,
 lease_until REAL NOT NULL, completion_hash TEXT, error TEXT, runtime_id TEXT, exit_code INTEGER, duration REAL
);
CREATE TABLE logs (
 attempt_id TEXT NOT NULL REFERENCES attempts(id), seq INTEGER NOT NULL, stream TEXT NOT NULL,
 text TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(attempt_id, seq)
);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, created REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), path TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE pairing (id INTEGER PRIMARY KEY CHECK(id=1), code_hash TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, csrf_hash TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE tickets (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
CREATE TABLE pair_limits (address TEXT PRIMARY KEY, window REAL NOT NULL, failures INTEGER NOT NULL);
CREATE INDEX chunk_queue ON chunks(state, available, job_id);
CREATE INDEX attempt_lease ON attempts(state, lease_until);
CREATE INDEX job_history ON jobs(created);
INSERT INTO schema_version VALUES (1);
