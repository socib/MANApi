import subprocess
import os
import time

def picam_snap_interactive(save_path, filename, resolution, framerate, num_snapshots=20):
    """
    This function captures multiple snapshots interactively using rpicam-still.

    Args:
        save_path (str): The path to the directory where the snapshots will be saved.
        filename (str): The base name of the snapshot files.
        resolution (str): The resolution of the snapshots.
        framerate (str): The framerate of the preview.
        num_snapshots (int): The number of snapshots to capture.
    """

    os.chdir(save_path)

    # Camera Configuration
    preview_command = [
        'rpicam-still',
        '--width', resolution.split('x')[0],
        '--height', resolution.split('x')[1],
        '--framerate', framerate,
        '--preview', #'0,0,640,480'  # Show a preview window with specified dimensions
    ]
    capture_command = [
        'rpicam-still',
        '--width', resolution.split('x')[0],
        '--height', resolution.split('x')[1],
        '--framerate', framerate,
        # '-rot', '270',                     # Set rotation if needed
        '-o', ''  # Filename will be added later
    ]

    for i in range(num_snapshots):
        # Show the preview
        preview_process = subprocess.Popen(preview_command)

        # Wait for user input
        input("Adjust the camera and press Enter to capture a snapshot...")

        # Terminate the preview process
        preview_process.terminate()
        time.sleep(0.5)  # Give some time for the preview to close

        # Capture the snapshot
        snapshot_filename = f'{filename}_{i+1}.jpg'
        capture_command[-1] = snapshot_filename  # Update the filename
        subprocess.run(capture_command)

        print(f'Snapshot {i+1} saved as {snapshot_filename}')
        
        # Ask if the user wants to continue
        if input("Press Enter to continue or type 'q' to quit: ").lower() == 'q':
            break


# Parameters:
resolution = '1920x1080'
framerate = '7.5'
save_path = '/home/bmf/raspi/picam/videos'
filename = 'snap_test'

picam_snap_interactive(save_path, filename, resolution, framerate)