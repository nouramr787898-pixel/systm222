"""Explicit offline business-data reset. Never invoked by deployment/startup/HTTP.

Run interactively in storage-setup maintenance, on the existing mounted database.
Keeps administrator accounts and database identity; requires a verified backup.
"""
import argparse
import contextlib
import datetime
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import warnings

import production_db
import server

CLEAR_TABLES=(
    'approval_delegations','company_policy_terms','import_previews','candidates','vacancies','appraisals','training_records',
    'loan_repayments','payroll_events','payroll_lines','payroll_runs','loans','pay_adjustments',
    'employee_terms','lifecycle_tasks','employee_assets','auth_sessions','login_limits',
    'request_attachments','request_decisions','request_comments','employee_files',
    'email_outbox','notifications','settlements','absences','overtime_reviews',
    'attendance','password_resets','employee_number_history','requests','contracts',
    'schedules','work_sites','holidays','employees','companies','audit',
)
KEEP_TABLES={'users','employee_number_sequence','schema_migrations','production_identity','admin_profiles','user_mfa'}


def guard(database,expected_id):
    database=Path(database).resolve()
    mount=Path(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH','/nonexistent')).resolve()
    configured=os.environ.get('EHKAM_DB_PATH','')
    if os.environ.get('EHKAM_START_MODE')!='storage-setup':raise ValueError('Switch service to storage-setup maintenance before reset')
    if not mount.is_dir() or not database.is_relative_to(mount):raise ValueError('Database must be inside the mounted volume')
    if not configured or Path(configured).resolve()!=database:raise ValueError('Database must match EHKAM_DB_PATH')
    if not expected_id or os.environ.get('EHKAM_DB_ID')!=expected_id:raise ValueError('Expected identity must match EHKAM_DB_ID')
    production_db.inspect_database(database,expected_id)
    return database


def reset_data(database,expected_id,username,password,confirmation,backup):
    database=guard(database,expected_id)
    if confirmation!='CLEAR BUSINESS DATA '+expected_id:raise ValueError('Confirmation does not match the database identity')
    with contextlib.closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=30)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('BEGIN IMMEDIATE')
        try:
            admin=c.execute("SELECT * FROM users WHERE username=? AND role='admin'",(username,)).fetchone()
            if not admin or not server.verifypw(password,admin['password_hash']):raise ValueError('Administrator credentials are invalid')
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables-set(CLEAR_TABLES)-KEEP_TABLES:raise ValueError('Unknown tables found; review reset scope first')
            if c.execute('SELECT 1 FROM admin_profiles WHERE company_id IS NOT NULL LIMIT 1').fetchone():raise ValueError('Scoped administrators exist; review account preservation before any business reset')
            original_identity=c.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()[0]
            if original_identity!=expected_id:raise ValueError('Database identity changed')
            before={name:c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in CLEAR_TABLES if name in tables}
            # This verified snapshot is made before DELETE while competing writers are blocked.
            production_db.copy_database(database,backup,expected_id)
            c.execute("DELETE FROM user_mfa WHERE user_id IN (SELECT id FROM users WHERE role!='admin')")
            for table in CLEAR_TABLES:
                if table in tables:c.execute('DELETE FROM '+table)
            c.execute("DELETE FROM users WHERE role!='admin'")
            c.execute('UPDATE users SET employee_id=NULL,company_id=NULL')
            c.execute('UPDATE employee_number_sequence SET value=0 WHERE id=1')
            server.record(c,admin,'business_data_reset','database',info={'deleted_counts':before,'backup_file':Path(backup).name})
            if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key check failed; reset rolled back')
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Integrity check failed; reset rolled back')
            c.commit()
        except Exception:
            c.rollback();raise
    return {**production_db.inspect_database(database,expected_id),'backup':str(Path(backup).absolute()),'deleted_counts':before}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--expected-id',required=True)
    parser.add_argument('--backup',help='New backup file; an existing file is never overwritten')
    args=parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():raise ValueError('Use a private interactive terminal; credentials are never accepted through a pipe')
    database=guard(args.database,args.expected_id)
    username=input('Administrator username: ').strip()
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        password=getpass.getpass('Administrator password (hidden): ')
    print('This removes ALL companies, employees, non-admin accounts and related business records. Administrator accounts and database identity remain.')
    confirmation=input('Type CLEAR BUSINESS DATA '+args.expected_id+': ').strip()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=args.backup or str(database.parent/'backups'/('before-reset-'+stamp+'.sqlite3'))
    result=reset_data(database,args.expected_id,username,password,confirmation,backup)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
