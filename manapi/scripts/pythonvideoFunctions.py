"""
MANApi acquisition, products, verified transfer and annual logging.

Annual logs: first use preserves existing files as legacy archives. Completed
UTC years are archived under the image destination's YYYY/01/01/logs directory
(2026 logs -> 2027/01/01/logs). Current filenames stay unchanged. Upload retries
keep both pending and staged copies until checksum verification succeeds.
State is stored in scripts/logs/.annual; do not delete it. Restart the existing
monitor once after deployment so all writers use the updated locking protocol.
Existing configuration and 19-value readInfo interface are unchanged.

Created on Thu Nov 08 11:35:16 2018
@author: ffcriado
"""
VER = '2026-11-06 MANApi function organisation and logging v6'
# VER = '2022-03-08 ffcriado@socib.es' # Changing code for raspi MANApi
#
###########
#
# LIST OF FUNCTIONS:
# 0-helper                 | General helpers and acquisition workflow lock
# 1-readInfo               | Read and validate MANApi configuration
# 2-create_folders         | Create daily folders on the raspi
# 3-detect                 | Detect PiCam and send morning status
# 4-picam_video            | Function to record video
# 5-daytime_products       | Extract daytime products: snapshot and timex
# 6-night_products         | Extract night products: variance and timestack
# 7-update_folders_socib   | Upload, verify and update folders in the file system
# 8-clean_raspi            | Verify and remove unchanged local files safely
# 9-toolAddLog             | Add logs and manage annual log archives
# 10-measure_cpu_temp      | Measure CPU temperature
# 11-monitor_cpu_ram       | Monitor CPU, RAM and disk usage
# 12-toollogsize           | Compatibility shim; annual log helpers retain full years
# 13-toolSendMail          | Send mail and log accepted messages
# 14-toolInstant           | Time now
# 15-showBanner_MANAPI     | Print Banner MANApi
# 16-plot_temperature_and_system | Plot monitored system variables

import re
from contextlib import contextmanager

###############################################################################
#%%
#
# 0-helper: General MANApi helper functions ###################################

# 0.1-helper: Filename parser to extract Y-M-D-H-mm ###########################
def parse_ymdhm_from_filename(filename: str):
    """
    Extract YYYY, MM, DD, HH and optional mm from filenames like:
      ..._2024-10-28-15-00..., ..._2024-10-28_15..., ...20241028_15...,
      ...-2024_10_28-15-...
    Returns (YEAR, MONTH, DAY, HOUR, MINUTE) as strings. MINUTE defaults to '00' if missing.
    """
    s = filename

    # 1) Prefer explicit separated forms: YYYY[-_]MM[-_]DD [sep] HH [-_.] mm (minute optional)
    m = re.search(
        r'(?P<Y>\d{4})[-_]?'
        r'(?P<M>\d{2})[-_]?'
        r'(?P<D>\d{2})'
        r'[\sT\-_.]?'
        r'(?P<H>\d{2})'
        r'(?:[-_.]?(?P<m>\d{2}))?', s
    )
    if m:
        return m.group('Y'), m.group('M'), m.group('D'), m.group('H'), (m.group('m') or '00')

    # 2) Fallback compact form: YYYYMMDD[non-digit]?HH[sep]mm? (minute optional)
    m2 = re.search(
        r'(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})[^0-9]?'
        r'(?P<H>\d{2})(?:[^0-9]?(?P<m>\d{2}))?', s
    )
    if m2:
        return m2.group('Y'), m2.group('M'), m2.group('D'), m2.group('H'), (m2.group('m') or '00')

    raise ValueError(f"Could not parse Y-M-D-H from filename: {filename}")

# 0.2-helper: Parse and validate timestack transect ###########################
def parse_timestack_transect(value, image_width, image_height):
    """Parse x1,y1;x2,y2 timestack coordinates."""

    try:
        points = value.split(";")

        if len(points) != 2:
            raise ValueError

        x1, y1 = [int(v.strip()) for v in points[0].split(",")]
        x2, y2 = [int(v.strip()) for v in points[1].split(",")]

        width = int(image_width)
        height = int(image_height)

    except (ValueError, TypeError):
        raise ManapiConfigError(
            "TimestackTransect must use the format x1,y1;x2,y2."
        )

    if (x1, y1) == (x2, y2):
        raise ManapiConfigError(
            "TimestackTransect start and end points must be different."
        )

    for x, y in ((x1, y1), (x2, y2)):
        if not (0 <= x < width and 0 <= y < height):
            raise ManapiConfigError(
                "TimestackTransect coordinates are outside the image dimensions."
            )

    if not (
        x1 == x2
        or y1 == y2
        or abs(x1 - x2) == abs(y1 - y2)
    ):
        raise ManapiConfigError(
            "TimestackTransect must be horizontal, vertical or a 45-degree diagonal."
        )

    return ((x1, y1), (x2, y2))

# 0.3-helper: Serialise daytime and night acquisition workflows ###############
@contextmanager
def acquisition_lock():
    """Shared lock for daytime and night workflows, automatically released on exit."""
    import fcntl
    from pathlib import Path
    folder = Path.home()/'.local/state/manapi'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'acquisition.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

###############################################################################
#%%
#
# 1-readInfo: Read and validate MANApi configuration ##########################

# 1.1-readInfo helper: Configuration error type ###############################
class ManapiConfigError(ValueError):
    """Configuration error whose message never includes setting values."""

