from src.screen_state import TIMEOUT, UNKNOWN, ScreenRule, detect_screen, screen_has_any, wait_for_screen

RULES = [
    ScreenRule('ERROR', any_of=('something went wrong', 'try again later')),
    ScreenRule('CODE', any_of=('enter code',), none_of=('calling you',)),
    ScreenRule('CALL', any_of=('calling you',)),
    ScreenRule('FORM', any_of=('your number',)),
    ScreenRule('LOADING', any_of=('please wait',)),
]


def test_first_matching_rule_wins():
    # the error dialog sits on top of the form, so both texts are in the dump
    dump = '<node text="Your number" /><node text="Something went wrong" />'
    assert detect_screen(dump, RULES) == 'ERROR'


def test_none_of_excludes_a_rule():
    assert detect_screen('<node text="Enter code" /><node text="Calling you" />', RULES) == 'CALL'
    assert detect_screen('<node text="Enter code" />', RULES) == 'CODE'


def test_unknown_when_nothing_matches():
    assert detect_screen('<node text="Chats" />', RULES) == UNKNOWN
    assert detect_screen('', RULES) == UNKNOWN


class SequenceADB:
    def __init__(self, *screens):
        self._screens = list(screens)
        self.calls = 0

    def get_screen_text(self):
        self.calls += 1
        if len(self._screens) > 1:
            return self._screens.pop(0)
        return self._screens[0]


def test_wait_skips_transient_state():
    adb = SequenceADB('<node text="Please wait" />', '<node text="Please wait" />', '<node text="Enter code" />')
    assert wait_for_screen(adb, RULES, timeout=3, poll_interval=0.01, transient=('LOADING',)) == 'CODE'
    assert adb.calls == 3


def test_wait_returns_unlisted_transient():
    adb = SequenceADB('<node text="Please wait" />')
    assert wait_for_screen(adb, RULES, timeout=1, poll_interval=0.01) == 'LOADING'


def test_wait_timeout():
    adb = SequenceADB('<node text="Chats" />')
    assert wait_for_screen(adb, RULES, timeout=0.2, poll_interval=0.05) == TIMEOUT


def test_screen_has_any():
    adb = SequenceADB('<node text="Connecting..." /><node text="Chats"/>')
    assert screen_has_any(adb, ('connecting...', 'waiting for network')) is True
    assert screen_has_any(None, ('connecting...',), screen_text='<node text="Chats" />') is False
