"""Load the authoritative 17-challenge catalog from challenge-catalog.json."""
import json
import os


def catalog_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.environ.get("CHALLENGE_CATALOG"),
        os.path.join(here, "challenge-catalog.json"),
        "/opt/CTFd/challenge-catalog.json",
        "/setup/challenge-catalog.json",
        os.path.join(here, "..", "challenge-catalog.json"),
        os.path.join(here, "..", "..", "challenge-catalog.json"),
    ]


def load_catalog(path=None):
    candidates = [path] + catalog_paths()
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as fh:
                data = json.load(fh)
            if not data.get("challenges"):
                raise ValueError(f"Catalog {candidate} has no challenges")
            return data
    raise FileNotFoundError("challenge-catalog.json not found")


def challenges(data=None):
    return list((data or load_catalog())["challenges"])


def canonical_names(data=None):
    return [c["name"] for c in challenges(data)]


def image_tags(data=None):
    tags = []
    for c in challenges(data):
        tag = c.get("image")
        if tag and tag not in tags:
            tags.append(tag)
    return tags
