-- Initial schema: users, assets and their generated variants.
--
-- Only the information *about* the files lives here. The bytes themselves stay
-- in the object storage: this database holds what the object storage cannot
-- answer, namely who owns what, how far along a job is, and how to page through
-- a library sorted by date.

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE assets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Deleting a user removes their assets: no orphan rows pointing nowhere.
    user_id       UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- Key of the uploaded file in the originals bucket, {user_id}/{asset_id}.{ext}
    original_key  TEXT        NOT NULL,
    filename      TEXT        NOT NULL,
    mime          TEXT        NOT NULL,
    size_bytes    BIGINT,
    status        TEXT        NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING', 'PROCESSING', 'DONE', 'FAILED')),
    -- Human readable reason shown next to a failed asset in the library.
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The library always asks the same question: "this user's assets, newest first".
CREATE INDEX assets_user_created_idx ON assets (user_id, created_at DESC);

-- The frontend polls the assets that are not in a terminal state yet.
CREATE INDEX assets_status_idx ON assets (status);

CREATE TABLE variants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id    UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    kind        TEXT        NOT NULL CHECK (kind IN ('thumb', 'medium', 'large')),
    storage_key TEXT        NOT NULL,
    width       INTEGER     NOT NULL,
    height      INTEGER     NOT NULL,
    size_bytes  BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An asset has at most one variant of each kind. This is what makes the
    -- worker idempotent in phase 2: if the broker redelivers a message the
    -- second insert is rejected instead of duplicating the work.
    UNIQUE (asset_id, kind)
);

CREATE INDEX variants_asset_idx ON variants (asset_id);
