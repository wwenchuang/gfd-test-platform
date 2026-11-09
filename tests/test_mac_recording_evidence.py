import importlib.util
import pathlib
import threading
import time
from unittest import mock
import pytest

spec=importlib.util.spec_from_file_location('mac_recording_runner',pathlib.Path(__file__).parents[1]/'mac-midscene-runner.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def request(id='r1',device='ecbfd645',step=''):
 return {'request_id':id,'session_id':'s1','step_id':step,'device_id':device,'sequence':0}

def test_valid_android_request_uses_explicit_device_and_protocol():
 calls=[]
 def post(method,path,payload,timeout=0):
  calls.append((method,path,dict(payload)));return {'ok':True}
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True) as check, \
      mock.patch.object(r,'capture_screen_png',return_value=b'\x89PNG\r\n\x1a\nabc') as png, \
      mock.patch.object(r,'capture_recording_ui_xml',return_value='<hierarchy/>') as xml, \
      mock.patch.object(r,'http_json',side_effect=post):
  assert r.capture_recording_evidence(request(),[{'device_id':'ecbfd645','status':'online','resolution':'1080x2412'}],threading.Event())
 assert check.call_args.args[:2]==('adb','ecbfd645')
 assert png.call_args.args==('adb','ecbfd645') and xml.call_args.args==('adb','ecbfd645')
 assert calls[0][2]['phase']=='capturing' and calls[0][1]=='/api/runner/recording-evidence'
 assert calls[1][2]['content_base64'] and calls[1][2]['ui_xml']=='<hierarchy/>'
 assert calls[1][2]['coordinate_width']==1080 and calls[1][2]['coordinate_height']==2412


def test_rejects_unavailable_target_without_fallback_and_reports_failure():
 posts=[]
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=False), \
      mock.patch.object(r,'capture_screen_png') as capture, \
      mock.patch.object(r,'http_json',side_effect=lambda m,p,b,timeout=0:(posts.append(dict(b)) or {'ok':True})):
  assert r.capture_recording_evidence(request(),[{'device_id':'ecbfd645','status':'online'}],threading.Event())
 assert not capture.called and posts[-1]['device_id']=='ecbfd645' and posts[-1].get('error')


def test_xml_failure_preserves_png_with_explicit_error():
 posts=[]
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True), \
      mock.patch.object(r,'capture_screen_png',return_value=b'\x89PNG\r\n\x1a\nabc'), \
      mock.patch.object(r,'capture_recording_ui_xml',side_effect=RuntimeError('private detail')), \
      mock.patch.object(r,'http_json',side_effect=lambda m,p,b,timeout=0:(posts.append(dict(b)) or {'ok':True})):
  assert r.capture_recording_evidence(request(id='step1',step='step1'),[{'device_id':'ecbfd645','status':'online'}],threading.Event())
 assert posts[-1]['content_base64'] and posts[-1]['ui_xml_error'] and 'private detail' not in str(posts)


def test_nonblocking_serial_dedup_and_stop():
 entered=threading.Event();release=threading.Event();calls=[]
 def capture(req,devices,stopped):
  calls.append(req['request_id']);entered.set();release.wait(2);return True
 with mock.patch.object(r,'RUNNER_PLATFORM','android'),mock.patch.object(r,'capture_recording_evidence',side_effect=capture):
  d=r.RecordingEvidenceDispatcher()
  started=time.monotonic();d.submit({'recording_evidence_requests':[request()]},[{'device_id':'ecbfd645'}]);assert time.monotonic()-started<.2
  assert entered.wait(1)
  d.submit({'recording_evidence_requests':[request(),request('r2')]},[{'device_id':'ecbfd645'}])
  d.close(wait=0);release.set();time.sleep(.1)
  assert calls==['r1']


def test_unacknowledged_delivery_can_retry():
 count=[];done=threading.Event()
 def capture(req,devices,stopped):
  count.append(req['request_id']);done.set();return len(count)>1
 with mock.patch.object(r,'RUNNER_PLATFORM','android'),mock.patch.object(r,'capture_recording_evidence',side_effect=capture):
  d=r.RecordingEvidenceDispatcher();payload={'recording_evidence_requests':[request()]}
  d.submit(payload,[{'device_id':'ecbfd645'}]);assert done.wait(1)
  for _ in range(100):
   if not d.has_pending('r1'):break
   time.sleep(.01)
  done.clear();d.submit(payload,[{'device_id':'ecbfd645'}]);assert done.wait(1)
  d.close();assert count==['r1','r1']


