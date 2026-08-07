"""Download episodes from the AliveandKickn Libsyn archive."""

from argparse import ArgumentParser
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from django.conf import settings
from django.core.files import File
from django.core.files.storage import Storage, storages
from django.core.management.base import BaseCommand, CommandError


class EpisodeLinksParser(HTMLParser):
    """Extract episode links from a Libsyn archive page."""

    def __init__(self) -> None:
        super().__init__()
        self.episode_links: list[str] = []
        self.in_episode_title = False

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        """Collect links nested in Libsyn episode headings."""
        attributes = dict(attributes)
        if tag == 'h2':
            self.in_episode_title = 'section-heading' in attributes.get('class', '').split()
            return
        if tag == 'a' and self.in_episode_title:
            self.episode_links.append(attributes['href'])

    def handle_endtag(self, tag: str) -> None:
        """Leave the current episode heading."""
        if tag == 'h2':
            self.in_episode_title = False


class AudioParser(HTMLParser):
    """Extract audio URLs from a Libsyn episode page."""

    def __init__(self) -> None:
        super().__init__()
        self.audio_urls: list[str] = []

    def handle_starttag(self, _tag: str, attributes: list[tuple[str, str | None]]) -> None:
        """Collect MP3 and M4A attributes."""
        attributes = dict(attributes)
        urls = [attributes.get(name, '') for name in ('content', 'href', 'src')]
        self.audio_urls.extend(
            url for url in urls if urlparse(url).path.lower().endswith(('.m4a', '.mp3'))
        )


def read_url(url: str) -> bytes:
    """Read an HTTPS resource."""
    if urlparse(url).scheme != 'https':
        raise CommandError(f'Only HTTPS URLs are supported: {url}')
    with urlopen(url) as response:  # noqa: S310 -- scheme checked above
        return response.read()


def parse_episode_links(document: bytes) -> list[str]:
    """Return episode links in archive display order."""
    parser = EpisodeLinksParser()
    parser.feed(document.decode())
    return parser.episode_links


def parse_audio_url(document: bytes) -> str:
    """Return the first audio URL from an episode page."""
    parser = AudioParser()
    parser.feed(document.decode())
    if not parser.audio_urls:
        raise CommandError('Episode page has no audio URL')
    return parser.audio_urls[0]


def count_months(month: str) -> int:
    """Convert a year-month into months since year zero."""
    year, month_of_year = map(int, month.split('-'))
    return 12 * year + month_of_year - 1


def name_month(months: int, separator: str = '-') -> str:
    """Convert months since year zero back into a year-month, slashed for URLs and R2 keys."""
    return f'{months // 12}{separator}{months % 12 + 1:02d}'


def save_episode(months: int, episode_links: list[str], episode_number: int | None) -> int:
    """Save the requested or oldest missing episode of the month; return how many saved."""
    if episode_number and not 1 <= episode_number <= len(episode_links):
        raise CommandError(
            f'Episode number must be between 1 and {len(episode_links)} for {name_month(months)}'
        )
    for number in [episode_number] if episode_number else range(1, len(episode_links) + 1):
        episode_url = episode_links[number - 1]
        audio_url = parse_audio_url(read_url(episode_url))
        if urlparse(audio_url).scheme != 'https':
            raise CommandError(f'Only HTTPS URLs are supported: {audio_url}')
        destination = '/'.join((
            urlparse(episode_url).hostname or '',
            name_month(months, '/'),
            urlparse(episode_url).path.strip('/'),
            urlparse(audio_url).path.rsplit('/', 1)[-1],
        ))
        if storages['library'].exists(destination):
            print(f'{destination} already archived')
            continue
        with closing(urlopen(audio_url)) as response:  # noqa: S310 -- scheme checked above
            print(storages['library'].save(destination, File(response)))
        return 1
    return 0


def count_archived(storage: Storage, hostname: str) -> Counter[str]:
    """Count episodes already in R2 by year-month."""
    return Counter({
        f'{year}-{month}': len(storage.listdir(f'{hostname}/{year}/{month}')[0])
        for year in storage.listdir(hostname)[0]
        for month in storage.listdir(f'{hostname}/{year}')[0]
    })


ARCHIVE_URL = 'https://aliveandkickn.libsyn.com'


def find_episode_links(archived: Counter[str], current: str) -> tuple[int, list[str]]:
    """Return the newest archived month and its links, moving on while R2 holds every episode."""
    months = count_months(max(archived, default='2019-12', key=count_months))
    while True:
        page = read_url(f'{ARCHIVE_URL}/{name_month(months, "/")}')
        episode_links = list(reversed(parse_episode_links(page)))
        # keep in sync with the handle() completeness check
        if archived[name_month(months)] < len(episode_links) or months >= count_months(current):
            return months, episode_links
        months += 1


def estimate_totals(
    archived: Counter[str], frontier: str, counted: int, current: str
) -> Counter[str]:
    """Estimate episodes per month, only the frontier being fetched and therefore known.

    Months before it count as complete, later ones as average, never below what R2 holds.
    """
    known = {
        month: count
        for month, count in archived.items()
        if count_months(month) < count_months(frontier)
    } | ({frontier: counted} if counted else {})
    average = round(sum(known.values()) / len(known)) if known else 1
    return Counter(
        {
            name_month(months): max(average, archived[name_month(months)])
            for months in range(count_months(frontier), count_months(current) + 1)
        }
        | known
    )


class Command(BaseCommand):
    """Stream an episode into the podcast archive and estimate how much remains."""

    help = 'Download the oldest AliveandKickn episode missing from R2 and report completion'

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Accept an optional episode number and scheduling period."""
        parser.add_argument('--episode-number', type=int)
        parser.add_argument(
            '--period-minutes', type=int, help='Minutes between runs, to forecast completion'
        )

    def handle(
        self, episode_number: int | None, period_minutes: int | None, **_options: Any
    ) -> None:
        """Save the oldest missing episode, then report completion from what R2 holds."""
        if settings.R2_URL.startswith('off'):
            print('R2_URL is off; skipping podcast fetch')
            return
        archived = count_archived(storages['library'], urlparse(ARCHIVE_URL).hostname or '')
        today = datetime.now(tz=UTC).date()
        current = f'{today:%Y-%m}'
        months, episode_links = find_episode_links(archived, current)
        month = name_month(months)
        archived[month] += (
            save_episode(months, episode_links, episode_number)
            # keep in sync with the find_episode_links() completeness check
            if episode_number or archived[month] < len(episode_links)
            else 0
        )
        totals = estimate_totals(archived, month, len(episode_links), current)
        complete, estimated = archived.total(), totals.total()
        ratio = complete / max(estimated, 1)
        print(f'{complete} of {estimated} estimated episodes archived ({ratio:.0%})')
        if period_minutes:
            finish = today + timedelta(minutes=period_minutes * (estimated - complete))
            print(
                f'Estimated finish {finish:%Y-%m-%d} at one episode every {period_minutes} minutes'
            )
