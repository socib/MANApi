#!/usr/bin/python3
"""Notification only: no acquisition, upload or cleanup."""
import argparse
from pathlib import Path
import pythonvideoFunctions as f

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level')
    parser.add_argument('message')
    args = parser.parse_args()
    if args.level not in ('LOG','ERROR','WARNING'):
        parser.error('level must be LOG, ERROR or WARNING')
    values = f.readInfo(Path.home()/'.config/manapi/info.txt')
    f.toolAddLog(values[2],values[1],values[0],args.level,
                 f.toolInstant('%Y-%m-%d %H:%M:%S')+' | '+args.message,
                 values[11],values[8],values[9],values[10])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
