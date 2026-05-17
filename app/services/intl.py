"""Genre / category normalisation.

Jellyfin pulls metadata from TMDB in the server's configured
PreferredMetadataLanguage, so on a German-language server "Adventure" arrives
as "Abenteuer" and "Comedy" as "Komödie". The data ends up mixed (some titles
use English genres, some German), which:

  - splits the recommender's content vector across duplicate features
  - clutters the UI with the same concept twice
  - makes "top genres" lists feel random

Fix: fold the German labels into their English equivalents. Applied both at
content-vector build time AND at user-genre-profile post-processing time.
"""
from __future__ import annotations


_GENRE_DE_EN: dict[str, str] = {
    "abenteuer": "Adventure",
    "animation": "Animation",
    "biografie": "Biography",
    "biographie": "Biography",
    "dokumentation": "Documentary",
    "dokumentarfilm": "Documentary",
    "drama": "Drama",
    "familie": "Family",
    "fantasy": "Fantasy",
    "geschichte": "History",
    "historie": "History",
    "horror": "Horror",
    "komödie": "Comedy",
    "komoedie": "Comedy",
    "krieg": "War",
    "krimi": "Crime",
    "kriminalfilm": "Crime",
    "kurzfilm": "Short",
    "liebesfilm": "Romance",
    "musical": "Musical",
    "musik": "Music",
    "mystery": "Mystery",
    "nachrichten": "News",
    "reality": "Reality",
    "reality-tv": "Reality",
    "romanze": "Romance",
    "science fiction": "Science Fiction",
    "science-fiction": "Science Fiction",
    "sci-fi & fantasy": "Sci-Fi & Fantasy",
    "seifenoper": "Soap",
    "sport": "Sport",
    "spielshow": "Game Show",
    "talk": "Talk",
    "thriller": "Thriller",
    "tv-film": "TV Movie",
    "tv movie": "TV Movie",
    "western": "Western",
    "kinder": "Kids",
    "kids": "Kids",
    "action & adventure": "Action & Adventure",
    "krieg & politik": "War & Politics",
    "war & politics": "War & Politics",
    "action": "Action",
}


def normalize_genre(value: str) -> str:
    if not value:
        return value
    key = value.strip().lower()
    return _GENRE_DE_EN.get(key, value.strip())
