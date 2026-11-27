#!/usr/bin/python3
from pathlib import Path
import pythonvideoFunctions as f

def main():
    info = Path.home()/'.config/manapi/info.txt'
    try:
        values = f.readInfo(info)
        Station,Site,Root,Dest,Remote,Host,Port,User,Sender,Password,Recipients,Camera,Model,Duration,Fps,Width,Height,Transect,Orientation = values
        with f.acquisition_lock():
            batches = f.night_products(Root,Site,Station,Dest,Remote,Host,Port,User,Camera,Duration,Transect,Orientation,Sender,Password,Recipients, VIDEO_FPS=Fps, IMAGE_WIDTH=Width, IMAGE_HEIGHT=Height)
            if not f.update_folders_socib(Root,Dest,Remote,Host,Port,User,Site,Station,Camera,Sender,Password,Recipients,'night',completed_batches=batches):
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
