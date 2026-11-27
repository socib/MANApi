import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent/'manapi'/'scripts'))
import pythonvideoFunctions as functions
ns = vars(functions)

class Tests(unittest.TestCase):
    def setUp(self):
        mock = patch.object(functions, 'toolAddLog')
        mock.start()
        self.addCleanup(mock.stop)

    def test_cleanup_only_verified_unchanged_files(self):
        with tempfile.TemporaryDirectory() as d:
            a,b,c = [Path(d)/x for x in ('video','product','unrelated')]
            for f in (a,b,c): f.write_bytes(b'valid')
            batch=[(str(f),ns['file_signature'](f)) for f in (a,b)]
            b.write_bytes(b'changed')
            self.assertEqual(ns['clean_raspi'](*(['']*7),verified_batches=[batch]),0)
            self.assertTrue(a.exists())
            batch=[(str(f),ns['file_signature'](f)) for f in (a,b)]
            self.assertEqual(ns['clean_raspi'](*(['']*7),verified_batches=[batch]),2)
            self.assertTrue(c.exists())
    def test_upload_failure_and_remote_difference_retain_files(self):
        import subprocess
        for result in ('failure','difference','success'):
            with tempfile.TemporaryDirectory() as d:
                folder=Path(d)/'images/site/station/site/c01';folder.mkdir(parents=True)
                product=folder/'product.png';product.write_bytes(b'valid')
                video=Path(d)/'video.h264';video.write_bytes(b'video')
                batch=[(str(f),ns['file_signature'](f)) for f in (video,product)]
                responses = ([subprocess.CalledProcessError(1,'rsync')] if result=='failure' else
                             [SimpleNamespace(stdout=''),SimpleNamespace(stdout='>f changed' if result=='difference' else '')])
                with patch('subprocess.run',side_effect=responses):
                    ok=ns['update_folders_socib'](d,d,'/remote','host',22,'user','site','station','01','','','','night',[batch])
                self.assertEqual(ok,result=='success')
                self.assertEqual(video.exists(),result!='success')
                self.assertEqual(product.exists(),result!='success')
    def test_alarm_threshold_reminder_rearm_and_failed_send(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json';sent=[]
            def run(percent,now,success=True):
                with patch.dict('sys.modules', {'psutil': SimpleNamespace(disk_usage=lambda path: SimpleNamespace(percent=percent,free=20,total=100))}):
                    ns['check_disk_usage']([d,d],state,lambda text: sent.append(text) or success,now=now)
            run(79.9,1);self.assertEqual(len(sent),0)
            run(80,2);run(85,3);self.assertEqual(len(sent),1)
            run(85,86402);self.assertEqual(len(sent),2)
            run(74,86403);run(80,86404,False);run(80,86405)
            self.assertEqual(len(sent),4)
    def test_empty_file_rejected(self):
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(ValueError): ns['file_signature'](f.name)

if __name__=='__main__': unittest.main()
