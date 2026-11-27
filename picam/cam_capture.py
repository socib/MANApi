## Capture Snapshot
#%%
import subprocess, os, time

def picam_snap(save_path, filename, resolution, framerate):
    """
    This function uses the rpicam-still command to capture a snapshot.

    Args:
        save_path (str): The path to the directory where the snapshot will be saved.
        filename (str): The name of the snapshot file.
        resolution (str): The resolution of the snapshot.
        framerate (str): The framerate of the snapshot.
    """

    os.chdir(save_path)

    # Camera Configuration 
    subprocess.run([
        'rpicam-still', 
        '--width', resolution.split('x')[0], '--height', resolution.split('x')[1],  # Set resolution
        '--framerate', framerate,                    # Set framerate
        # '-rot', '270',                     # Set rotation
        '-o', filename+'.jpg' 
    ]) 


# Parameters:
if __name__ == "__main__":
    resolution = '1920x1080'
    framerate = '7.5'
    save_path = '/home/bmf/raspi/picam/videos'
    filename = 'snap_test'

    picam_snap(save_path, filename, resolution, framerate)
