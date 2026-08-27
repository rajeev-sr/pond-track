"""Canonicalising Indian place names for search.

The problem this solves is HLD CH-24. A village has one name and many spellings:
*Jevra* and *Jewra*, *Kutelabhata* and *Kutelabhatha*, *Rampur* and *Rampura*,
and the same name again in Devanagari. A user types what they say; the register
holds what a Census enumerator wrote down decades ago. Matching on the raw
string finds neither reliably.

Two layers do the work, and the split matters:

* **This module** folds a name to a canonical form -- one spelling per name, as
  far as mechanical rules can get. It is applied to the stored name at seed time
  *and to the query at search time*, so both sides meet in the same space.
* **`pg_trgm`** then handles what rules cannot: typos, word order, partial
  input. Trigram similarity over already-folded strings is far more accurate
  than over raw ones, because the systematic variation is gone and only genuine
  difference is left.

The folding is deliberately conservative in one respect: it never merges names
that Indian usage treats as distinct. *Khurd* and *Kalan* (smaller/larger
settlement of the same name) are kept, because `Sirsa Khurd` and `Sirsa Kalan`
are two villages, not two spellings. The canonical form is for retrieval only --
`Village.name` keeps what the source said, and that is what the UI shows.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Devanagari to Latin, roughly ISO 15919 without the diacritics -- the output
#: feeds the same folding as Latin input, so `ā` would be stripped anyway.
#:
#: Independent vowels, then consonants (each carrying an inherent `a` that the
#: matra handling below removes when a different vowel follows), then the
#: dependent vowel signs. Nukta forms are folded onto their base consonant
#: because transliteration of village names does not distinguish them
#: consistently.
_DEVANAGARI: dict[str, str] = {
    # independent vowels
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ii",
    "उ": "u",
    "ऊ": "uu",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    # consonants (inherent 'a' added separately)
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "ळ": "l",
    # nukta consonants, folded onto the base sound
    "क़": "k",
    "ख़": "kh",
    "ग़": "g",
    "ज़": "j",
    "ड़": "r",
    "ढ़": "rh",
    "फ़": "f",
    # dependent vowel signs
    "ा": "aa",
    "ि": "i",
    "ी": "ii",
    "ु": "u",
    "ू": "uu",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    # marks
    "ं": "n",
    "ँ": "n",
    "ः": "h",  # noqa: RUF001 - visarga, not a colon
    "़": "",
}

#: Virama (halant): suppresses the inherent vowel of the preceding consonant.
_VIRAMA = "्"

#: Consonants, for deciding where an inherent `a` belongs.
_DEV_CONSONANTS = frozenset("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहळ") | {
    "क़",
    "ख़",
    "ग़",
    "ज़",
    "ड़",
    "ढ़",
    "फ़",
}

_DEV_MATRAS = frozenset("ािीुूृेैोौंँः़")

#: Systematic transliteration variants, applied in order. Each pair is a
#: substitution that Indian romanisation genuinely varies over -- not a
#: general-purpose phonetic fold.
_FOLDINGS: tuple[tuple[str, str], ...] = (
    # Doubled vowels carry length, which romanisation drops inconsistently:
    # Kutelabhata / Kutelabhaata.
    (r"aa+", "a"),
    (r"ee+", "i"),
    (r"ii+", "i"),
    (r"oo+", "u"),
    (r"uu+", "u"),
    # v and w are the same sound: Jevra / Jewra -- both spellings occur in the
    # Census register itself.
    (r"w", "v"),
    # z is written j across most of northern India: Zila / Jila.
    (r"z", "j"),
    # Aspiration is unstable in romanisation of the same village:
    # Kutelabhata / Kutelabhatha. The `h+` is load-bearing: matching a single
    # `h` turned `chh` into `ch`, which the same rule then reduced again on a
    # second application, so the fold was not idempotent. A name seeded once
    # and re-folded at query time would have stopped matching itself.
    (r"([kgcjtdpb])h+", r"\1"),
    # Retroflex/dental distinctions vanish in Latin, and 'sh'/'s' alternate:
    # Sirsa / Shirsa.
    (r"sh+", "s"),
    # Doubled consonants are not distinguished: Mitthi / Mithi.
    (r"([bcdfghjklmnpqrstvy])\1+", r"\1"),
    # The final inherent vowel is often written and often not, which is the
    # single largest source of variation: Rampur / Rampura, Sirsa / Siras.
    (r"a\b", ""),
)

_COMPILED = tuple((re.compile(pattern), repl) for pattern, repl in _FOLDINGS)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")


def has_devanagari(text: str) -> bool:
    """True if any character is in the Devanagari block."""
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def devanagari_to_latin(text: str) -> str:
    """Transliterate Devanagari to bare Latin, with schwa deletion.

    Not a scholarly transliteration -- the output exists only to be folded, so
    vowel length and retroflexion are allowed to collapse. What matters is that
    the same name written in Devanagari and in Latin lands on the same string.

    The hard part is the inherent vowel. Every Devanagari consonant carries an
    `a` unless a matra or virama says otherwise, but Hindi does not pronounce
    most of them: रामपुर is *Rampur*, not *Ramapura*. Emitting the inherent
    vowel everywhere produced `ramapur` against the Latin `rampur` -- the two
    spellings of one village failing to meet, which is the exact failure this
    module exists to prevent.

    So the standard schwa-deletion heuristic is applied, right to left:

    * a word-final inherent vowel is always dropped -- रामपुर ends `r`, not `ra`;
    * an inherent vowel is dropped when the *following* syllable carries an
      explicit matra.

    The second rule is what keeps कमल as *kamal* while reducing रामपुर to
    *rampur*: in कमल the final syllable ल has only an inherent vowel, so म keeps
    its own; in रामपुर the following पु carries ु, so म loses its own.
    """
    syllables = _devanagari_syllables(text)

    # Right to left, because both rules look at what follows.
    if syllables and syllables[-1].inherent:
        syllables[-1] = syllables[-1].devoweled()
    for index in range(len(syllables) - 2, 0, -1):
        following = syllables[index + 1]
        if syllables[index].inherent and following.explicit:
            syllables[index] = syllables[index].devoweled()

    return "".join(s.consonant + s.vowel for s in syllables)


@dataclass(frozen=True)
class _Syllable:
    """One transliterated unit: a consonant (possibly empty) and its vowel."""

    consonant: str
    vowel: str
    #: The vowel came from the consonant's inherent `a`, not from a matra.
    inherent: bool

    @property
    def explicit(self) -> bool:
        """The vowel was written, as a matra or an independent vowel letter."""
        return bool(self.vowel) and not self.inherent

    def devoweled(self) -> _Syllable:
        return _Syllable(self.consonant, "", inherent=False)


def _devanagari_syllables(text: str) -> list[_Syllable]:
    """Split Devanagari into consonant+vowel units, preserving other characters."""
    out: list[_Syllable] = []
    chars = list(text)
    index = 0
    while index < len(chars):
        ch = chars[index]

        # A consonant plus its nukta is a single unit.
        pair = ch + (chars[index + 1] if index + 1 < len(chars) else "")
        if pair in _DEV_CONSONANTS:
            unit, step = pair, 2
        else:
            unit, step = ch, 1

        if unit in _DEV_CONSONANTS:
            consonant = _DEVANAGARI.get(unit, "")
            index += step
            nxt = chars[index] if index < len(chars) else ""
            if nxt == _VIRAMA:
                out.append(_Syllable(consonant, "", inherent=False))
                index += 1
            elif nxt in _DEV_MATRAS:
                out.append(_Syllable(consonant, _DEVANAGARI.get(nxt, ""), inherent=False))
                index += 1
            else:
                out.append(_Syllable(consonant, "a", inherent=True))
            continue

        # Independent vowels, marks, and anything not Devanagari at all.
        if ch in _DEVANAGARI:
            out.append(_Syllable("", _DEVANAGARI[ch], inherent=False))
        elif not ("\u0900" <= ch <= "\u097f"):
            out.append(_Syllable(ch, "", inherent=False))
        index += 1

    return out


def strip_diacritics(text: str) -> str:
    """Drop combining marks, so `Rāmpur` and `Rampur` agree."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise_name(name: str) -> str:
    """Fold a place name to its canonical search form.

    Applied to the stored name when seeding and to the query when searching, so
    the two are always compared in the same space. Returns an empty string for
    input that holds no letters or digits at all.

    >>> normalise_name("Kutelabhatha")
    'kutelbht'
    >>> normalise_name("Kutelabhata") == normalise_name("Kutelabhatha")
    True
    >>> normalise_name("Jevra") == normalise_name("Jewra")
    True
    >>> normalise_name("Rampur") == normalise_name("Rampura")
    True
    """
    text = name.strip()
    if not text:
        return ""

    if has_devanagari(text):
        text = devanagari_to_latin(text)

    text = strip_diacritics(text).lower()
    # Separators become spaces before folding, so `Ram Pur` and `Ram-Pur` and
    # `Rampur` converge rather than diverging on punctuation.
    text = _NON_ALNUM.sub(" ", text)
    text = _SPACES.sub("", text).strip()

    for pattern, repl in _COMPILED:
        text = pattern.sub(repl, text)

    return text


def normalise_query(query: str) -> str:
    """Fold a user's search string. Same rules as the stored side."""
    return normalise_name(query)
