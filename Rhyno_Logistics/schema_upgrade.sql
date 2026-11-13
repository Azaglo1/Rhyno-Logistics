-- Non-destructive, repeatable upgrade for account recovery, customer
-- administration, verified PayPal/Paystack payments, and session revocation.
-- Run as the application schema: sqlplus user/pass@service @schema_upgrade.sql
WHENEVER SQLERROR EXIT SQL.SQLCODE

DECLARE
  item_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'CUSTOMERS' AND column_name = 'ACTIVE';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE customers ADD (active NUMBER(1) DEFAULT 1 NOT NULL)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'CUSTOMERS' AND column_name = 'PASSWORD_RESET_TOKEN_HASH';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE customers ADD (password_reset_token_hash VARCHAR2(64))';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'CUSTOMERS' AND column_name = 'PASSWORD_RESET_EXPIRES_AT';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE customers ADD (password_reset_expires_at TIMESTAMP WITH TIME ZONE)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'CUSTOMERS' AND column_name = 'SESSION_VERSION';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE customers ADD (session_version NUMBER(10) DEFAULT 0 NOT NULL)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'ADMINISTRATORS' AND column_name = 'SESSION_VERSION';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE administrators ADD (session_version NUMBER(10) DEFAULT 0 NOT NULL)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_constraints
    WHERE table_name = 'CUSTOMERS' AND constraint_name = 'CK_CUSTOMERS_ACTIVE';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE customers ADD CONSTRAINT ck_customers_active CHECK (active IN (0, 1))';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'PAYMENTS' AND column_name = 'CURRENCY';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE payments ADD (currency VARCHAR2(3))';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'PAYMENTS' AND column_name = 'PROVIDER_REFERENCE';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE payments ADD (provider_reference VARCHAR2(100))';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'PAYMENTS' AND column_name = 'PROVIDER_CHECKOUT_URL';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE payments ADD (provider_checkout_url VARCHAR2(1000))';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_tab_columns
    WHERE table_name = 'PAYMENTS' AND column_name = 'UPDATED_AT';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE payments ADD (updated_at TIMESTAMP WITH TIME ZONE)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_indexes
    WHERE index_name = 'UQ_CUSTOMERS_RESET_HASH';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX uq_customers_reset_hash ON customers(password_reset_token_hash)';
  END IF;

  SELECT COUNT(*) INTO item_count FROM user_indexes
    WHERE index_name = 'UQ_PAYMENTS_PROVIDER_REF';
  IF item_count = 0 THEN
    EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX uq_payments_provider_ref ON payments(provider_reference)';
  END IF;
END;
/

PROMPT Rhyno Logistics schema upgrade completed.
