"""Reset only temporary production-shaped databases; never the app database."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import production_db
import reset_business_data as reset
import server

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.database=self.root/'data.sqlite3';self.backup=self.root/'before.sqlite3'
        self.password='PrivateTestOnly@2026';self.info=production_db.initialize(self.database,'owner',self.password,'الإدارة');self.identity=self.info['database_id']
        self.environment=patch.dict(os.environ,{'EHKAM_START_MODE':'storage-setup','RAILWAY_VOLUME_MOUNT_PATH':str(self.root),'EHKAM_DB_PATH':str(self.database),'EHKAM_DB_ID':self.identity});self.environment.start()
        with sqlite3.connect(self.database) as c:
            c.execute("INSERT INTO companies(id,name) VALUES(1,'شركة اختبار')")
            c.execute("INSERT INTO employees(id,company_id,name,employee_no) VALUES(1,1,'موظف اختبار','EH-0001')")
            c.execute("INSERT INTO users(id,username,password_hash,role,employee_id,company_id) VALUES(2,'staff','test-hash','employee',1,1)")
            c.execute("INSERT INTO requests(id,company_id,employee_id,type,created_by,created_at,updated_at) VALUES(1,1,1,'open',2,'2026-10-28','2026-10-28')")
            c.execute("INSERT INTO request_attachments(request_id,name,mime,content) VALUES(1,'file.pdf','application/pdf',?)",(b'%PDF test',))
        with sqlite3.connect(self.database) as c:self.admin_hash=c.execute("SELECT password_hash FROM users WHERE role='admin'").fetchone()[0]
    def tearDown(self):self.environment.stop();self.temp.cleanup()
    def run_reset(self,**kwargs):return reset.reset_data(self.database,self.identity,'owner',kwargs.get('password',self.password),kwargs.get('confirmation','CLEAR BUSINESS DATA '+self.identity),self.backup)
    def test_backup_restore_and_preserve_admin_identity(self):
        out=self.run_reset();self.assertEqual(out['database_id'],self.identity)
        self.assertEqual((out['counts']['companies'],out['counts']['employees'],out['counts']['requests']),(0,0,0))
        with sqlite3.connect(self.database) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT password_hash FROM users').fetchone()[0],self.admin_hash)
            self.assertEqual(c.execute('SELECT value FROM employee_number_sequence').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='business_data_reset'").fetchone()[0],1)
        restored=self.root/'restored.sqlite3';production_db.copy_database(self.backup,restored,self.identity)
        with sqlite3.connect(restored) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM employees').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT content FROM request_attachments').fetchone()[0],b'%PDF test')
    def test_wrong_password_or_confirmation_does_not_delete(self):
        for values in [{'password':'incorrect'},{'confirmation':'no'}]:
            with self.assertRaises(ValueError):self.run_reset(**values)
            self.assertFalse(self.backup.exists())
            self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts']['employees'],1)
    def test_app_mode_refuses_reset(self):
        with patch.dict(os.environ,{'EHKAM_START_MODE':'app'}),self.assertRaises(ValueError):self.run_reset()
        self.assertFalse(self.backup.exists())
    def test_existing_backup_is_not_overwritten(self):
        self.backup.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.run_reset()
        self.assertEqual(self.backup.read_bytes(),b'keep')
        self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts']['companies'],1)
    def test_unknown_table_or_identity_refuses_reset(self):
        with patch.dict(os.environ,{'EHKAM_DB_ID':'wrong'}),self.assertRaises(ValueError):self.run_reset()
        with sqlite3.connect(self.database) as c:c.execute('CREATE TABLE future_records(id INTEGER)')
        with self.assertRaises(ValueError):self.run_reset()
        self.assertFalse(self.backup.exists())

    def test_backup_failure_or_post_delete_failure_rolls_back(self):
        with patch('reset_business_data.production_db.copy_database',side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):self.run_reset()
        self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts']['employees'],1)
        with patch('reset_business_data.server.record',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.run_reset()
        self.assertTrue(self.backup.exists())
        self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts']['employees'],1)

    def test_cli_refuses_noninteractive_credentials(self):
        result=subprocess.run([sys.executable,str(Path(reset.__file__)),'--database',str(self.database),'--expected-id',self.identity],input='do not read credentials',text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('private interactive terminal',result.stderr)
        self.assertNotIn('Administrator password',result.stdout)
        self.assertEqual(production_db.inspect_database(self.database,self.identity)['counts']['companies'],1)
