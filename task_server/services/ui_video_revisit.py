"""Diagnostic-only bounded acquisition of original video frames on demand."""
import os
from pathlib import Path
import re
import tempfile

from .ui_video_source_service import _process, _sync_args, _timestamp_ms, _tool


def make_revisit_loader(source_path, output_root, frames, cancelled):
    source=Path(source_path).resolve(strict=True)
    root=Path(output_root).resolve(strict=True)
    destination=root/'frames'
    if not source.is_file() or not destination.is_dir() or destination.is_symlink():
        raise ValueError('video_revisit_invalid')
    known={f['frame_id']:f['timestamp_ms'] for f in frames}
    timestamps=set(known.values())
    numbers=[int(m.group(1)) for key in known if (m:=re.fullmatch(r'frame-(\d{3})',key))]
    next_number=max(numbers,default=0)+1
    total=0

    def load(request, capacity):
        nonlocal total,next_number
        if cancelled():raise ValueError('video_cancelled')
        if (not isinstance(request,dict) or set(request)!={'first_frame','last_frame','question'} or
            not isinstance(request['question'],str) or not request['question'].strip() or len(request['question'])>2000 or
            type(capacity) is not int or not 2<=capacity<=20):raise ValueError('video_revisit_invalid')
        first,last=request['first_frame'],request['last_frame']
        if not isinstance(first,str) or not isinstance(last,str) or first not in known or last not in known:
            raise ValueError('video_revisit_invalid')
        start,end=known[first],known[last]
        if not 0<end-start<=3000:raise ValueError('video_revisit_invalid')
        available=min(capacity,64-total,1000-next_number)
        if available<=0:raise ValueError('video_revisit_limit')
        with tempfile.TemporaryDirectory(prefix='revisit-',dir=root) as temporary:
            os.chmod(temporary,0o700)
            pattern=str(Path(temporary)/'original-%03d.jpg')
            # Match the existing decoder time origin (FFmpeg normalizes source start).
            # No fps/resampling or setpts filter: showinfo preserves real frame timing.
            selector=f"select='between(t,{start/1000:.3f},{end/1000:.3f})'"
            scale="scale=w='min(iw,1280)':h='min(ih,1280)':force_original_aspect_ratio=decrease"
            command=[_tool('ffmpeg'),'-nostdin','-hide_banner','-v','info','-threads','1',
                     '-protocol_whitelist','file,pipe','-f','mov','-i',str(source),
                     '-xerror','-an','-sn','-dn','-vf',selector+','+scale+',showinfo',
                     *_sync_args(cancelled),'-frames:v','121','-threads','1','-pix_fmt','yuvj420p','-q:v','4',pattern]
            _,log=_process(command,cancelled,150)
            if cancelled():raise ValueError('video_cancelled')
            files=sorted(Path(temporary).glob('original-*.jpg'))
            for path in files:os.chmod(path,0o600)
            times=[_timestamp_ms(t) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)',log)]
            if (len(files)>120 or len(times)>120 or len(files)!=len(times) or not files or
                times!=sorted(times) or any(t<start or t>end for t in times) or
                sum(p.stat().st_size for p in files)>16*1024**2):raise ValueError('video_frames_invalid')
            unique=[];seen=set(timestamps)
            for path,timestamp in zip(files,times):
                if timestamp not in seen:unique.append((path,timestamp));seen.add(timestamp)
            count=min(available,len(unique))
            indexes=([0] if count==1 else [round(i*(len(unique)-1)/(count-1)) for i in range(count)])
            selected=[unique[i] for i in indexes]
            targets=[destination/f'frame-{next_number+i:03d}.jpg' for i in range(len(selected))]
            # Reject collisions in advance and still use exclusive creation against races.
            if any(p.exists() or p.is_symlink() for p in targets):raise ValueError('video_revisit_collision')
            created=[];metadata=[]
            try:
                for target,(path,timestamp) in zip(targets,selected):
                    if cancelled():raise ValueError('video_cancelled')
                    with target.open('xb') as output:
                        created.append(target);os.chmod(target,0o600);output.write(path.read_bytes())
                    metadata.append({'frame_id':target.stem,'timestamp_ms':timestamp})
                if cancelled():raise ValueError('video_cancelled')
            except BaseException:
                for target in created:target.unlink(missing_ok=True)
                raise
            for item in metadata:
                timestamps.add(item['timestamp_ms']);known[item['frame_id']]=item['timestamp_ms']
            next_number+=len(metadata);total+=len(metadata)
            return metadata
    return load
