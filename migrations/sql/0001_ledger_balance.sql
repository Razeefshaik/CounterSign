-- A genuine parent tuple update serializes writers to one journal. A lock alone
-- is insufficient under repeatable read: its older snapshot could miss another
-- transaction's committed postings. The update also causes a serialization
-- failure at repeatable read/serializable when that snapshot is stale.
CREATE FUNCTION lock_ledger_journal() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    journal uuid;
    old_journal uuid;
    new_journal uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_journal := OLD.journal_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_journal := NEW.journal_id;
    END IF;
    FOR journal IN
        SELECT DISTINCT candidate
        FROM unnest(ARRAY[old_journal, new_journal]) AS candidate
        WHERE candidate IS NOT NULL
        ORDER BY candidate
    LOOP
        UPDATE ledger_journals SET memo = memo WHERE id = journal;
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER ledger_journal_write_lock
BEFORE INSERT OR UPDATE OR DELETE ON ledger_postings
FOR EACH ROW EXECUTE FUNCTION lock_ledger_journal();

CREATE FUNCTION check_ledger_journal_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    journal uuid;
    old_journal uuid;
    new_journal uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_journal := OLD.journal_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_journal := NEW.journal_id;
    END IF;
    FOR journal IN
        SELECT DISTINCT candidate
        FROM unnest(ARRAY[old_journal, new_journal]) AS candidate
        WHERE candidate IS NOT NULL
        ORDER BY candidate
    LOOP
        IF EXISTS (
            SELECT 1 FROM ledger_postings
            WHERE journal_id = journal
            GROUP BY currency
            HAVING sum(amount_minor) <> 0
        ) THEN
            RAISE EXCEPTION 'ledger journal must balance in every currency'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_journal_balanced';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ledger_journal_balanced
AFTER INSERT OR UPDATE OR DELETE ON ledger_postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_ledger_journal_balance();

CREATE INDEX ix_ledger_postings_journal_currency
ON ledger_postings (journal_id, currency);
