""" House modes for ClimateCommander (optional).

    A `daytime_savings` / `daytime_increasing` entry with `mode: night` (or a list) applies only while the house is in that
    mode. The mode comes from the MODE_CHANGE event that Lightwand and ModeManagement use, and the names from Lightwand's
    translations when that app is installed (guarded import, English names otherwise), so a Norwegian house writes `night`
    in the YAML and the code compares with `natt`. Nothing here needs Lightwand or ModeManagement.

    @Pythm / https://github.com/Pythm
"""
from __future__ import annotations

import importlib.util

# The keys of Lightwand's translations that a period may name. Any other text is compared as it is (a user's own mode).
MODE_KEYS = ('automagical', 'morning', 'night', 'away', 'off', 'custom', 'wash', 'fire', 'false_alarm', 'reset')


class EnglishModes:
    """ Used when Lightwand's translations_lightmodes is not installed. """
    MODE_CHANGE = 'MODE_CHANGE'
    automagical = 'automagical'
    morning = 'morning'
    night = 'night'
    away = 'away'
    off = 'off'
    custom = 'custom'
    wash = 'wash'
    fire = 'fire'
    false_alarm = 'false-alarm'
    reset = 'reset'


def load_translations():
    """ Lightwand's `translations` object, or the English names. """
    if importlib.util.find_spec('translations_lightmodes') is not None:
        try:
            from translations_lightmodes import translations
            return translations
        except Exception:
            pass
    return EnglishModes()


def mode_word(translations, name: str) -> str:
    """ The word the house uses for a mode key (`night` -> `natt`), or the name itself for a custom mode. """
    if name in MODE_KEYS:
        try:
            word = getattr(translations, name)
        except Exception:
            return name
        if isinstance(word, str) and word:
            return word
    return name


def mode_matches(current, names, translations) -> bool:
    """ The house mode `current` is one of `names`. The night word matches by prefix (`night`, `nightKid`, ...), the
        others exactly. An unknown house mode (None) matches nothing. """
    if not isinstance(current, str) or not current:
        return False
    night = mode_word(translations, 'night')
    for name in names:
        word = mode_word(translations, name)
        if word == night:
            if current.startswith(word):
                return True
        elif current == word:
            return True
    return False


def track(mode, translations):
    """ What a MODE_CHANGE event does to the tracked house mode: the new house mode, '' for no change. Room modes
        (`<mode>_<room>`) never change the house mode, and `reset` is the normal mode. """
    if not isinstance(mode, str) or not mode or '_' in mode:
        return ''
    if mode == mode_word(translations, 'reset'):
        return mode_word(translations, 'automagical')
    return mode
