"""Tests for fetching an episode from a Libsyn archive."""

from collections import Counter
from datetime import UTC, datetime
from io import BytesIO

from django.core.files import File
from pytest import CaptureFixture, MonkeyPatch, mark
from pytest_django.fixtures import SettingsWrapper

from library.management.commands.fetch_podcast_episode import (
    Command,
    count_months,
    estimate_totals,
    name_month,
    parse_audio_url,
    parse_episode_links,
)

ALBERT = 'aliveandkickn-podcast-dr-andrew-albert'
BAUER = 'heathertollybauer'
ESTRADA = 'aliveandkickn-podcast-stephen-estrada-lynch-syndrome-patient'
GORDON = 'aliveandkickn-podcast-dr-ora-karp-gordon'
KLEISS = 'aliveandkickn-podcast-jill-kleiss'
AUDIO_FILES = {
    ALBERT: 'AndrewAlbert_.mp3',
    BAUER: 'heathertolleybauer.mp3',
    ESTRADA: 'StevenEstrada.m4a',
    GORDON: 'DrOraKarpGordon.mp3',
    KLEISS: 'Jill_Kleiss.mp3',
}
ALBERT_KEY = f'aliveandkickn.libsyn.com/2020/01/{ALBERT}/AndrewAlbert_.mp3'
BAUER_KEY = f'aliveandkickn.libsyn.com/2019/12/{BAUER}/heathertolleybauer.mp3'
ESTRADA_KEY = f'aliveandkickn.libsyn.com/2019/12/{ESTRADA}/StevenEstrada.m4a'
GORDON_KEY = f'aliveandkickn.libsyn.com/2020/01/{GORDON}/DrOraKarpGordon.mp3'
KLEISS_KEY = f'aliveandkickn.libsyn.com/2019/12/{KLEISS}/Jill_Kleiss.mp3'


def test_parse_episode_links():
    document = (
        f'<h2 class="section-heading"><a data-iframe-id="embed_12501014" class="read_more"'
        f' href="https://aliveandkickn.libsyn.com/{BAUER}">Heather Tolly Bauer</a></h2>'
        f'<h2 class="section-heading"><a data-iframe-id="embed_12371630" class="read_more"'
        f' href="https://aliveandkickn.libsyn.com/{ESTRADA}">Stephen Estrada</a></h2>'
    ).encode()
    assert parse_episode_links(document) == [
        f'https://aliveandkickn.libsyn.com/{BAUER}',
        f'https://aliveandkickn.libsyn.com/{ESTRADA}',
    ]


def test_parse_audio_url():
    document = (
        b'<meta name="twitter:player:stream"'
        b' content="https://traffic.libsyn.com/secure/aliveandkickn/StevenEstrada.m4a">'
    )
    assert parse_audio_url(document) == (
        'https://traffic.libsyn.com/secure/aliveandkickn/StevenEstrada.m4a'
    )


