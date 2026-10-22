import pytest

import manage


def type_keys(capsys, keys):
    """Feed keystrokes to the masked prompt; return (value, what was shown)."""
    it = iter(keys)
    value = manage._hidden("PW: ", getch=lambda: next(it))
    return value, capsys.readouterr().out


def test_each_character_shows_as_a_star(capsys):
    assert type_keys(capsys, "p@ss w0rd!\r") == ("p@ss w0rd!", "PW: **********\n")


def test_backspace_erases_and_is_harmless_when_empty(capsys):
    value, shown = type_keys(capsys, "\b\babcd\b\bX\r")
    assert value == "abX"
    assert shown == "PW: ****\b \b\b \b*\n"


def test_arrow_and_function_keys_are_ignored(capsys):
    # Windows sends these as two codes: a prefix (\xe0 or \x00) and then the key.
    assert type_keys(capsys, "a\xe0Kb\x00;c\r")[0] == "abc"


def test_ctrl_c_cancels(capsys):
    with pytest.raises(KeyboardInterrupt):
        type_keys(capsys, "ab\x03")
