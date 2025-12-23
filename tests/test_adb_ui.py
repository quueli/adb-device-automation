import subprocess
import time

import pytest

from src.adb import ADBManager, escape_input_text

XML_HEAD = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
XML_TAIL = "</hierarchy>"

EMPTY_XML = XML_HEAD + XML_TAIL

TEXT_A_XML = XML_HEAD + (
    '<node text="Option A" class="android.widget.TextView" bounds="[10,10][100,50]" />'
) + XML_TAIL

TEXT_B_XML = XML_HEAD + (
    '<node text="Option B" class="android.widget.TextView" bounds="[10,60][100,100]" />'
) + XML_TAIL

FORM_XML = XML_HEAD + (
    '<node class="android.widget.FrameLayout" bounds="[0,0][720,1600]">'
    '<node text="+1" class="android.widget.EditText"'
    ' resource-id="com.example.app:id/code" bounds="[95,665][199,732]" />'
    '<node text="" class="android.widget.EditText"'
    ' resource-id="com.example.app:id/number" bounds="[232,665][626,732]" />'
    '<node text="Continue" class="android.widget.Button"'
    ' resource-id="com.example.app:id/next" bounds="[60,800][660,880]" />'
    '<node text="" content-desc="Back" class="android.widget.ImageButton" bounds="[0,0][80,80]" />'
    '</node>'
) + XML_TAIL


class CountingADB(ADBManager):
    def __init__(self, screen_provider):
        super().__init__('FAKE')
        self._u2_failed = True
        self._screen_provider = screen_provider
        self.dump_calls = 0
        self.tap_calls = []

    def _adb(self, *args, timeout=None):
        # exec-out path: behave like a device that does not support it
        return subprocess.CompletedProcess(args, 1, stdout='', stderr='')

    def execute_command(self, command, timeout=None):
        if command.startswith('uiautomator dump'):
            self.dump_calls += 1
            return ''
        if command.startswith('cat '):
            return self._screen_provider()
        return ''

    def tap(self, x, y):
        self.tap_calls.append((x, y))
        self.invalidate_xml_cache()


@pytest.fixture(autouse=True)
def _no_tap_delay(monkeypatch):
    monkeypatch.setenv('BUTTON_TAP_DELAY', '0')


def test_find_all_edittexts():
    adb = CountingADB(lambda: FORM_XML)

    edits = adb.find_all_elements(class_name='EditText')
    edits.sort(key=lambda e: e['center'][0])

    assert len(edits) == 2
    assert edits[0]['center'] == (147, 698)
    assert edits[1]['center'] == (429, 698)


def test_find_by_resource_id_suffix():
    adb = CountingADB(lambda: FORM_XML)
    found = adb.find_element(resource_id='id/next')
    assert found is not None
    assert found['center'] == (360, 840)
    assert found['node'].attrib['text'] == 'Continue'


def test_find_by_desc_and_class():
    adb = CountingADB(lambda: FORM_XML)
    # class_name matches on the suffix, so 'Button' would also take the ImageButton
    assert adb.find_element(content_desc='back', class_name='ImageButton') is not None
    assert adb.find_element(content_desc='back', class_name='EditText') is None
    assert adb.find_element(content_desc='bac', partial_desc=False) is None


def test_wait_any_text_hit():
    adb = CountingADB(lambda: TEXT_B_XML)
    found = adb.wait_for_any_text(['Option A', 'Option B'], timeout=2, poll_interval=0.05)
    assert found is not None
    assert found['matched_text'] == 'Option B'
    assert found['center'] == (55, 80)


def test_wait_any_text_timeout():
    adb = CountingADB(lambda: EMPTY_XML)
    start = time.time()
    found = adb.wait_for_any_text(['Option A', 'Option B'], timeout=1, poll_interval=0.1)
    assert found is None
    assert time.time() - start >= 1


def test_wait_any_text_one_dump_per_round():
    adb = CountingADB(lambda: TEXT_B_XML)
    adb.wait_for_any_text(['Option A', 'Option B', 'Option C'], timeout=1)
    assert adb.dump_calls == 1


def test_xml_cache_reused_within_ttl():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb.get_screen_text()
    assert adb.dump_calls == 1


def test_xml_cache_expires_after_ttl():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb._xml_cache_time -= 1.0
    adb.get_screen_text()
    assert adb.dump_calls == 2


def test_xml_cache_dropped_by_tap():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb.tap(50, 50)
    adb.get_screen_text()
    assert adb.dump_calls == 2


def test_use_cache_false_bypasses_cache():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb.get_screen_text(use_cache=False)
    assert adb.dump_calls == 2


def test_safe_tap_xml_hit():
    adb = CountingADB(lambda: TEXT_A_XML)
    assert adb.safe_tap(text='Option A', partial_text=False, timeout=1) is True
    assert adb.tap_calls == [(55, 30)]
    assert adb.get_tap_metrics() == {'xml_hit': 1, 'fallback': 0, 'miss': 0}


def test_safe_tap_fallback_coords():
    adb = CountingADB(lambda: EMPTY_XML)
    assert adb.safe_tap(text='Nonexistent', timeout=1, fallback_coords=(1, 1)) is True
    assert adb.tap_calls == [(1, 1)]
    assert adb.get_tap_metrics() == {'xml_hit': 0, 'fallback': 1, 'miss': 0}


def test_safe_tap_miss():
    adb = CountingADB(lambda: EMPTY_XML)
    assert adb.safe_tap(text='Nonexistent', timeout=1) is False
    assert adb.tap_calls == []
    assert adb.get_tap_metrics() == {'xml_hit': 0, 'fallback': 0, 'miss': 1}


def test_escape_input_text():
    assert escape_input_text('hello world') == 'hello%sworld'
    assert escape_input_text('a&b|c;d') == 'a\\&b\\|c\\;d'
    assert escape_input_text('it\'s "x" $y') == 'it\\\'s%s\\"x\\"%s\\$y'
    assert escape_input_text('plain-text_123') == 'plain-text_123'