def patch_network_and_storage(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, saved: dict[str, bytes]
):
    settings.R2_URL = 'https://key:secret@r2.example/bucket'

    def archive_page(*slugs: str) -> bytes:
        """Render the newest-first section headings the Libsyn archive lists episodes in."""
        return ''.join(
            f'<h2 class="section-heading">'
            f'<a href="https://aliveandkickn.libsyn.com/{slug}">{slug}</a></h2>'
            for slug in slugs
        ).encode()

    responses = (
        {
            'https://aliveandkickn.libsyn.com/2019/12': archive_page(KLEISS, BAUER, ESTRADA),
            'https://aliveandkickn.libsyn.com/2020/01': archive_page(GORDON, ALBERT),
        }
        | {
            f'https://aliveandkickn.libsyn.com/{slug}': (
                f'<meta name="twitter:player:stream"'
                f' content="https://traffic.libsyn.com/secure/aliveandkickn/{audio_file}">'
            ).encode()
            for slug, audio_file in AUDIO_FILES.items()
        }
        | {
            f'https://traffic.libsyn.com/secure/aliveandkickn/{audio_file}': (
                f'{audio_file} audio'.encode()
            )
            for audio_file in AUDIO_FILES.values()
        }
        | {
            f'https://aliveandkickn.libsyn.com/{name_month(months, "/")}': b''
            for months in range(
                count_months('2020-02'), count_months(f'{datetime.now(tz=UTC):%Y-%m}') + 1
            )
        }
    )
    monkeypatch.setattr(
        'library.management.commands.fetch_podcast_episode.urlopen',
        lambda url: BytesIO(responses[url]),
    )

    class Storage:
        def exists(self, name: str) -> bool:
            return name in saved

        def listdir(self, path: str) -> tuple[list[str], list[str]]:
            entries = [
                name.removeprefix(f'{path}/').split('/', 1)
                for name in saved
                if name.startswith(f'{path}/')
            ]
            return (
                sorted({head for head, *rest in entries if rest}),
                sorted(head for head, *rest in entries if not rest),
            )

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
    Command().handle(episode_number=2, period_minutes=None)
    assert saved[BAUER_KEY] == b'heathertolleybauer.mp3 audio'
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == BAUER_KEY
    assert printed[1].startswith('1 of ')
    assert len(printed) == 2


def test_fetch_oldest_missing_episode(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, capsys: CaptureFixture[str]
):
    saved = {ESTRADA_KEY: b'StevenEstrada.m4a audio'}
    patch_network_and_storage(monkeypatch, settings, saved)
    Command().handle(episode_number=None, period_minutes=1440)
    assert saved[BAUER_KEY] == b'heathertolleybauer.mp3 audio'
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == [f'{ESTRADA_KEY} already archived', BAUER_KEY]
    assert printed[3].startswith('Estimated finish ')
    assert printed[3].endswith('at one episode every 1440 minutes')


def test_fetch_moves_on_from_a_complete_month(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, capsys: CaptureFixture[str]
):
    saved: dict[str, bytes] = dict.fromkeys((ESTRADA_KEY, BAUER_KEY, KLEISS_KEY), b'archived')
    patch_network_and_storage(monkeypatch, settings, saved)
    Command().handle(episode_number=None, period_minutes=1440)
    assert saved[ALBERT_KEY] == b'AndrewAlbert_.mp3 audio'
    assert capsys.readouterr().out.splitlines()[0] == ALBERT_KEY


def test_report_when_caught_up(
    monkeypatch: MonkeyPatch, settings: SettingsWrapper, capsys: CaptureFixture[str]
):
    saved: dict[str, bytes] = dict.fromkeys(
        (ESTRADA_KEY, BAUER_KEY, KLEISS_KEY, ALBERT_KEY, GORDON_KEY), b'archived'
    )
    patch_network_and_storage(monkeypatch, settings, saved)
    Command().handle(episode_number=None, period_minutes=1440)
    assert len(saved) == 5
    assert capsys.readouterr().out.splitlines()[0].startswith('5 of ')


def test_fetch_skips_when_r2_off(settings: SettingsWrapper, capsys: CaptureFixture[str]):
    settings.R2_URL = 'off'
    Command().handle(episode_number=None, period_minutes=1440)
    assert 'skipping' in capsys.readouterr().out


@mark.parametrize(
    ('frontier', 'counted', 'expected'),
    (
        ('2020-01', 5, {'2019-11': 4, '2019-12': 3, '2020-01': 5, '2020-02': 4, '2020-03': 4}),
        ('2019-12', 0, {'2019-11': 4, '2019-12': 4, '2020-01': 4, '2020-02': 4, '2020-03': 4}),
        ('2019-11', 6, {'2019-11': 6, '2019-12': 6, '2020-01': 6, '2020-02': 6, '2020-03': 6}),
    ),
)
def test_estimate_totals(frontier: str, counted: int, expected: dict[str, int]):
    archived = Counter({'2019-11': 4, '2019-12': 3})
    assert estimate_totals(archived, frontier, counted, '2020-03') == expected
