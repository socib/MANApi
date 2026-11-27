"""
Simplified test suite for MANAPI deployment validation

This test file validates critical functions and ensures scripts are syntactically correct
before deployment to Raspberry Pis.
"""

import sys
import os
from unittest.mock import MagicMock
import pytest

# Add the parent directory to sys.path to import modules from 'manapi/scripts'
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'manapi', 'scripts')))

import pythonvideoFunctions

# Test 1: Verify pythonvideoFunctions can be imported
def test_pythonvideoFunctions_imports():
    """Test that pythonvideoFunctions module can be imported"""
    try:
        import pythonvideoFunctions
        assert hasattr(pythonvideoFunctions, 'readInfo')
        assert hasattr(pythonvideoFunctions, 'create_folders')
        assert hasattr(pythonvideoFunctions, 'parse_ymdhm_from_filename')
    except ImportError as e:
        pytest.fail(f"Failed to import pythonvideoFunctions: {e}")


# Test 2: Test filename parsing
def test_parse_filename_standard_format():
    """Test parsing filename with standard format: YYYY-MM-DD-HH-MM"""
    import pythonvideoFunctions
    
    filename = "video_2024-10-28-15-30.h264"
    year, month, day, hour, minute = pythonvideoFunctions.parse_ymdhm_from_filename(filename)
    
    assert year == "2024"
    assert month == "10"
    assert day == "28"
    assert hour == "15"
    assert minute == "30"


# Test 3: Test filename parsing without minutes
def test_parse_filename_without_minutes():
    """Test parsing filename without minutes (should default to '00')"""
    import pythonvideoFunctions
    
    filename = "video_2024-10-28_15.h264"
    year, month, day, hour, minute = pythonvideoFunctions.parse_ymdhm_from_filename(filename)
    
    assert year == "2024"
    assert month == "10"
    assert day == "28"
    assert hour == "15"
    assert minute == "00"


# Test 4: Verify script files exist and are readable
def test_required_scripts_exist():
    """Verify all required script files exist"""
    script_dir = os.path.join(os.path.dirname(__file__), 'manapi', 'scripts')
    required_scripts = [
        'pythonvideoFunctions.py',
        'pythonDaytime.py',
        'pythonNight.py',
        'pythonMonitor.py'
    ]
    
    for script in required_scripts:
        script_path = os.path.join(script_dir, script)
        assert os.path.isfile(script_path), f"Required script {script} not found at {script_path}"


# Test 5: Verify scripts are syntactically valid
def test_scripts_syntax_valid():
    """Verify all Python scripts have valid syntax"""
    import py_compile
    
    script_dir = os.path.join(os.path.dirname(__file__), 'manapi', 'scripts')
    scripts = [
        'pythonvideoFunctions.py',
        'pythonDaytime.py',
        'pythonNight.py',
        'pythonMonitor.py',
        'pythonNotify.py',
        'check_manapi_config.py',
    ]
    
    for script in scripts:
        script_path = os.path.join(script_dir, script)
        assert os.path.isfile(script_path), f"Missing required script: {script_path}"
        if os.path.exists(script_path):
            try:
                py_compile.compile(script_path, doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"Syntax error in {script}: {e}")


# Test 6: Verify folder creation in a temporary directory
def test_create_folders_basic(tmp_path):
    ROOT = str(tmp_path) + "/"

    pythonvideoFunctions.create_folders(
        ROOT=ROOT,
        SITE="Manapi",
        STATION="MNP-01",
        CAMNAME="01",
        YEAR_v="2024",
        MONTH_v="12",
        DAY_v="01"
    )

    expected = (
        tmp_path
        / "images"
        / "Manapi"
        / "MNP-01"
        / "Manapi"
        / "c01"
        / "2024"
        / "12"
        / "01"
    )

    assert expected.is_dir()
    # Test 7: Verify picam scripts exist
def test_picam_scripts_exist():
    """Verify picam scripts exist"""
    picam_dir = os.path.join(os.path.dirname(__file__), 'picam')
    picam_scripts = [
        'cam_capture.py',
        'cam_video.py'
    ]
    
    for script in picam_scripts:
        script_path = os.path.join(picam_dir, script)
        assert os.path.isfile(script_path), f"Picam script {script} not found"


# Test 8: Verify picam scripts are syntactically valid
def test_picam_syntax_valid():
    """Verify picam scripts have valid syntax"""
    import py_compile
    
    picam_dir = os.path.join(os.path.dirname(__file__), 'picam')
    scripts = [
        'cam_capture.py',
        'cam_prev.py',
        'cam_video.py',
        'camera_snaps.py'
    ]
    
    for script in scripts:
        script_path = os.path.join(picam_dir, script)
        assert os.path.isfile(script_path), f"Missing required script: {script_path}"
        if os.path.exists(script_path):
            try:
                py_compile.compile(script_path, doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"Syntax error in {script}: {e}")


# Test 9: Basic smoke test
def test_deployment_readiness():
    """Final smoke test to verify deployment readiness"""
    # Check directory structure
    assert os.path.isdir(os.path.join(os.path.dirname(__file__), 'manapi'))
    assert os.path.isdir(os.path.join(os.path.dirname(__file__), 'manapi', 'scripts'))
    assert os.path.isdir(os.path.join(os.path.dirname(__file__), 'picam'))
    
    # Verify .gitlab-ci.yml exists
    assert os.path.isfile(os.path.join(os.path.dirname(__file__), '.gitlab-ci.yml'))


# Test 10: Test extreme temperature shutdown handling
def test_measure_cpu_temp_extreme_shutdown(monkeypatch, tmp_path):
    """Verify that extreme temperature (>90°C) triggers shutdown and breaks loop"""
    import pythonvideoFunctions

    # Do not flush the real runner's disks.
    monkeypatch.setattr('os.sync', lambda: None)

    # Fail immediately if the monitoring loop does not exit.
    def unexpected_sleep(seconds):
        raise AssertionError(
            "Temperature test entered another monitoring cycle "
            "instead of exiting after simulated shutdown."
        )

    monkeypatch.setattr('time.sleep', unexpected_sleep)
    
    monkeypatch.setattr(pythonvideoFunctions, 'toollogsize', MagicMock())
    monkeypatch.setattr(pythonvideoFunctions, 'toolAddLog', MagicMock())
    mock_send_mail = MagicMock()
    monkeypatch.setattr(pythonvideoFunctions, 'toolSendMail', mock_send_mail)

    executed_cmds = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return MagicMock(returncode=0, stdout="Shutdown initiated")

    monkeypatch.setattr('subprocess.run', mock_subprocess_run)
    monkeypatch.setattr('subprocess.check_output', lambda *args, **kwargs: "temp=95.0'C")

    # Call measure_cpu_temp with extreme temperature; should trigger shutdown and exit loop cleanly
    pythonvideoFunctions.measure_cpu_temp(
        ROOT=str(tmp_path),
        SITE="TestSite",
        STATION="ST01",
        CAMNAME="01",
        MailFROM="from@test.org",
        MailPASS="secret",
        MailDEST="dest@test.org"
    )

    # Verify shutdown email was dispatched
    assert mock_send_mail.call_count >= 1
    args = mock_send_mail.call_args[0]
    assert "Extreme Temperature" in args[3]
    # Verify shutdown command was attempted
    assert len(executed_cmds) > 0
    assert any(any("shutdown" in part or "poweroff" in part for part in cmd) for cmd in executed_cmds)
