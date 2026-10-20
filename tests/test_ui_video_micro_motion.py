"""Small touch evidence must survive without treating it as an executable action."""
import unittest
from task_server.services import ui_video_source_service as video

class MicroMotionTests(unittest.TestCase):
    def test_short_local_change_preserves_contact_motion_and_release(self):
        times=[0,1000,1033,1067,1100,1300,2000]
        changes=[0,.5,.2,.3,.4,0,0]
        self.assertTrue({0,1,2,4,5}.issubset(video._micro_motion_anchors(times,changes)))

    def test_brief_quiet_frame_does_not_split_one_local_state_change(self):
        times=[175867,176833,176900,176933,176967,177000]
        changes=[.027,.14,.002,5.49,.014,.043]
        anchors=video._micro_motion_anchors(times,changes)
        self.assertTrue({0,1,3,4}.issubset(anchors))

    def test_local_bursts_never_bridge_a_major_transition(self):
        self.assertEqual(video._micro_motion_anchors([0,33,66,100],[.3,25,.4,0]),set())

    def test_budget_completes_more_partially_present_bursts_before_expensive_new_one(self):
        times=list(range(10))
        selected,count=video._select_micro_motion([{0,1,2,3},{4,5,6},{7,8,9}],times,{4,5,7,8},8)
        self.assertEqual(selected,{4,5,6,7,8,9})
        self.assertEqual(count,2)

    def test_bridging_never_erases_existing_short_touch_groups(self):
        times=list(range(0,1500,50));changes=[.3 if i%3!=2 else .001 for i in range(len(times))]
        self.assertTrue({0,1,2}.issubset(video._micro_motion_anchors(times,changes)))

    def test_clock_tick_and_sustained_spinner_do_not_expand(self):
        self.assertEqual(video._micro_motion_anchors([0,1000,2000],[0,.3,.3]),set())
        self.assertEqual(video._micro_motion_anchors(list(range(0,2001,50)),[.3]*41),set())

    def test_major_navigation_and_invalid_scan_not_micro_touch(self):
        self.assertEqual(video._micro_motion_anchors([0,33,66,100],[0,25,30,40]),set())
        self.assertEqual(video._micro_motion_anchors([0,33],[.3]),set())

class RealTouchDecodeTests(unittest.TestCase):
    def test_short_contact_motion_survives_decode(self):
        import tempfile,subprocess,shutil
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);movie=root/'touch.mp4'
            filters="drawbox=x=100:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,1.10,1.12)',drawbox=x=115:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,1.13,1.15)',drawbox=x=130:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,1.16,1.19)'"
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=white:s=256x512:r=30:d=3','-vf',filters,'-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
            result=video.decode_video(movie,root/'frames',lambda:False)
            times=[f['timestamp_ms'] for f in result['frames']]
            self.assertIn(1133,times)
            self.assertTrue(any(1200<=t<=1500 for t in times))
            self.assertLessEqual(len(times),300)

    def test_long_repeated_micro_motion_keeps_base_and_tail(self):
        import tempfile,subprocess,shutil
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);movie=root/'long.mp4'
            filters="drawbox=x=100:y=220:w=16:h=16:color=gray:t=fill:enable='between(mod(t,1),0.1,0.12)',drawbox=x=115:y=220:w=16:h=16:color=gray:t=fill:enable='between(mod(t,1),0.13,0.15)',drawbox=x=130:y=220:w=16:h=16:color=gray:t=fill:enable='between(mod(t,1),0.16,0.19)'"
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=white:s=256x512:r=30:d=100','-vf',filters,'-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
            result=video.decode_video(movie,root/'frames',lambda:False)
            times=[f['timestamp_ms'] for f in result['frames']]
            self.assertLessEqual(len(times),300)
            self.assertGreater(times[-1],99000)
            self.assertLessEqual(max(b-a for a,b in zip(times,times[1:])),1500)
            self.assertLess(result['micro_motion_groups_sampled'],result['micro_motion_groups_total'])

    def test_fractional_frame_rate_touch_uses_consistent_timestamps(self):
        import tempfile,subprocess,shutil
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);movie=root/'fractional.mp4'
            filters="drawbox=x=100:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,0.46,0.49)',drawbox=x=115:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,0.50,0.53)',drawbox=x=130:y=220:w=16:h=16:color=gray:t=fill:enable='between(t,0.534,0.56)'"
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=white:s=256x512:r=30000/1001:d=3','-vf',filters,'-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
            result=video.decode_video(movie,root/'frames',lambda:False)
            self.assertIn(501,[f['timestamp_ms'] for f in result['frames']])

if __name__=='__main__':unittest.main()
