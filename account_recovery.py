"""Offline MFA recovery in maintenance only; interactive, backed up and audited."""
import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import warnings
import production_db
import server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--expected-id',required=True);parser.add_argument('--backup',required=True)
    args=parser.parse_args()
    if os.environ.get('EHKAM_START_MODE')!='storage-setup':raise ValueError('Enter approved maintenance before recovery')
    if not sys.stdin.isatty() or not sys.stdout.isatty():raise ValueError('Private interactive terminal required')
    production_db.inspect_database(args.database,args.expected_id)
    username=input('Account username: ').strip()
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        password=getpass.getpass('Current account password (hidden): ')
    with contextlib.closing(sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=rw',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
        user=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if not user or not server.verifypw(password,user['password_hash']):raise ValueError('Invalid credentials')
        if input('Type DISABLE MFA for this account: ')!='DISABLE MFA':raise ValueError('Confirmation refused')
        production_db.copy_database(args.database,args.backup,args.expected_id)
        c.execute('DELETE FROM user_mfa WHERE user_id=?',(user['id'],));c.execute('DELETE FROM auth_sessions WHERE user_id=?',(user['id'],))
        server.record(c,user,'mfa_offline_recovery','user',user['id']);c.commit()
    print('MFA disabled and sessions revoked. Re-enroll after restoring normal application mode.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,sqlite3.Error,EOFError) as exc:raise SystemExit(str(exc))
