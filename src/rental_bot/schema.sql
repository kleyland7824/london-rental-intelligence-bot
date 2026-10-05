CREATE TABLE IF NOT EXISTS listings (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    source_listing_id TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    address TEXT,
    postcode TEXT,
    description TEXT,
    monthly_rent INTEGER,
    bedrooms INTEGER,
    bathrooms INTEGER,
    available_from DATE,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    commute_minutes INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    digest_sent_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewed_at TIMESTAMPTZ,
    CHECK (review_status IN ('unreviewed', 'rejected', 'left', 'favourite')),
    UNIQUE (source, source_listing_id)
);

ALTER TABLE listings ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE listings ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
UPDATE listings SET review_status = 'unreviewed' WHERE review_status IS NULL;
DO $$
BEGIN
    ALTER TABLE listings ADD CONSTRAINT listings_review_status_check
        CHECK (review_status IN ('unreviewed', 'rejected', 'left', 'favourite'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS listings_first_seen_idx ON listings (first_seen_at);
CREATE INDEX IF NOT EXISTS listings_digest_sent_idx ON listings (digest_sent_at);
CREATE INDEX IF NOT EXISTS listings_notified_idx ON listings (notified_at);
CREATE INDEX IF NOT EXISTS listings_review_status_idx ON listings (review_status);
