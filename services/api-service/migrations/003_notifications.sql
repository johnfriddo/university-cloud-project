-- Notifications sent — or that would have been sent — to the owner of an asset.
--
-- The notification-service writes here, the api-service only reads. Migrations
-- stay in one place on purpose: the schema has a single owner, and one ordered
-- history, no matter how many services touch the tables.
--
-- What is stored is the message, not a pointer to it. Rebuilding the wording
-- later from the asset would give a different text than the one delivered, and
-- a record of a notification has to say what was actually said.

CREATE TABLE notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    asset_id   UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    event      TEXT        NOT NULL CHECK (event IN ('asset.processed', 'asset.failed')),
    -- Address the message went to, as it was at that moment: an account that
    -- later changes its email must not rewrite what was already delivered.
    recipient  TEXT        NOT NULL,
    subject    TEXT        NOT NULL,
    body       TEXT        NOT NULL,
    -- Set once the message leaves for its channel. NULL means recorded but not
    -- delivered, which is what a failure of the channel itself looks like.
    sent_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- This is what makes the consumer idempotent: a redelivered event finds the
    -- row already there and notifies nobody a second time. The same asset can
    -- still produce one processed and one failed notification, which is correct
    -- for an image that failed and was then retried successfully.
    UNIQUE (asset_id, event)
);

-- The inbox asks one question: "this user's notifications, newest first".
CREATE INDEX notifications_user_created_idx ON notifications (user_id, created_at DESC);
