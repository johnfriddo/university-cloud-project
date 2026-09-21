-- Records the moment an asset entered the queue.
--
-- The four states of the specification cannot tell "registered, waiting for the
-- file" apart from "file uploaded, job queued": both are PENDING. Without this
-- column a second call to /complete would publish the job a second time.

ALTER TABLE assets ADD COLUMN submitted_at TIMESTAMPTZ;

-- Assets registered before this migration and already confirmed: treat the
-- creation time as the submission time.
UPDATE assets SET submitted_at = created_at WHERE status <> 'PENDING';
