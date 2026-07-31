"""Tests for fetching an episode from a Libsyn archive."""

from io import BytesIO

from django.core.files import File
from pytest import CaptureFixture, MonkeyPatch
from pytest_django.fixtures import SettingsWrapper

from library.management.commands.fetch_podcast_episode import (
    Command,
    parse_audio_url,
    parse_episode_links,
)


def test_parse_episode_links():
    document = b"""<h2 class="section-heading"><a href="https://example.com/new">New</a></h2>
<h2 class="section-heading"><a href="https://example.com/old">Old</a></h2>"""
    assert parse_episode_links(document) == ['https://example.com/new', 'https://example.com/old']


def test_parse_audio_url():
    document = b'<meta property="og:audio" content="https://media.example.com/episode.m4a">'
    assert parse_audio_url(document) == 'https://media.example.com/episode.m4a'


def patch_network_and_storage(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, saved: dict[str, bytes]
):
    settings.R2_URL = 'https://key:secret@r2.example/bucket'
    responses = {
        'https://example.com/2019/12': b"""<h2 class="section-heading"><a href="https://example.com/second">Second</a></h2>
<h2 class="section-heading"><a href="https://example.com/first">First</a></h2>""",
        'https://example.com/first': b'<audio src="https://media.example.com/first.mp3"></audio>',
        'https://example.com/second': b'<audio src="https://media.example.com/second.mp3"></audio>',
        'https://media.example.com/first.mp3': b'audio one',
        'https://media.example.com/second.mp3': b'audio two',
    }
    monkeypatch.setattr(
        'library.management.commands.fetch_podcast_episode.urlopen',
        lambda url: BytesIO(responses[url]),
    )

    class Storage:
        def exists(self, name: str) -> bool:
            return name in saved

        def save(self, name: str, content: File) -> str:
            saved[name] = b''.join(content.chunks(chunk_size=2))
            return name

    monkeypatch.setattr(
        'library.management.commands.fetch_podcast_episode.storages', {'library': Storage()}
    )


def test_fetch_second_episode(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, capsys: CaptureFixture[str]
):
    saved = {}
    patch_network_and_storage(monkeypatch, settings, saved)
    Command().handle(episode_number=2, archive_url='https://example.com/2019/12')
    destination = 'example.com/2019/12/second/second.mp3'
    assert saved[destination] == b'audio two'
    assert capsys.readouterr().out == f'{destination}\n'


def test_fetch_oldest_missing_episode(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, capsys: CaptureFixture[str]
):
    first = 'example.com/2019/12/first/first.mp3'
    saved = {first: b'audio one'}
    patch_network_and_storage(monkeypatch, settings, saved)
    Command().handle(episode_number=None, archive_url='https://example.com/2019/12')
    destination = 'example.com/2019/12/second/second.mp3'
    assert saved[destination] == b'audio two'
    assert capsys.readouterr().out == f'{first} already archived\n{destination}\n'


def test_fetch_skips_when_r2_off(settings: SettingsWrapper, capsys: CaptureFixture[str]):
    settings.R2_URL = 'off'
    Command().handle(episode_number=None, archive_url='https://example.com/2019/12')
    assert 'skipping' in capsys.readouterr().out
