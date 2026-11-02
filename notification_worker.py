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


def send_email(destination,subject,body=None):
    key=os.environ.get('RESEND_API_KEY','').strip();sender=os.environ.get('RESEND_FROM_EMAIL','').strip()
    if not key or not sender:raise RuntimeError('email_provider_not_configured')
    body=json.dumps({'from':sender,'to':[destination],'subject':subject,'text':body or subject+'\nراجع بوابة الموارد البشرية لمعرفة التفاصيل.'}).encode()
    request=urllib.request.Request('https://api.resend.com/emails',data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:
        if not json.load(response).get('id'):raise RuntimeError('email_not_confirmed')


def deliver(c,send=False):
    """One worker only; claim before sending. Claimed uncertain deliveries need review."""
    configured=bool(os.environ.get('RESEND_API_KEY') and os.environ.get('RESEND_FROM_EMAIL'))
    rows=c.execute("SELECT id,destination,subject,body FROM email_outbox WHERE status IN ('pending_configuration','pending') ORDER BY id LIMIT 100").fetchall()
    if not send or not configured:return {'queued':len(rows),'sent':0,'configured':configured}
    sent=0
    for row in rows:
        claim=c.execute("UPDATE email_outbox SET status='sending',attempts=attempts+1 WHERE id=? AND status IN ('pending_configuration','pending')",(row[0],));c.commit()
        if not claim.rowcount:continue
        try:send_email(row[1],row[2],row[3])
        except Exception:
            # A timeout can occur after acceptance. Do not automatically resend and duplicate it.
            c.execute("UPDATE email_outbox SET status='needs_review' WHERE id=?",(row[0],));c.commit()
        else:
            c.execute("UPDATE email_outbox SET status='sent',sent_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),row[0]));c.commit();sent+=1
    return {'queued':len(rows),'sent':sent,'configured':True}


def whatsapp_configured():
    return all(os.environ.get(key) for key in ('WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_TEMPLATE_NAME'))


def send_whatsapp(destination,parameters,request_id):
    phone_id=os.environ.get('WHATSAPP_PHONE_NUMBER_ID','')
    if not phone_id.isdigit():raise RuntimeError('whatsapp_phone_id_invalid')
    origin=os.environ.get('EHKAM_PUBLIC_URL','').rstrip('/')
    from urllib.parse import urlparse
    parsed=urlparse(origin)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:raise RuntimeError('public_url_not_configured')
    values=parameters+[origin+f'/#request-{request_id}']
    payload={'messaging_product':'whatsapp','to':destination,'type':'template','template':{'name':os.environ['WHATSAPP_TEMPLATE_NAME'],'language':{'code':os.environ.get('WHATSAPP_TEMPLATE_LANGUAGE','ar')},'components':[{'type':'body','parameters':[{'type':'text','text':str(v)} for v in values]}]}}
    request=urllib.request.Request('https://graph.facebook.com/v22.0/'+phone_id+'/messages',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+os.environ['WHATSAPP_ACCESS_TOKEN'],'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:
        if not json.load(response).get('messages'):raise RuntimeError('whatsapp_not_confirmed')


def deliver_whatsapp(c,send=False):
    rows=c.execute("SELECT id,destination,parameters,request_id FROM whatsapp_outbox WHERE status IN ('pending_configuration','pending') ORDER BY id LIMIT 100").fetchall()
    configured=whatsapp_configured()
    if not send or not configured:return {'queued':len(rows),'sent':0,'configured':configured}
    sent=0
    for row in rows:
        claim=c.execute("UPDATE whatsapp_outbox SET status='sending',attempts=attempts+1 WHERE id=? AND status IN ('pending_configuration','pending')",(row[0],));c.commit()
        if not claim.rowcount:continue
        try:send_whatsapp(row[1],json.loads(row[2]),row[3])
        except Exception:c.execute("UPDATE whatsapp_outbox SET status='needs_review' WHERE id=?",(row[0],))
        else:c.execute("UPDATE whatsapp_outbox SET status='sent',sent_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),row[0]));sent+=1
        c.commit()
    return {'queued':len(rows),'sent':sent,'configured':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--send',action='store_true');args=parser.parse_args()
    path=os.environ.get('EHKAM_DB_PATH')
    if not path or not Path(path).is_file():parser.error('Set EHKAM_DB_PATH explicitly to an existing, authorized database')
    c=sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=rw',uri=True)
    try:print(json.dumps({'email':deliver(c,args.send),'whatsapp':deliver_whatsapp(c,args.send)}))
    finally:c.close()


def start_background(connect):
    """Run one delivery loop per server process; atomic claims prevent duplicates."""
    import threading
    import contextlib
    import time
    if not ((os.environ.get('RESEND_API_KEY') and os.environ.get('RESEND_FROM_EMAIL')) or whatsapp_configured()):return
    def run():
        while True:
            try:
                with contextlib.closing(connect()) as c:
                    deliver(c,send=True)
                    deliver_whatsapp(c,send=True)
            except Exception:
                print('NOTIFICATION WORKER: delivery check failed; inspect outbox status',flush=True)
            time.sleep(15)
    threading.Thread(target=run,name='notification-delivery',daemon=True).start()


if __name__=='__main__':main()
