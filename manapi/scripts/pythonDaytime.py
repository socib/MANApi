#!/usr/bin/python3
from pathlib import Path
import pythonvideoFunctions as f

def main():
    info = Path.home()/'.config/manapi/info.txt'
    try:
        values = f.readInfo(info)
        Station,Site,Root,Dest,Remote,Host,Port,User,Sender,Password,Recipients,Camera,Model,Duration,Fps,Width,Height,Transect,Orientation = values
        with f.acquisition_lock():
            f.detect(Root,Site,Station,Camera,Sender,Password,Recipients)
            if not f.picam_video(Root,Site,Station,Dest,Camera,Sender,Password,Recipients,Duration,Fps,Width,Height):
                return 1
            video = max(Path(Dest).glob('*.h264'), key=lambda p:p.stat().st_mtime_ns)
            f.validate_video(video,Duration,Fps,Width,Height)
            f.daytime_products(Root,Site,Station,Dest,Camera,Duration,Sender,Password,Recipients,video_path=str(video))
            if not f.update_folders_socib(Root,Dest,Remote,Host,Port,User,Site,Station,Camera,Sender,Password,Recipients,'day'):
                return 1
        return 0
    except BlockingIOError:
        print('Another acquisition/processing job is active; skipping this run.')
        return 1
    except Exception as exc:
        print('MANApi failed:', type(exc).__name__, str(exc))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
