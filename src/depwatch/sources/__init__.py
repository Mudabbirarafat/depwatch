"""The sources, and the one thing they disagree about: package identity.

A source is any module exposing `ECOSYSTEM`, `parse()`, `package_url()` and
`normalise()`. The first three were obvious. The fourth was not, and it was
found the hard way.

**Why `normalise` exists.** Asking PyPI for `django` returns a document whose
`info.name` is `Django`, and asking for `pyyaml` returns `PyYAML` — PyPI answers
with the project's canonical display name, not the string you asked for. So the
package row was stored as `Django`, the advisories were stored under the
`django` that OSV had been asked for, and the two could never join. Every PyPI
package whose display name is not already lowercase scored as though it had no
advisories at all, which on a security tool is the worst available direction to
be wrong in.

npm does not have this problem, which is exactly why the abstraction had no
such function until a second ecosystem was plugged into it.
"""

from depwatch.sources import npm, osv, pypi

__all__ = ["SOURCES", "normalise_name", "npm", "osv", "pypi", "source_for"]

SOURCES = {npm.ECOSYSTEM: npm, pypi.ECOSYSTEM: pypi}


def source_for(ecosystem: str):
    try:
        return SOURCES[ecosystem]
    except KeyError:
        raise ValueError(f"unknown ecosystem {ecosystem!r}; known: {sorted(SOURCES)}") from None


def normalise_name(ecosystem: str, name: str) -> str:
    """The form of a package name this ecosystem uses as a key.

    An unknown ecosystem passes the name through unchanged rather than raising:
    this runs on user input in the API, where a bad ecosystem should come back
    as "not ingested" rather than as a 500.
    """
    source = SOURCES.get(ecosystem)
    return source.normalise(name) if source else name
