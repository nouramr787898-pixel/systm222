"""Opt-in delivery of queued email. Never run against real contacts without authorization.

EHKAM_DB_PATH must be set explicitly; use --send only when delivery is authorized.
Requires RESEND_API_KEY and a verified RESEND_FROM_EMAIL. No SMS is sent.
"""
import argparse
import datetime
import json
import os
import sqlite3
import urllib.request
from pathlib import Path


def send_email(destination,subject):
    key=os.environ.get('RESEND_API_KEY','').strip();sender=os.environ.get('RESEND_FROM_EMAIL','').strip()
    if not key or not sender:raise RuntimeError('email_provider_not_configured')
    body=json.dumps({'from':sender,'to':[destination],'subject':subject,'text':subject+'\nراجع بوابة الموارد البشرية لمعرفة التفاصيل.'}).encode()
    request=urllib.request.Request('https://api.resend.com/emails',data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:
        if not json.load(response).get('id'):raise RuntimeError('email_not_confirmed')


def deliver(c,send=False):
    """One worker only; claim before sending. Claimed uncertain deliveries need review."""
    configured=bool(os.environ.get('RESEND_API_KEY') and os.environ.get('RESEND_FROM_EMAIL'))
    rows=c.execute("SELECT id,destination,subject FROM email_outbox WHERE status IN ('pending_configuration','pending') ORDER BY id LIMIT 100").fetchall()
    if not send or not configured:return {'queued':len(rows),'sent':0,'configured':configured}
    sent=0
    for row in rows:
        claim=c.execute("UPDATE email_outbox SET status='sending',attempts=attempts+1 WHERE id=? AND status IN ('pending_configuration','pending')",(row[0],));c.commit()
        if not claim.rowcount:continue
        try:send_email(row[1],row[2])
        except Exception:
            # A timeout can occur after acceptance. Do not automatically resend and duplicate it.
            c.execute("UPDATE email_outbox SET status='needs_review' WHERE id=?",(row[0],));c.commit()
        else:
            c.execute("UPDATE email_outbox SET status='sent',sent_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),row[0]));c.commit();sent+=1
    return {'queued':len(rows),'sent':sent,'configured':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--send',action='store_true');args=parser.parse_args()
    path=os.environ.get('EHKAM_DB_PATH')
    if not path or not Path(path).is_file():parser.error('Set EHKAM_DB_PATH explicitly to an existing, authorized database')
    c=sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=rw',uri=True)
    try:print(json.dumps(deliver(c,args.send)))
    finally:c.close()


if __name__=='__main__':main()
