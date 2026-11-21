import gc, time
from array import array
from media.media import MediaManager
from media.sensor import Sensor
import ulab.numpy as np
WIDTH=640; ROI_Y=150; ROI_H=48; FRAMES=60

def bench(name,sensor):
    for _ in range(8): image=sensor.snapshot(chn=0,timeout=1000)
    gc.collect(); gc.disable(); times=array('I',[0]*FRAMES); alloc=array('i',[0]*FRAMES); sums=array('I',[0]*WIDTH); out=bytearray(WIDTH)
    for frame in range(FRAMES):
        image=sensor.snapshot(chn=0,timeout=1000); before=gc.mem_free(); start=time.ticks_us()
        if name=='mean':
            arr=image.to_numpy_ref(); roi=arr[ROI_Y:ROI_Y+ROI_H,:]; profile=np.mean(roi,axis=0); value=int(profile[0]); del profile,roi,arr
        elif name=='sum':
            arr=image.to_numpy_ref(); roi=arr[ROI_Y:ROI_Y+ROI_H,:]; profile=np.sum(roi,axis=0); value=int(profile[0]//ROI_H); del profile,roi,arr
        elif name=='scale':
            roi=image.crop(roi=(0,ROI_Y,WIDTH,ROI_H)); small=roi.scale(1.0,1.0/ROI_H); small_arr=small.to_numpy_ref(); value=int(small_arr[0,0]); del small_arr,small,roi
        elif name=='inplace':
            arr=image.to_numpy_ref()
            for col in range(WIDTH): sums[col]=0
            for row in range(ROI_Y,ROI_Y+ROI_H):
                src=arr[row]
                for col in range(WIDTH): sums[col]+=int(src[col])
            for col in range(WIDTH): out[col]=sums[col]//ROI_H
            value=out[0]; del arr
        times[frame]=time.ticks_diff(time.ticks_us(),start); alloc[frame]=before-gc.mem_free(); image=None
    gc.enable(); gc.collect(); ordered=sorted(int(v) for v in times); a=[int(v) for v in alloc]
    print('BENCH,method=%s,frames=%d,p50_us=%d,p95_us=%d,max_us=%d,alloc_first=%d,alloc_last=%d,alloc_max=%d,free=%d' % (name,FRAMES,ordered[int((FRAMES-1)*.5)],ordered[int((FRAMES-1)*.95)],ordered[-1],a[0],a[-1],max(a),gc.mem_free()))

sensor=Sensor(width=1280,height=720,fps=90); sensor.reset(); sensor.set_framesize(width=640,height=360,chn=0); sensor.set_pixformat(Sensor.GRAYSCALE,chn=0); MediaManager.init(); sensor.run()
try:
 for name in ('mean','sum','scale','inplace'): bench(name,sensor)
finally:
 sensor.stop(); MediaManager.deinit()
