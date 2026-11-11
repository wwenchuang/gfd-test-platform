"""Generate Apache JMeter HTML dashboards offline, using steady requests only."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import tempfile

def main():
    p=argparse.ArgumentParser()
    p.add_argument("run",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--jmeter",required=True,type=Path)
    p.add_argument("--properties",required=True,type=Path)
    a=p.parse_args()
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in ('baseline','medium','high','retest'):
        folder=a.run/name
        meta=json.loads((folder/'metadata.json').read_bytes())
        if meta['status']!='passed':raise ValueError('stage not complete: '+name)
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            with (folder/'requests.jtl').open(newline='') as f,(tmp/'steady.csv').open('w',newline='') as out:
                r=csv.DictReader(f);fields=r.fieldnames.copy()
                # Apache dashboard requires this column. The sampler deliberately
                # does not collect free-text response reasons; leave it empty.
                fields.insert(fields.index('responseCode')+1,'responseMessage')
                w=csv.DictWriter(out,fieldnames=fields);w.writeheader();n=0
                for row in r:
                    if meta['steady_start_ms']<=int(row['timeStamp'])<meta['steady_end_ms']:
                        w.writerow({**row,'responseMessage':''});n+=1
            assert n>0
            dest=(a.output/name).resolve()
            subprocess.run([str(a.jmeter.resolve()),'-q',str(a.properties.resolve()),'-Jjmeter.save.saveservice.response_message=true',
                            '-g','steady.csv','-o',str(dest),'-j','offline.log'],cwd=tmp,stdout=subprocess.DEVNULL,check=True)
            stats=json.loads((dest/'statistics.json').read_bytes())
            if not (dest/'index.html').is_file() or stats['Total']['sampleCount']!=n:
                raise ValueError('dashboard and measured sample count differ')
            print(name,n,'samples verified')
if __name__=='__main__':main()
