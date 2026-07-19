"""Download episodes from the AliveandKickn Libsyn archive."""

from argparse import ArgumentParser
from contextlib import closing
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from django.conf import settings
from django.core.files import File
from django.core.files.storage import storages
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


class Command(BaseCommand):
    """Stream an episode into the podcast archive."""

    help = 'Download an AliveandKickn episode by its chronological number; default oldest missing'

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Accept an optional chronological episode number and archive URL."""
        parser.add_argument('episode_number', nargs='?', type=int)
        parser.add_argument('--archive-url', default='https://aliveandkickn.libsyn.com/2019/12')

    def handle(self, episode_number: int | None, archive_url: str, **_options: Any) -> None:
        """Stream the selected episode, or the oldest one missing from R2."""
        if settings.R2_URL.startswith('off'):
            self.stderr.write(self.style.WARNING('R2_URL is off; skipping podcast fetch'))
            return
        episode_links = list(reversed(parse_episode_links(read_url(archive_url))))
        if episode_number and not 1 <= episode_number <= len(episode_links):
            raise CommandError(
                f'Episode number must be between 1 and {len(episode_links)} for {archive_url}'
            )
        for number in [episode_number] if episode_number else range(1, len(episode_links) + 1):
            episode_url = episode_links[number - 1]
            audio_url = parse_audio_url(read_url(episode_url))
            if urlparse(audio_url).scheme != 'https':
                raise CommandError(f'Only HTTPS URLs are supported: {audio_url}')
            destination = '/'.join((
                urlparse(episode_url).hostname or '',
                urlparse(archive_url).path.strip('/'),
                urlparse(episode_url).path.strip('/'),
                urlparse(audio_url).path.rsplit('/', 1)[-1],
            ))
            if storages['library'].exists(destination):
                self.stdout.write(f'{destination} already archived')
                continue
            with closing(urlopen(audio_url)) as response:  # noqa: S310 -- scheme checked above
                self.stdout.write(storages['library'].save(destination, File(response)))
            return
