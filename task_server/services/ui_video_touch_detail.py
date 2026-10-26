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


def locate_solid(before,after):
    """Find a changed translucent disk for visual review, not an action claim.

    Android's filled touch indicator can be tinted by a coloured control. Its
    disappearance is useful evidence too when its onset overlaps a transition.
    Callers retain the frame actually containing the disk as its parent.
    """
    if before.size!=after.size or min(after.size)<100:return None
    scale=320/after.width;size=(320,round(after.height*scale))
    a=before.convert('RGB').resize(size);b=after.convert('RGB').resize(size)
    delta=ImageChops.difference(a,b).convert('L')
    if ImageStat.Stat(delta).mean[0]>20:return None
    pixels={(i%size[0],i//size[0]) for i,(rgb,change) in enumerate(zip(b.getdata(),delta.getdata()))
            if i//size[0]>size[1]*.12 and 100<sum(rgb)/3<240 and max(rgb)-min(rgb)<85 and change>18}
    candidates=[]
    while pixels:
        seed=pixels.pop();component={seed};pending=[seed]
        while pending:
            x,y=pending.pop()
            for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if point in pixels:pixels.remove(point);component.add(point);pending.append(point)
        if not 70<=len(component)<=800:continue
        xs,ys=zip(*component);left,right,top,bottom=min(xs),max(xs),min(ys),max(ys)
        w,h=right-left+1,bottom-top+1
        if not (12<=w<=30 and 12<=h<=30 and .8<=w/h<=1.25 and .64<=len(component)/(w*h)<=.89):continue
        x,y=(left+right)/2,(top+bottom)/2
        disk={(px,py) for px in range(left,right+1) for py in range(top,bottom+1)
              if ((px-x)/(w/2))**2+((py-y)/(h/2))**2<=1}
        match=len(disk&component)/len(disk|component)
        if match>=.86:candidates.append((match,x/scale,y/scale))
    # Multiple changed circles may be ordinary UI content; do not choose one.
    return candidates[0][1:] if len(candidates)==1 else None


def append_details(frames,root,assets,cancelled,local_ids=None):
    limit=min(6,60-len(assets));details=[]
    if limit<=0:return details
    total=sum(len(a['base64']) for a in assets);last=None
    detailed={a.get('name') for a in assets}
    for before,after in zip(frames,frames[1:]):
        if cancelled():raise ValueError('video_cancelled')
        if not 0<after['timestamp_ms']-before['timestamp_ms']<=500:continue
        try:
            with Image.open(root/'frames'/(before['frame_id']+'.jpg')) as a,Image.open(root/'frames'/(after['frame_id']+'.jpg')) as b:
                parent,source=after,b
                point=locate_ring(a,b) or locate_solid(a,b)
                if not point:
                    point=locate_solid(b,a);parent,source=before,a
                if not point:continue
                if local_ids is not None and parent['frame_id'] not in local_ids:continue
                if parent['frame_id']+'-detail' in detailed:continue
                x,y=point
                if last and parent['timestamp_ms']-last[0]<600 and abs(x-last[1])+abs(y-last[2])<30:continue
                w,h=source.size;cw,ch=round(w*.35),round(h*.17)
                left=max(0,min(w-cw,round(x-cw/2)));top=max(0,min(h-ch,round(y-ch/2)))
                crop=source.crop((left,top,left+cw,top+ch)).convert('RGB');crop=crop.resize((480,round(ch*480/cw)))
                out=io.BytesIO();crop.save(out,format='JPEG',quality=90)
        except OSError:
            # Optional detail cannot replace normal original-asset validation.
            continue
        encoded=base64.b64encode(out.getvalue()).decode()
        if total+len(encoded)>10*1024*1024:break
        name=parent['frame_id']+'-detail';stamp=parent['timestamp_ms']
        details.append({'frame_id':name,'source_frame_id':parent['frame_id'],'timestamp_ms':stamp})
        assets.append({'name':name,'timestamp_ms':stamp,'mime':'image/jpeg','base64':encoded})
        detailed.add(name)
        total+=len(encoded);last=(stamp,x,y)
        if len(details)==limit:break
    return details
