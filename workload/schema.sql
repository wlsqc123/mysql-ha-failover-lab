CREATE TABLE IF NOT EXISTS accounts (
    account_id BIGINT PRIMARY KEY,
    balance BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT ck_balance CHECK (balance >= 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS reward_grants (
    request_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    account_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    status ENUM('GRANTED') NOT NULL DEFAULT 'GRANTED',
    granted_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_grant_account FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    CONSTRAINT ck_grant_amount CHECK (amount > 0)
) ENGINE=InnoDB;
