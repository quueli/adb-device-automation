import subprocess

from src.adb_manager import ADBManager

XML_HEAD = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
XML_TAIL = "</hierarchy>"

EMPTY_XML = XML_HEAD + XML_TAIL

TEXT_A_XML = XML_HEAD + (
    '<node text="Option A" class="android.widget.TextView" bounds="[10,10][100,50]" />'
) + XML_TAIL


class CountingADB(ADBManager):
    def __init__(self, screen_provider):
        super().__init__('FAKE')
        self._u2_failed = True
        self._screen_provider = screen_provider
        self.dump_calls = 0
        self.tap_calls = []

    def _adb(self, *args, timeout=None):
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


def test_find_element_by_text():
    adb = CountingADB(lambda: TEXT_A_XML)
    found = adb.find_element(text='Option A')
    assert found is not None
    assert found['center'] == (55, 30)


def test_missing_element_is_none():
    adb = CountingADB(lambda: EMPTY_XML)
    assert adb.find_element(text='Option A') is None


def test_xml_cache_reused_within_ttl():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb.get_screen_text()
    assert adb.dump_calls == 1


def test_xml_cache_dropped_by_tap():
    adb = CountingADB(lambda: TEXT_A_XML)
    adb.get_screen_text()
    adb.tap(50, 50)
    adb.get_screen_text()
    assert adb.dump_calls == 2
