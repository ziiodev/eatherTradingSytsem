-- ----------------------------------------------------------------------------
-- 0003_skills.sql — snapshot of the delta applied by 0003_skills.py.
--
-- Hand-verified against the spec (#1971) and against the upgrade() body
-- in 0003_skills.py. NOT executed by Alembic; exists for human review.
--
-- If you change 0003_skills.py, regenerate / hand-update this file too.
-- ----------------------------------------------------------------------------

-- skills ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    name                VARCHAR(100) NOT NULL,
    type                VARCHAR(20)  NOT NULL,
    description         TEXT,

    version             INTEGER NOT NULL DEFAULT 1,

    code                TEXT    NOT NULL,
    runtime             VARCHAR(20) NOT NULL DEFAULT 'python',

    input_signature     JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_signature    JSONB NOT NULL DEFAULT '{}'::jsonb,

    is_active           BOOLEAN NOT NULL DEFAULT true,

    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),

    CONSTRAINT skills_type_valid
        CHECK (type IN ('indicator', 'data_source', 'analytic', 'executor', 'risk')),
    CONSTRAINT skills_runtime_only_python
        CHECK (runtime = 'python')
);

CREATE INDEX IF NOT EXISTS idx_skills_user_active
    ON skills(user_id) WHERE is_active = true;
