-- Reset the rma_status_event.id sequence to the current max identifier.
--
-- Sequence drift can happen after manual data imports or when a database
-- backup/restore bypasses the sequence's next value. Bringing the sequence
-- back in sync prevents future INSERTs from reusing an existing primary key.
SELECT setval(
    pg_get_serial_sequence('rma_status_event', 'id'),
    COALESCE((SELECT MAX(id) FROM rma_status_event), 1),
    TRUE
);
