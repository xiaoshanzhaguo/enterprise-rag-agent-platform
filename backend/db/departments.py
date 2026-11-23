"""Department catalog and migration from user and approval-rule department names."""
from unicodedata import normalize


def routing_key(value: str) -> str:
    return ' '.join(normalize('NFKC', value).casefold().split())


def get_department(connection, department_id):
    return connection.execute('SELECT * FROM workspace_departments WHERE id = ?', (department_id,)).fetchone()


def migrate_departments(connection):
    names = connection.execute('''SELECT department FROM workspace_users WHERE department IS NOT NULL
        UNION SELECT department FROM process_approval_rules ORDER BY department''').fetchall()
    for row in names:
        name = row['department'].strip()
        key = routing_key(name)
        if not key:
            continue
        connection.execute('INSERT INTO workspace_departments (name, name_key) VALUES (?, ?) ON CONFLICT(name_key) DO NOTHING',
                           (name, key))
        canonical = connection.execute('SELECT name FROM workspace_departments WHERE name_key = ?', (key,)).fetchone()['name']
        if canonical != row['department']:
            connection.execute('UPDATE workspace_users SET department = ? WHERE department = ?', (canonical, row['department']))
            connection.execute('UPDATE process_approval_rules SET department = ? WHERE department_key = ? AND department <> ?',
                               (canonical, key, canonical))
