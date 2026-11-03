"""C51: owner-only WebAuthn credentials and short-lived bound challenges."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE owner_passkeys (
      id uuid PRIMARY KEY,
      credential_id bytea NOT NULL UNIQUE,
      public_key bytea NOT NULL,
      sign_count bigint NOT NULL CHECK (sign_count >= 0),
      transports text[] NOT NULL DEFAULT '{}',
      label text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      last_used_at timestamptz
    );
    CREATE TABLE passkey_challenges (
      id uuid PRIMARY KEY,
      purpose text NOT NULL CHECK (purpose IN ('register','approve')),
      -- Logical binding is checked under the owner lock, including after demo reset.
      -- No FK: the facade deliberately truncates only its fixed financial tables.
      proposal_id uuid,
      payload_hash text,
      challenge bytea NOT NULL UNIQUE,
      rp_id text NOT NULL,
      origin text NOT NULL,
      expires_at timestamptz NOT NULL DEFAULT (now() + interval '2 minutes'),
      used_at timestamptz,
      CHECK ((purpose='register' AND proposal_id IS NULL AND payload_hash IS NULL)
        OR (purpose='approve' AND proposal_id IS NOT NULL AND payload_hash IS NOT NULL))
    );
    REVOKE ALL ON owner_passkeys, passkey_challenges FROM PUBLIC, countersign_agent_ro;
    """)


def downgrade():
    op.execute("DROP TABLE passkey_challenges")
    op.execute("DROP TABLE owner_passkeys")
