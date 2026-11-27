# MANApi (Raspberry Pi Coastal Camera Station)

MANApi is a low-cost, Raspberry Pi–based system for automated coastal video monitoring. It generates snapshot, timex, standard-deviation and timestack products and transfers them to a data server using rsync over SSH.

## Quick start

1. **Prepare the hardware and OS:** assemble the camera unit and install a validated Raspberry Pi OS image.
2. **Configure the station:** create the operational user, configure networking and SSH, and synchronise the system clock in UTC.
3. **Install MANApi:** clone the repository and install the required camera tools and system Python dependencies.
4. **Set up configuration:** create `info.txt` and `manapi.env` under `~/.config/manapi/`. Keep credentials and station configuration outside Git.
5. **Validate the installation:** run `check_manapi_config.py` and test camera acquisition, server transfers and email notifications.
6. **Schedule and verify operations:** configure the operational user’s crontab for daytime acquisition, night processing and monitoring. Verify a complete operating cycle.

## Updates with GitLab/GitHub

For managed stations, validate changes on `test-branch` before merging into `main`. Use the CI/CD pipeline to run checks, preview changes and manually deploy to test and production units. Station configuration and data remain local.

## Full documentation

See the project **Wiki** for complete installation instructions, configuration examples, deployment procedures, calibration, optional streaming and troubleshooting.