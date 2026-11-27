## Capture Video Funtion
#
#%%
import subprocess, os, time

def picam_video(save_path, filename, resolution, framerate, duration):
    """
    This function uses the rpicam-vid command to record a video.

    Args:
        save_path (str): The path to the directory where the video will be saved.
        filename (str): The name of the video file.
        resolution (str): The resolution of the video.
        framerate (str): The framerate of the video.
        duration (str): The duration of the video in milliseconds.
    """

    os.chdir(save_path)

    # Camera Configuration 
    subprocess.run([
        'rpicam-vid', 
        '--width', resolution.split('x')[0], '--height', resolution.split('x')[1],  # Set resolution
        '--framerate', framerate,                    # Set framerate
        # '-rot', '270',                     # Set rotation 
        '-t', duration,                           # Recording duration in milliseconds
        '-o', filename+'.h264' 
    ]) 


# Parameters:
resolution = '1920x1080'
framerate = '7.5'
duration = '10000' #ms
save_path = '/home/bmf/raspi/picam/videos'
filename = 'video_test'

picam_video(save_path, filename, resolution, framerate, duration)
