"""Isolated, local-only EHKAM demo: python3 demo.py (no third-party packages)."""
import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import webbrowser

import server
import hr

ACCOUNTS = [
    ('employee_demo', 'DemoEmp@2026', 'employee', 2, 'الموظف'),
    ('supervisor_demo', 'DemoSuper@2026', 'manager', 1, 'المشرف'),
    ('director_demo', 'DemoDirector@2026', 'admin', None, 'المدير'),
    ('admin_demo', 'DemoAdmin@2026', 'admin', None, 'الإدارة'),
]


def seed_fixture(c):
 """Public test-only fixture; never invoked by normal production startup."""
 c.execute('INSERT INTO companies(name,name_en,contract_end,city,address,latitude,longitude,geofence_radius) VALUES(?,?,?,?,?,?,?,?)',('شركة الأفق','Al Ofoq Co','2027-12-31','الرياض','موقع تجريبي',24.7136,46.6753,100))
 co=c.execute('SELECT last_insert_rowid()').fetchone()[0]
 staff=[('أحمد محمد','Ahmed Mohammed','A-001','مدير موارد بشرية','الموارد البشرية',None,5200,1000,400,0,'2026-01-01','2027-01-01',0),('سارة خالد','Sara Khalid','A-002','أخصائي موارد بشرية','الموارد البشرية',1,4000,0,0,25,'2026-01-01','2027-01-01',0),('خالد علي','Khalid Ali','A-003','محاسب','المالية',1,3500,800,200,8,'2026-05-01','2027-05-01',0)]
 for name,en,no,job,dep,manager,basic,housing,trans,carry,start,end,arch in staff:
  c.execute('''INSERT INTO employees(company_id,name,name_en,employee_no,job,department,manager_id,basic,housing,transport,carryover,hire_date,contract_start,contract_end,archived) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(co,name,en,no,job,dep,manager,basic,housing,trans,carry,start,start,end,arch))
 for username,pw,role,eid in [('admin','Admin@123','admin',None),('manager','Manager@123','manager',1),('employee','Employee@123','employee',2)]:
  c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES(?,?,?,?,?)',(username,server.hashpw(pw),role,eid,co if role!='admin' else None))



def prepare_demo(directory):
    """Never open the application database; reuse only a marked demo directory."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / 'ehkam-demo.json'
    database = directory / 'demo.sqlite3'
    if directory == server.BASE.resolve() or database.resolve() == (server.BASE / 'ehkam_data.sqlite3').resolve():
        raise RuntimeError('Choose a separate demo directory')
    if database.exists():
        if not marker.exists() or json.loads(marker.read_text()).get('kind') != 'ehkam-isolated-demo-v1':
            raise RuntimeError('Refusing to open an existing unmarked database')
        with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as c:
            if c.execute('SELECT value FROM demo_environment WHERE id=1').fetchone() != ('ehkam-isolated-demo-v1',):
                raise RuntimeError('Database is not an isolated demo')
        server.DB = database
        server.init()
        return database
    # The path above is fixed to demo.sqlite3 in a separate directory.
    server.DB = database
    server.init(seeder=seed_fixture,allow_create=True)
    today = datetime.date.today()
    start = today.replace(year=today.year - 1, day=1)
    end = today.replace(year=today.year + 2, day=1)
    next_day = today + datetime.timedelta(days=7)
    while next_day.weekday() == 4:
        next_day += datetime.timedelta(days=1)
    with server.conn() as c:
        c.execute("CREATE TABLE demo_environment(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO demo_environment VALUES(1,'ehkam-isolated-demo-v1')")
        # Only the newly created, isolated demo database is populated here.
        c.execute('DELETE FROM users')
        c.execute("UPDATE companies SET name='شركة إحكام التجريبية',name_en='EHKAM Demo',city='موقع اختباري',contract_end=?", (end.isoformat(),))
        c.execute('UPDATE employees SET contract_start=?,hire_date=?,contract_end=?,carryover=30', (start.isoformat(),start.isoformat(),end.isoformat()))
        c.execute("UPDATE employees SET name='سارة — موظفة تجريبية',name_en='Sara Demo' WHERE id=2")
        c.execute("UPDATE employees SET name='أحمد — مشرف تجريبي',name_en='Ahmed Demo' WHERE id=1")
        for username, password, role, eid, _ in ACCOUNTS:
            c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES(?,?,?,?,?)',
                      (username, server.hashpw(password), role, eid, 1 if eid else None))
        c.execute('UPDATE contracts SET start_date=?,end_date=?,entitlement=21,opening=30 WHERE closed_at IS NULL',(start.isoformat(),end.isoformat()))
        c.execute("UPDATE users SET display_name=COALESCE((SELECT name FROM employees WHERE id=users.employee_id),username)")
        ids = {r['username']: r['id'] for r in c.execute('SELECT id,username FROM users')}
        stamp = server.now()
        for typ, notes, amount, installment in [
            ('annual_leave','طلب إجازة تجريبي جاهز لتجربة الموافقة أو الرفض',0,0),
            ('advance','سلفة تجريبية — المبلغ 500 ريال والقسط 100 ريال',500,100),
            ('open','طلب مفتوح تجريبي مع مرفق PDF',0,0),
            ('hr_contact','مراسلة تجريبية خاصة بإدارة الموارد البشرية',0,0),
            ('delegation','انتداب تجريبي ينتظر قبول الموظف أو رفضه',0,0),
        ]:
            dated = typ in ('annual_leave','delegation')
            cur=c.execute('''INSERT INTO requests(company_id,employee_id,type,start_date,end_date,days,amount,installment,notes,created_by,approver_id,created_at,updated_at)
                             VALUES(1,2,?,?,?,?,?,?,?,?,1,?,?)''',
                          (typ,next_day.isoformat() if dated else None,next_day.isoformat() if dated else None,
                           1 if dated else 0,amount,installment,notes,ids['supervisor_demo' if typ=='delegation' else 'employee_demo'],stamp,stamp))
            c.execute('UPDATE requests SET contract_id=(SELECT id FROM contracts WHERE employee_id=2 AND closed_at IS NULL),details=? WHERE id=?',(json.dumps({'title':notes[:80]}),cur.lastrowid))
            hr.notify(c,c.execute('SELECT * FROM requests WHERE id=?',(cur.lastrowid,)).fetchone(),server,'طلب تجريبي جاهز للمراجعة')
            if typ=='open':
                c.execute('INSERT INTO request_attachments(request_id,name,mime,content) VALUES(?,?,?,?)',
                          (cur.lastrowid,'demo.pdf','application/pdf',sample_pdf()))
        yesterday = today-datetime.timedelta(days=1)
        c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,?,'09:00','17:00','demo_fixture')",(yesterday.isoformat(),))
    marker.write_text(json.dumps({'kind':'ehkam-isolated-demo-v1'},indent=2))
    return database


def sample_pdf():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream=b'BT /F1 16 Tf 30 100 Td (EHKAM DEMO ATTACHMENT) Tj ET'
    objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    data=b'%PDF-1.4\n';offsets=[0]
    for i, obj in enumerate(objects,1):
        offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:data+=f'{offset:010d} 00000 n \n'.encode()
    return data+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='Dedicated demo directory. Default: create a fresh temporary directory each run.')
    parser.add_argument('--port',type=int,default=8877)
    parser.add_argument('--open-browser',action='store_true',help='Open the local browser when running on your own computer')
    args=parser.parse_args()
    # The demo never sends real OTP/email through inherited provider bindings.
    for name in ('AUTHEVO_API_KEY','RESEND_API_KEY','RESEND_FROM_EMAIL'):
        os.environ.pop(name,None)
    database=prepare_demo(args.data_dir or tempfile.mkdtemp(prefix='ehkam-demo-'))
    http=server.ThreadingHTTPServer(('127.0.0.1',args.port),server.Handler)
    print(f'DEMO ONLY | database: {database}',flush=True)
    print(f'Local demo listening on port {args.port}.',flush=True)
    for username,password,role,eid,label in ACCOUNTS:
        print(f'{label}: {username} / {password} | account_role={role}',flush=True)
    if args.open_browser:webbrowser.open(f'http://127.0.0.1:{args.port}')
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()


if __name__=='__main__':
    main()
