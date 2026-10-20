"""Optional same-frame pointer detail, never an action or an execution coordinate."""
import base64
import io
import math
from PIL import Image,ImageChops,ImageStat


def locate_ring(before,after):
    if before.size!=after.size or min(after.size)<100:return None
    width,height=after.size;scale=320/width;size=(320,round(height*scale))
    a=before.convert('RGB').resize(size);b=after.convert('RGB').resize(size)
    delta=ImageChops.difference(a,b).convert('L')
    # Whole-page transitions cannot establish a pointer location.
    if sum(ImageStat.Stat(delta).mean)>20:return None
    mask=Image.new('L',size)
    mask.putdata([int(85<sum(rgb)/3<248 and max(rgb)-min(rgb)<22 and change>5 and sum(old)-sum(rgb)>15)
                  for old,rgb,change in zip(a.getdata(),b.getdata(),delta.getdata())])
    from PIL import ImageDraw
    draw=ImageDraw.Draw(mask);draw.rectangle((0,0,size[0],int(size[1]*.12)),fill=0)
    draw.rectangle((0,int(size[1]*.94),size[0],size[1]),fill=0)
    best=None
    for radius in range(7,15):
        score=Image.new('L',size)
        offsets={(round(radius*math.cos(i*math.pi/16)),round(radius*math.sin(i*math.pi/16))) for i in range(32)}
        for dx,dy in offsets:score=ImageChops.add(score,ImageChops.offset(mask,-dx,-dy))
        draw=ImageDraw.Draw(score)
        for box in [(0,0,size[0],radius),(0,size[1]-radius,size[0],size[1]),(0,0,radius,size[1]),(size[0]-radius,0,size[0],size[1])]:draw.rectangle(box,fill=0)
        peak=score.getextrema()[1]
        if peak<28 or (best and peak<=best[0]):continue
        box=score.point(lambda v:255 if v==peak else 0).getbbox()
        if not box or box[2]-box[0]>radius*2 or box[3]-box[1]>radius*2:continue
        x,y=(box[0]+box[2]-1)/2,(box[1]+box[3]-1)/2
        inner=max(2,radius//2)
        if ImageStat.Stat(mask.crop((int(x-inner),int(y-inner),int(x+inner),int(y+inner)))).mean[0]>.25:continue
        best=(peak,x/scale,y/scale)
    return best[1:] if best else None


def append_details(frames,root,assets,cancelled,local_ids=None):
    limit=min(6,60-len(assets));details=[]
    if limit<=0:return details
    total=sum(len(a['base64']) for a in assets);last=None
    for before,after in zip(frames,frames[1:]):
        if cancelled():raise ValueError('video_cancelled')
        if local_ids is not None and after['frame_id'] not in local_ids:continue
        if not 0<after['timestamp_ms']-before['timestamp_ms']<=500:continue
        try:
            with Image.open(root/'frames'/(before['frame_id']+'.jpg')) as a,Image.open(root/'frames'/(after['frame_id']+'.jpg')) as b:
                point=locate_ring(a,b)
                if not point:continue
                x,y=point
                if last and after['timestamp_ms']-last[0]<600 and abs(x-last[1])+abs(y-last[2])<30:continue
                w,h=b.size;cw,ch=round(w*.35),round(h*.17)
                left=max(0,min(w-cw,round(x-cw/2)));top=max(0,min(h-ch,round(y-ch/2)))
                crop=b.crop((left,top,left+cw,top+ch)).convert('RGB');crop=crop.resize((480,round(ch*480/cw)))
                out=io.BytesIO();crop.save(out,format='JPEG',quality=90)
        except OSError:
            # Optional detail cannot replace normal original-asset validation.
            continue
        encoded=base64.b64encode(out.getvalue()).decode()
        if total+len(encoded)>10*1024*1024:break
        name=after['frame_id']+'-detail';stamp=after['timestamp_ms']
        details.append({'frame_id':name,'source_frame_id':after['frame_id'],'timestamp_ms':stamp})
        assets.append({'name':name,'timestamp_ms':stamp,'mime':'image/jpeg','base64':encoded})
        total+=len(encoded);last=(stamp,x,y)
        if len(details)==limit:break
    return details
