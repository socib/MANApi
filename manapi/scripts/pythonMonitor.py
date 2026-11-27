#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
MANApi system monitor.

Runs CPU temperature monitoring and CPU/RAM monitoring concurrently.

Created on Thu Apr 21 16:27:16 2024
@author: ffcriado
"""

# VERSIONS RECORD
VER = '2026-11-06 ffcriado@socib.es - temperature integrated into pythonMonitor'

import os
import threading

from pythonvideoFunctions import (
    measure_cpu_temp,
    monitor_cpu_ram,
    noInfo,
    readInfo,
)


def run_system_monitoring(info):
    """Run temperature and CPU/RAM monitoring concurrently for one MANApi."""
    if not os.path.isfile(info):
        noInfo(info)
        return

    (
        Station,
        Site,
        DirLocal,
        _DirDest,
        _DirSocib,
        _Portal,
        _SshPort,
        _User,
        MailFrom,
        MailPass,
        MailDest,
        CamName,
        _CamModel,
        _VideoDuration,
        _VideoFps,
        _ImageWidth,
        _ImageHeight,
        _TimestackTransect,
        _TimestackOrientation,
    ) = readInfo(info)

    temperature_thread = threading.Thread(
        target=measure_cpu_temp,
        args=(
            DirLocal,
            Site,
            Station,
            CamName,
            MailFrom,
            MailPass,
            MailDest,
        ),
        name='manapi-temperature',
    )

    system_thread = threading.Thread(
        target=monitor_cpu_ram,
        args=(
            DirLocal,
            Site,
            Station,
            CamName,
            MailFrom,
            MailPass,
            MailDest,
        ),
        name='manapi-cpu-ram',
    )

    temperature_thread.start()
    system_thread.start()

    temperature_thread.join()
    system_thread.join()



# Backward-compatible name used by older MANApi code.
run_cpu_and_ram_monitoring = run_system_monitoring


def main():
    """Start MANApi system monitoring using the external station configuration."""
    info = os.path.expanduser('~/.config/manapi/info.txt')
    run_system_monitoring(info)


if __name__ == '__main__':
    main()
