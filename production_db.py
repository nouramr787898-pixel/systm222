"""Explicit production initialization and non-destructive SQLite backup/restore checks.

Normal server startup NEVER invokes initialization from this module.
Passwords are read interactively, never from command arguments or environment variables.
"""
import argparse
import contextlib
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import sys
import warnings
import uuid

import server


@contextlib.contextmanager
def readonly(path):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('Database file does not exist; nothing was created')
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
    try:yield connection
    finally:connection.close()


def inspect_database(path, expected_id=None):
    """Integrity and identity validation, with aggregate counts only; never outputs PII."""
    with readonly(path) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity check failed')
        if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key check failed')
        identity=c.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()
        if not identity:raise ValueError('Production database identity is missing')
        if expected_id and identity[0]!=expected_id:raise ValueError('Production database identity mismatch')
        if not c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():raise ValueError('A production administrator is required')
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version='hr-v2'").fetchone():raise ValueError('Required HR schema is missing')
        counts={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('users','companies','employees','requests','request_attachments','employee_files')}
    return {'database_id':identity[0],'integrity':'ok','foreign_keys':'ok','counts':counts}


def publish_new_file(staged,target):
    """Atomically create a new name; an existing file or symlink is never replaced."""
    target=Path(target)
    os.chmod(staged,0o600)
    # Both paths live under the same parent filesystem. link fails if target exists.
    os.link(staged,target)
    if os.name=='posix':
        descriptor=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def initialize(path,username,password,display_name):
    target=Path(path).expanduser().absolute()
    if os.path.lexists(target):raise FileExistsError('Target already exists. Initialization never overwrites a database.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username):raise ValueError('Admin username must be 3-64 ASCII letters, digits, dot, dash or underscore')
    if len(password)<16 or len(password)>128:raise ValueError('Use a unique administrator password of 16-128 characters')
    if not display_name.strip() or len(display_name)>150:raise ValueError('Administrator display name is required (max 150 characters)')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ehkam-init-',dir=target.parent) as folder:
        staged=Path(folder)/'new.sqlite3'
        original=server.DB
        try:
            server.DB=staged
            server.init(allow_create=True)  # Explicit offline creation; no demo seeder, company, employee, or public default password.
        finally:server.DB=original
        database_id=str(uuid.uuid4())
        with contextlib.closing(sqlite3.connect(staged)) as c:
            with c:
                c.execute('CREATE TABLE production_identity(id INTEGER PRIMARY KEY CHECK(id=1),database_id TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)')
                c.execute('INSERT INTO production_identity VALUES(1,?,?)',(database_id,datetime.datetime.now(datetime.timezone.utc).isoformat()))
                c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id,display_name) VALUES(?,?,?,NULL,NULL,?)',(username,server.hashpw(password),'admin',display_name.strip()))
        result=inspect_database(staged,database_id)
        if result['counts']!={'users':1,'companies':0,'employees':0,'requests':0,'request_attachments':0,'employee_files':0}:raise ValueError('Initialization unexpectedly created data')
        with open(staged,'rb') as f:os.fsync(f.fileno())
        publish_new_file(staged,target)
    return result


def copy_database(source,target,expected_id=None):
    """Consistent SQLite Backup API snapshot, including committed WAL data.

    Used for backup and for a restore COPY to a NEW path. No overwrite option exists.
    """
    source=Path(source).expanduser().resolve();target=Path(target).expanduser().absolute()
    if os.path.lexists(target):raise FileExistsError('Target already exists. Backup/restore never overwrites it.')
    source_info=inspect_database(source,expected_id)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ehkam-copy-',dir=target.parent) as folder:
        staged=Path(folder)/'snapshot.sqlite3'
        with readonly(source) as original, contextlib.closing(sqlite3.connect(staged)) as destination:
            original.backup(destination)
        result=inspect_database(staged,source_info['database_id'])
        with open(staged,'rb') as f:os.fsync(f.fileno())
        publish_new_file(staged,target)
    return result


def interactive_password():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Initialization requires a private interactive terminal. Use railway ssh from your own terminal; piped input is refused.')
    # getpass must never fall back to an echoed input stream.
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:
            password=getpass.getpass('Unique admin password (16+ characters, hidden): ')
            confirmation=getpass.getpass('Confirm password (hidden): ')
        except getpass.GetPassWarning as error:
            raise ValueError('Secure hidden terminal input is unavailable; initialization refused') from error
    if password!=confirmation:raise ValueError('Passwords do not match')
    return password

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('initialize',help='Create a NEW database and one real admin account; never overwrite')
    init.add_argument('--database',required=True)
    check=commands.add_parser('verify',help='Read-only integrity and identity verification')
    check.add_argument('--database',required=True);check.add_argument('--expected-id')
    for command in ('backup','restore-copy'):
        copy=commands.add_parser(command,help='Create a consistent copy at a NEW path only')
        copy.add_argument('--database',required=True);copy.add_argument('--output',required=True);copy.add_argument('--expected-id')
    args=parser.parse_args()
    try:
        if args.command=='initialize':
            # Refuse an existing path before prompting for any credential.
            if os.path.lexists(Path(args.database).expanduser().absolute()):raise FileExistsError('Target already exists; initialization refused')
            if not sys.stdin.isatty() or not sys.stdout.isatty():raise ValueError('Initialization requires a private interactive terminal; no credentials were read')
            username=input('Production admin username: ').strip()
            name=input('Production admin display name: ').strip()
            password=interactive_password()
            result=initialize(args.database,username,password,name)
            del password
            print('Save this non-secret database identity as EHKAM_DB_ID:',result['database_id'])
        elif args.command=='verify':result=inspect_database(args.database,args.expected_id)
        else:result=copy_database(args.database,args.output,args.expected_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,FileExistsError,OSError,sqlite3.Error,EOFError) as error:
        parser.exit(1,'Operation refused: '+str(error)+'\n')


if __name__=='__main__':main()
