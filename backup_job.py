"""Scheduled consistent backups to an explicitly configured separate mount.

Run --once from your scheduler. Never restores or deletes business data.
A destination on the same device is refused by default: a second directory on
one volume is not disaster recovery. Provision encrypted independent storage.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import production_db


def run(database,directory,identity,allow_same_device=False):
    source=Path(database).resolve();destination=Path(directory).resolve()
    if not source.is_file() or not destination.is_dir():raise ValueError('Existing database and backup destination directory required')
    if source.parent==destination or source.is_relative_to(destination):raise ValueError('Backup destination must be separate from database directory')
    if source.stat().st_dev==destination.stat().st_dev and not allow_same_device:raise ValueError('Independent backup mount required; same-device backup is not disaster recovery')
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=destination/('ehkam-'+stamp+'.sqlite3')
    result=production_db.copy_database(source,target,identity)
    with open(target,'rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest={'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'database_id':result['database_id'],'file':target.name,'sha256':checksum,'integrity':result['integrity'],'independent_device':source.stat().st_dev!=destination.stat().st_dev}
    # Exclusive create, same filesystem and restrictive permissions. Never overwrite.
    fd=os.open(str(target)+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(manifest,stream)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--directory',required=True);parser.add_argument('--expected-id',required=True)
    parser.add_argument('--once',action='store_true');parser.add_argument('--interval',type=int,default=86400)
    args=parser.parse_args()
    if args.interval<300:parser.error('Minimum interval is 300 seconds')
    while True:
        print(json.dumps(run(args.database,args.directory,args.expected_id)),flush=True)
        if args.once:return
        time.sleep(args.interval)


if __name__=='__main__':main()
