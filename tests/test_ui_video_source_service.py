"""Real decoder fixtures; AI behavior is explicitly mocked in separate tests."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class VideoSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from task_server.services.ui_video_source_service import VideoStore
        self.store = VideoStore(Path(self.temp.name))

    def test_stream_rejects_oversize_and_removes_partial(self):
        with self.assertRaisesRegex(ValueError, 'video_size'):
            self.store.upload({'user_id': 'one'}, 'com.example.app', 'x.mp4', io.BytesIO(b'12345'), 5, max_bytes=4)
        self.assertEqual(self.store.list('one', 'com.example.app'), [])
        self.assertEqual(list(Path(self.temp.name).glob('uvs_*')), [])

    def test_private_lookup_cancel_retry_and_recovery(self):
        doc = self.store.upload({'user_id': 'one'}, 'com.example.app', 'x.mov', io.BytesIO(b'1234'), 4)
        with self.assertRaises(KeyError): self.store.get(doc['source_id'], 'two', 'com.example.app')
        with self.assertRaises(KeyError): self.store.get(doc['source_id'], 'one', 'com.other.app')
        self.store.transition(doc['source_id'], {'queued'}, 'decoding')
        self.store.recover()
        self.assertEqual(self.store.get(doc['source_id'], 'one', 'com.example.app')['status'], 'interrupted')
        self.store.transition(doc['source_id'], {'interrupted'}, 'queued')
        self.store.cancel(doc['source_id'])
        self.assertEqual(self.store.get(doc['source_id'], 'one', 'com.example.app')['status'], 'cancelled')

    def test_retry_does_not_reuse_previous_analysis_title(self):
        doc=self.store.upload({'user_id':'one'},'com.example.app','x.mp4',io.BytesIO(b'1234'),4)
        self.store.transition(doc['source_id'],{'queued'},'review_needed',title='上一版业务名称')
        retry=self.store.retry(doc['source_id'],'one','com.example.app')
        self.assertNotIn('title',retry)
        self.store.transition(doc['source_id'],{'queued'},'ready',events=[])
        self.assertNotIn('title',self.store.get(doc['source_id'],'one','com.example.app'))

    def test_real_mp4_static_and_spoof(self):
        import shutil, subprocess
        from task_server.services.ui_video_source_service import decode_video
        ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
        if not Path(ffmpeg).exists(): self.skipTest('ffmpeg required for real fixture')
        video = Path(self.temp.name)/'fixture.mp4'
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x160:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
        out = Path(self.temp.name)/'frames';out.mkdir()
        result = decode_video(video, out, lambda: False)
        self.assertEqual(result['duration_ms'], 2000)
        self.assertEqual(result['coverage_end_ms'], 2000)
        self.assertTrue(result['static'])
        fake=Path(self.temp.name)/'fake.mp4';fake.write_bytes(b'not a video')
        with self.assertRaises(ValueError): decode_video(fake, out, lambda: False)

    def fixture(self, output, *args):
        import shutil,subprocess
        tool=shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
        subprocess.run([tool,'-v','error',*args,str(output)],check=True)
        return output

    def test_real_mov_transition_black_rotation_and_limits(self):
        from task_server.services.ui_video_source_service import decode_video
        from PIL import Image
        directory=Path(self.temp.name)
        movie=self.fixture(directory/'clip.mov','-f','lavfi','-i','color=c=red:s=96x160:d=1','-f','lavfi','-i','color=c=blue:s=96x160:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0','-c:v','libx264','-pix_fmt','yuv420p')
        result=decode_video(movie,directory/'moving',lambda:False)
        self.assertFalse(result['static']);self.assertFalse(result['black'])
        self.assertTrue(any(900<=f['timestamp_ms']<=1100 for f in result['frames']))
        rotated=self.fixture(directory/'rotated.mov','-display_rotation','90','-i',str(movie),'-c','copy')
        result=decode_video(rotated,directory/'rotation',lambda:False)
        with Image.open(directory/'rotation'/'frame-001.jpg') as image:self.assertGreater(image.width,image.height)
        black=self.fixture(directory/'black.mp4','-f','lavfi','-i','color=c=black:s=96x160:d=1','-c:v','libx264')
        self.assertTrue(decode_video(black,directory/'black',lambda:False)['black'])
        with patch('task_server.services.ui_video_source_service.MAX_DURATION_MS',500):
            with self.assertRaisesRegex(ValueError,'video_limits'):decode_video(movie,directory/'long',lambda:False)
        truncated=directory/'truncated.mp4';truncated.write_bytes(movie.read_bytes()[:100])
        with self.assertRaises(ValueError):decode_video(truncated,directory/'truncated',lambda:False)

    def test_short_native_animation_over_sixty_changed_frames_keeps_settled_context(self):
        from task_server.services.ui_video_source_service import decode_video
        directory=Path(self.temp.name)
        movie=self.fixture(directory/'animation.mp4','-f','lavfi','-i','testsrc2=s=96x160:r=60:d=2','-c:v','libx264','-pix_fmt','yuv420p')
        result=decode_video(movie,directory/'animation',lambda:False)
        self.assertFalse(result['static'])
        self.assertLessEqual(len(result['frames']),5)
        self.assertEqual(result['coverage_end_ms'],2000)
        self.assertGreater(result['frames'][-1]['timestamp_ms'],1850)

    def test_long_continuous_motion_samples_entire_timeline_with_bounded_frames(self):
        from task_server.services.ui_video_source_service import decode_video
        directory=Path(self.temp.name)
        for duration in (31,90):
            with self.subTest(duration=duration):
                movie=self.fixture(directory/f'scroll-{duration}.mp4','-f','lavfi','-i',f'testsrc2=s=96x160:r=30:d={duration}','-c:v','libx264','-pix_fmt','yuv420p')
                result=decode_video(movie,directory/f'scroll-{duration}',lambda:False)
                times=[frame['timestamp_ms'] for frame in result['frames']]
                self.assertLessEqual(len(times),60)
                self.assertEqual(times[0],0)
                self.assertGreater(times[-1],duration*1000-100)
                self.assertLessEqual(max(b-a for a,b in zip(times,times[1:])),3000)
                self.assertFalse(result['static'])

    def test_portrait_evidence_keeps_readable_control_and_record_text(self):
        from task_server.services.ui_video_source_service import decode_video
        from PIL import Image
        directory=Path(self.temp.name)
        movie=self.fixture(directory/'portrait.mp4','-f','lavfi','-i','testsrc2=s=580x1280:r=2:d=1','-c:v','libx264','-pix_fmt','yuv420p')
        result=decode_video(movie,directory/'portrait',lambda:False)
        for frame in result['frames']:
            with Image.open(directory/'portrait'/(frame['frame_id']+'.jpg')) as image:
                self.assertEqual(image.size,(580,1280))

    def test_real_ten_bit_hevc_uses_identical_scan_and_extraction_pixels(self):
        from task_server.services.ui_video_source_service import decode_video
        directory=Path(self.temp.name)
        movie=self.fixture(directory/'ten-bit.mp4','-f','lavfi','-i','testsrc2=s=96x160:r=30:d=7','-c:v','libx265','-x265-params','pools=1:frame-threads=1:log-level=error','-pix_fmt','yuv420p10le')
        result=decode_video(movie,directory/'ten-bit',lambda:False)
        times=[frame['timestamp_ms'] for frame in result['frames']]
        self.assertEqual(result['coverage_end_ms'],7000)
        self.assertGreater(times[-1],6850)
        self.assertTrue(any(2500<=time<=3500 for time in times))
        self.assertLessEqual(len(times),60)

    def test_rapid_transitions_over_frame_budget_require_split(self):
        from task_server.services.ui_video_source_service import decode_video
        from PIL import Image
        directory=Path(self.temp.name)
        for i in range(64):Image.new('RGB',(96,160),['black','white'][i%2]).save(directory/f'input-{i:03d}.png')
        movie=self.fixture(directory/'fast.mp4','-framerate','5','-i',str(directory/'input-%03d.png'),'-c:v','libx264','-pix_fmt','yuv420p')
        with self.assertRaisesRegex(ValueError,'video_split_required'):decode_video(movie,directory/'fast',lambda:False)

    def test_equal_luminance_rapid_spatial_cuts_require_split(self):
        from task_server.services.ui_video_source_service import decode_video
        from PIL import Image,ImageDraw
        directory=Path(self.temp.name)
        for index in range(64):
            image=Image.new('RGB',(96,160),'black')
            ImageDraw.Draw(image).rectangle((0,0,47,159) if index%2 else (0,0,95,79),fill='white')
            image.save(directory/f'spatial-{index:03d}.png')
        movie=self.fixture(directory/'spatial.mp4','-framerate','5','-i',str(directory/'spatial-%03d.png'),'-c:v','libx264','-pix_fmt','yuv420p')
        with self.assertRaisesRegex(ValueError,'video_split_required'):
            decode_video(movie,directory/'spatial',lambda:False)

    def test_cancel_before_decoder_launch(self):
        from task_server.services.ui_video_source_service import decode_video
        with self.assertRaisesRegex(ValueError, 'video_cancelled'):
            decode_video(Path('/unused.mp4'), Path(self.temp.name), lambda: True)

if __name__ == '__main__': unittest.main()

class VideoRetryAdmissionTests(unittest.TestCase):
    def test_retry_reuses_active_queue_and_disk_admission_atomically(self):
        import threading
        from task_server.services.ui_video_source_service import VideoStore
        with tempfile.TemporaryDirectory() as directory:
            store=VideoStore(Path(directory)); docs=[]
            for i in range(6):
                doc=store.upload({'user_id':'one'},'com.example.app','x.mp4',io.BytesIO(b'video'),5)
                store.transition(doc['source_id'],{'queued'},'failed');docs.append(doc)
            for doc in docs[:3]:store.retry(doc['source_id'],'one','com.example.app')
            barrier=threading.Barrier(2);outcomes=[]
            def retry(doc):
                barrier.wait()
                try:store.retry(doc['source_id'],'one','com.example.app');outcomes.append('queued')
                except ValueError as error:outcomes.append(str(error))
            threads=[threading.Thread(target=retry,args=(doc,)) for doc in docs[3:5]]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            self.assertCountEqual(outcomes,['queued','video_capacity'])
            self.assertEqual(sum(d['status']=='queued' for d in store.list('one','com.example.app')),4)
            with self.assertRaisesRegex(ValueError,'video_capacity'):store.retry(docs[5]['source_id'],'one','com.example.app')
            for doc in docs[:3]:store.cancel(doc['source_id'])
            with patch.object(store,'_disk_bytes',return_value=2*1024**3-1):
                with self.assertRaisesRegex(ValueError,'video_capacity'):store.retry(docs[5]['source_id'],'one','com.example.app')
            self.assertEqual(store.get(docs[5]['source_id'],'one','com.example.app')['analysis_version'],1)
            with self.assertRaises(KeyError):store.retry(docs[5]['source_id'],'other','com.example.app')

    def test_decoder_selects_ffmpeg44_and_ffmpeg9_supported_sync(self):
        from task_server.services import ui_video_source_service as video
        old=video._process;commands=[]
        def run(command,*args,**kwargs):commands.append(command);return old(command,*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            movie=Path(directory)/'x.mp4'
            subprocess.run([video._tool('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=96x160:d=1','-c:v','libx264',str(movie)],check=True)
            with patch.object(video,'_process',run):video.decode_video(movie,Path(directory)/'frames',lambda:False)
        self.assertTrue(any(('-fps_mode' in command or '-vsync' in command) and 'vfr' in command for command in commands))
        with patch.object(video,'_process',return_value=(b'ffmpeg 4.4 help: -vsync video sync method',b'')):
            self.assertEqual(video._sync_args(lambda:False),['-vsync','vfr'])
        with patch.object(video,'_process',return_value=(b'ffmpeg 9 help: -fps_mode per stream sync',b'')):
            self.assertEqual(video._sync_args(lambda:False),['-fps_mode','vfr'])
