import pytest
from unittest.mock import patch, MagicMock, ANY

# We need to mock undetected_chromedriver before importing browser_factory.
import sys

# We create the mock module BEFORE importing.
mock_uc = MagicMock()
mock_pkg = MagicMock()
mock_pkg_version = MagicMock()

# Save original modules to restore later
orig_uc = sys.modules.get('undetected_chromedriver')
orig_pkg = sys.modules.get('packaging')
orig_pkg_version = sys.modules.get('packaging.version')

sys.modules['undetected_chromedriver'] = mock_uc
sys.modules['packaging'] = mock_pkg
sys.modules['packaging.version'] = mock_pkg_version

try:
    from engine.kernel.browser_factory import create_chrome
finally:
    # Safely restore original sys.modules
    if orig_uc is not None:
        sys.modules['undetected_chromedriver'] = orig_uc
    else:
        sys.modules.pop('undetected_chromedriver', None)

    if orig_pkg is not None:
        sys.modules['packaging'] = orig_pkg
    else:
        sys.modules.pop('packaging', None)

    if orig_pkg_version is not None:
        sys.modules['packaging.version'] = orig_pkg_version
    else:
        sys.modules.pop('packaging.version', None)

@pytest.fixture
def mock_dependencies():
    with patch('engine.kernel.browser_factory.detect_chrome_version') as m_detect, \
         patch('engine.kernel.browser_factory.purge_stale_chromedriver') as m_purge, \
         patch('engine.kernel.browser_factory._kill_all_chrome_processes') as m_kill_all, \
         patch('engine.kernel.browser_factory._kill_chrome_processes_for_profile') as m_kill_prof, \
         patch('engine.kernel.browser_factory._unlock_profile') as m_unlock, \
         patch('engine.kernel.browser_factory._find_chrome_binary') as m_find_bin, \
         patch('engine.kernel.browser_factory._launch_chrome_with_timeout') as m_launch, \
         patch('engine.kernel.browser_factory._prune_tabs_to_one') as m_prune, \
         patch('engine.kernel.browser_factory._extract_version_from_error') as m_extract, \
         patch('engine.kernel.browser_factory.time.sleep') as m_sleep:




        # Setup default successful mock returns
        m_detect.return_value = 115
        mock_driver = MagicMock()
        mock_driver.capabilities = {'goog:chromeOptions': {'debuggerAddress': 'localhost:9222'}}
        mock_driver.quit = MagicMock()
        m_launch.return_value = (mock_driver, None)

        yield {
            'detect': m_detect,
            'purge': m_purge,
            'kill_all': m_kill_all,
            'kill_prof': m_kill_prof,
            'unlock': m_unlock,
            'find_bin': m_find_bin,
            'launch': m_launch,
            'prune': m_prune,
            'extract': m_extract,
            'driver': mock_driver,
            'sleep': m_sleep
        }

def test_create_chrome_happy_path(mock_dependencies):
    deps = mock_dependencies

    driver = create_chrome(user_data_dir=None, profile_directory=None)

    assert driver == deps['driver']
    deps['launch'].assert_called_once()

    # Check that fresh options are created and used
    args, kwargs = deps['launch'].call_args
    assert 'fresh_options' in kwargs

    # Prune tabs should be called
    deps['prune'].assert_called_once_with(driver, start_url=None)

def test_create_chrome_headless(mock_dependencies):
    deps = mock_dependencies

    create_chrome(user_data_dir=None, profile_directory=None, headless=True)

    args, kwargs = deps['launch'].call_args
    fresh_options = kwargs['fresh_options']

    # In our mock, fresh_options is a MagicMock returned by uc.ChromeOptions()
    # We check if add_argument was called with headless arguments
    add_arg_calls = [call.args[0] for call in fresh_options.add_argument.mock_calls]
    assert '--headless' in add_arg_calls
    assert '--disable-gpu' in add_arg_calls
    assert '--no-sandbox' in add_arg_calls

def test_create_chrome_kill_existing(mock_dependencies):
    deps = mock_dependencies

    create_chrome(user_data_dir=None, profile_directory=None, kill_existing=True)

    deps['kill_all'].assert_called_once()

def test_create_chrome_retry_and_self_healing(mock_dependencies):
    deps = mock_dependencies

    # First attempt fails with version error, second succeeds
    deps['launch'].side_effect = [
        (None, "This version of ChromeDriver only supports Chrome version 114"),
        (deps['driver'], None)
    ]
    deps['extract'].return_value = 114

    driver = create_chrome(user_data_dir=None, profile_directory=None, max_retries=3)

    assert driver == deps['driver']
    assert deps['launch'].call_count == 2
    deps['extract'].assert_called_once()
    assert deps['purge'].call_count > 0

def test_create_chrome_exhausted_retries(mock_dependencies):
    deps = mock_dependencies

    # Always fail
    deps['launch'].return_value = (None, "Some error")

    driver = create_chrome(user_data_dir=None, profile_directory=None, max_retries=3)

    assert driver is None
    assert deps['launch'].call_count == 3

def test_create_chrome_zombie_cleanup_on_retry(mock_dependencies):
    deps = mock_dependencies

    deps['launch'].side_effect = [
        (None, "Error 1"),
        (None, "Error 2"),
        (deps['driver'], None)
    ]

    create_chrome(user_data_dir="/tmp/profile", profile_directory=None, max_retries=3)

    # On first launch failure, it should kill profile processes and unlock
    # Also at the very beginning of the function
    assert deps['kill_prof'].call_count >= 2
    assert deps['unlock'].call_count >= 2
