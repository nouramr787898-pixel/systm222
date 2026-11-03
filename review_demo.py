"""Create review accounts only in a NEW isolated demo database; never production."""
import argparse
import json
import os
from pathlib import Path
import demo
import server
import security


def prepare(directory,accounts):
    directory=Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):raise ValueError('Use a new empty review directory; existing data is never overwritten')
    if set(accounts)!={'admin','manager','employee'}:raise ValueError('Exactly admin, manager and employee accounts are required')
    names=[]
    for data in accounts.values():
        name=str(data.get('username','')).strip()
        if not name or len(name)>128 or any(ord(ch)<32 for ch in name) or not security.valid_password(data.get('password')):raise ValueError('Invalid review username or password (8–128 characters)')
        names.append(name)
    if len(set(names))!=3:raise ValueError('Review usernames must be distinct')
    database=demo.prepare_demo(directory)
    with server.conn() as c:
        for role,old in [('admin','director_demo'),('manager','supervisor_demo'),('employee','employee_demo')]:
            data=accounts[role]
            c.execute('UPDATE users SET username=?,display_name=?,password_hash=?,must_change_password=0 WHERE username=?',(data['username'].strip(),data['username'].strip(),server.hashpw(data['password']),old))
        c.execute("UPDATE users SET disabled=1 WHERE username='admin_demo'")
        c.execute('DELETE FROM notifications');c.execute('DELETE FROM email_outbox');c.execute('DELETE FROM whatsapp_outbox')
        # Generate fresh notifications under the requested review identities.
        for request in c.execute('SELECT * FROM requests').fetchall():server.hr.notify(c,request,server,'طلب تجريبي جاهز للمراجعة')
    (directory/'review-environment.json').write_text(json.dumps({'kind':'ehkam-store-review-v1','usernames':names},ensure_ascii=False,indent=2))
    return database


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True);parser.add_argument('--accounts-file')
    parser.add_argument('--serve-existing',action='store_true');parser.add_argument('--port',type=int,default=8877)
    args=parser.parse_args()
    if args.serve_existing:
        directory=Path(args.directory).resolve();marker=directory/'review-environment.json'
        if not marker.is_file() or json.loads(marker.read_text()).get('kind')!='ehkam-store-review-v1':parser.error('A marked isolated review environment is required')
        demo.prepare_demo(directory)
        # Never start provider workers or send real OTP from the local review server.
        for name in ('AUTHEVO_API_KEY','RESEND_API_KEY','RESEND_FROM_EMAIL','WHATSAPP_ACCESS_TOKEN','FIREBASE_PROJECT_ID'):
            os.environ.pop(name,None)
        http=server.ThreadingHTTPServer(('127.0.0.1',args.port),server.Handler)
        print(f'Isolated review server: http://127.0.0.1:{args.port}; no external notifications enabled.',flush=True)
        try:http.serve_forever()
        except KeyboardInterrupt:pass
        finally:http.server_close()
    else:
        if not args.accounts_file:parser.error('--accounts-file is required when creating review data')
        prepare(args.directory,json.loads(Path(args.accounts_file).read_text()))
        print('Review data created in the dedicated directory. No production data was opened. No notifications sent.')


if __name__=='__main__':main()
