##  Preview the camera feed 

import subprocess, os, time

def picam_prev(resolution, framerate, duration):
    """
    This function uses the rpicam-hello command to preview the camera feed.

    Args:
        resolution (str): The resolution of the preview.
        framerate (str): The framerate of the preview.
    """

    # Camera Configuration 
    subprocess.run([
        'rpicam-hello', 
        '--width', resolution.split('x')[0], '--height', resolution.split('x')[1],  # Set resolution
        '--framerate', framerate,                    # Set framerate
        # '-rot', '270',                     # Set rotation 
        '-t', duration,                           # Recording duration in milliseconds
    ]) 


# Parameters:
resolution = '1920x1080'
framerate = '7.5'
duration = '100000' #ms


picam_prev(resolution, framerate, duration)

## Run for ever until Crtl-C
# rpicam-hello -t 0 
