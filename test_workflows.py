import importlib.util
import sys
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch, MagicMock
import unittest
scripts=Path(__file__).parent/'manapi'/'scripts'
sys.path.insert(0,str(scripts))
import pythonvideoFunctions as f

def load(name):
 spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py'))
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

class WorkflowTests(unittest.TestCase):
 def test_night_passes_batches(self):
  with patch.object(f,'readInfo',return_value=('x',)*19),patch.object(f,'acquisition_lock',return_value=nullcontext()),patch.object(f,'night_products',return_value=['batch']),patch.object(f,'update_folders_socib',return_value=True) as upload:
   self.assertEqual(load('pythonNight').main(),0)
   self.assertEqual(upload.call_args.kwargs['completed_batches'],['batch'])
 def test_recording_failure_stops_products(self):
  with patch.object(f,'readInfo',return_value=('x',)*19),patch.object(f,'acquisition_lock',return_value=nullcontext()),patch.object(f,'detect'),patch.object(f,'picam_video',return_value=False),patch.object(f,'daytime_products') as products:
   self.assertEqual(load('pythonDaytime').main(),1);products.assert_not_called()
 def test_missing_config(self):
  with patch.object(f,'readInfo',side_effect=ValueError('missing')):
   self.assertEqual(load('pythonDaytime').main(),1)
 def test_notify_does_not_process(self):
  with patch.object(f,'readInfo',return_value=('x',)*19),patch.object(f,'toolAddLog') as log,patch.object(f,'night_products') as night,patch.object(sys,'argv',['pythonNotify.py','ERROR','message']):
   self.assertEqual(load('pythonNotify').main(),0);log.assert_called_once();night.assert_not_called()
 def test_shutdown_despite_email_failure(self):
  import tempfile
  with tempfile.TemporaryDirectory() as root,patch.object(f,'toolSendMail',side_effect=RuntimeError('SMTP')),patch.object(f,'toolAddLog'),patch.object(f,'toollogsize'),patch('subprocess.check_output',return_value="temp=87.0'C"),patch('subprocess.run',return_value=MagicMock(stdout='ok')) as shutdown,patch('os.sync'):
   f.measure_cpu_temp(root,'site','station','01','','','')
   self.assertTrue(shutdown.called)
 def test_video_validation(self):
  import types
  for count,valid in [(0,False),(1,False),(10,True)]:
   capture=MagicMock();frame=types.SimpleNamespace(shape=(2,3,3))
   capture.read.side_effect=[(True,frame)]*count+[(False,None)]
   with patch.dict(sys.modules,{'cv2':types.SimpleNamespace(VideoCapture=lambda p:capture)}):
    if valid:self.assertEqual(f.validate_video('file',2,5,3,2),10)
    else:
     with self.assertRaises(ValueError):f.validate_video('file',2,5,3,2)
   capture.release.assert_called_once()

if __name__=='__main__':unittest.main()
