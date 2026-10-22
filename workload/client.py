"""One reward transaction per connection; automatic retries belong to a later step."""
import os
import re

import mysql.connector
from mysql.connector import errorcode

MAX_BIGINT = 2**63 - 1


class RequestMismatch(ValueError):
    """A request ID was reused with a different account or amount."""


class UnknownOutcome(RuntimeError):
    """COMMIT was attempted but its response was not confirmed."""


def connect(admin=False):
    return mysql.connector.connect(
        host=os.getenv('LAB_DB_HOST', 'router'),
        port=int(os.getenv('LAB_DB_PORT', '6446')),
        user='root' if admin else 'lab_app',
        password=os.environ['MYSQL_ROOT_PASSWORD' if admin else 'LAB_APP_PASSWORD'],
        database='reward_lab',
        autocommit=False,
        connection_timeout=10,
    )


def grant_reward(request_id, account_id, amount):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request_id):
        raise ValueError('request_id must contain 1-64 ASCII letters, digits, _ or -')
    for name, value in [('account_id', account_id), ('amount', amount)]:
        if type(value) is not int or not 1 <= value <= MAX_BIGINT:
            raise ValueError(f'{name} must be a positive signed BIGINT integer')
    conn = connect()
    try:
        with conn.cursor() as cursor:
            conn.start_transaction()
            try:
                cursor.execute(
                    'INSERT INTO reward_grants(request_id, account_id, amount) VALUES (%s, %s, %s)',
                    (request_id, account_id, amount),
                )
            except mysql.connector.IntegrityError as error:
                if error.errno != errorcode.ER_DUP_ENTRY:
                    raise
                # Release duplicate-key locks and discard any old snapshot before lookup.
                conn.rollback()
                cursor.execute('SELECT account_id, amount FROM reward_grants WHERE request_id=%s', (request_id,))
                existing = cursor.fetchone()
                if existing != (account_id, amount):
                    raise RequestMismatch('request_id already belongs to a different account or amount')
                return 'ALREADY_GRANTED'
            cursor.execute('UPDATE accounts SET balance=balance+%s WHERE account_id=%s', (amount, account_id))
            if cursor.rowcount != 1:
                raise ValueError('Account not found')
            try:
                conn.commit()
            except mysql.connector.Error as error:
                raise UnknownOutcome(f'COMMIT response unconfirmed; reconcile request_id={request_id}') from error
            return 'GRANTED'
    except Exception:
        try:
            conn.rollback()
        except mysql.connector.Error:
            pass
        raise
    finally:
        conn.close()