def test_ios_does_not_start_android_evidence():
 with mock.patch.object(r,'RUNNER_PLATFORM','ios'),mock.patch.object(r,'capture_recording_evidence') as capture:
  d=r.RecordingEvidenceDispatcher();d.submit({'recording_evidence_requests':[request()]},[{'device_id':'ecbfd645'}]);time.sleep(.05);d.close()
  capture.assert_not_called()


def test_capture_retries_phase_with_same_request_when_first_ack_is_lost():
 posts=[]
 def post(m,p,body,timeout=0):
  posts.append(dict(body))
  if len(posts)==1:raise TimeoutError('lost ack')
  return {'ok':True}
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True), \
      mock.patch.object(r,'capture_screen_png',return_value=b'\x89PNG\r\n\x1a\nabc'), \
      mock.patch.object(r,'capture_recording_ui_xml',return_value='<hierarchy/>'), \
      mock.patch.object(r,'http_json',side_effect=post):
  assert r.capture_recording_evidence(request(),[{'device_id':'ecbfd645'}],threading.Event())
 assert len(posts)==3 and posts[0]==posts[1] and posts[0]['phase']=='capturing'


def test_invalid_step_identifier_and_unlisted_device_are_never_captured():
 with mock.patch.object(r,'recording_adb_path') as resolve, mock.patch.object(r,'http_json',return_value={'ok':True}) as post:
  assert not r.capture_recording_evidence(request(id='r1',step='step2'),[{'device_id':'ecbfd645'}],threading.Event())
  assert r.capture_recording_evidence(request(device='other'),[{'device_id':'ecbfd645'}],threading.Event())
 assert not resolve.called and post.call_args.args[2]['error']


def test_recording_device_check_requires_exact_adb_device_state():
 for output,code,expected in [(b'device\n',0,True),(b'offline\n',0,False),(b'device\n',1,False),(b'other\n',0,False)]:
  with mock.patch.object(r.subprocess,'run',return_value=type('P',(),{'stdout':output,'returncode':code})()) as run:
   assert r.recording_device_ready('adb','ecbfd645') is expected
   assert run.call_args.args[0]==['adb','-s','ecbfd645','get-state']
 with mock.patch.object(r.subprocess,'run') as run:
  assert not r.recording_device_ready('adb','../unsafe')
  assert not run.called


def test_stop_during_screenshot_does_not_start_xml_or_upload():
 stop=threading.Event();posts=[]
 def snapshot(*args,**kwargs):stop.set();return b'\x89PNG\r\n\x1a\nabc'
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True), \
      mock.patch.object(r,'capture_screen_png',side_effect=snapshot), \
      mock.patch.object(r,'capture_recording_ui_xml') as xml, \
      mock.patch.object(r,'http_json',side_effect=lambda m,p,b,timeout=0:(posts.append(dict(b)) or {'ok':True})):
  assert not r.capture_recording_evidence(request(),[{'device_id':'ecbfd645'}],stop)
 assert len(posts)==1 and posts[0]['phase']=='capturing' and not xml.called


def test_workers_queue_limit_and_no_duplicate_inflight_request():
 entered=threading.Event();release=threading.Event();calls=[]
 def capture(req,devices,stop):
  calls.append(req['request_id']);entered.set();release.wait(2);return True
 with mock.patch.object(r,'RUNNER_PLATFORM','android'),mock.patch.object(r,'capture_recording_evidence',side_effect=capture):
  d=r.RecordingEvidenceDispatcher();devices=[{'device_id':'ecbfd645'}]
  d.submit({'recording_evidence_requests':[request()]},devices);assert entered.wait(1)
  d.submit({'recording_evidence_requests':[request()]+[request('r'+str(i)) for i in range(2,20)]},devices)
  assert d.queue.qsize()<=10 and calls==['r1']
  d.close(wait=0);release.set()