# 1.2-readInfo helper: Read protected per-device runtime settings #############
def read_runtime_settings(env_path=None, *, require_complete=True) -> dict[str, str]:
    """Read per-device identity and email settings without exporting them.

    The directory must belong to the runtime user with mode 700, and the
    regular file must belong to that user with mode 600 (or stricter).
    ``require_complete=False`` is reserved for best-effort error reporting.
    """
    import os
    import stat
    from pathlib import Path

    try:
        from dotenv import dotenv_values
    except ImportError:
        raise ManapiConfigError(
            "Missing python-dotenv dependency; install it in the runtime Python."
        ) from None

    env_path = (Path(env_path).expanduser() if env_path is not None
                else Path.home() / ".config" / "manapi" / "manapi.env")
    try:
        parent = env_path.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise ManapiConfigError(
                "The manapi.env directory must belong to the runtime user "
                "and have no group/other access (chmod 700)."
            )
        with env_path.open(encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ManapiConfigError("manapi.env must be a regular file.")
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise ManapiConfigError(
                    "manapi.env must belong to the runtime user "
                    "and have no group/other access (chmod 600)."
                )
            # Read only this explicit file. Do not search other directories,
            # import shell variables, or expand ${...} in passwords.
            raw = dotenv_values(stream=handle, interpolate=False)
    except ManapiConfigError:
        raise
    except (OSError, UnicodeError):
        raise ManapiConfigError("Cannot read manapi.env; check its path and access.") from None

    keys = (
        "MANAPI_STATION", "MANAPI_SITE", "MANAPI_DIR_LOCAL", "MANAPI_CAM_NAME",
        "MANAPI_MAIL_FROM", "MANAPI_MAIL_PASS", "MANAPI_MAIL_DEST",
    )
    settings = {key: raw.get(key) or "" for key in keys}
    if not require_complete:
        return settings

    missing = [key for key in keys if not settings[key].strip()]
    if missing:
        raise ManapiConfigError("Missing manapi.env settings: " + ", ".join(missing))
    multiline = [key for key in keys if "\n" in settings[key] or "\r" in settings[key]]
    if multiline:
        raise ManapiConfigError("Settings must be single-line: " + ", ".join(multiline))
    root = settings["MANAPI_DIR_LOCAL"]
    if not Path(root).is_absolute() or not root.endswith("/"):
        raise ManapiConfigError(
            "MANAPI_DIR_LOCAL must be an absolute directory path ending in /."
        )
    return settings

# 1.3-readInfo: Read required information #####################################
def readInfo(info):
    """Read info.txt plus sibling manapi.env; return the 19-value configuration interface.

    info.txt uses a [MANAPI] INI section. Device identity and email settings
    live ONLY in manapi.env. No fallback to old embedded passwords is allowed.
    """
    from configparser import ConfigParser, Error as ConfigParserError
    from pathlib import Path

    info_path = Path(info).expanduser()
    config = ConfigParser(interpolation=None)
    try:
        with info_path.open(encoding="utf-8") as handle:
            config.read_file(handle)
    except FileNotFoundError:
        noInfo(str(info_path))
    except (OSError, UnicodeError, ConfigParserError):
        # Parser exception text can contain entire configuration lines.
        noInfo(str(info_path), reason="Cannot read or parse info.txt; check its INI format.")

    if "MANAPI" not in config:
        noInfo(str(info_path), reason="info.txt must contain a [MANAPI] section.")
    c = config["MANAPI"]

    moved = ("Station", "Site", "DirLocal", "CamName", "MailFrom", "MailPass", "MailDest")
    duplicated = [key for key in moved if key in c]
    if duplicated:
        noInfo(str(info_path), reason=(
            "Remove these settings from info.txt; they belong only in manapi.env: "
            + ", ".join(duplicated)
        ))

    required = (
        "DirDest", "DirSocib", "Portal", "SshPort", "User", "CamModel",
        "VideoDuration", "VideoFps", "ImageWidth", "ImageHeight", "TimestackTransect", "TimestackOrientation",
    )
    missing = [key for key in required if key not in c or (
        not c[key].strip()
    )]
    if missing:
        noInfo(str(info_path), reason="Missing/empty info.txt settings: " + ", ".join(missing))
    quoted = [key for key in required if c[key].startswith(("'", '"'))]
    if quoted:
        noInfo(str(info_path), reason=(
            "Use unquoted INI values in info.txt: " + ", ".join(quoted)
        ))
    for key in ("DirDest", "DirSocib"):
        if not c[key].endswith("/"):
            noInfo(str(info_path), reason=key + " must end in / for the existing path joins.")
    # Validate timestack configuration
    try:
        timestack_transect = parse_timestack_transect(
            c["TimestackTransect"],
            c["ImageWidth"],
            c["ImageHeight"],
        )
    except ManapiConfigError as exc:
        noInfo(str(info_path), reason=str(exc))

    timestack_orientation = c["TimestackOrientation"].strip().upper()

    if timestack_orientation not in ("N", "E", "S", "W"):
        noInfo(
            str(info_path),
            reason="TimestackOrientation must be N, E, S or W."
        )
    try:
        r = read_runtime_settings(info_path.with_name("manapi.env"))
    except ManapiConfigError as exc:
        # Only this exception's controlled messages are safe to report.
        noInfo(str(info_path), reason=str(exc))

    print("-functionsVersion: " + VER + "-")
    return (
        r["MANAPI_STATION"], r["MANAPI_SITE"], r["MANAPI_DIR_LOCAL"],
        c["DirDest"], c["DirSocib"], c["Portal"], c["SshPort"], c["User"],
        r["MANAPI_MAIL_FROM"], r["MANAPI_MAIL_PASS"], r["MANAPI_MAIL_DEST"],
        r["MANAPI_CAM_NAME"], c["CamModel"], c["VideoDuration"],
        c["VideoFps"], c["ImageWidth"], c["ImageHeight"], timestack_transect, timestack_orientation,
    )

# 1.4-readInfo helper: Report invalid or missing configuration ################
def noInfo(INFO, *, reason="Cannot find the station configuration file."):
    """Report a config error using manapi.env only, then exit with status 1.

    Local logging and email are independent. If the environment file cannot
    be trusted/read, report the hostname to stderr without assuming mail works.
    ``reason`` must be a controlled message, never raw exception/file contents.
    """
    import re
    import socket
    import sys
    from pathlib import Path

    info_path = Path(INFO).expanduser()
    try:
        r = read_runtime_settings(info_path.with_name("manapi.env"), require_complete=False)
    except ManapiConfigError as exc:
        r = {}
        print("Private configuration unavailable: " + str(exc), file=sys.stderr)

    def identity(key):
        """Return a configuration identity or UNKNOWN without exposing secret settings."""
        return r.get(key) or "UNKNOWN"

    text = (
        toolInstant("%Y-%m-%d %H:%M:%S") + " | ERROR_CONFIG. " + reason
        + "\nStation: " + identity("MANAPI_STATION")
        + "\nSite: " + identity("MANAPI_SITE")
        + "\nCamera: " + identity("MANAPI_CAM_NAME")
        + "\nHost: " + socket.gethostname()
        + "\nConfiguration file: " + str(info_path)
        + "\nLocal directory: " + identity("MANAPI_DIR_LOCAL")
    )
    print(text, file=sys.stderr)

    # Do NOT use toolAddLog('ERROR', ...): its logging failure would skip mail.
    root, cam = r.get("MANAPI_DIR_LOCAL", ""), r.get("MANAPI_CAM_NAME", "")
    if root and Path(root).is_absolute() and re.fullmatch(r"[A-Za-z0-9_.-]+", cam):
        try:
            logs_dir = Path(root) / "scripts" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            log_file = logs_dir / ("ERROR-" + cam + ".log")
            if all(r.get(key) for key in ("MANAPI_SITE", "MANAPI_STATION")):
                _append_annual_log(root, r["MANAPI_SITE"], r["MANAPI_STATION"],
                                   cam, log_file.name, text)
            else:
                # Incomplete identity: preserve the diagnostic without guessing
                # its remote destination. It will migrate as legacy on recovery.
                with _log_lock(logs_dir / '.annual' / (log_file.name + '.lock')):
                    with log_file.open("a", encoding="utf-8") as handle:
                        handle.write(text + "\n")
            print("Configuration error recorded in " + str(log_file), file=sys.stderr)
        except OSError as exc:
            print("Local error log unavailable (" + type(exc).__name__ + ").", file=sys.stderr)
    else:
        print("Local error log unavailable: missing/invalid DirLocal or CamName.", file=sys.stderr)

    mail_keys = ("MANAPI_MAIL_FROM", "MANAPI_MAIL_PASS", "MANAPI_MAIL_DEST")
    if all(r.get(key, "").strip() for key in mail_keys):
        subject = ("[" + identity("MANAPI_SITE") + " " + identity("MANAPI_CAM_NAME")
                   + "] MANApi configuration error")
        try:
            toolSendMail(
                r["MANAPI_MAIL_FROM"], r["MANAPI_MAIL_PASS"], r["MANAPI_MAIL_DEST"],
                subject, text, lookup_wan_ip=False,
                ROOT=root or None, SITE=r.get("MANAPI_SITE") or None,
                STATION=r.get("MANAPI_STATION") or None,
                CAMNAME=cam or None,
            )
        except Exception as exc:
            # SMTP exception messages may contain details we must not log.
            print("Email alert failed (" + type(exc).__name__ + ").", file=sys.stderr)
    else:
        print("Email alert unavailable: incomplete mail settings.", file=sys.stderr)

    print("THE SCRIPT HAS STOPPED.", file=sys.stderr)
    raise SystemExit(1)

###############################################################################
#%%
#
# 2-create_folders: Create daily folders on the raspi #########################

# 2.1-create_folders: Create dated camera-product and video folders ###########
def create_folders(ROOT, SITE, STATION, CAMNAME, YEAR_v, MONTH_v, DAY_v):
    """Create the dated camera-product tree and the local video directory."""
    from pathlib import Path
    import os

    # Create folders for the high-resolution images
    DIR_VID = ROOT + 'images/' + SITE + '/' + STATION + '/' + SITE + '/' + 'c' +CAMNAME
    DIR_VID_YEAR = DIR_VID + '/' + YEAR_v
    DIR_VID_MONTH = DIR_VID_YEAR + '/' + MONTH_v
    DIR_VID_DAY = DIR_VID_MONTH + '/' + DAY_v

    Path(DIR_VID).mkdir(parents=True, exist_ok=True)
    Path(DIR_VID_YEAR).mkdir(parents=True, exist_ok=True)
    Path(DIR_VID_MONTH).mkdir(parents=True, exist_ok=True)
    Path(DIR_VID_DAY).mkdir(parents=True, exist_ok=True)

    # Create Video Folder if needed (optional)
    DIR_VIDEOS = ROOT + 'videos'
     # Set permissions for the video folder
    # os.chmod(DIR_VIDEOS, 0o775)

    Path(DIR_VIDEOS).mkdir(parents=True, exist_ok=True)

###############################################################################
#%%
#
# 3-detect: Detect PiCam and send morning status ##############################

# 3.1-detect: Detect PiCam and run streaming health check #####################
def detect(ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST):
    """
    Detects Pi camera using rpicam-hello --list-cameras and a short rpicam-vid
    streaming health-check. Returns True only if listed and streaming_ok.
    """
    import shutil
    import subprocess
    import sys
    import re
    import os

    def log(level, message):
        """Add a timestamped operational message to console and the configured log."""
        TEXT = toolInstant('%Y-%m-%d %H:%M:%S') + ' | ' + message
        toolAddLog(ROOT, SITE, STATION, level, TEXT, CAMNAME, MailFROM, MailPASS, MailDEST)
        print(TEXT)

    cmd = shutil.which('rpicam-hello')
    output = ''
    detected = False
    streaming_ok = False
    short_diag = ''

    if not cmd:
        # Log as non-emailing log; final ERROR will be sent below if needed
        log('LOG', 'ERROR_Detect. rpicam-hello not found on system')
    else:
        try:
            res = subprocess.run([cmd, '--list-cameras'], capture_output=True, text=True, timeout=6)
            output = (res.stdout or '') + '\n' + (res.stderr or '')
            print('rpicam-hello --list-cameras output:\n' + output)

            if 'No cameras available' in output or 'No cameras available!' in output:
                detected = False
            else:
                if re.search(r'^\s*\d+\s*:\s*\S+', output, re.M) or re.search(r'Adding camera|Registered camera|Registered sensor', output, re.I):
                    detected = True
                else:
                    detected = False

            if detected:
                cmd_vid = shutil.which('rpicam-vid')
                if cmd_vid:
                    health_file = '/tmp/rpicam_health.h264'
                    try:
                        hc = subprocess.run([
                            cmd_vid,
                            '-n',
                            '--width', '320', '--height', '240',
                            '--framerate', '5',
                            '-t', '1000',
                            '-o', health_file
                        ], capture_output=True, text=True, timeout=6)
                        combined = (hc.stdout or '') + '\n' + (hc.stderr or '')
                        if hc.returncode != 0 or re.search(r'Failed to queue buffer|Device timeout|ERROR V4L2|Input/output error', combined, re.I):
                            streaming_ok = False
                            short_diag = '\n'.join(combined.splitlines()[:3])[:1000]
                            # keep as non-emailing log so only the final ERROR triggers an email
                            log('LOG', f'ERROR_Detect. streaming health-check failed for {CAMNAME}: {short_diag}')
                        else:
                            streaming_ok = True
                    except subprocess.TimeoutExpired:
                        streaming_ok = False
                        short_diag = 'health-check timeout'
                        log('LOG', f'ERROR_Detect. streaming health-check timed out for {CAMNAME}')
                    except Exception as e:
                        streaming_ok = False
                        short_diag = str(e)
                        log('LOG', f'ERROR_Detect. Exception during streaming health-check: {e}')
                    finally:
                        try:
                            if os.path.exists(health_file):
                                os.remove(health_file)
                        except Exception:
                            pass
                else:
                    log('LOG', 'rpicam-vid not found; skipping streaming health-check')
                    streaming_ok = True

        except subprocess.TimeoutExpired:
            # Non-emailing intermediate log; final ERROR will be sent when exiting
            log('LOG', 'ERROR_Detect. rpicam-hello --list-cameras timed out')
        except Exception as e:
            log('LOG', f'ERROR_Detect. Exception running rpicam-hello: {e}')
            output = str(e)

    if _utc_log_now().hour == 8:
        try:
            accepted = send_morning_status(
                ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
                detected and streaming_ok, short_diag or output
            )
            if not accepted:
                log('LOG', 'Morning status email was not accepted; retry on the next 08:00 UTC invocation.')
        except Exception as exc:
            # A report/SMTP failure must not stop camera acquisition.
            log('LOG', 'Morning status email failed: ' + type(exc).__name__)

    if detected and streaming_ok:
        log('LOG', 'Camera detected and streaming OK')
        return True
    else:
        log('ERROR', f'ERROR_Detect. PiCAM {CAMNAME} not detected or not streaming in {SITE}')
        sys.exit(1)

# 3.2-detect helper: Build morning status text ################################
def _morning_status_text(ROOT, SITE, STATION, CAMNAME, camera_ok, *, now=None):
    """Build a factual status report; historical values are labelled with UTC times."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    import os
    import shutil
    now = _utc_log_now() if now is None else now
    text = [
        f'MANApi daily status — {SITE} / {STATION} / camera {CAMNAME}',
        f'Report time: {now:%Y-%m-%d %H:%M:%S} UTC',
        'Camera health check: ' + ('passed' if camera_ok else 'FAILED or unavailable'),
    ]
    try:
        seconds = float(Path('/proc/uptime').read_text().split()[0])
        text.append(f'Uptime: {int(seconds // 86400)} days, {int(seconds % 86400 // 3600)} hours')
    except (OSError, ValueError, IndexError):
        text.append('Uptime: unavailable')
    seen = set()
    for label, path in [('Root filesystem', '/'), ('MANApi installation filesystem', ROOT)]:
        try:
            device = os.stat(path).st_dev
            if device in seen:
                continue
            usage = shutil.disk_usage(path)
            free_percent = 100 * usage.free / usage.total
            text.append(f'{label}: {free_percent:.1f}% free '
                        f'({usage.free / 2**30:.2f} GiB available / '
                        f'{usage.total / 2**30:.2f} GiB total)')
            if free_percent <= 20:
                text.append('WARNING: available disk space is at or below 20%.')
            seen.add(device)
        except (OSError, ZeroDivisionError):
            text.append(f'{label}: disk usage unavailable')
    temperature, _ = _monitoring_samples(ROOT, CAMNAME, now=now)
    def age_label(stamp):
        """Expose stale monitoring data rather than treating it as a live reading."""
        minutes = (now - stamp).total_seconds() / 60
        return f'{stamp:%Y-%m-%d %H:%M:%S} UTC' + ('; STALE (>10 min)' if minutes > 10 else '')
    if temperature:
        stamp, value = temperature[-1]
        text.append(f'Latest logged CPU temperature: {value:.1f} °C ({age_label(stamp)})')
    else:
        text.append('CPU temperature: no valid local samples in the last 14 days')
    recent = [row for row in temperature if row[0] >= now - timedelta(hours=24)]
    if recent:
        text.append(f'Maximum logged CPU temperature in the last 24 h: '
                    f'{max(row[1] for row in recent):.1f} °C ({len(recent)} available samples)')
    else:
        text.append('Maximum CPU temperature in the last 24 h: unavailable')
    return '\n'.join(text)

# 3.3-detect helper: Send daily MANApi status report ##########################
def send_morning_status(ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
                        camera_ok, diagnostic=''):
    """Send the morning report once per UTC day after SMTP acceptance.

    Called by detect() in its existing 08:00 UTC window. Failed sends may retry
    on another invocation in that window; no new scheduler is introduced.
    Plot failure does not suppress the text report. No real email is sent by tests.
    """
    import json
    from pathlib import Path
    now = _utc_log_now()
    folder = Path(ROOT) / 'scripts' / 'logs' / '.annual'
    state_path = folder / f'morning-{CAMNAME}.json'
    with _log_lock(folder / f'morning-{CAMNAME}.lock'):
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        if isinstance(state, dict) and state.get('sent_date') == now.date().isoformat():
            return True
        body = _morning_status_text(ROOT, SITE, STATION, CAMNAME, camera_ok, now=now)
        if diagnostic and not camera_ok:
            body += '\nCamera diagnostic:\n' + str(diagnostic)[:1500]
        image_path = None
        try:
            image_path = plot_temperature_and_system(ROOT, SITE, CAMNAME)
            if not image_path or not Path(image_path).is_file():
                raise FileNotFoundError('Plot function did not create a PNG')
        except Exception as exc:
            # Never silently drop the plot or present a cached image as current.
            from datetime import datetime, timezone
            cached = Path(ROOT) / 'scripts' / 'logs' / 'figures' / f'{CAMNAME}_temperature_and_system.png'
            try:
                with cached.open('rb') as handle:
                    if handle.read(8) != b'\x89PNG\r\n\x1a\n':
                        raise ValueError('Cached plot is not a PNG')
                    handle.seek(-12, 2)
                    if handle.read(12) != b'\x00\x00\x00\x00IEND\xaeB`\x82':
                        raise ValueError('Cached PNG is incomplete')
                saved_at = datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc)
                image_path = str(cached)
                body += (f'\nPrevious monitoring plot attached (file saved {saved_at:%Y-%m-%d %H:%M:%S} UTC). '
                         f'Plot was not refreshed: {type(exc).__name__}.')
            except (OSError, ValueError):
                image_path = None
                body += '\nMonitoring plot unavailable: ' + type(exc).__name__ + '; no valid cached PNG.'
            print('Morning monitoring plot generation failed:', type(exc).__name__, str(exc), flush=True)
        status = 'MANApi Ok' if camera_ok else 'MANApi camera check FAILED'
        accepted = toolSendMail(
            MailFROM, MailPASS, MailDEST,
            f'[{SITE} {CAMNAME}] Daily status --- {status}', body,
            Image=image_path, lookup_wan_ip=True, include_footer=False,
            ROOT=ROOT, SITE=SITE, STATION=STATION, CAMNAME=CAMNAME
        )
        if accepted:
            _write_log_state(state_path, {'sent_date': now.date().isoformat()})
        return bool(accepted)

###############################################################################
#%%
#
# 4-picam_video: Function to record video #####################################

# 4.1-picam_video: Record video atomically and validate output ################
def picam_video(ROOT, SITE,STATION, DIR_DEST, CAMNAME, MailFROM, MailPASS, MailDEST, DURATION, FRAME_RATE, IMAGE_WIDTH, IMAGE_HEIGTH):
    """Record H.264 to a temporary file; rename only after non-empty successful capture."""
    import os
    import subprocess
    import time

    YEAR = time.strftime('%Y')
    MONTH = time.strftime('%m')
    DAY = time.strftime('%d')
    HOUR = time.strftime('%H')

    extension = 'h264'

    video_name = (
        SITE
        + '_video_'
        + CAMNAME
        + '_'
        + YEAR
        + '-'
        + MONTH
        + '-'
        + DAY
        + '-'
        + HOUR
        + '-00.'
        + extension
    )

    # Ensure the video destination exists
    os.makedirs(DIR_DEST, exist_ok=True)

    final_filename = os.path.join(DIR_DEST, video_name)
    if os.path.exists(final_filename):
        raise FileExistsError('Recording already exists for this hour; retain it before retrying')
    filename = final_filename + '.recording'

    print('recording...')

    # Convert seconds to milliseconds for rpicam-vid
    DURATION = DURATION + '000'

    print('filename= ' + filename)
    print('frame rate= ' + FRAME_RATE)
    print('WIDTH= ' + IMAGE_WIDTH)
    print('HEIGHT= ' + IMAGE_HEIGTH)

    # Capture video
    result = subprocess.run([
        'rpicam-vid',
        '--nopreview',
        '--codec', 'libav',
        '--libav-format', 'h264',
        '--width', IMAGE_WIDTH,
        '--height', IMAGE_HEIGTH,
        '--framerate', FRAME_RATE,
        '-t', DURATION,
        '-o', filename
    ])

    # Check if rpicam-vid failed
    if result.returncode != 0:
        TEXT = (
            toolInstant('%Y-%m-%d %H:%M:%S')
            + ' | ERROR: Video recording failed: '
            + filename
        )

        print(TEXT)

        toolAddLog(
            ROOT,
            SITE,
            STATION,
            'ERROR',
            TEXT,
            CAMNAME,
            MailFROM,
            MailPASS,
            MailDEST
        )

        return False

    # Additional check: confirm that the file exists and is not empty
    if not os.path.isfile(filename) or os.path.getsize(filename) == 0:
        TEXT = (
            toolInstant('%Y-%m-%d %H:%M:%S')
            + ' | ERROR: Video file was not created correctly: '
            + filename
        )

        print(TEXT)

        toolAddLog(
            ROOT,
            SITE,
            STATION,
            'ERROR',
            TEXT,
            CAMNAME,
            MailFROM,
            MailPASS,
            MailDEST
        )

        return False

    os.replace(filename, final_filename)
    filename = final_filename
    # Success
    TEXT = (
        toolInstant('%Y-%m-%d %H:%M:%S')
        + ' | Video properly recorded and saved as '
        + filename
    )

    print(TEXT)

    toolAddLog(
        ROOT,
        SITE,
        STATION,
        'LOG',
        TEXT,
        CAMNAME,
        MailFROM,
        MailPASS,
        MailDEST
    )

    return True

###############################################################################
#%%
#
# 5-daytime_products: Extract daytime products: snapshot and timex ############

# 5.1-daytime_products helper: Validate video before product generation #######
def validate_video(filename, duration, fps, width, height):
    """Decode input; tolerate up to 5% missing frames, never empty/wrong geometry."""
    import cv2 as cv
    capture = cv.VideoCapture(str(filename))
    count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (int(height), int(width)):
                raise ValueError('Unexpected frame dimensions')
            count += 1
    finally:
        capture.release()
    expected = float(duration) * float(fps)
    if count == 0 or count < expected * 0.95:
        raise ValueError(f'Incomplete video: {count} decoded frames; expected approximately {expected:g}')
    return count

# 5.2-daytime_products: Extract daytime products: snapshot and timex ##########
def daytime_products(ROOT,SITE,STATION,DIR_DEST,CAMNAME,VIDEO_DURATION,MailFROM,MailPASS,MailDEST, *, video_path=None):
    """Generate atomic timex and midpoint snapshot images; log names and elapsed time."""
    import cv2 as cv
    import numpy as np
    from pathlib import Path
    import time
    started = time.monotonic()
    paths = [Path(video_path)] if video_path else sorted(Path(DIR_DEST).glob('*.h264'))[-1:]
    if not paths:
        raise ValueError('No video available')
    filename = paths[0]
    year, month, day, hour, minute = parse_ymdhm_from_filename(filename.name)
    create_folders(ROOT,SITE,STATION,CAMNAME,year,month,day)
    folder = Path(ROOT)/'images'/SITE/STATION/SITE/('c'+CAMNAME)/year/month/day
    capture = cv.VideoCapture(str(filename))
    total = None
    count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if total is None:
                total = frame.astype(np.float64)
            else:
                total += frame
            count += 1
    finally:
        capture.release()
    if not count:
        raise ValueError('No decodable frames')
    timex = np.clip(total/count,0,255).astype(np.uint8)
    capture = cv.VideoCapture(str(filename))
    snapshot = None
    try:
        for index in range(count//2+1):
            ok, frame = capture.read()
            if not ok:
                raise ValueError('Cannot decode midpoint snapshot')
            snapshot = frame
    finally:
        capture.release()
    for kind, image in [('m',timex),('s',snapshot)]:
        destination = folder/f'{SITE}_{kind}_{CAMNAME}_{year}-{month}-{day}-{hour}-{minute}.png'
        temporary = destination.with_suffix('.tmp.png')
        if not cv.imwrite(str(temporary),image) or not valid_image(temporary):
            raise ValueError('Failed to write product')
        temporary.replace(destination)
    
    elapsed_minutes = (time.monotonic() - started) / 60

    product_names = [
        f'{SITE}_{kind}_{CAMNAME}_{year}-{month}-{day}-{hour}-{minute}.png'
        for kind in ('m', 's')
    ]

    TEXT = (
        f"{toolInstant('%Y-%m-%d %H:%M:%S')} | "
        f"{' and '.join(product_names)} were properly calculated "
        f"and saved in {folder}/ in {elapsed_minutes:.1f} minutes"
    )

    print(TEXT)
    toolAddLog(
        ROOT, SITE, STATION, 'LOG', TEXT,
        CAMNAME, MailFROM, MailPASS, MailDEST
    )
           
    return True

###############################################################################
#%%
#
# 6-night_products: Extract night products: variance and timestack ############

# 6.1-night_products: Validate videos and generate night products #############
def night_products(ROOT,SITE,STATION,DIR_DEST,DIR_SOCIB,PORTAL,SSH_PORT,USER, CAMNAME, VIDEO_DURATION, TIMESTACK_TRANSECT, TIMESTACK_ORIENTATION, MailFROM, MailPASS, MailDEST, *, VIDEO_FPS=None, IMAGE_WIDTH=None, IMAGE_HEIGHT=None):
    """Validate each retained video, generate night products and return cleanup candidates."""
    import time, glob, os, shutil, cv2 as cv, numpy as np, sys
    from numexpr import evaluate # Used for multiprocessing of numpy arrays computations
    from pathlib import Path
    from os import path


    # Snapshot this run: retained videos must not be processed repeatedly.
    completed = []
    videos_recorded = sorted(glob.glob(os.path.join(DIR_DEST, '*.h264')), reverse=True)
    for latest_video in videos_recorded:
        if toolInstant('%H') == '05':
            break
        try:
            if None in (VIDEO_FPS, IMAGE_WIDTH, IMAGE_HEIGHT):
                raise ValueError('Night processing requires frame rate and dimensions')
            video_signature = file_signature(latest_video)
            validate_video(latest_video, VIDEO_DURATION, VIDEO_FPS, IMAGE_WIDTH, IMAGE_HEIGHT)
            # Repair the day products for this specific video, not the newest retained one.
            ### Get filename from latest video to find the right timex
            filename = os.path.basename(latest_video)

            # ---- NEW: robust parsing (works for clm & muro) ----
            YEAR_v, MONTH_v, DAY_v, HOUR_v, MIN_v = parse_ymdhm_from_filename(filename)

            extension = 'png'
            # extension_tn = 'jpeg'

            DIR_products = ROOT + 'images/' + SITE + '/' + STATION + '/' + SITE +'/c'+ CAMNAME + '/' + YEAR_v + '/' + MONTH_v + '/' + DAY_v + '/'
            DIR_products_socib = DIR_SOCIB + SITE +'/c'+ CAMNAME + '/' + YEAR_v + '/' + MONTH_v + '/' + DAY_v + '/'

            stdev_name = SITE+'_d_'+CAMNAME+'_'+YEAR_v+'-'+MONTH_v+'-'+DAY_v+'-'+HOUR_v+'-'+ MIN_v + '.' +extension
            timestack_name = SITE+'_t_test_'+CAMNAME+'_'+YEAR_v+'-'+MONTH_v+'-'+DAY_v+'-'+HOUR_v+'-'+ MIN_v + '.' +extension

            ## CREATE FOLDERS FOR THE VIDEO TO BE PROCESSED
            # Avoiding troubles if pythonDatime.py is running and has to process elderlier videos

            # Create folders for the video to be processed if needed.
            # create_video_folders(ROOT,SITE,STATION,DIR_DEST,CAMNAME)


            # Check if day_products already exists and if not compute dayproducts before night_products.

            timex_name = SITE+'_m_'+CAMNAME+'_'+YEAR_v+'-'+MONTH_v+'-'+DAY_v+'-'+HOUR_v+'-'+ MIN_v + '.' +extension
            snapshot_name = SITE+'_s_'+CAMNAME+'_'+YEAR_v+'-'+MONTH_v+'-'+DAY_v+'-'+HOUR_v+'-'+ MIN_v + '.' +extension

    		#########################################################################################################################
            # Function to check if file exists on the remote machine using rsync without transferring

            def check_remote_file_exist(USER, SSH_PORT, PORTAL, DIR_products_socib, filename):
                """
                Checks if two specified files (main and thumbnail) exist on a remote server via SSH.

                This function uses SSH to check the existence of two files on a remote machine. It constructs
                the paths for both the main file and its corresponding thumbnail, sends an SSH command to
                the remote machine to check if both files exist, and reads the response to determine if
                both files are present.

                Args:
                    USER (str): Username for the remote server.
                    PORTAL (str): Hostname or IP address of the remote server.
                    SSH_PORT (int): SSH port number for the remote server.
                    DIR_products_socib (str): Remote directory path for the first file.
                    DIR_products_tn_socib (str): Remote directory path for the second file.
                    filename (str): Name of the first file.
                    filename_tn (str): Name of the second file.


                Returns:
                    bool: Returns True if both files exist on the remote server, False otherwise.

                Notes:
                    - If both files exist, the SSH command returns "EXISTS", and the function confirms their existence.
                """
                import pexpect

                # Construct the remote paths
                remote_path = DIR_products_socib + filename
                # remote_path_tn = DIR_products_tn_socib + filename_tn

                # Spawn the SSH process with pexpect
                # child = pexpect.spawn(f'ssh -p {SSH_PORT} {USER}@{PORTAL} "test -f \'{remote_path}\' && test -f \'{remote_path_tn}\' && echo EXISTS"')
                child = pexpect.spawn(f'ssh -p {SSH_PORT} {USER}@{PORTAL} "test -f \'{remote_path}\' && echo EXISTS"')
                child.expect(pexpect.EOF)  # Wait for the SSH command to finish

                # Read the result of SSH command
                result = child.before.decode('utf-8').strip()  # Get output before EOF and decode it
                print(f'Remote check result: {result}')  # Debugging: print the result

                # Check if the output contains "EXISTS", indicating both files exist
                if 'EXISTS' in result:
                    return True
                else:
                    return False


            ####################################
            # def check_all_files_exist(USER, SSH_PORT, PORTAL, DIR_products, DIR_products_tn, DIR_products_socib, DIR_products_tn_socib, Files2check):
            def check_all_files_exist(USER, SSH_PORT, PORTAL, DIR_products, DIR_products_socib, Files2check):
                """
                Checks if all files and their thumbnails exist either locally or remotely.

                This function uses SSH to check the existence of two files on a remote machine. It constructs
                the paths for both the main file and its corresponding thumbnail, sends an SSH command to
                the remote machine to check if both files exist, and reads the response to determine if
                both files are present.

                Args:
                    USER (str): Username for the remote server.
                    PORTAL (str): Hostname or IP address of the remote server.
                    SSH_PORT (int): SSH port number for the remote server.
                    DIR_products_socib (str): Remote directory path for the first file.
                    DIR_products_tn_socib (str): Remote directory path for the second file.
                    filename (str): Name of the first file.
                    filename_tn (str): Name of the second file.


                Returns:
                    bool: Returns True if both files exist on the remote server, False otherwise.

                Notes:
                    - The function expects a password prompt for SSH login and handles it via pexpect.
                    - If both files exist, the SSH command returns "EXISTS", and the function confirms their existence.
                """


                # Check if all files exist in both local directories or remotely

                # for filename, filename_tn in Files2check:
                #     local_path = DIR_products + filename
                #     local_path_tn = DIR_products_tn + filename_tn

                #     # Debugging: print the paths being checked
                #     print(f'Checking local path: {local_path}')
                #     print(f'Checking local path_tn: {local_path_tn}')

                #     # Check if the file exists in both local directories
                #     if not (path.isfile(local_path) and path.isfile(local_path_tn)):
                #         print(f'{filename} or {filename_tn} not found in both local directories. Checking remotely...')

                #         # If not found in both local paths, check remotely for the same pair of files
                #         if not check_remote_file_exist(USER, SSH_PORT, PORTAL, DIR_products_socib, DIR_products_tn_socib, filename, filename_tn):
                #             print(f'{filename} or {filename_tn} not found remotely either')  # Debugging: File not found remotely
                #             return False  # If any file doesn't exist in both local paths and remotely, return False
                #         else:
                #             print(f'{filename} and {filename_tn} found remotely')  # Debugging: Files found remotely
                #     else:
                #         print(f'{filename} and {filename_tn} found in both local directories')  # Debugging: Files found in both local directories

                # return True  # All files exist in both local directories or remotely

                # Check if all files exist in both local directories or remotely

                for filename in Files2check:
                    local_path = DIR_products + filename

                    # Debugging: print the paths being checked
                    print(f'Checking local path: {local_path}')
                    if not (path.isfile(local_path)):
                        print(f'{filename} not found in both local directories. Checking remotely...')

                        # If not found in both local paths, check remotely for the same pair of files
                        if not check_remote_file_exist(USER, SSH_PORT, PORTAL, DIR_products_socib, filename):
                            print(f'{filename} not found remotely either')  # Debugging: File not found remotely
                            return False  # If any file doesn't exist in both local paths and remotely, return False
                        else:
                            print(f'{filename} found remotely')  # Debugging: Files found remotely
                    else:
                        print(f'{filename} found in local directory')  # Debugging: Files found in both local directories

                return True  # All files exist in both local directories or remotely


            # List of filename pairs to check (main file and tn file)
            Files2check = [(timex_name), (snapshot_name)]
            print(f'Files2check={Files2check}')

            # Check if all day products exist locally or remotely
            day_products_exist = all(valid_image(DIR_products + name) for name in Files2check)
            if day_products_exist:
                TEXT = toolInstant('%Y-%m-%d %H:%M:%S') + ' | Day products already exist locally or remotely. Compute Night products... '
                print(TEXT)
                toolAddLog(ROOT, SITE, STATION, 'LOG', TEXT, CAMNAME, MailFROM, MailPASS, MailDEST)
            else:
                # Create folders and compute day products
                TEXT = toolInstant('%Y-%m-%d %H:%M:%S') + " | Day products don't exist locally or remotely. Computing day products... "
                print(TEXT)
                create_folders(ROOT, SITE, STATION, CAMNAME, YEAR_v, MONTH_v, DAY_v)
                daytime_products(ROOT, SITE, STATION, DIR_DEST, CAMNAME, VIDEO_DURATION, MailFROM, MailPASS, MailDEST, video_path=latest_video)
                toolAddLog(ROOT, SITE, STATION, 'LOG', TEXT, CAMNAME, MailFROM, MailPASS, MailDEST)

            ############################################################################################################
            ## Store videos from 12h
            create_folders(ROOT, SITE, STATION, CAMNAME, YEAR_v, MONTH_v, DAY_v)
            if not all(valid_image(DIR_products + name) for name in Files2check):
                raise RuntimeError('Day products are invalid; retaining source video')
            videos_store = DIR_products   # Videos to be stored

            if HOUR_v == str(12):
                shutil.copy(latest_video, videos_store)
                TEXT = toolInstant('%Y-%m-%d %H:%M:%S')+' | '+ filename + ' was properly stored'
                print(TEXT)
                toolAddLog(ROOT,SITE,STATION,'LOG',TEXT,CAMNAME,MailFROM,MailPASS,MailDEST)

            timex=cv.imread(DIR_products + timex_name)
            timex  = np.array(timex,dtype='float')

            # Configuration uses (x, y); NumPy/OpenCV indexing uses [y, x].
            (x1, y1), (x2, y2) = TIMESTACK_TRANSECT

            timeStack_transect = np.array(
                [[y1, x1],
                [y2, x2]],
                dtype=int
            )

            orientation = TIMESTACK_ORIENTATION

            if np.any(np.isnan(timeStack_transect)):
                print('''Timestack Error: timeStack_transect has a nan, it won't be computed''')
            else:
                ## Make full transect from first and last pixels in timeStack_transect to extract pixels from each frame
                # Generate first and last x and y from timeStack transect
                firstX = timeStack_transect[0,0]
                firstY = timeStack_transect[0,1]
                lastX = timeStack_transect[1,0]
                lastY = timeStack_transect[1,1]

                # Check if timeStack_transect conditions are met
                print('Checking timeStack_transect')

                if timeStack_transect.shape == (2,2) and (firstX == lastX or firstY == lastY or (abs(firstX-lastX) == abs(firstY-lastY))):
                    print('''timeStack_transeck Check: Ok -- Building transect...''')
                    ## Make empty transect array with final number of rows and columns x,y
                    jump = max(abs(firstX-lastX),abs(firstY-lastY))+1 #+1 is added to include last pixel. Jump sets the number of pixels in the transect
                    transect = np.zeros((jump,2),dtype=int)

                    ## Built transect array including all cells between first and last xy
                    # Same column pixels
                    if firstX == lastX: # Condition met: first.x == last.x
                        transect[:,0] = firstX
                        # Check the direction of the transect and order array bottom-up(first-last). Two possible directions.
                        if firstY < lastY : # from left to right
                            transect[:,1] = list(reversed(range(firstY,lastY+1,1))) #+1 is added to include last pixel

                        else: # from right to left
                            transect[:,1] = list(range(lastY,firstY+1,1)) #+1 is added to include last pixel

                    # Same row pixels
                    if firstY == lastY: # Condition met: first.y == last.y
                         transect[:,1] = firstY
                         # Check the direction of the transect and order array bottom-up(first-last). Two possible directions.
                         if firstX < lastX : # top to bottom
                             transect[:,0] = list(reversed(range(firstX,lastX+1,1))) #+1 is added to include last pixel
                         else: # bottom to top
                             transect[:,0] = list(range(lastX,firstX+1,1)) #+1 is added to include last pixel

                    # Diagonal pixels
                    else: # Condition met: abs(first.x - last.x) == abs(last.x - last.y)
                        # Check the direction of the transect and order array bottom-up(first-last). Four possible directions.
                        if (firstX > lastX) and (firstY < lastY): # diagonal up left to right
                            transect[:,0]= list(range(lastX,firstX+1,1)) #+1 is added to include last pixel
                            transect[:,1]= list(reversed(range(firstY,lastY+1,1))) #+1 is added to include last pixel

                        elif (firstX < lastX) and (firstY > lastY): # diagonal down right to left
                            transect[:,0]= list(reversed(range(firstX,lastX+1,1))) #+1 is added to include last pixel
                            transect[:,1]= list(range(lastY,firstY+1,1)) #+1 is added to include last pixel

                        elif (firstX > lastX) and (firstY > lastY): # diagonal up right to left
                            transect[:,0]= list(range(lastX,firstX+1,1)) #+1 is added to include last pixel
                            transect[:,1]= list(range(lastY,firstY+1,1)) #+1 is added to include last pixel

                        else: # (firstX < lastX) and (firstY < lastY) - diagonal down left to right
                            transect[:,0]= list(reversed(range(firstX,lastX+1,1))) #+1 is added to include last pixel
                            transect[:,1]= list(reversed(range(firstY,lastY+1,1))) #+1 is added to include last pixel

                    print('Transect build with success with length of '+str(jump)+' pixels')

                #Conditions are not met
                else:
                  print('''Conditions of timeStack_transect are not met. Check that:
                           · timeStack_transect has 2 rows and 2 columns; and
                           · first.x == last.x; or
                           · first.y==last.y; or
                           · abs(first.x - last.x) == abs(last.x - last.y)''')

            ## Loop Frames in video and compute timestack stats
            print('Starting variance computation')

            start = time.time()
            ## Compute stdev images while frames exist##
            #Set default starting numberFrames
            numberFrames = 0

            ## Capture the video
            video = cv.VideoCapture(latest_video)

            ## Loop Frames in video and compute stdev
            # print('Starting stdev computation')

            ## Loop Frames in video and compute variance (partially)
            while(True):

                # Capture frame-by-frame. In opencv (i.e. cv2) images are read as BGR instead of RGB
                ret, frame = video.read()

                # If not frame available, exit.
                if not ret:
                     break

                numberFrames += 1

                # If frame is first, prepare final array.
                if numberFrames == 1:
                     variance = evaluate('(frame - timex)**2')
                     #timestack channels
                     blue = frame[transect[:,0],transect[:,1],0]
                     green = frame[transect[:,0],transect[:,1],1]
                     red = frame[transect[:,0],transect[:,1],2]
                else:
                     variance = evaluate('variance + ((frame - timex)**2)')

                     # Stack arrays from consecutive frames to each channel  of timestack
                     blue = np.vstack((blue,frame[transect[:,0],transect[:,1],0]))
                     green = np.vstack((green,frame[transect[:,0],transect[:,1],1]))
                     red = np.vstack((red,frame[transect[:,0],transect[:,1],2]))
                     print ('Computing Variance...')

                #Divide variance by N and sqrt, to compute stdev
            video.release()
            if numberFrames == 0:
                raise ValueError('No night frames decoded')
            stdev = evaluate('sqrt(variance/numberFrames)')

            #Stack three channels in 3D array, and transpose to get number of cols == number of frames
            timestack = np.transpose(np.dstack((blue,green,red)),axes=(1,0,2))

            print('Stdev and timestack computation finsihed')


            stdev = stdev.astype(dtype='uint8') # Compute variance as variance/N; and then stdev from variance

            timestack = np.uint8(timestack)
            # Check if orientation change is required. When 'N', time 0 is left
            if not orientation == 'N':
                if orientation == 'E': # time 0 is top
                    timestack = cv.rotate(timestack, cv.ROTATE_90_CLOCKWISE)
                elif orientation == 'S': # time 0 is left
                    timestack = cv.flip(timestack, 0)
                else: #orientation is 'W'; time 0 is top
                    timestack = cv.flip(cv.rotate(timestack, cv.ROTATE_90_COUNTERCLOCKWISE),0)


            save_stedv = cv.imwrite(DIR_products + stdev_name, stdev)
            if save_stedv:
                os.chmod(DIR_products + stdev_name, 0o775)
            save_timestack = cv.imwrite(DIR_products + timestack_name, timestack)
            if save_timestack:
                os.chmod(DIR_products + timestack_name, 0o775)

            width = 256
            height = 192
            dim = (width, height)


            products = [DIR_products + name for name in
                        (timex_name, snapshot_name, stdev_name, timestack_name)]
            valid = bool(save_stedv and save_timestack) and all(valid_image(f) for f in products)
            valid = valid and file_signature(latest_video) == video_signature
            if valid:
                if HOUR_v == '12':
                    archived = os.path.join(DIR_products, filename)
                    valid = file_signature(archived)[-1] == video_signature[-1]
                    products.append(archived)
                if valid:
                    completed.append([(latest_video, video_signature)] +
                                     [(f, file_signature(f)) for f in products])
            if not valid:
                raise RuntimeError('Product validation failed; retaining local files: ' + filename)

            ##Compute processing time
            end = time.time()

            if save_stedv is True and save_timestack is True:
                TEXT = toolInstant('%Y-%m-%d %H:%M:%S')+' | '+ stdev_name + ' and ' + timestack_name + ' were properly calculated and saved in ' + DIR_products+ ' in '+ str(np.round((end-start)/60)) + ' minutes'

                print(TEXT)
                toolAddLog(ROOT,SITE,STATION,'LOG',TEXT,CAMNAME,MailFROM,MailPASS,MailDEST)

            else:
                TEXT = toolInstant('%Y-%m-%d %H:%M:%S')+' | '+ stdev_name + ' and ' + timestack_name + ''' were not saved \n\n Check if the path is corrent and that all the folders were created'''
                print(TEXT)
                toolAddLog(ROOT,SITE,STATION,'ERROR',TEXT,CAMNAME,MailFROM,MailPASS,MailDEST)

            print("Now it's time to rest during 5 minutes")
            time.sleep(3) #300



        except Exception as exc:
            toolAddLog(ROOT,SITE,STATION,'ERROR',
                       'Video retained after processing failure: '+str(latest_video)+'; '+str(exc),
                       CAMNAME,MailFROM,MailPASS,MailDEST)
            continue

    # TEXT = toolInstant('%Y-%m-%d %H:%M:%S')+' | '+ 'All videos were properly processed. Good Night!'
    # print(TEXT)
    # toolAddLog(ROOT,SITE,STATION,'LOG',TEXT,CAMNAME,MailFROM,MailPASS,MailDEST)

    return completed

###############################################################################
#%%
#
# 7-update_folders_socib: Upload, verify and update folders in the file system #

# 7.1-update_folders_socib: Serialise upload operations under one lock ########
def update_folders_socib(
    ROOT, DIR_DEST, DIR_SOCIB, PORTAL, SSH_PORT, USER,
    SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
    caller, completed_batches=()
):
    """Serialise uploads so archive staging, verification and cleanup cannot race."""
    from pathlib import Path
    lock = Path(ROOT) / 'scripts' / 'logs' / '.annual' / 'upload.lock'
    with _log_lock(lock):
        return _update_folders_socib_locked(
            ROOT, DIR_DEST, DIR_SOCIB, PORTAL, SSH_PORT, USER,
            SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
            caller, completed_batches
        )

# 7.2-update_folders_socib helper: Stage annual log archives for upload #######
def _prepare_log_archives(ROOT, SITE, STATION, CAMNAME, source):
    """Stage registered archives in the image tree; return an immutable snapshot.

    Called under the upload lock. Copy via a temporary file outside the uploaded
    tree so an incomplete copy is never uploaded. The original stays in pending/
    until delivery has been verified. No unregistered .log files are deleted.
    """
    import os
    import shutil
    import tempfile
    from pathlib import Path
    folder = Path(ROOT) / 'scripts' / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    owner = [str(SITE), str(STATION), str(CAMNAME)]
    # Rotate dormant logs too, not only streams which happen to write on 1 Jan.
    # Current mail logs are camera-based (MAIL-x1.log). Keep recognising the
    # previous station-based name only so an existing MAIL-sirena.log can be
    # archived once as legacy data instead of being abandoned.
    current_mail = 'MAIL-' + str(CAMNAME) + '.log'
    legacy_mail = 'MAIL-' + str(STATION) + '.log'
    names = {p.name for p in folder.glob('*.log')
             if p.name.endswith('-' + str(CAMNAME) + '.log')
             or p.name in (current_mail, legacy_mail)}
    names.update(p.name[:-5] for p in (folder / '.annual').glob('*.log.json')
                 if p.name[:-5].endswith('-' + str(CAMNAME) + '.log')
                 or p.name[:-5] in (current_mail, legacy_mail))
    snapshot = []
    for filename in sorted(names):
        _append_annual_log(ROOT, SITE, STATION, CAMNAME, filename)
        state_path = folder / '.annual' / (filename + '.json')
        with _log_lock(state_path.with_suffix('.lock')):
            state = _read_log_state(state_path, owner)
            for record in state['pending']:
                original = state_path.parent / 'pending' / record['name']
                staged = (Path(source) / str(record['destination_year']) /
                          '01' / '01' / 'logs' / record['name'])
                # A restart after verified cleanup can leave only the journal.
                if not original.exists() and not staged.exists():
                    if record.get('delivery_verified'):
                        continue
                    raise RuntimeError('Pending log archive is missing; inspect journal')
                if original.exists() and not _log_archive_matches(original, record):
                    raise RuntimeError('Pending log archive changed; retaining it')
                if not staged.exists():
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    # Staging filesystem may differ from the active-log filesystem.
                    staging = Path(source) / '.manapi-log-staging'
                    staging.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(prefix='archive-', dir=staging)
                    try:
                        with os.fdopen(fd, 'wb') as target, original.open('rb') as src:
                            shutil.copyfileobj(src, target)
                            target.flush()
                            os.fsync(target.fileno())
                        if not _log_archive_matches(temporary, record):
                            raise RuntimeError('Archive copy verification failed')
                        os.replace(temporary, staged)
                        _sync_log_directory(staged.parent)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                if not _log_archive_matches(staged, record):
                    raise RuntimeError('Staged log archive changed; retaining both copies')
                snapshot.append((state_path, record['name'], staged, file_signature(staged)))
            state['pending'] = [r for r in state['pending'] if not (
                r.get('delivery_verified')
                and not (state_path.parent / 'pending' / r['name']).exists()
                and not (Path(source) / str(r['destination_year']) /
                         '01' / '01' / 'logs' / r['name']).exists())]
            _write_log_state(state_path, state)
    return snapshot

# 7.3-update_folders_socib helper: Remove verified local log archives #########
def _clean_verified_log_archives(snapshot, SITE, STATION, CAMNAME):
    """Remove only unchanged snapshot archives after the caller verifies delivery."""
    owner = [str(SITE), str(STATION), str(CAMNAME)]
    removed = 0
    for state_path, name, staged, signature in snapshot:
        with _log_lock(state_path.with_suffix('.lock')):
            state = _read_log_state(state_path, owner)
            record = next(r for r in state['pending'] if r['name'] == name)
            original = state_path.parent / 'pending' / name
            if file_signature(staged) != signature:
                raise RuntimeError('Log archive changed after upload; retaining it')
            if original.exists() and not _log_archive_matches(original, record):
                raise RuntimeError('Pending log changed after upload; retaining it')
            record['delivery_verified'] = True
            _write_log_state(state_path, state)
            if original.exists():
                original.unlink()
                _sync_log_directory(original.parent)
            staged.unlink()
            _sync_log_directory(staged.parent)
            state['pending'].remove(record)
            _write_log_state(state_path, state)
            removed += 1
    return removed

# 7.4-update_folders_socib helper: Upload, checksum-verify and clean validated files #
def _update_folders_socib_locked(
    ROOT, DIR_DEST, DIR_SOCIB, PORTAL, SSH_PORT, USER,
    SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
    caller, completed_batches=()
):
    """Upload, verify remote checksums, then clean validated night batches."""
    import subprocess
    import shlex
    from pathlib import Path

    source = (
        Path(ROOT) / 'images' / SITE / STATION / SITE / ('c' + CAMNAME)
    )

    def log(level, message):
        """Add a timestamped operational message to console and the configured log."""
        text = f"{toolInstant('%Y-%m-%d %H:%M:%S')} | {message}"
        print(text)
        toolAddLog(
            ROOT, SITE, STATION, level, text,
            CAMNAME, MailFROM, MailPASS, MailDEST
        )

    try:
        archives = _prepare_log_archives(ROOT, SITE, STATION, CAMNAME, source)
        if not source.is_dir():
            raise RuntimeError('Local product directory is missing')

        # Check that batch products belong to the uploaded tree
        # and have not changed since validation.
        for batch in completed_batches:
            for filename, signature in batch[1:]:
                Path(filename).resolve().relative_to(source.resolve())
                if file_signature(filename) != signature:
                    raise RuntimeError('Products changed before upload')

        ssh = (
            'ssh -o BatchMode=yes -o ConnectTimeout=30 -p '
            + shlex.quote(str(SSH_PORT))
        )

        command = [
            'rsync', '-a', '--checksum', '--protect-args',
            '--exclude=/.manapi-log-staging/',
            '-e', ssh,
            str(source) + '/',
            f'{USER}@{PORTAL}:{DIR_SOCIB}'
        ]

        transfer = subprocess.run(
            command[:1]
            + ['--out-format=MANAPI_TRANSFER|%i|%l']
            + command[1:],
            capture_output=True,
            text=True,
            check=True,
            timeout=3600
        )

        transferred_files = 0
        transferred_bytes = 0

        for line in transfer.stdout.splitlines():
            if not line.startswith('MANAPI_TRANSFER|'):
                continue

            _, change, size = line.split('|', 2)

            # Count transferred regular files, excluding
            # directories and metadata-only changes.
            if change.startswith(('<f', '>f')):
                transferred_files += 1
                transferred_bytes += int(size.strip())

        check = subprocess.run(
            command[:1]
            + ['--dry-run', '--itemize-changes', '--out-format=%i %n']
            + command[1:],
            capture_output=True,
            text=True,
            check=True,
            timeout=3600
        )

        # Retain local data if any differences remain.
        if check.stdout.strip():
            raise RuntimeError(
                'Remote comparison reports differences; retaining local data'
            )

        if transferred_files:
            log(
                'LOG',
                'MANApi files (products and any log archives) properly sent to SOCIB '
                f'({transferred_files} files with a total size of '
                f'{transferred_bytes / 1_000_000:.2f} MB); '
                'remote checksum comparison succeeded.'
            )
        else:
            log(
                'LOG',
                'No file-content transfers were required; '
                'remote checksum comparison succeeded.'
            )

        archived = _clean_verified_log_archives(archives, SITE, STATION, CAMNAME)
        if archived:
            log('LOG', f'Annual/legacy log archives verified remotely: {archived}; '
                'local archive copies removed.')

        if caller == 'night':
            removed = clean_raspi(
                ROOT, SITE, STATION, CAMNAME,
                MailFROM, MailPASS, MailDEST,
                verified_batches=completed_batches
            )
            log(
                'LOG',
                f'Night cleanup removed {removed} validated local files.'
            )
        else:
            log(
                'LOG',
                'Daytime cleanup deferred; '
                'local image/video files retained for night processing.'
            )

        return True

    except Exception as exc:
        log(
            'ERROR',
            'Upload/verification/cleanup failed; '
            'unprocessed files retained: ' + str(exc)
        )
        return False

###############################################################################
#%%
#
# 8-clean_raspi: Verify and remove unchanged local files safely ###############

# 8.1-clean_raspi helper: Build immutable file signature for verification #####
def file_signature(filename):
    """Detect replacement or modification before deleting an individual file."""
    import hashlib
    import os
    with open(filename, 'rb') as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if identity(before) != identity(after) or after.st_size == 0:
        raise ValueError('Empty or changing file: ' + str(filename))
    return identity(after) + (digest.hexdigest(),)

# 8.2-clean_raspi helper: Validate generated image products ###################
def valid_image(filename):
    """Return whether OpenCV can decode a non-empty image."""
    import cv2
    image = cv2.imread(str(filename), cv2.IMREAD_UNCHANGED)
    return image is not None and image.size > 0

# 8.3-clean_raspi: Remove individually verified, unchanged files ##############
def clean_raspi(ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST,
                *, verified_batches=()):
    """Only remove unchanged files explicitly verified by the upload caller."""
    from pathlib import Path
    count = 0
    for batch in verified_batches:
        # Validate the whole batch before deleting its source video.
        if not all(Path(f).is_file() and not Path(f).is_symlink() and
                   file_signature(f) == signature for f, signature in batch):
            continue
        for filename, signature in batch:
            if file_signature(filename) == signature:
                Path(filename).unlink()
                count += 1
    return count

###############################################################################
#%%
#
# 9-toolAddLog: Add logs and manage annual log archives #######################
# Annual log archives: local rotation never performs network I/O.
# Deploy this module to every writer and restart pythonMonitor once. Old processes
# do not use these locks. Keep .annual state files; deleting them triggers legacy
# migration, never reconstruction of a log's year from its modification time.

# 9.1-toolAddLog helper: Return UTC time for annual log rotation ##############
def _utc_log_now():
    """Return UTC time for calendar-year rotation (also replaceable in tests)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

# 9.2-toolAddLog helper: Serialise cooperating log writers ####################
@contextmanager
def _log_lock(filename):
    """Serialise cooperating threads/processes using a stable, separate lock file."""
    import fcntl
    from pathlib import Path
    filename = Path(filename)
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open('a', encoding='utf-8') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

# 9.3-toolAddLog helper: Persist annual log directory entries #################
def _sync_log_directory(folder):
    """Persist directory entries after atomic state replacement or log renaming."""
    import os
    fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

# 9.4-toolAddLog helper: Write annual log journal state #######################
def _write_log_state(path, data):
    """Atomically write and fsync a small JSON journal; preserve the old on failure."""
    import json
    import os
    import tempfile
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_log_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

# 9.5-toolAddLog helper: Read and validate annual log journal state ###########
def _read_log_state(path, owner):
    """Load validated journal metadata; never discard corrupt or foreign state."""
    import json
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if (state.get('version') != 1 or state.get('owner') != owner
            or not isinstance(state.get('year'), int)
            or not isinstance(state.get('pending'), list)):
        raise ValueError('Invalid annual log state: ' + path.name)
    records = state['pending'] + ([state['rotation']] if state.get('rotation') else [])
    for record in records:
        name = record.get('name', '')
        if (not name or Path(name).name != name or not name.endswith('.log')
                or not isinstance(record.get('destination_year'), int)
                or not 1 <= record['destination_year'] <= 9999
                or not re.fullmatch('[0-9a-f]{64}', record.get('sha256', ''))
                or not isinstance(record.get('size'), int) or record['size'] <= 0):
            raise ValueError('Invalid pending log archive: ' + path.name)
    return state

# 9.6-toolAddLog helper: Verify archived log content ##########################
def _log_archive_matches(path, record):
    """Check immutable archive content against its recorded SHA-256 and size."""
    signature = file_signature(path)
    return signature[2] == record['size'] and signature[-1] == record['sha256']

# 9.7-toolAddLog helper: Complete annual log rotation #########################
def _finish_log_rotation(active, state_path, state):
    """Recover a journalled rename, then mark the new active year durably."""
    import os
    record = state.get('rotation')
    if not record:
        return
    archive = state_path.parent / 'pending' / record['name']
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        if not active.is_file() or not _log_archive_matches(active, record):
            raise RuntimeError('Cannot recover log rotation; preserving all files')
        os.replace(active, archive)
        _sync_log_directory(active.parent)
        _sync_log_directory(archive.parent)
    elif active.exists():
        # A cooperating writer never recreates active before journal completion.
        raise RuntimeError('Ambiguous log rotation; preserving both files')
    if not _log_archive_matches(archive, record):
        raise RuntimeError('Rotated log changed; retaining it for inspection')
    state['pending'].append(record)
    state['year'] = state.pop('next_year')
    state['rotation'] = None
    _write_log_state(state_path, state)

# 9.8-toolAddLog helper: Append and rotate active logs safely #################
def _append_annual_log(ROOT, SITE, STATION, CAMNAME, filename, text=None):
    """Rotate before appending under one lock; None rotates without adding a line.

    Existing untracked files become legacy archives on first use. A completed
    year Y goes to Y+1/01/01/logs even if this station restarts several years later.
    New messages are assigned by UTC write time; historical message text is not
    reinterpreted. Rotation/state failures preserve files and raise an error.
    """
    import os
    import uuid
    from pathlib import Path
    if Path(filename).name != filename or not filename.endswith('.log'):
        raise ValueError('Expected a plain log filename')
    folder = Path(ROOT) / 'scripts' / 'logs'
    active = folder / filename
    state_path = folder / '.annual' / (filename + '.json')
    owner = [str(SITE), str(STATION), str(CAMNAME)]
    with _log_lock(folder / '.annual' / (filename + '.lock')):
        now = _utc_log_now()
        state = _read_log_state(state_path, owner)
        if state is None:
            state = {'version': 1, 'owner': owner, 'year': now.year,
                     'pending': [], 'rotation': None}
            legacy = True
        else:
            legacy = False
        _finish_log_rotation(active, state_path, state)
        if now.year < state['year']:
            raise RuntimeError('UTC year moved backwards; check the station clock')
        if legacy or now.year > state['year']:
            if active.exists() and active.stat().st_size:
                # Flush the closed log before recording its immutable identity.
                with active.open('rb') as handle:
                    os.fsync(handle.fileno())
                signature = file_signature(active)
                label = 'legacy' if legacy else str(state['year'])
                record = {
                    'name': f'{active.stem}-{label}-{uuid.uuid4().hex}.log',
                    'destination_year': now.year if legacy else state['year'] + 1,
                    'size': signature[2], 'sha256': signature[-1]
                }
                state['rotation'] = record
                state['next_year'] = now.year
                _write_log_state(state_path, state)
                _finish_log_rotation(active, state_path, state)
            else:
                state['year'] = now.year
                _write_log_state(state_path, state)
        with active.open('a', encoding='utf-8') as handle:
            if text is not None:
                handle.write(str(text) + '\n')

# 9.9-toolAddLog: Add timestamped MANApi log records ##########################
def toolAddLog(ROOT, SITE, STATION, TIPO, TEXT,
               CAMNAME, MailFROM, MailPASS, MailDEST):
    """Append one timestamped MANApi log record and email ERROR records.

    All normal MANApi log streams use the annual rotation/locking helper.
    If the caller already supplied a leading ``YYYY-MM-DD HH:MM:SS |`` stamp,
    it is retained; otherwise one is added here.
    """
    import re

    text = str(TEXT)
    timestamp_pattern = (
        r'^\d{4}-\d{2}-\d{2} '
        r'\d{2}:\d{2}:\d{2} \|'
    )
    if not re.match(timestamp_pattern, text):
        text = toolInstant('%Y-%m-%d %H:%M:%S') + ' | ' + text

    filename = f"{TIPO}-{CAMNAME}.log"
    _append_annual_log(ROOT, SITE, STATION, CAMNAME, filename, text)

    if TIPO == 'ERROR':
        subject = f'ERROR in camera {CAMNAME} of {SITE}'
        toolSendMail(
            MailFROM, MailPASS, MailDEST, subject, text,
            ROOT=ROOT, SITE=SITE, STATION=STATION, CAMNAME=CAMNAME
        )

###############################################################################
#%%
#
# 10-measure_cpu_temp: Measure CPU temperature ################################

# 10.1-measure_cpu_temp: Measure CPU temperature and protect against overheating #
def measure_cpu_temp(ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST):
    """Monitor CPU temperature; warn above 75 C and attempt shutdown above max_temp."""
    import os
    import time
    import subprocess
    from datetime import datetime

    # Construct log file path
    log_file = f"{ROOT}/scripts/logs/TEMPERATURE-{CAMNAME}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    def _get_cpu_temperature():
        """Retrieve CPU temperature using vcgencmd with a Linux sysfs fallback."""
        try:
            raw_output = subprocess.check_output(
                ['vcgencmd', 'measure_temp'],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
            # Output format: temp=48.2'C
            return float(raw_output.replace('temp=', '').replace("'C", ''))
        except Exception:
            # Fallback to sysfs thermal zone (standard on Linux / Raspberry Pi OS)
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as thermal_file:
                    return round(float(thermal_file.read().strip()) / 1000.0, 1)
            except Exception as read_error:
                print(f"Failed to read temperature: {read_error}")
                return None

    def _execute_system_shutdown():
        """Execute system shutdown using standard paths with passwordless sudo or direct root."""
        # Flush disk caches before shutting down
        try:
            os.sync()
        except Exception:
            pass

        if os.geteuid() == 0:
            shutdown_commands = [
                ['/sbin/shutdown', '-h', 'now'],
                ['/sbin/poweroff'],
                ['/bin/systemctl', 'poweroff'],
                ['shutdown', '-h', 'now'],
                ['poweroff']
            ]
        else:
            shutdown_commands = [
                ['sudo', '-n', '/sbin/shutdown', '-h', 'now'],
                ['sudo', '-n', '/sbin/poweroff'],
                ['sudo', '-n', '/bin/systemctl', 'poweroff'],
                ['sudo', '-n', 'shutdown', '-h', 'now'],
                ['sudo', '-n', 'poweroff']
            ]

        last_error = None
        for command in shutdown_commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15
                )
                return True, result.stdout
            except subprocess.CalledProcessError as error:
                err_msg = error.stderr.strip() if error.stderr else str(error)
                last_error = f"{error}: {err_msg}"
                continue
            except (subprocess.SubprocessError, FileNotFoundError) as error:
                last_error = str(error)
                continue

        return False, str(last_error)

    try:
        log_counter = 0  # Counter to track logging iterations
        high_temp = 75
        max_temp = 90

        while True:
            # Get current timestamp
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Get Raspberry Pi temperature
            temperature = _get_cpu_temperature()
            if temperature is None:
                time.sleep(60)
                continue

            # Create log message
            TEXT = f"{timestamp} | Raspi {CAMNAME} temperature: {temperature}°C"

            # Check if temperature is over 90°C and turn off
            if temperature > max_temp:
                extreme_temp_message = f"{temperature} ºC\n{CAMNAME} in {SITE} is turning off"
                email_subject = f"Extreme Temperature of {CAMNAME} in {SITE} Turning off"
                try:
                    toolSendMail(MailFROM, MailPASS, MailDEST, email_subject, extreme_temp_message,
                                 ROOT=ROOT, SITE=SITE, STATION=STATION, CAMNAME=CAMNAME)
                except Exception as exc:
                    print('Temperature email failed:', type(exc).__name__)

                # Attempt to turn off the Raspberry Pi safely
                success, error_details = _execute_system_shutdown()
                if success:
                    print(f"Shutdown signal issued successfully for {CAMNAME} in {SITE}.")
                    break
                else:
                    # Alert administrator if shutdown failed (e.g. sudo password needed)
                    failure_subject = f"ERROR: Shutdown failed on {CAMNAME} in {SITE}"
                    failure_message = (
                        f"CRITICAL: Extreme temperature {temperature}°C reached on {CAMNAME} in {SITE}, "
                        f"but automatic shutdown failed: {error_details}\n"
                        f"Check passwordless sudo permissions on the Raspberry Pi (/etc/sudoers.d/)."
                    )
                    print(failure_message)
                    try:
                        toolSendMail(MailFROM, MailPASS, MailDEST, failure_subject, failure_message,
                                     ROOT=ROOT, SITE=SITE, STATION=STATION, CAMNAME=CAMNAME)
                    except Exception as exc:
                        print('Shutdown failure email failed:', type(exc).__name__)
                    toolAddLog(ROOT, SITE, STATION, 'ERROR', failure_message, CAMNAME, MailFROM, MailPASS, MailDEST)

            # Check if temperature is over 75°C (warning)
            elif temperature > high_temp:
                if log_counter % 15 == 0:  # Send email every 15 minutes (15 iterations)
                    degrees_to_shutdown = round(max_temp - temperature, 1)
                    high_temp_message = (
                        f"{timestamp} | Raspi {CAMNAME} temperature: {temperature}°C. "
                        f"If the temperature rises: {degrees_to_shutdown} ºC the Raspi will be turned off."
                    )
                    email_subject = f"Temperature of {CAMNAME} in {SITE} is too high"
                    toolAddLog(ROOT, SITE, STATION, 'ERROR', high_temp_message, CAMNAME, MailFROM, MailPASS, MailDEST)
                    print(f"Email sent: {email_subject} {high_temp_message}")

            # Write temperature to log file
            toolAddLog(ROOT, SITE, STATION, 'TEMPERATURE', TEXT, CAMNAME, MailFROM, MailPASS, MailDEST)

            # Print log message to console
            print(TEXT)

            # Increment log_counter
            log_counter += 1

            # Wait for 60 seconds before logging again
            time.sleep(60)

    except KeyboardInterrupt:
        print("Logging stopped.")

###############################################################################
#%%
#
# 11-monitor_cpu_ram: Monitor CPU, RAM and disk usage #########################

# 11.1-monitor_cpu_ram: Monitor CPU and RAM ###################################
def monitor_cpu_ram(ROOT, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST):
    """Log CPU utilisation averaged over 60 seconds and current RAM utilisation."""
    import psutil
    import time
    from datetime import datetime

    print("Starting CPU and RAM monitoring...")

    try:
        while True:
            # Get current timestamp
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Get CPU and RAM usage
            cpu_usage = psutil.cpu_percent(interval=60) #calculate the average CPU usage over the last 60 seconds
            ram_usage = psutil.virtual_memory().percent

            # Create log message
            TEXT = f"{timestamp} | CPU Usage: {cpu_usage}%, RAM Usage: {ram_usage}%"
            toolAddLog(ROOT,SITE, STATION, 'SYSTEM', TEXT,CAMNAME, MailFROM, MailPASS, MailDEST)
            print(TEXT)
    except Exception as e:
        print(f"Error: {e}")

# 11.2-monitor_cpu_ram helper: Check disk threshold and persistent alert state #
def check_disk_usage(paths, state_file, send_mail, *, now=None):
    """Alert at >=80%, remind daily, rearm below 75%; persist after SMTP success."""
    import json
    import os
    import time
    import psutil
    from pathlib import Path
    now = time.time() if now is None else now
    state_file = Path(state_file)
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    seen = set()
    for path in paths:
        try:
            device = str(os.stat(path).st_dev)
            if device in seen:
                continue
            seen.add(device)
            usage = psutil.disk_usage(path)
            last = state.get(device)
            if usage.percent < 75:
                state.pop(device, None)
            elif usage.percent >= 80 and (last is None or now - last >= 86400):
                text = (f'Disk usage: {usage.percent:.1f}% at {path}. '
                        f'Free: {usage.free / 2**30:.2f} GiB; total: {usage.total / 2**30:.2f} GiB. '
                        'Check uploads, processing failures and retained files. No automatic emergency deletion.')
                if send_mail(text):
                    state[device] = now
        except Exception as exc:
            print('Disk check failed for', path, type(exc).__name__)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.replace(state_file)

# 11.3-monitor_cpu_ram helper: Monitor disk usage every five minutes ##########
def monitor_disk_usage(ROOT, DIR_DEST, SITE, STATION, CAMNAME, MailFROM, MailPASS, MailDEST):
    """Run independent disk checks every five minutes with persisted alert state."""
    import time
    from pathlib import Path
    # A process lock prevents duplicate monitor processes sending repeated alarms.
    import fcntl
    state = Path.home() / '.local/state/manapi' / ('disk-' + str(CAMNAME) + '.json')
    state.parent.mkdir(parents=True, exist_ok=True)
    with open(str(state) + '.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            try:
                check_disk_usage(['/', ROOT, DIR_DEST], state,
                    lambda text: toolSendMail(MailFROM, MailPASS, MailDEST,
                        f'[{SITE}] Disk usage alert - camera {CAMNAME}', text,
                        lookup_wan_ip=False, ROOT=ROOT, SITE=SITE,
                        STATION=STATION, CAMNAME=CAMNAME))
            except Exception as exc:
                print('Disk monitor failed; will retry in five minutes:', exc, flush=True)
            time.sleep(300)

###############################################################################
#%%
#
# 12-toollogsize: Compatibility shim for annual log retention #################

# 12.1-toollogsize: Compatibility shim; annual log helpers retain full years ##
def toollogsize(log_file):
    """Legacy no-op: never truncate logs; toolAddLog manages annual retention.

    Kept for compatibility with external imports. Direct external file writes
    must migrate to toolAddLog to participate in rotation and locking.
    """
    return None

###############################################################################
#%%
#
# 13-toolSendMail: Send mail and log accepted messages ########################

# 13.1-toolSendMail: Send mail and record successful SMTP acceptance ##########
def toolSendMail(MailFROM, MailPASS, MailDEST, SUBJECT, TEXT, Image=None,
                 *, timeout=20.0, lookup_wan_ip=True, include_footer=True,
                 ROOT=None, SITE=None, STATION=None, CAMNAME=None):
    """Send plain/HTML mail with a PNG both inline and as a downloadable attachment.

    include_footer=False omits the host footer; requested WAN IP remains visible.
    PNG bytes are read once so the inline and downloadable copies are identical.
    Existing positional calls remain valid. Configuration errors skip the
    optional WAN-IP lookup, so it cannot delay the error alert. Socket timeout
    is per blocking operation, not a guaranteed total wall-clock deadline.
    """
    import os
    import socket
    import ssl
    import subprocess
    import smtplib
    from html import escape
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    import ipaddress

    if not all((MailFROM, MailPASS, MailDEST)):
        print('Missing email credentials. Unable to send the email notification.')
        return False

    IP = "not requested"
    if lookup_wan_ip:
        try:
            result = subprocess.run(
                ["curl", "--silent", "--show-error", "--fail", "--max-time", "5",
                 "https://canhazip.com"],
                capture_output=True, text=True, check=True, timeout=6,
            )
            IP = str(ipaddress.ip_address(result.stdout.strip()))
        except (OSError, ValueError, subprocess.SubprocessError):
            IP = "unavailable"
    HOST = socket.gethostname()
    body = str(TEXT)
    if include_footer:
        body += ('\n______________________________\nSent from: '
                 + HOST + '       IP WAN: ' + IP)
    elif lookup_wan_ip:
        body += '\nIP WAN: ' + IP
    recipients = [address.strip() for address in MailDEST.split(',') if address.strip()]
    if not recipients:
        raise ValueError("No email recipients configured.")

    msg = MIMEMultipart('mixed')
    related = MIMEMultipart('related')
    msg['Subject'] = SUBJECT
    msg['From'] = MailFROM
    msg['To'] = ', '.join(recipients)
    image = None
    image_bytes = None
    if Image:
        try:
            with open(Image, 'rb') as image_file:
                image_bytes = image_file.read()
            image = MIMEImage(image_bytes)
            image.add_header('Content-ID', '<image1>')
            image.add_header('Content-Disposition', 'inline', filename=os.path.basename(Image))
        except (OSError, TypeError, ValueError) as exc:
            body += '\nMonitoring image could not be attached: ' + type(exc).__name__
            print('Monitoring image attachment failed:', type(exc).__name__, flush=True)
            image = None
            image_bytes = None

    alternative = MIMEMultipart('alternative')
    alternative.attach(MIMEText(body, 'plain', 'utf-8'))
    html_body = '<html><body><pre style="white-space:pre-wrap">' + escape(body) + '</pre>'
    if image is not None:
        html_body += '<p><img src="cid:image1" alt="MANApi monitoring plot" style="max-width:100%;height:auto"></p>'
    html_body += '</body></html>'
    alternative.attach(MIMEText(html_body, 'html', 'utf-8'))
    related.attach(alternative)
    if image is not None:
        related.attach(image)
    msg.attach(related)
    if image is not None:
        # Some clients hide inline images from the attachment list. Include an
        # explicit attachment too, without a second CID or external image URL.
        downloadable = MIMEImage(image_bytes)
        downloadable.add_header('Content-Disposition', 'attachment',
                                filename=os.path.basename(Image))
        msg.attach(downloadable)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=timeout,
                          context=ssl.create_default_context()) as server:
        server.login(MailFROM, MailPASS)
        refused = server.sendmail(MailFROM, recipients, msg.as_string())

    accepted = not refused
    if accepted:
        # Use explicit context when supplied. For legacy/external callers that
        # still use the old positional API, recover non-secret identity values
        # from manapi.env so successful MANApi emails are still logged.
        log_root, log_site = ROOT, SITE
        log_station, log_cam = STATION, CAMNAME
        if not all((log_root, log_site, log_station, log_cam)):
            try:
                runtime = read_runtime_settings(require_complete=False)
                log_root = log_root or runtime.get('MANAPI_DIR_LOCAL')
                log_site = log_site or runtime.get('MANAPI_SITE')
                log_station = log_station or runtime.get('MANAPI_STATION')
                log_cam = log_cam or runtime.get('MANAPI_CAM_NAME')
            except Exception as exc:
                print('SMTP accepted but MAIL log context lookup failed:',
                      type(exc).__name__, flush=True)

        if all((log_root, log_site, log_station, log_cam)):
            mail_text = (
                toolInstant('%Y-%m-%d %H:%M:%S')
                + ' | SMTP accepted: '
                + ', '.join(recipients)
            )
            try:
                _append_annual_log(
                    log_root, log_site, log_station, log_cam,
                    f'MAIL-{log_cam}.log', mail_text
                )
            except Exception as exc:
                # Do not report an already accepted SMTP message as failed; that
                # could cause a retry and duplicate email. Surface the log issue.
                print('SMTP accepted but MAIL log update failed:',
                      type(exc).__name__, flush=True)
        else:
            print('SMTP accepted but MAIL log context is incomplete.', flush=True)

    return accepted

###############################################################################
#%%
#
# 14-toolInstant: Time now ####################################################

# 14.1-toolInstant: Return current station time ###############################
def toolInstant (FORMAT):
    """Return the current local timestamp in the requested format; station timezone should be UTC."""
    from time import strftime
    return strftime(FORMAT)

###############################################################################
#%%
#
# 15-showBanner_MANAPI: Print Banner MANApi ###################################

# 15.1-showBanner_MANAPI: Print MANApi programme banner #######################
def showBanner_MANAPI(VER):
    """Print the MANApi programme banner and supplied version."""
    from os import system
    system('clear')
    print(' ')
    print('  Starting MANApi')
    print(' ')
    system('toilet --filter border:metal Socib Manapi')
    print(' ')
    print('   -scriptVersion: '+VER+'-')

###############################################################################
#%%
#
# 16-plot_temperature_and_system: Plot monitored system variables #############

# 16.1-plot_temperature_and_system helper: Read active and pending monitoring logs #
def _read_status_lines(ROOT, CAMNAME, kind):
    """Read active and pending local logs; tolerate rotation and missing files.

    Delivered archives are not fetched from the server. Reports therefore state
    local coverage rather than promising a complete 24-hour or 14-day history.
    """
    from pathlib import Path
    folder = Path(ROOT) / 'scripts' / 'logs'
    paths = [folder / f'{kind}-{CAMNAME}.log']
    paths.extend(sorted((folder / '.annual' / 'pending').glob(f'{kind}-{CAMNAME}-*.log')))
    for path in paths:
        try:
            with path.open(encoding='utf-8', errors='replace') as handle:
                yield from handle
        except OSError:
            continue

# 16.2-plot_temperature_and_system helper: Parse recent monitoring samples ####
def _monitoring_samples(ROOT, CAMNAME, *, now=None, days=14):
    """Return sorted valid UTC temperature and CPU/RAM samples from local logs."""
    from datetime import datetime, timedelta, timezone
    import math
    now = _utc_log_now() if now is None else now
    cutoff = now - timedelta(days=days)
    temperature, system = set(), set()
    cutoff_text = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    patterns = {
        'TEMPERATURE': re.compile(r'temperature:\s*([-+\d.]+)', re.IGNORECASE),
        'SYSTEM': re.compile(r'CPU Usage:\s*([\d.]+)%.*RAM Usage:\s*([\d.]+)%')
    }
    for kind, pattern in patterns.items():
        for line in _read_status_lines(ROOT, CAMNAME, kind):
            # Skip old records before parsing their values (year-long active logs).
            if line[:19] < cutoff_text:
                continue
            try:
                stamp = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                if not cutoff <= stamp <= now:
                    continue
                match = pattern.search(line)
                if not match:
                    continue
                values = tuple(float(v) for v in match.groups())
                if not all(math.isfinite(v) for v in values):
                    continue
                if kind == 'TEMPERATURE':
                    temperature.add((stamp, values[0]))
                elif all(0 <= v <= 100 for v in values):
                    system.add((stamp, *values))
            except (ValueError, OverflowError):
                continue
    return sorted(temperature), sorted(system)

# 16.3-plot_temperature_and_system: Plot monitored system variables ###########
def plot_temperature_and_system(ROOT, SITE, CAMNAME):
    """Save and return a headless PNG, including a labelled no-data plot if empty.

    Missing streams are labelled in the plot. Malformed records are ignored.
    The x-axis spans the first/last plotted samples within the 14-day selection.
    Only available active/pending local logs are used; annual remote archives
    are not fetched. The figure is closed to avoid leaking memory in long runs.
    """
    from datetime import timedelta
    from pathlib import Path
    import os
    import tempfile
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import matplotlib.dates as mdates
    from datetime import timezone

    now = _utc_log_now()
    temperature, system = _monitoring_samples(ROOT, CAMNAME, now=now)
    figure = Figure(figsize=(10, 8), layout='constrained')
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1, sharex=True)
    series = [(temperature, 1, 'CPU temperature (°C)', '#c64b35'),
              (system, 1, 'CPU usage (%)', '#2674a6'),
              (system, 2, 'RAM usage (%)', '#20875b')]
    for axis, (rows, index, ylabel, colour) in zip(axes, series):
        if rows:
            axis.plot([r[0] for r in rows], [r[index] for r in rows], color=colour,
                      linewidth=0.8, marker='.' if len(rows) == 1 else None)
        else:
            axis.text(0.5, 0.5, 'No recent valid samples', ha='center', transform=axis.transAxes)
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.25)
        if index == 2 or ylabel == 'CPU usage (%)':
            axis.set_ylim(0, 100)
    # Fit the shared x-axis to the valid samples actually plotted, not a fixed
    # two-week window. One timestamp needs a small span to avoid identical limits.
    timestamps = [row[0] for row in temperature] + [row[0] for row in system]
    first, last = (min(timestamps), max(timestamps)) if timestamps else (now, now)
    if first == last:
        first -= timedelta(seconds=30)
        last += timedelta(seconds=30)
    axes[-1].set_xlim(first, last)
    locator = mdates.AutoDateLocator(tz=timezone.utc)
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=timezone.utc))
    axes[-1].set_xlabel('UTC — available local samples in the last 14 days')
    title = f'MANApi monitoring — {SITE} / camera {CAMNAME}'
    if not timestamps:
        title += '\nNo valid local samples in the last 14 days'
    figure.suptitle(title)
    folder = Path(ROOT) / 'scripts' / 'logs' / 'figures'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{CAMNAME}_temperature_and_system.png'
    fd, temporary = tempfile.mkstemp(prefix='.monitoring-', suffix='.png', dir=folder)
    os.close(fd)
    try:
        figure.savefig(temporary, format='png', dpi=130)
        os.replace(temporary, destination)
    finally:
        figure.clear()
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)
