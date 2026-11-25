"""The adapter must not resolve lower-priority language settings twice."""
import sys
from pathlib import Path

import pytest

from omd import reel


@pytest.mark.parametrize(('url', 'args', 'expected'), [
    ('https://www.douyin.com/video/123', ['--spoken-language-mode', 'inherit-adapter-default'], 'zh'),
    ('https://www.youtube.com/shorts/123', ['--spoken-language-mode', 'inherit-adapter-default'], None),
    ('https://www.douyin.com/video/123', [], 'en'),
    ('https://www.youtube.com/shorts/123', [], 'en'),
    ('https://www.douyin.com/video/123', ['--whisper-lang', 'auto'], None),
    ('https://www.douyin.com/video/123', ['--whisper-lang', 'fr'], 'fr'),
])
def test_reel_main_preserves_resolved_asr_precedence(monkeypatch, tmp_path, url, args, expected):
    monkeypatch.setenv('OMD_CONFIG', str(tmp_path / 'missing.json'))
    monkeypatch.setenv('OMD_SPOKEN_LANGUAGE_MODE', 'explicit')
    monkeypatch.setenv('OMD_SPOKEN_LANGUAGE_HINT', 'en')
    monkeypatch.setenv('OMD_PREFERRED_LANGUAGES', 'de')
    monkeypatch.setattr(sys, 'argv', ['omd-reel', url, *args])
    monkeypatch.setattr(reel, 'download', lambda *_args, **_kwargs: (Path('unused.wav'), {}, None))
    seen = []
    class StopBeforeTranscription(BaseException):
        pass
    def transcribe(_audio, _workdir, _model, language, _backend):
        seen.append(language)
        raise StopBeforeTranscription
    monkeypatch.setattr(reel, 'transcribe', transcribe)
    with pytest.raises(StopBeforeTranscription):
        reel.main()
    assert seen == [expected]