def test_final_payload_survives_multiple_lost_posts_without_recapture():
 posts=[];shots=[]
 def post(m,p,body,timeout=0):
  posts.append(dict(body))
  if body.get('content_base64') and sum('content_base64' in item for item in posts)<5:raise TimeoutError('network down')
  return {'ok':True}
 with mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True), \
      mock.patch.object(r,'capture_screen_png',side_effect=lambda *args,**kwargs:(shots.append(1) or b'\x89PNG\r\n\x1a\nabc')), \
      mock.patch.object(r,'capture_recording_ui_xml',return_value='<hierarchy/>'), \
      mock.patch.object(r,'http_json',side_effect=post):
  assert r.capture_recording_evidence(request(),[{'device_id':'ecbfd645'}],threading.Event())
 assert shots==[1]
 final=[p for p in posts if 'content_base64' in p]
 assert len(final)==5 and all(p==final[0] for p in final)


def test_post_never_retries_after_stop_or_deadline():
 stopped=threading.Event();stopped.set()
 with mock.patch.object(r,'http_json') as post:
  assert not r._recording_post({'request_id':'r1'},stopped,time.monotonic()+10)
  assert not r._recording_post({'request_id':'r1'},threading.Event(),time.monotonic()-1)
 assert not post.called


def test_adb_subprocess_terminates_when_worker_stops():
 stop=threading.Event()
 class Process:
  returncode=0
  calls=0
  terminated=False
  def communicate(self,timeout):
   self.calls+=1
   if self.calls==1:
    stop.set();raise r.subprocess.TimeoutExpired(['adb'],timeout)
   return b'',b''
  def terminate(self):self.terminated=True
  def kill(self):self.terminated=True
 proc=Process()
 with mock.patch.object(r.subprocess,'Popen',return_value=proc):
  with pytest.raises(RuntimeError,match='取消'):
   r._run_recording_adb(['adb','-s','ecbfd645','get-state'],stop,8)
 assert proc.terminated


def test_http_slow_response_cannot_outlive_hard_deadline_or_be_marked_complete():
 stopped=threading.Event()
 with mock.patch.object(r,'http_json',side_effect=lambda *a,**k:(time.sleep(.25) or {'ok':True})):
  start=time.monotonic();assert not r._recording_post({'request_id':'r1'},stopped,start+.05)
  assert time.monotonic()-start < .2
  assert stopped.is_set()


def test_adb_operations_share_remaining_request_budget():
 clock=[100.0];timeouts=[];posts=[]
 def post(m,p,body,timeout=0):
  posts.append(dict(body));clock[0]=189.0;return {'ok':True}
 def adb_run(command,stopped,timeout):
  timeouts.append(timeout);clock[0]=191.0
  return type('P',(),{'returncode':0,'stdout':b'\x89PNG\r\n\x1a\nabc'})()
 with mock.patch.object(r.time,'monotonic',side_effect=lambda:clock[0]), \
      mock.patch.object(r,'recording_adb_path',return_value='adb'), \
      mock.patch.object(r,'recording_device_ready',return_value=True), \
      mock.patch.object(r,'_run_recording_adb',side_effect=adb_run), \
      mock.patch.object(r,'http_json',side_effect=post), \
      mock.patch.object(r,'capture_recording_ui_xml') as xml:
  assert not r.capture_recording_evidence(request(),[{'device_id':'ecbfd645'}],threading.Event())
 assert timeouts==[1.0] and len(posts)==1
 assert not xml.called


def test_xml_capture_uses_unique_requested_path_and_cleans_it():
 commands=[];remote='/sdcard/midscene-recording-123.xml'
 def run(command,**kwargs):
  commands.append(command)
  return type('P',(),{'returncode':0,'stdout':b'<hierarchy/>'})()
 assert r.capture_recording_ui_xml('adb','ecbfd645',run=run,remote=remote)=='<hierarchy/>'
 assert commands[0][-1]==remote and commands[1][-1]==remote
 assert commands[2][-3:]==['rm','-f',remote]


def test_recording_adb_path_does_not_start_unbounded_device_probe():
 with mock.patch.object(r,'_ADB_BIN_CACHE',None),mock.patch.object(r,'adb_candidates',return_value=['/fake/adb']), \
      mock.patch.object(r,'resolve_adb_with_devices') as detect:
  assert r.recording_adb_path()=='/fake/adb'
  assert not detect.called
