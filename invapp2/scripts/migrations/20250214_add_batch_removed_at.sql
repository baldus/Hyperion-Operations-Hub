ALTER TABLE batch
ADD COLUMN IF NOT EXISTS removed_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_batch_removed_at ON batch (removed_at);
