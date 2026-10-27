"""No Railway operations: tests use private temporary folders to simulate volumes."""
import base64
import contextlib
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import shutil
import urllib.error
import urllib.request

import production_db
import server


class ProductionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ehkam-production-test-')
        self.root=Path(self.temp.name)
        self.database=self.root/'volume'/'ehkam_data.sqlite3'
        self.password='IntegrationOnly!2026'
        self.identity=production_db.initialize(self.database,'owner_test',self.password,'مسؤول الاختبار')['database_id']
        self.original_db=server.DB

    def tearDown(self):
        server.DB=self.original_db
        self.temp.cleanup()

    def test_initialization_has_one_real_admin_and_no_demo_data(self):
        info=production_db.inspect_database(self.database,self.identity)
        self.assertEqual(info['counts'],{'users':1,'companies':0,'employees':0,'requests':0,'request_attachments':0,'employee_files':0})
        with production_db.readonly(self.database) as c:
            name,hashed,role=c.execute('SELECT username,password_hash,role FROM users').fetchone()
        self.assertEqual((name,role),('owner_test','admin'))
        self.assertNotEqual(hashed,self.password)
        self.assertTrue(server.verifypw(self.password,hashed))
        if os.name=='posix':self.assertEqual(self.database.stat().st_mode&0o777,0o600)

    def test_existing_file_and_symlink_are_never_overwritten(self):
        original=self.database.read_bytes()
        with self.assertRaises(FileExistsError):production_db.initialize(self.database,'owner_new','OtherSecret!2026','آخر')
        self.assertEqual(original,self.database.read_bytes())
        link=self.root/'existing-link.sqlite3'
        link.symlink_to(self.database)
        with self.assertRaises(FileExistsError):production_db.initialize(link,'owner_new','OtherSecret!2026','آخر')
        self.assertEqual(original,self.database.read_bytes())

    def test_missing_database_does_not_auto_reinitialize(self):
        retained=self.database.with_name('retained.sqlite3')
        self.database.rename(retained)
        server.DB=self.database
        with patch.dict(os.environ,{'EHKAM_REQUIRE_EXISTING_DB':'1','EHKAM_DB_ID':self.identity}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()
            with self.assertRaises(sqlite3.OperationalError):server.conn()
        self.assertFalse(self.database.exists())
        self.assertEqual(production_db.inspect_database(retained,self.identity)['database_id'],self.identity)

    def test_wrong_identity_or_volume_is_rejected_without_changes(self):
        original=self.database.read_bytes();server.DB=self.database
        with patch.dict(os.environ,{'EHKAM_REQUIRE_EXISTING_DB':'1','EHKAM_DB_ID':'wrong-identity'}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()
        with patch.dict(os.environ,{'RAILWAY_SERVICE_ID':'local-test','EHKAM_DB_ID':self.identity,'RAILWAY_VOLUME_MOUNT_PATH':str(self.root/'wrong-volume')}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()
        self.assertEqual(original,self.database.read_bytes())

    def test_production_marker_requires_identity_configuration(self):
        server.DB=self.database
        with patch.dict(os.environ,{'EHKAM_REQUIRE_EXISTING_DB':'1','EHKAM_DB_ID':''}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()

    def test_backup_includes_wal_and_restore_copy_never_overwrites(self):
        with contextlib.closing(sqlite3.connect(self.database)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO companies(name) VALUES('WAL sentinel')");writer.commit()
            snapshot=self.root/'snapshot.sqlite3'
            production_db.copy_database(self.database,snapshot,self.identity)
            with production_db.readonly(snapshot) as c:self.assertEqual(c.execute('SELECT name FROM companies').fetchone()[0],'WAL sentinel')
        restored=self.root/'restore-drill'/'restored.sqlite3'
        production_db.copy_database(snapshot,restored,self.identity)
        self.assertEqual(production_db.inspect_database(restored,self.identity)['counts']['companies'],1)
        original=self.database.read_bytes()
        with self.assertRaises(FileExistsError):production_db.copy_database(snapshot,self.database,self.identity)
        self.assertEqual(original,self.database.read_bytes())
        with self.assertRaises(ValueError):production_db.copy_database(snapshot,self.root/'wrong.sqlite3','wrong-identity')
        self.assertFalse((self.root/'wrong.sqlite3').exists())

    def test_restart_new_code_directory_and_restore_drill(self):
        code_a=self.root/'release-a';code_b=self.root/'release-b'
        runtime_files=['railway_start.py','server.py','hr.py','gps_location.py','i18n.js','app.js','hr-ui.js','style.css','index.html','ehkam-logo-full.png']
        for directory in (code_a,code_b):
            directory.mkdir()
            for filename in runtime_files:shutil.copyfile(Path(server.__file__).parent/filename,directory/filename)
        def launch(directory,database):
            port_socket=socket.socket();port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1];port_socket.close()
            env={**os.environ,'EHKAM_START_MODE':'app','RAILWAY_SERVICE_ID':'test-only','RAILWAY_VOLUME_MOUNT_PATH':str(database.parent),'EHKAM_REQUIRE_EXISTING_DB':'1','EHKAM_DB_PATH':str(database),'EHKAM_DB_ID':self.identity,'PORT':str(port)}
            process=subprocess.Popen([sys.executable,'-u','railway_start.py'],cwd=directory,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            base=f'http://127.0.0.1:{port}/api/'
            for attempt in range(60):
                if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
                try:
                    with urllib.request.urlopen(base+'health',timeout=1) as r:self.assertEqual(json.load(r)['status'],'ok')
                    return process,base
                except urllib.error.URLError:time.sleep(.05)
            process.terminate();process.wait(timeout=5);raise AssertionError('Test server did not start')
        def client(base):
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def call(path,body=None,binary=False):
                req=urllib.request.Request(base+path,json.dumps(body).encode() if body is not None else None,{'Content-Type':'application/json'} if body is not None else {})
                with opener.open(req,timeout=10) as response:return response.read() if binary else json.load(response)
            self.assertTrue(call('login',{'username':'owner_test','password':self.password,'account_role':'admin'})['ok'])
            return call
        process,base=launch(code_a,self.database)
        try:
            call=client(base)
            call('companies',{'name':'Persistence sentinel','latitude':30,'longitude':31,'geofence_radius':100})
            company=call('companies')[0]['id']
            employee=call('employees',{'company_id':company,'name':'Test employee','login_username':'employee_test','login_password':'EmployeeFixture!2026'})['id']
            request=call('requests',{'employee_id':employee,'type':'open','title':'Persistence request','notes':'durable marker','attachments':[{'name':'sample.pdf','mime':'application/pdf','data':base64.b64encode(b'%PDF-1.4\n%%EOF').decode()}]})['id']
            call('requests/'+str(request),{'status':'approved','note':'test decision'})
        finally:process.terminate();process.wait(timeout=5);process.stderr.close()
        snapshot=self.root/'pre-update.sqlite3';production_db.copy_database(self.database,snapshot,self.identity)
        before=production_db.inspect_database(self.database,self.identity)
        for directory in (code_a,code_b):
            process,base=launch(directory,self.database)
            try:
                call=client(base)
                self.assertEqual(call('companies')[0]['name'],'Persistence sentinel')
                row=call('requests/'+str(request));self.assertEqual((row['notes'],row['status']),('durable marker','approved'))
                self.assertEqual(call('attachments/'+str(row['attachments'][0]['id']),binary=True),b'%PDF-1.4\n%%EOF')
            finally:process.terminate();process.wait(timeout=5);process.stderr.close()
            self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts'],before['counts'])
        restored=self.root/'isolated-restore-volume'/'restored.sqlite3'
        production_db.copy_database(snapshot,restored,self.identity)
        source_hash=hashlib.sha256(self.database.read_bytes()).hexdigest()
        process,base=launch(code_b,restored)
        try:self.assertEqual(client(base)('requests/'+str(request))['status'],'approved')
        finally:process.terminate();process.wait(timeout=5);process.stderr.close()
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(),source_hash)


if __name__=='__main__':unittest.main()
