"""Add the reward schema and limited application permissions without deleting data."""
from pathlib import Path

from workload.client import connect


def main():
    conn = connect(admin=True)
    try:
        with conn.cursor() as cursor:
            for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute("GRANT SELECT, UPDATE ON reward_lab.accounts TO 'lab_app'@'%'")
            cursor.execute("GRANT SELECT, INSERT ON reward_lab.reward_grants TO 'lab_app'@'%'")
        conn.commit()
        print('Reward schema and application permissions ready')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
