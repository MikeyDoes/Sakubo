"""Learning/drill session logic for pre-SRS drills.

API:
- create_session(conn, entry_ids, kind, session_meta) -> session_id
- get_session(conn, session_id)
- get_next_vector(conn, session_id) -> vector
- submit_vector_result(conn, session_id, vector_id, correct) -> result dict
- helper: make_vectors_for_entries(entry_ids, kind)

Session data model (stored as JSON in learning_sessions.data):
{
  'pending': [vector, ...],
  'in_progress': [vector, ...],  # max num_target_slots (7)
  'meta': { 'created_by': 'user', 'num_slots': 7 }
}

Vector shape:
{
  'id': 'entry:123:show_kana',
  'entry_id': 123,
  'kind': 'kana',
  'vector_type': 'show_kana' / 'show_romaji',
  'prompt': 'あ',
  'answer': 'a',
  'streak': 0,
  'is_filler': False
}
"""
from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import json
import uuid
import random
from . import db as dict_db

DEFAULT_SLOTS = 7


def _now_iso():
    return datetime.utcnow().isoformat()


def _vector_id(entry_id: int, vtype: str) -> str:
    return f"entry:{entry_id}:{vtype}"


def _romaji_to_hiragana_units(s: str) -> list[str] | None:
    s = s.lower()
    if not s:
        return None
    table = {
        'kya':'きゃ','kyu':'きゅ','kyo':'きょ',
        'gya':'ぎゃ','gyu':'ぎゅ','gyo':'ぎょ',
        'sha':'しゃ','shu':'しゅ','sho':'しょ','shi':'し',
        'sya':'しゃ','syu':'しゅ','syo':'しょ',
        'ja':'じゃ','ju':'じゅ','jo':'じょ','ji':'じ',
        'jya':'じゃ','jyu':'じゅ','jyo':'じょ',
        'zya':'じゃ','zyu':'じゅ','zyo':'じょ',
        'cha':'ちゃ','chu':'ちゅ','cho':'ちょ','chi':'ち',
        'tya':'ちゃ','tyu':'ちゅ','tyo':'ちょ',
        'cya':'ちゃ','cyu':'ちゅ','cyo':'ちょ',
        'dya':'ぢゃ','dyu':'ぢゅ','dyo':'ぢょ',
        'nya':'にゃ','nyu':'にゅ','nyo':'にょ',
        'hya':'ひゃ','hyu':'ひゅ','hyo':'ひょ',
        'bya':'びゃ','byu':'びゅ','byo':'びょ',
        'pya':'ぴゃ','pyu':'ぴゅ','pyo':'ぴょ',
        'mya':'みゃ','myu':'みゅ','myo':'みょ',
        'rya':'りゃ','ryu':'りゅ','ryo':'りょ',
        'tsu':'つ','tch':'っ',
        'ka':'か','ki':'き','ku':'く','ke':'け','ko':'こ',
        'ga':'が','gi':'ぎ','gu':'ぐ','ge':'げ','go':'ご',
        'sa':'さ','su':'す','se':'せ','so':'そ','si':'し',
        'za':'ざ','zu':'ず','ze':'ぜ','zo':'ぞ','zi':'じ',
        'ta':'た','te':'て','to':'と','ti':'ち','tu':'つ',
        'da':'だ','de':'で','do':'ど',
        'na':'な','ni':'に','nu':'ぬ','ne':'ね','no':'の',
        'ha':'は','hi':'ひ','fu':'ふ','hu':'ふ','he':'へ','ho':'ほ',
        'ba':'ば','bi':'び','bu':'ぶ','be':'べ','bo':'ぼ',
        'pa':'ぱ','pi':'ぴ','pu':'ぷ','pe':'ぺ','po':'ぽ',
        'ma':'ま','mi':'み','mu':'む','me':'め','mo':'も',
        'ya':'や','yu':'ゆ','yo':'よ',
        'ra':'ら','ri':'り','ru':'る','re':'れ','ro':'ろ',
        'wa':'わ','wo':'を','wi':'うぃ','we':'うぇ',
        'a':'あ','i':'い','u':'う','e':'え','o':'お',
        'n':'ん','-':'ー','\'':'',
    }
    units: list[str] = []
    i = 0
    L = len(s)
    while i < L:
        # double consonant -> small tsu (っ)
        if i+1 < L and s[i] == s[i+1] and s[i] not in 'aeiouyn-':
            units.append('っ')
            i += 1
            continue
        matched = False
        for l in (3,2,1):
            if i + l <= L:
                chunk = s[i:i+l]
                # Handle "nn" explicitly -> "ん"
                if chunk == 'nn':
                    units.append('ん')
                    i += 2
                    matched = True
                    break
                # Handle "n" + consonant -> "ん" + consonant (but not trailing "n")
                if l == 1 and chunk == 'n' and i + 1 < L:
                    next_char = s[i+1] if i+1 < L else ''
                    # If next char is not a vowel/y/n (i.e., it's a consonant), convert n to ん
                    if next_char and next_char not in 'aeiouyn':
                        units.append('ん')
                        i += 1
                        matched = True
                        break
                # Special case: don't convert trailing 'n' to 'ん' (leave it uncommitted)
                if chunk == 'n' and i == L - 1:
                    return None  # Trailing 'n' means conversion is incomplete
                if chunk in table:
                    units.append(table[chunk])
                    i += l
                    matched = True
                    break
        if not matched:
            return None
    return units


def _romaji_to_hiragana(s: str) -> str | None:
    units = _romaji_to_hiragana_units(s)
    if units is None:
        return None
    return ''.join(units)


def romaji_to_hiragana(s: str) -> str | None:
    """Public wrapper: converts a romaji string into hiragana if possible.

    Returns None if conversion fails.
    """
    try:
        return _romaji_to_hiragana(s)
    except Exception:
        return None


def convert_greedy_romaji(s: str) -> tuple[str, str]:
    """Greedily convert as much of `s` as possible into hiragana.

    Returns (converted_hiragana, remainder_ascii).
    Example: 'kaka' -> ('か', 'ka') if only the first 'ka' is converted in a chunk-wise manner.
    """
    out = ''
    rest = s or ''
    while rest:
        matched = False
        # try longest prefix
        for i in range(len(rest), 0, -1):
            p = rest[:i]
            hira = romaji_to_hiragana(p)
            if hira is not None:
                out += hira
                rest = rest[i:]
                matched = True
                break
        if not matched:
            # no prefix converts; stop
            break
    return out, rest


def check_answer_match(user_text: str, expected: str, vector_type: str | None = None) -> bool:
    """Return True if user's typed answer should be considered correct for the expected value.

    Rules per vector_type:
    - 'show_kana': prompt shows kana; user must type ROMAJI (ASCII); compare against expected (ASCII) case-insensitively.
    - 'show_romaji': prompt shows romaji; user must type KANA (or type romaji which we transliterate to kana) — accept either kana or romaji->kana match.
    - fallback: best-effort match: romaji == romaji or kana transliteration.
    """
    if user_text is None:
        return False
    u = user_text.strip()
    if not u:
        return False
    expected = (expected or '').strip()

    def _has_japanese(s: str) -> bool:
        for ch in s:
            o = ord(ch)
            if (0x3040 <= o <= 0x30ff) or (0x4e00 <= o <= 0x9fff):
                return True
        return False

    def _kata_to_hira(text: str) -> str:
        """Convert katakana to hiragana for comparison."""
        result = []
        for char in text:
            code = ord(char)
            if 0x30A0 <= code <= 0x30FF:  # Katakana range
                result.append(chr(code - 0x60))  # Convert to hiragana
            else:
                result.append(char)
        return ''.join(result)

    # If vector_type explicitly tells us which direction, enforce stricter rules
    if vector_type == 'show_kana':
        # user should type ROMAJI (ASCII). There are two common cases:
        # - expected is ASCII (romaji): compare case-insensitively
        # - expected is kana: accept romaji transliterated to kana (but not raw kana input)
        if _has_japanese(expected):
            # expected is kana/kanji: accept romaji -> hiragana transliteration
            if all(ord(c) < 128 for c in u):
                hira = _romaji_to_hiragana(u.lower())  # Lowercase before converting
                # Compare as hiragana to handle katakana/hiragana differences
                if hira is not None and _kata_to_hira(hira) == _kata_to_hira(expected):
                    return True
            return False
        else:
            # expected is ASCII/romaji: require ASCII input matching case-insensitively
            if all(ord(c) < 128 for c in u):
                return u.lower() == expected.lower()
            return False

    if vector_type == 'show_romaji':
        # user should type KANA only - do NOT accept romaji input
        # This enforces that users practice typing actual kana characters
        # Accept either hiragana or katakana - normalize both to hiragana for comparison
        user_normalized = _kata_to_hira(u)
        expected_normalized = _kata_to_hira(expected)
        return user_normalized == expected_normalized

    if vector_type == 'orthography_to_reading':
        # Kanji reading - accept romaji or kana, and any valid reading from on/kun/nanori
        # Convert romaji to kana if needed
        user_kana = u
        if all(ord(c) < 128 for c in u):
            # User typed romaji, convert to kana
            user_kana = _romaji_to_hiragana(u.lower())
            if not user_kana:
                user_kana = u
        
        # Normalize to hiragana for comparison
        user_normalized = _kata_to_hira(user_kana).lower()
        
        # Parse multiple readings from the expected answer
        # Format: "on: シン | kun: あたら.しい | nanori: ..."
        valid_readings = set()
        
        # Split by | and process each section
        sections = expected.split('|')
        for section in sections:
            # Remove labels like "on:", "kun:", "nanori:"
            section = section.replace('on:', '').replace('kun:', '').replace('nanori:', '')
            # Split by spaces and dots to get individual readings
            parts = section.replace('.', ' ').replace('-', '').split()
            for part in parts:
                cleaned = part.strip()
                if cleaned:
                    # Add both original and hiragana version for comparison
                    valid_readings.add(cleaned.lower())
                    valid_readings.add(_kata_to_hira(cleaned).lower())
        
        # Check if user's input matches any valid reading
        if user_normalized in valid_readings:
            return True
        
        # Also try converting valid readings to romaji to compare
        if all(ord(c) < 128 for c in u):
            try:
                import pykakasi
                kakasi = pykakasi.kakasi()
                for reading in valid_readings:
                    if reading:
                        result = kakasi.convert(reading)
                        romaji = ''.join([item['hepburn'] for item in result])
                        if u.lower() == romaji.lower():
                            return True
            except Exception:
                pass
        
        return False

    # fallback behavior from previous implementation
    if _has_japanese(expected):
        # expected has kana/kanji: accept romaji transliteration or direct kana
        if all(ord(c) < 128 for c in u):
            hira = _romaji_to_hiragana(u.lower())  # Lowercase before converting
            if hira is not None and _kata_to_hira(hira) == _kata_to_hira(expected):
                return True
        # Direct kana match - normalize to hiragana for comparison
        if _kata_to_hira(u) == _kata_to_hira(expected):
            return True
        long_removed = u.replace('-', '').lower()  # Lowercase before converting
        if _romaji_to_hiragana(long_removed) and _kata_to_hira(_romaji_to_hiragana(long_removed)) == _kata_to_hira(expected):
            return True
        return False
    else:
        # expected likely ASCII (romaji/english); case-insensitive compare and accept simple variants
        # For meanings, accept any of the semicolon-separated alternatives
        import re

        def _strip_punct(s: str) -> str:
            """Remove punctuation characters for flexible comparison."""
            return re.sub(r'[^\w\s]', '', s).strip()

        def _normalize_tense(word: str) -> set:
            """Return a set of base/stem forms for an English word to allow tense-flexible matching.
            E.g. 'liked' → {'liked', 'like'}, 'running' → {'running', 'run'}, 'studies' → {'studies', 'study'}
            """
            forms = {word}
            w = word
            # -ed endings → base form
            if w.endswith('ied') and len(w) > 4:
                forms.add(w[:-3] + 'y')   # studied → study
            elif w.endswith('ed'):
                if len(w) > 3 and w[-3] == w[-4] and w[-4] not in 'aeiou':
                    forms.add(w[:-3])      # stopped → stop
                forms.add(w[:-2])          # liked → lik (handled below too)
                forms.add(w[:-1])          # liked → like
                if w.endswith('ed') and len(w) > 2:
                    forms.add(w[:-2])      # loved → lov (+ 'e' below)
                    forms.add(w[:-1])      # baked → bake? no, baked→bake via -d
                # Direct -d strip for words ending in -ed where base ends in -e
                if w.endswith('ed') and len(w) > 2:
                    forms.add(w[:-1])      # loved → love (strip d)
            # -ing endings → base form
            if w.endswith('ing') and len(w) > 4:
                forms.add(w[:-3])          # running → runn (doubled consonant)
                forms.add(w[:-3] + 'e')   # making → make
                if len(w) > 5 and w[-4] == w[-5]:
                    forms.add(w[:-4])      # running → run
            # -s/-es endings → base form
            if w.endswith('ies') and len(w) > 4:
                forms.add(w[:-3] + 'y')   # studies → study
            elif w.endswith('es') and len(w) > 3:
                forms.add(w[:-2])          # watches → watch
                forms.add(w[:-1])          # cases → case
            elif w.endswith('s') and not w.endswith('ss') and len(w) > 2:
                forms.add(w[:-1])          # likes → like
            # Also generate common inflections FROM a base word
            # so "like" matches against "liked"
            forms.add(w + 'd')             # like → liked
            forms.add(w + 'ed')            # watch → watched
            forms.add(w + 's')             # like → likes
            forms.add(w + 'ing')           # like → liking
            if w.endswith('e'):
                forms.add(w[:-1] + 'ing')  # like → liking
                forms.add(w[:-1] + 'ed')   # like → liked (already covered by +d)
            return forms

        user_lower = u.lower()
        expected_lower = expected.lower()
        
        # Check exact match first (with and without punctuation)
        if user_lower == expected_lower:
            return True
        if _strip_punct(user_lower) == _strip_punct(expected_lower):
            return True
        
        # Split by semicolon and check each alternative meaning
        alternatives = [alt.strip() for alt in expected.split(';')]
        user_clean = _strip_punct(user_lower)
        user_tense_forms = _normalize_tense(user_clean)
        for alt in alternatives:
            # Remove anything in parentheses from the alternative
            alt_clean = re.sub(r'\s*\([^)]*\)', '', alt).strip().lower()
            alt_nopunct = _strip_punct(alt_clean)
            
            # Also create a version with parens removed but content kept:
            # "straight (ahead)" → "straight ahead"
            alt_with_paren_content = re.sub(r'[()]', '', alt).strip().lower()
            alt_with_paren_content = re.sub(r'\s+', ' ', alt_with_paren_content)
            alt_paren_nopunct = _strip_punct(alt_with_paren_content)
            
            # Direct match (ignoring punctuation)
            if user_clean == alt_nopunct:
                return True
            # Match with parenthetical content included
            if user_clean == alt_paren_nopunct:
                return True
            
            # For verbs, make preposition "to " optional
            user_no_to = user_clean
            alt_no_to = alt_nopunct
            alt_paren_no_to = alt_paren_nopunct
            
            if user_no_to.startswith('to '):
                user_no_to = user_no_to[3:]
            if alt_no_to.startswith('to '):
                alt_no_to = alt_no_to[3:]
            if alt_paren_no_to.startswith('to '):
                alt_paren_no_to = alt_paren_no_to[3:]
            
            if user_no_to == alt_no_to or user_no_to == alt_paren_no_to:
                return True
            
            # Tense-flexible match: check if any normalized form of user input
            # matches any normalized form of the alternative
            user_forms = _normalize_tense(user_no_to)
            alt_forms = _normalize_tense(alt_no_to)
            alt_paren_forms = _normalize_tense(alt_paren_no_to)
            if user_forms & alt_forms or user_forms & alt_paren_forms:
                return True
        
        return False


def make_vectors_from_row(eid: int, kind: str, kanji: str, kana: str, gloss: str, study_vectors_str: str) -> List[dict]:
    """Create canonical vectors from pre-fetched entry data (no DB query).
    
    This is the inner logic of make_vectors_for_entries but accepts raw
    column values so callers that already JOIN-ed entries don't re-query.
    """
    vectors = []
    # Parse study_vectors if available
    study_vectors = []
    if study_vectors_str:
        study_vectors = [v.strip() for v in study_vectors_str.split(',')]

    # Detect kana characters stored as kind='kanji' (kanji==kana, single
    # hiragana/katakana char, gloss is romaji).  Treat them as kind='kana'
    # so they get show_kana / show_romaji vectors instead of the useless
    # orthography_to_reading (prompt == answer) vector.
    if kind == 'kanji' and kanji and kana and kanji == kana and len(kanji) <= 2:
        ch = kanji[0]
        if ('\u3040' <= ch <= '\u309F') or ('\u30A0' <= ch <= '\u30FF'):
            kind = 'kana'

    if kind == 'kana':
        romaji = gloss or ''
        v1 = {
            'id': _vector_id(eid, 'show_kana'),
            'entry_id': eid,
            'kind': 'kana',
            'vector_type': 'show_kana',
            'prompt': kana or kanji,
            'answer': romaji,
            'streak': 0,
            'is_filler': False,
        }
        v2 = {
            'id': _vector_id(eid, 'show_romaji'),
            'entry_id': eid,
            'kind': 'kana',
            'vector_type': 'show_romaji',
            'prompt': romaji,
            'answer': kana or kanji,
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v1)
        vectors.append(v2)
    elif kind == 'grammar':
        # Grammar entries get a single fill-blank vector.
        # Grammar entries always get all four vector types.
        # The actual exercise content is injected at drill time from the
        # lesson JSON; here we store the grammar point and meaning as
        # fallback prompt/answer.
        v_fill = {
            'id': _vector_id(eid, 'fill-blank'),
            'entry_id': eid,
            'kind': 'grammar',
            'vector_type': 'fill-blank',
            'prompt': kana or kanji or '',
            'answer': gloss or '',
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v_fill)
        v_scramble = {
            'id': _vector_id(eid, 'scramble'),
            'entry_id': eid,
            'kind': 'grammar',
            'vector_type': 'scramble',
            'prompt': kana or kanji or '',
            'answer': gloss or '',
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v_scramble)
        v_translate = {
            'id': _vector_id(eid, 'translate'),
            'entry_id': eid,
            'kind': 'grammar',
            'vector_type': 'translate',
            'prompt': gloss or '',
            'answer': kana or kanji or '',
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v_translate)
        v_dictation = {
            'id': _vector_id(eid, 'dictation'),
            'entry_id': eid,
            'kind': 'grammar',
            'vector_type': 'dictation',
            'prompt': kana or kanji or '',
            'answer': kana or kanji or '',
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v_dictation)
    elif kind in ('vocab', 'kanji'):
        if kanji:
            v_reading = {
                'id': _vector_id(eid, 'orthography_to_reading'),
                'entry_id': eid,
                'kind': kind,
                'vector_type': 'orthography_to_reading',
                'prompt': kanji,
                'answer': kana,
                'streak': 0,
                'is_filler': False,
            }
            v_meaning = {
                'id': _vector_id(eid, 'orthography_to_meaning'),
                'entry_id': eid,
                'kind': kind,
                'vector_type': 'orthography_to_meaning',
                'prompt': kanji,
                'answer': gloss or '',
                'streak': 0,
                'is_filler': False,
            }
            vectors.append(v_reading)
            vectors.append(v_meaning)
        else:
            v_kana_to_meaning = {
                'id': _vector_id(eid, 'kana_to_meaning'),
                'entry_id': eid,
                'kind': kind,
                'vector_type': 'kana_to_meaning',
                'prompt': kana or '',
                'answer': gloss or '',
                'streak': 0,
                'is_filler': False,
            }
            v_meaning_to_kana = {
                'id': _vector_id(eid, 'meaning_to_kana'),
                'entry_id': eid,
                'kind': kind,
                'vector_type': 'meaning_to_kana',
                'prompt': gloss or '',
                'answer': kana or '',
                'streak': 0,
                'is_filler': False,
            }
            vectors.append(v_kana_to_meaning)
            vectors.append(v_meaning_to_kana)
    else:
        v = {
            'id': _vector_id(eid, 'default'),
            'entry_id': eid,
            'kind': kind,
            'vector_type': 'default',
            'prompt': kana or kanji or gloss or str(eid),
            'answer': gloss or kana or kanji or str(eid),
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v)

    # Add handwriting vector if it exists in study_vectors
    if 'handwriting' in study_vectors:
        import dictionary.handwriting_drill as handwriting_drill
        entry_dict = {'id': eid, 'kind': kind, 'kanji': kanji, 'kana': kana, 'gloss': gloss}
        if handwriting_drill.should_use_handwriting(entry_dict):
            hw_vector = handwriting_drill.create_handwriting_vector(entry_dict)
            if hw_vector:
                v_handwriting = {
                    'id': _vector_id(eid, 'handwriting'),
                    'entry_id': eid,
                    'kind': kind,
                    'vector_type': 'handwriting',
                    'prompt': hw_vector['prompt'],
                    'answer': hw_vector['answer'],
                    'characters': hw_vector.get('characters', []),
                    'reading': hw_vector.get('reading', ''),
                    'current_char_index': 0,
                    'char_results': [],
                    'streak': 0,
                    'is_filler': False,
                }
                vectors.append(v_handwriting)

    return vectors


# -----------  Grammar exercise helpers  -----------

GRAMMAR_EXERCISES_PER_ENTRY = 3  # Number of category-slot vectors per grammar entry


def load_grammar_exercise_pool(conn, entry_id: int) -> Dict[str, list]:
    """Load ALL grammar exercises for *entry_id*, grouped by category.

    Returns ``{category: [exercise_dict, ...]}``.  Each exercise dict has columns
    from the *grammar_exercises* table.  The pool is stored in session metadata so
    that each time a grammar vector is displayed a **different** sentence from the
    same category can be shown, giving the user variety instead of drilling the
    exact same card repeatedly.
    """
    cur = conn.cursor()
    cur.execute('''
        SELECT id, category, category_label, japanese, japanese_with_blank,
               english, target_answer, alternative_answers, hint,
               scramble_blocks, audio_text, notes, phase_mask, position
        FROM grammar_exercises
        WHERE entry_id = ?
        ORDER BY category, position
    ''', (entry_id,))
    rows = cur.fetchall()
    if not rows:
        return {}
    cols = [d[0] for d in cur.description]
    pool: Dict[str, list] = {}
    for row in rows:
        ex = dict(zip(cols, row))
        pool.setdefault(ex['category'], []).append(ex)
    return pool


def load_grammar_exercises(conn, entry_id: int, limit: int = GRAMMAR_EXERCISES_PER_ENTRY) -> List[dict]:
    """Load grammar exercises from DB — returns one sample per category slot.

    This is kept for backward compatibility (e.g. ``_load_grammar_exercise_for_entry``
    fallback).  For drill sessions prefer ``load_grammar_exercise_pool`` +
    ``make_grammar_cloze_vectors``.
    """
    pool = load_grammar_exercise_pool(conn, entry_id)
    if not pool:
        return []
    categories = list(pool.keys())
    random.shuffle(categories)
    selected: List[dict] = []
    cat_offsets = {cat: 0 for cat in categories}
    cat_idx = 0
    while len(selected) < limit and any(cat_offsets[c] < len(pool[c]) for c in categories):
        cat = categories[cat_idx % len(categories)]
        offset = cat_offsets[cat]
        if offset < len(pool[cat]):
            selected.append(pool[cat][offset])
            cat_offsets[cat] = offset + 1
        cat_idx += 1
    return selected


def make_grammar_cloze_vectors(entry_id: int, grammar_point: str,
                               exercise_pool: Dict[str, list]) -> List[dict]:
    """Build ``fill-blank`` *category-slot* vectors from an exercise pool.

    One vector is created per category (up to ``GRAMMAR_EXERCISES_PER_ENTRY``
    total, round-robin across categories).  The vector stores the *category*
    and *grammar_point* but NOT a fixed sentence — the actual sentence is
    picked at display time from ``_grammar_exercise_pools`` so the user sees
    a different example each time the card comes around.

    An initial ``exercise_data`` is populated so the first display already has
    content, but ``_load_current_drill`` will swap it on each subsequent show.
    """
    categories = list(exercise_pool.keys())
    random.shuffle(categories)

    # Round-robin to choose which categories get slots
    slots: List[str] = []  # ordered list of category names
    cat_counts: Dict[str, int] = {c: 0 for c in categories}
    ci = 0
    while len(slots) < GRAMMAR_EXERCISES_PER_ENTRY and ci < len(categories) * 10:
        cat = categories[ci % len(categories)]
        if cat_counts[cat] < len(exercise_pool[cat]):
            slots.append(cat)
            cat_counts[cat] += 1
        ci += 1
        if all(cat_counts[c] >= len(exercise_pool[c]) for c in categories):
            break  # exhausted all exercises (unlikely to exceed limit but be safe)

    vectors: List[dict] = []
    # Track which exercise index we used per category so initial picks differ
    pick_idx: Dict[str, int] = {c: 0 for c in categories}
    for slot_cat in slots:
        idx = pick_idx[slot_cat]
        ex = exercise_pool[slot_cat][idx]
        pick_idx[slot_cat] = idx + 1

        sentence = ex.get('japanese_with_blank', '')
        display_sentence = sentence.replace('[___]', '＿＿＿')
        alt_answers = _parse_alt_answers(ex)

        v = {
            'id': f"grammar-cat:{entry_id}:{slot_cat}:{idx}",
            'entry_id': entry_id,
            'kind': 'grammar',
            'vector_type': 'fill-blank',
            'prompt': display_sentence,
            'answer': ex.get('target_answer', ''),
            'grammar_category': slot_cat,  # used for pool rotation
            'exercise_data': _build_exercise_data(ex, grammar_point),
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v)
    return vectors


def make_grammar_translate_vectors(entry_id: int, grammar_point: str,
                                   exercise_pool: Dict[str, list]) -> List[dict]:
    """Build ``translate`` *category-slot* vectors from an exercise pool.

    The user sees the English translation and must type the full Japanese
    sentence.  Mirrors :func:`make_grammar_cloze_vectors` layout.
    """
    categories = list(exercise_pool.keys())
    random.shuffle(categories)

    slots: List[str] = []
    cat_counts: Dict[str, int] = {c: 0 for c in categories}
    ci = 0
    while len(slots) < GRAMMAR_EXERCISES_PER_ENTRY and ci < len(categories) * 10:
        cat = categories[ci % len(categories)]
        if cat_counts[cat] < len(exercise_pool[cat]):
            slots.append(cat)
            cat_counts[cat] += 1
        ci += 1
        if all(cat_counts[c] >= len(exercise_pool[c]) for c in categories):
            break

    vectors: List[dict] = []
    pick_idx: Dict[str, int] = {c: 0 for c in categories}
    for slot_cat in slots:
        idx = pick_idx[slot_cat]
        ex = exercise_pool[slot_cat][idx]
        pick_idx[slot_cat] = idx + 1

        # The correct answer is the full original sentence (audio_text)
        correct_sentence = ex.get('audio_text', '') or ex.get('japanese', '')
        correct_sentence = correct_sentence.rstrip('\u3002\u3001\uff01\uff1f\u2026')  # strip 。、！？…

        v = {
            'id': f"grammar-translate:{entry_id}:{slot_cat}:{idx}",
            'entry_id': entry_id,
            'kind': 'grammar',
            'vector_type': 'translate',
            'prompt': ex.get('english', ''),
            'answer': correct_sentence,
            'grammar_category': slot_cat,
            'exercise_data': _build_exercise_data(ex, grammar_point),
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v)
    return vectors


def make_grammar_dictation_vectors(entry_id: int, grammar_point: str,
                                   exercise_pool: Dict[str, list]) -> List[dict]:
    """Build ``dictation`` *category-slot* vectors from an exercise pool.

    The user hears the Japanese sentence via TTS and must type it.
    Mirrors :func:`make_grammar_translate_vectors` layout.
    """
    categories = list(exercise_pool.keys())
    random.shuffle(categories)

    slots: List[str] = []
    cat_counts: Dict[str, int] = {c: 0 for c in categories}
    ci = 0
    while len(slots) < GRAMMAR_EXERCISES_PER_ENTRY and ci < len(categories) * 10:
        cat = categories[ci % len(categories)]
        if cat_counts[cat] < len(exercise_pool[cat]):
            slots.append(cat)
            cat_counts[cat] += 1
        ci += 1
        if all(cat_counts[c] >= len(exercise_pool[c]) for c in categories):
            break

    vectors: List[dict] = []
    pick_idx: Dict[str, int] = {c: 0 for c in categories}
    for slot_cat in slots:
        idx = pick_idx[slot_cat]
        ex = exercise_pool[slot_cat][idx]
        pick_idx[slot_cat] = idx + 1

        # The correct answer is the full original sentence (audio_text)
        correct_sentence = ex.get('audio_text', '') or ex.get('japanese', '')
        correct_sentence = correct_sentence.rstrip('\u3002\u3001\uff01\uff1f\u2026')  # strip 。、！？…

        v = {
            'id': f"grammar-dictation:{entry_id}:{slot_cat}:{idx}",
            'entry_id': entry_id,
            'kind': 'grammar',
            'vector_type': 'dictation',
            'prompt': correct_sentence,  # used for TTS playback
            'answer': correct_sentence,
            'grammar_category': slot_cat,
            'exercise_data': _build_exercise_data(ex, grammar_point),
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v)
    return vectors


def _shuffle_blocks(blocks: list) -> list:
    """Shuffle *blocks* so the result never equals the original order.

    For 1-element lists the original is returned unchanged.
    For 2-element lists the order is simply reversed.
    For 3+ elements Fisher-Yates is used with a retry guard.
    """
    if len(blocks) <= 1:
        return list(blocks)
    if len(blocks) == 2:
        return list(reversed(blocks))
    shuffled = list(blocks)
    for _ in range(20):
        random.shuffle(shuffled)
        if shuffled != blocks:
            return shuffled
    # Fallback: reverse (always differs for len >= 2)
    return list(reversed(blocks))


def make_grammar_scramble_vectors(entry_id: int, grammar_point: str,
                                  exercise_pool: Dict[str, list]) -> List[dict]:
    """Build ``scramble`` *category-slot* vectors from an exercise pool.

    Mirrors :func:`make_grammar_cloze_vectors` but sets
    ``vector_type='scramble'``.  The correct answer is the full original
    sentence (joined blocks, punctuation stripped).  Shuffling happens at
    display time so repeated views get different orderings.
    """
    categories = list(exercise_pool.keys())
    random.shuffle(categories)

    slots: List[str] = []
    cat_counts: Dict[str, int] = {c: 0 for c in categories}
    ci = 0
    while len(slots) < GRAMMAR_EXERCISES_PER_ENTRY and ci < len(categories) * 10:
        cat = categories[ci % len(categories)]
        if cat_counts[cat] < len(exercise_pool[cat]):
            slots.append(cat)
            cat_counts[cat] += 1
        ci += 1
        if all(cat_counts[c] >= len(exercise_pool[c]) for c in categories):
            break

    vectors: List[dict] = []
    pick_idx: Dict[str, int] = {c: 0 for c in categories}
    for slot_cat in slots:
        idx = pick_idx[slot_cat]
        ex = exercise_pool[slot_cat][idx]
        pick_idx[slot_cat] = idx + 1

        # The correct sentence is the original blocks joined (audio_text as fallback)
        blocks_raw = ex.get('scramble_blocks', '[]')
        if isinstance(blocks_raw, str):
            try:
                blocks = json.loads(blocks_raw)
            except (json.JSONDecodeError, TypeError):
                blocks = []
        else:
            blocks = list(blocks_raw)

        correct_sentence = ''.join(blocks) if blocks else ex.get('audio_text', '')
        # Strip trailing punctuation for comparison
        correct_sentence = correct_sentence.rstrip('。、！？…')

        v = {
            'id': f"grammar-scramble:{entry_id}:{slot_cat}:{idx}",
            'entry_id': entry_id,
            'kind': 'grammar',
            'vector_type': 'scramble',
            'prompt': '',  # filled at display time with shuffled blocks
            'answer': correct_sentence,
            'grammar_category': slot_cat,
            'exercise_data': _build_exercise_data(ex, grammar_point),
            'streak': 0,
            'is_filler': False,
        }
        vectors.append(v)
    return vectors


def pick_fresh_grammar_exercise(vector: dict, pool: Dict[str, list]) -> dict:
    """Pick a different exercise from the pool for *vector*'s category.

    Mutates ``vector['exercise_data']``, ``vector['prompt']``, and
    ``vector['answer']`` in-place and returns the chosen exercise dict.
    If the category only has one exercise, that same one is returned.
    """
    cat = vector.get('grammar_category', '')
    cat_exercises = pool.get(cat, [])
    if not cat_exercises:
        return vector.get('exercise_data', {})

    # Avoid repeating the last-shown exercise if possible
    last_id = vector.get('exercise_data', {}).get('exercise_id')
    candidates = [e for e in cat_exercises if e['id'] != last_id] if len(cat_exercises) > 1 else cat_exercises
    ex = random.choice(candidates)

    grammar_point = vector.get('exercise_data', {}).get('grammar_point', '')
    ed = _build_exercise_data(ex, grammar_point)

    vector['exercise_data'] = ed

    if vector.get('vector_type') == 'scramble':
        # For scramble vectors, answer = full correct sentence (blocks joined)
        blocks_raw = ex.get('scramble_blocks', '[]')
        if isinstance(blocks_raw, str):
            try:
                blocks = json.loads(blocks_raw)
            except (json.JSONDecodeError, TypeError):
                blocks = []
        else:
            blocks = list(blocks_raw)
        correct = ''.join(blocks) if blocks else ex.get('audio_text', '')
        vector['answer'] = correct.rstrip('。、！？…')
        vector['prompt'] = ''  # filled at display time
    elif vector.get('vector_type') == 'translate':
        # For translate vectors, answer = full Japanese, prompt = English
        correct = ex.get('audio_text', '') or ex.get('japanese', '')
        vector['answer'] = correct.rstrip('。、！？…')
        vector['prompt'] = ed['translation']
    elif vector.get('vector_type') == 'dictation':
        # For dictation vectors, answer = full Japanese, prompt = same (for TTS)
        correct = ex.get('audio_text', '') or ex.get('japanese', '')
        vector['answer'] = correct.rstrip('。、！？…')
        vector['prompt'] = vector['answer']
    else:
        vector['prompt'] = ed['sentence_with_blank']
        vector['answer'] = ed['correct_answer']
    return ed


# -- small helpers --

def _parse_alt_answers(ex: dict) -> list:
    try:
        raw = ex.get('alternative_answers', '[]')
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _build_exercise_data(ex: dict, grammar_point: str) -> dict:
    sentence = ex.get('japanese_with_blank', '')
    display_sentence = sentence.replace('[___]', '＿＿＿')
    return {
        'exercise_id': ex['id'],
        'sentence_with_blank': display_sentence,
        'correct_answer': ex.get('target_answer', ''),
        'alternative_answers': _parse_alt_answers(ex),
        'translation': ex.get('english', ''),
        'hint': ex.get('hint', ''),
        'notes': ex.get('notes', ''),
        'category': ex.get('category', ''),
        'category_label': ex.get('category_label', ''),
        'scramble_blocks': ex.get('scramble_blocks', ''),
        'audio_text': ex.get('audio_text', ''),
        'grammar_point': grammar_point,
        'form_being_tested': '',
    }


def make_vectors_for_entries(conn, entry_ids: List[int], kind: str) -> List[dict]:
    """Create canonical vectors for entries. Currently supports `kana` kind.
    
    Note: The `kind` parameter is used as a fallback/default, but each entry's
    actual kind from the database takes precedence to support mixed-type sessions.
    """
    vectors = []
    for eid in entry_ids:
        cur = conn.cursor()
        cur.execute('SELECT kanji, kana, gloss, study_vectors, kind FROM entries WHERE id = ?', (eid,))
        row = cur.fetchone()
        if not row:
            continue
        kanji, kana, gloss, study_vectors_str, entry_kind = row
        # Use the entry's actual kind from database, fall back to passed kind if missing
        actual_kind = entry_kind if entry_kind else kind
        vectors.extend(make_vectors_from_row(eid, actual_kind, kanji, kana, gloss, study_vectors_str))
    return vectors


FILLER_POOL_SIZE = 30  # batch-load this many fillers at once


def _get_pooled_filler(conn, data: dict, kind: str, exclude_ids: set,
                       meta: dict) -> dict | None:
    """Return a single filler from the session's pre-loaded pool.

    The pool is stored in ``data['filler_pool']`` and refilled in batches
    of FILLER_POOL_SIZE so _select_fillers (the expensive DB call) runs
    ~once per 10 fillers instead of every submission.
    """
    recent_eids = set(data.get('recent_filler_entry_ids', []))
    pool = data.get('filler_pool', [])

    # Try to find a usable filler already in the pool
    for i, f in enumerate(pool):
        if f['id'] not in exclude_ids and f.get('entry_id') not in recent_eids:
            pool.pop(i)
            data['filler_pool'] = pool
            return f

    # Pool exhausted — refill from DB
    fillers = _select_fillers(
        conn, kind, FILLER_POOL_SIZE, exclude_ids=exclude_ids,
        filter_vector_type=meta.get('filter_vector_type'),
        exclude_vector_type=meta.get('exclude_vector_type'),
        exclude_entry_ids=recent_eids,
    )
    if not fillers:
        return None
    filler = fillers.pop(0)
    data['filler_pool'] = fillers  # cache the rest
    return filler


def _select_fillers(conn, kind: str, count: int, exclude_ids: set | None = None,
                    filter_vector_type: str = None, exclude_vector_type: str = None,
                    exclude_entry_ids: set | None = None) -> List[dict]:
    """Select filler entries from user's existing SRS system (only entries they've already learned).
    
    Args:
        conn: Database connection
        kind: Entry kind (vocab, kanji, grammar)
        count: Number of fillers to select
        exclude_ids: Vector IDs to exclude
        filter_vector_type: If set, only include vectors of this type (e.g., 'handwriting')
        exclude_vector_type: If set, exclude vectors of this type (e.g., 'handwriting')
        exclude_entry_ids: Entry IDs to exclude (e.g., recently shown fillers)
    """
    exclude_ids = exclude_ids or set()
    exclude_entry_ids = exclude_entry_ids or set()
    cur = conn.cursor()
    
    # Only get entries that have SRS data for this specific kind
    # This ensures fillers are only words the user has already learned
    # Use LIMIT 30 + random offset to keep the query cheap while still
    # getting variety.  Counting first is a single index-only scan.
    total_cur = conn.cursor()
    total_cur.execute('SELECT COUNT(*) FROM entries e JOIN srs s ON e.id = s.entry_id WHERE e.kind = ?', (kind,))
    total_rows = total_cur.fetchone()[0]
    if total_rows < 3:
        return []
    import random as _rand
    offset = _rand.randint(0, max(0, total_rows - 30))
    cur.execute("""
        SELECT e.id, s.data, e.kanji, e.kana, e.gloss, e.study_vectors, e.kind
        FROM entries e
        JOIN srs s ON e.id = s.entry_id
        WHERE e.kind = ?
        ORDER BY e.id 
        LIMIT 30 OFFSET ?
    """, (kind, offset))
    rows = cur.fetchall()
    
    candidates = []
    import json
    for eid, data_json, kanji, kana, gloss, study_vectors_str, entry_kind in rows:
        if eid in exclude_entry_ids:
            continue
        srs_data = json.loads(data_json) if data_json else {}
        per_kind = srs_data.get('per_kind', {})
        if kind not in per_kind:
            continue
        kind_data = per_kind[kind]
        accuracy = kind_data.get('accuracy') if kind_data.get('accuracy') is not None else 0.5
        ease = kind_data.get('ease', 2.5)
        score = (accuracy, -ease)
        candidates.append((score, eid, kanji, kana, gloss, study_vectors_str, entry_kind))
    
    candidates.sort()
    out = []
    for _, eid, kanji, kana, gloss, study_vectors_str, entry_kind in candidates:
        if len(out) >= count:
            break
        actual_kind = entry_kind if entry_kind else kind
        vs = make_vectors_from_row(eid, actual_kind, kanji, kana, gloss, study_vectors_str)
        if not vs:
            continue
        if filter_vector_type:
            vs = [v for v in vs if v.get('vector_type') == filter_vector_type]
        if exclude_vector_type:
            vs = [v for v in vs if v.get('vector_type') != exclude_vector_type]
        if not vs:
            continue
        v = vs[0].copy()
        if v['id'] in exclude_ids:
            continue
        v['is_filler'] = True
        out.append(v)
    return out


def _make_info_cards(conn, entry_ids: List[int], kind: str) -> List[dict]:
    """Build rich info cards for lesson preview.

    Each card includes: kanji, kana, gloss, pos, jlpt_level,
    and up to 3 Tatoeba example sentences.
    """
    cards = []
    for eid in entry_ids:
        cur = conn.cursor()
        cur.execute(
            'SELECT kanji, kana, gloss, pos, tags, kind FROM entries WHERE id = ?',
            (eid,),
        )
        row = cur.fetchone()
        if not row:
            continue
        kanji, kana, gloss, pos, tags_raw, entry_kind = row
        prompt = kana or kanji or ''

        # Parse JLPT level from tags JSON
        jlpt_level = ''
        if tags_raw:
            try:
                import json as _json
                tags_obj = _json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
                jlpt_level = tags_obj.get('jlpt', '') if isinstance(tags_obj, dict) else ''
            except Exception:
                pass

        # Fetch up to 3 example sentences
        examples = []
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tatoeba_sentences'"
            )
            if cur.fetchone():
                # Look up jid for the entry
                cur.execute('SELECT jid FROM entries WHERE id = ?', (eid,))
                jid_row = cur.fetchone()
                jmdict_id = jid_row[0] if jid_row else None
                if jmdict_id:
                    cur.execute(
                        '''SELECT DISTINCT japanese, english FROM tatoeba_sentences
                           WHERE jmdict_id = ?
                           ORDER BY CASE WHEN english IS NOT NULL AND english != '' THEN 0 ELSE 1 END,
                                    LENGTH(japanese)
                           LIMIT 3''',
                        (jmdict_id,),
                    )
                else:
                    lookup = kanji or kana
                    cur.execute(
                        '''SELECT DISTINCT japanese, english FROM tatoeba_sentences
                           WHERE word = ?
                           ORDER BY CASE WHEN english IS NOT NULL AND english != '' THEN 0 ELSE 1 END,
                                    LENGTH(japanese)
                           LIMIT 3''',
                        (lookup,),
                    )
                for ex_row in cur.fetchall():
                    examples.append({'japanese': ex_row[0], 'english': ex_row[1] or ''})
        except Exception:
            pass

        cards.append({
            'id': f"info:{eid}",
            'entry_id': eid,
            'kind': entry_kind or kind,
            'kanji': kanji or '',
            'kana': kana or '',
            'prompt': prompt,
            'gloss': gloss or '',
            'pos': pos or '',
            'jlpt_level': jlpt_level,
            'examples': examples,
        })
    return cards


INITIAL_BATCH_SIZE = 4  # Number of entries to show info cards for at start
MAX_ACTIVE_ENTRIES = 7  # Maximum entries "in the bunch" at once


def create_session(conn, entry_ids: List[int], kind: str, created_by: str = 'user',
                   filter_vector_type: str = None, exclude_vector_type: str = None,
                   enable_batched_info: bool = False) -> int:
    """Create a drill/learning session.

    When *enable_batched_info* is True (lesson drills), the session uses a
    progressive unlock flow:
      • First INITIAL_BATCH_SIZE (4) entries get info cards shown upfront.
      • User drills those entries; max MAX_ACTIVE_ENTRIES (7) vectors "active" at once.
      • When user gets 1 correct on any entry AND active < 7 AND more entries remain:
        a new info card is shown, then drilling continues.
      • Vectors still need 3 correct in a row to graduate/promote.
    """
    # Info cards are shown first; in_progress/pending are used during drill phase
    info = _make_info_cards(conn, entry_ids, kind)
    all_vectors = make_vectors_for_entries(conn, entry_ids, kind)

    # Filter vectors by type if specified (e.g., handwriting-only mode)
    if filter_vector_type:
        all_vectors = [v for v in all_vectors if v.get('vector_type') == filter_vector_type]

    # Exclude vectors by type if specified (e.g., no handwriting mode)
    if exclude_vector_type:
        all_vectors = [v for v in all_vectors if v.get('vector_type') != exclude_vector_type]

    # ---- Progressive unlock lesson flow ----
    if enable_batched_info and info:
        # Map info cards and vectors by entry_id
        info_by_entry = {c['entry_id']: c for c in info}
        vectors_by_entry: Dict[int, list] = {}
        for v in all_vectors:
            vectors_by_entry.setdefault(v['entry_id'], []).append(v)

        # Determine initial entries (first INITIAL_BATCH_SIZE)
        initial_entry_ids = entry_ids[:INITIAL_BATCH_SIZE]
        remaining_entry_ids = entry_ids[INITIAL_BATCH_SIZE:]

        # Initial info cards to show
        initial_info = [info_by_entry[eid] for eid in initial_entry_ids if eid in info_by_entry]

        # Initial vectors for drilling
        initial_vectors = []
        for eid in initial_entry_ids:
            initial_vectors.extend(vectors_by_entry.get(eid, []))

        # Remaining info cards (to be unlocked progressively)
        remaining_info = [info_by_entry[eid] for eid in remaining_entry_ids if eid in info_by_entry]

        # Remaining vectors (to be injected when their info card is shown)
        remaining_vectors = []
        for eid in remaining_entry_ids:
            remaining_vectors.extend(vectors_by_entry.get(eid, []))

        # Set up in_progress and pending from initial vectors
        in_progress = initial_vectors[:MAX_ACTIVE_ENTRIES]
        pending = initial_vectors[MAX_ACTIVE_ENTRIES:]

        # Don't add fillers initially - we want to keep room for new lesson entries
        random.shuffle(in_progress)

        data = {
            'info': initial_info,  # Info cards to show in current info phase
            'info_index': 0,
            'phase': 'info',  # Start with info cards
            'pending': pending,
            'in_progress': in_progress,
            'meta': {
                'created_by': created_by,
                'num_slots': MAX_ACTIVE_ENTRIES,
                'created_at': datetime.utcnow().isoformat(),
                'filter_vector_type': filter_vector_type,
                'exclude_vector_type': exclude_vector_type,
                'batched_lesson': True,
                'progressive_unlock': True,
            },
            'history': [],
            # Progressive unlock tracking
            'all_entry_ids': entry_ids,
            'active_entry_ids': list(initial_entry_ids),  # Entries currently being drilled
            'remaining_info': remaining_info,  # Info cards not yet shown
            'remaining_vectors': remaining_vectors,  # Vectors not yet injected
            'first_correct_entries': [],  # Entries that have gotten at least 1 correct
            'pending_info_card': None,  # Set when we need to show an info card next
        }

        # Pre-seed filler pool so the first 30 filler requests cost zero DB queries
        _preseed_exclude = {v['id'] for v in in_progress + pending}
        _preseed_fillers = _select_fillers(
            conn, kind, FILLER_POOL_SIZE, exclude_ids=_preseed_exclude,
            filter_vector_type=filter_vector_type,
            exclude_vector_type=exclude_vector_type,
        )
        if _preseed_fillers:
            data['filler_pool'] = _preseed_fillers
    else:
        # ---- Standard (non-batched) flow ----
        pending = all_vectors
        in_progress = pending[:DEFAULT_SLOTS]
        pending = pending[DEFAULT_SLOTS:]
        if len(in_progress) < DEFAULT_SLOTS:
            exclude = {v['id'] for v in in_progress}
            fillers = _select_fillers(conn, kind, DEFAULT_SLOTS - len(in_progress),
                                      exclude_ids=exclude,
                                      filter_vector_type=filter_vector_type,
                                      exclude_vector_type=exclude_vector_type)
            in_progress.extend(fillers)
        random.shuffle(in_progress)

        data = {
            'info': info,
            'info_index': 0,
            'phase': 'info' if info else 'drill',
            'pending': pending,
            'in_progress': in_progress,
            'meta': {
                'created_by': created_by,
                'num_slots': DEFAULT_SLOTS,
                'created_at': datetime.utcnow().isoformat(),
                'filter_vector_type': filter_vector_type,
                'exclude_vector_type': exclude_vector_type,
            },
            'history': [],
        }

    # Pre-seed filler pool for standard sessions too
    if 'filler_pool' not in data:
        _preseed_exclude = {v['id'] for v in data.get('in_progress', []) + data.get('pending', [])}
        _preseed_fillers = _select_fillers(
            conn, kind, FILLER_POOL_SIZE, exclude_ids=_preseed_exclude,
            filter_vector_type=filter_vector_type,
            exclude_vector_type=exclude_vector_type,
        )
        if _preseed_fillers:
            data['filler_pool'] = _preseed_fillers

    sid = dict_db.create_learning_session(conn, kind, data)
    dict_db.update_learning_session(conn, sid, data)
    return sid


def get_session(conn, session_id: int) -> dict | None:
    s = dict_db.get_learning_session(conn, session_id)
    return s


def advance_to_next_batch(conn, session_id: int) -> dict:
    """Advance a batched lesson session to the next batch.

    Returns a status dict:
      {'status': 'advanced', 'batch_index': N}   – moved to next batch info phase
      {'status': 'final_drill'}                    – all batches shown, entering final drill with all entries
      {'status': 'not_batched'}                    – session isn't using batched flow
    """
    s = get_session(conn, session_id)
    if not s:
        raise ValueError('session not found')
    data = s['data']
    meta = data.get('meta', {})

    if not meta.get('batched_lesson'):
        return {'status': 'not_batched'}

    # ---- Progressive unlock mode (new) ----
    if meta.get('progressive_unlock'):
        # Check if there are remaining entries to unlock
        remaining_info = data.get('remaining_info', [])
        remaining_vectors = data.get('remaining_vectors', [])
        
        if not remaining_info and not remaining_vectors:
            # All entries have been unlocked, check if drill is complete
            active_real = [v for v in data.get('in_progress', [])
                           if not v.get('is_filler') and not v.get('already_in_srs')]
            pending_real = [v for v in data.get('pending', [])
                            if not v.get('is_filler') and not v.get('already_in_srs')]
            if not active_real and not pending_real:
                return {'status': 'complete'}
        return {'status': 'progressive'}
    
    # ---- Old batch mode (backwards compatibility) ----
    batch_index = data.get('batch_index', 0)
    batch_count = data.get('batch_count', 1)
    batched_vectors = data.get('batched_vectors', [])
    batched_info = data.get('batched_info', [])

    next_batch = batch_index + 1

    if next_batch < batch_count and batched_vectors:
        # Inject next batch's vectors into in_progress/pending
        new_vectors = batched_vectors.pop(0)  # consume from front

        # Add them to pending (they'll flow into in_progress normally)
        pending = data.get('pending', [])
        in_progress = data.get('in_progress', [])
        pending.extend(new_vectors)

        data['batch_index'] = next_batch
        data['info_index'] = 0
        data['phase'] = 'info'
        data['pending'] = pending
        data['in_progress'] = in_progress
        data['batched_vectors'] = batched_vectors

        dict_db.update_learning_session(conn, session_id, data)
        return {'status': 'advanced', 'batch_index': next_batch}
    else:
        # All batches exhausted – final drill round with everything
        # Any remaining batched_vectors get injected too
        pending = data.get('pending', [])
        for bv in batched_vectors:
            pending.extend(bv)
        data['batched_vectors'] = []
        data['batch_index'] = batch_count  # past last batch
        data['phase'] = 'drill'
        data['pending'] = pending
        meta['final_drill'] = True
        data['meta'] = meta
        dict_db.update_learning_session(conn, session_id, data)
        return {'status': 'final_drill'}


def get_pending_info_card(conn, session_id: int) -> dict | None:
    """Get the pending info card if one was unlocked by first-correct.
    
    Returns the info card dict or None if no card is pending.
    """
    s = get_session(conn, session_id)
    if not s:
        return None
    data = s['data']
    return data.get('pending_info_card')


def clear_pending_info_card(conn, session_id: int):
    """Clear the pending info card after it has been shown."""
    s = get_session(conn, session_id)
    if not s:
        return
    data = s['data']
    data['pending_info_card'] = None
    dict_db.update_learning_session(conn, session_id, data)


def set_session_phase(conn, session_id: int, phase: str, info_index: int = 0):
    """Update session phase and info_index."""
    s = get_session(conn, session_id)
    if not s:
        return
    data = s['data']
    data['phase'] = phase
    data['info_index'] = info_index
    dict_db.update_learning_session(conn, session_id, data)


def get_next_vector(conn, session_id: int) -> dict | None:
    s = get_session(conn, session_id)
    if not s:
        return None
    data = s['data']
    in_progress = data.get('in_progress') or []
    if not in_progress:
        return None
    return in_progress[0]


def submit_vector_result(conn, session_id: int, vector_id: str, correct: bool) -> dict:
    s = get_session(conn, session_id)
    if not s:
        raise ValueError('session not found')
    data = s['data']
    in_progress = data.get('in_progress') or []
    pending = data.get('pending') or []
    history = data.get('history') or []

    # find the vector in in_progress
    idx = next((i for i, v in enumerate(in_progress) if v['id'] == vector_id), None)
    if idx is None:
        return {'status': 'not_found'}
    v = in_progress[idx]
    
    # Check if vector is a filler or already in SRS - these always pass regardless of answer
    # Fast path: skip snapshot/history for fillers to avoid expensive deep-copies
    if v.get('is_filler') or v.get('already_in_srs'):
        # Track recently shown filler entry IDs to avoid consecutive repeats
        if v.get('is_filler'):
            recent = data.get('recent_filler_entry_ids', [])
            recent.append(v.get('entry_id'))
            data['recent_filler_entry_ids'] = recent[-5:]  # keep last 5
        # treat as pass; remove from stack and replace but do not promote
        in_progress.pop(idx)

        # ---- Drain recovery: if all real vectors gone but entries remain, unlock next ----
        # Without this, once every active entry is promoted the session fills
        # with fillers and nothing ever unlocks the remaining entries.
        drain_unlocked = False
        meta = data.get('meta', {})
        if meta.get('progressive_unlock'):
            has_real = any(
                not x.get('is_filler') and not x.get('already_in_srs')
                for x in in_progress
            ) or any(
                not x.get('is_filler') and not x.get('already_in_srs')
                for x in pending
            )
            if not has_real:
                remaining_info = data.get('remaining_info', [])
                if remaining_info:
                    active_entry_ids = set(data.get('active_entry_ids', []))
                    next_entry_id = remaining_info[0]['entry_id']
                    drain_cards = []
                    while remaining_info and remaining_info[0].get('entry_id') == next_entry_id:
                        drain_cards.append(remaining_info.pop(0))
                    active_entry_ids.add(next_entry_id)
                    data['active_entry_ids'] = list(active_entry_ids)
                    data['remaining_info'] = remaining_info
                    remaining_vectors = data.get('remaining_vectors', [])
                    new_vectors = [rv for rv in remaining_vectors if rv['entry_id'] == next_entry_id]
                    remaining_vectors = [rv for rv in remaining_vectors if rv['entry_id'] != next_entry_id]
                    data['remaining_vectors'] = remaining_vectors
                    # Interleave new vectors into pending so entries mix
                    for nv in new_vectors:
                        pos = random.randint(0, len(pending)) if pending else 0
                        pending.insert(pos, nv)
                    data['pending'] = pending
                    data['pending_info_card'] = drain_cards if len(drain_cards) > 1 else drain_cards[0]
                    drain_unlocked = True

        # pull replacement (skip redrill-scheduled pending)
        replacement = None
        while pending:
            cand = pending.pop(0)
            ra = cand.get('redrill_after')
            if not ra or ra <= _now_iso():
                replacement = cand
                break
        if replacement:
            in_progress.append(replacement)
        else:
            # just take the next pending or filler if none
            if pending:
                in_progress.append(pending.pop(0))
            else:
                exclude = {x['id'] for x in in_progress}
                filler = _get_pooled_filler(conn, data, v['kind'], exclude,
                                           data.get('meta', {}))
                if filler:
                    in_progress.append(filler)
        # Ensure a real vector is next (position 0) if one exists, so the user
        # never sees two fillers in a row when real work is still queued.
        if in_progress and (in_progress[0].get('is_filler') or in_progress[0].get('already_in_srs')):
            for ri, rv in enumerate(in_progress):
                if not rv.get('is_filler') and not rv.get('already_in_srs'):
                    in_progress.insert(0, in_progress.pop(ri))
                    break
        data['in_progress'] = in_progress
        data['pending'] = pending
        res = {'status': 'already_in_srs' if v.get('already_in_srs') else 'filler_pass',
               'show_next_info': drain_unlocked}
        dict_db.update_learning_session(conn, session_id, data)
        return res
    
    # capture pre-change snapshot for undo support (non-filler vectors only)
    import json as _json
    snapshot = {
        'timestamp': _now_iso(),
        'vector_id': v['id'],
        'prev_in_progress': _json.loads(_json.dumps(in_progress)),
        'prev_pending': _json.loads(_json.dumps(pending)),
        'prev_meta': _json.loads(_json.dumps(data.get('meta', {}))),
        'prev_vector': _json.loads(_json.dumps(v)),
        'result': None,
    }

    if correct:
        prev_streak = v.get('streak', 0)
        v['streak'] = prev_streak + 1
        
        # ---- Progressive unlock: check if this is the FIRST correct for this entry ----
        meta = data.get('meta', {})
        show_next_info = False
        if meta.get('progressive_unlock') and v['streak'] == 1:
            entry_id = v['entry_id']
            first_correct_entries = data.get('first_correct_entries', [])
            if entry_id not in first_correct_entries:
                first_correct_entries.append(entry_id)
                data['first_correct_entries'] = first_correct_entries
                
                # Check if we should unlock a new entry
                active_entry_ids = set(data.get('active_entry_ids', []))
                remaining_info = data.get('remaining_info', [])
                
                if len(active_entry_ids) < MAX_ACTIVE_ENTRIES and remaining_info:
                    # Unlock the next entry: pop all cards for this entry (teaching + info)
                    next_entry_id = remaining_info[0]['entry_id']
                    pending_cards = []
                    while remaining_info and remaining_info[0].get('entry_id') == next_entry_id:
                        pending_cards.append(remaining_info.pop(0))
                    
                    # Add to active entries
                    active_entry_ids.add(next_entry_id)
                    data['active_entry_ids'] = list(active_entry_ids)
                    data['remaining_info'] = remaining_info
                    
                    # Find and inject vectors for this entry
                    remaining_vectors = data.get('remaining_vectors', [])
                    new_vectors = [rv for rv in remaining_vectors if rv['entry_id'] == next_entry_id]
                    remaining_vectors = [rv for rv in remaining_vectors if rv['entry_id'] != next_entry_id]
                    data['remaining_vectors'] = remaining_vectors
                    
                    # Interleave new vectors into pending so entries mix
                    for nv in new_vectors:
                        pos = random.randint(0, len(pending)) if pending else 0
                        pending.insert(pos, nv)
                    
                    # Set flag to show info card(s) on next load
                    data['pending_info_card'] = pending_cards if len(pending_cards) > 1 else pending_cards[0]
                    show_next_info = True
        
        if v.get('streak', 0) >= 3:
            # Check if this is a weak words drill (no SRS promotion)
            meta = data.get('meta', {})
            skip_srs = meta.get('drill_type') == 'weak_words'
            
            if not skip_srs:
                # promote to SRS — pass vector_type so each type gets independent SRS state
                dict_db.apply_review(conn, v['entry_id'], 5, kind=v['kind'], vector_type=v.get('vector_type'))
                # Track which specific vector was promoted so queue cleanup
                # can tell when ALL vectors for an entry have been learned.
                # For grammar category-slot vectors, store the full vector ID
                # (e.g. "grammar-cat:325425:contrast:0") since they all share
                # vector_type "fill-blank" but represent distinct categories.
                _vt = v.get('id', '') if v.get('grammar_category') else v.get('vector_type', '')
                if _vt:
                    _cur = conn.cursor()
                    _cur.execute('SELECT data FROM srs WHERE entry_id = ?', (v['entry_id'],))
                    _srow = _cur.fetchone()
                    if _srow:
                        import json as _j
                        _sd = _j.loads(_srow[0] or '{}')
                        _pk = _sd.get('per_kind', {}).get(v['kind'], {})
                        _pv = _pk.get('promoted_vectors', [])
                        if _vt not in _pv:
                            _pv.append(_vt)
                            _pk['promoted_vectors'] = _pv
                            _sd.setdefault('per_kind', {})[v['kind']] = _pk
                            _cur.execute('UPDATE srs SET data = ? WHERE entry_id = ?',
                                         (_j.dumps(_sd), v['entry_id']))
                            conn.commit()

            # ---- Progressive grammar unlock: inject deferred vectors ----
            # fill-blank promoted → inject one scramble for the same entry
            # scramble promoted  → inject one translate for the same entry
            # translate promoted → inject one dictation for the same entry
            if v.get('kind') == 'grammar':
                _next_type = None
                if v.get('vector_type') == 'fill-blank':
                    _next_type = 'scramble'
                elif v.get('vector_type') == 'scramble':
                    _next_type = 'translate'
                elif v.get('vector_type') == 'translate':
                    _next_type = 'dictation'
                if _next_type:
                    remaining = data.get('remaining_vectors', [])
                    for ri, rv in enumerate(remaining):
                        if rv.get('entry_id') == v['entry_id'] and rv.get('vector_type') == _next_type:
                            pending.append(remaining.pop(ri))
                            data['remaining_vectors'] = remaining
                            break
            
            # remove this vector from in_progress
            in_progress.pop(idx)
            
            # ---- Progressive unlock on promotion ----
            # When a vector is promoted, check if we should unlock the next
            # entry. This is critical: without this, once the initial batch
            # masters out, no new entries ever get unlocked and the session
            # fills with fillers forever.
            meta = data.get('meta', {})
            promotion_unlocked = False
            if meta.get('progressive_unlock'):
                active_entry_ids = set(data.get('active_entry_ids', []))
                
                # Remove the promoted entry from active if it has no more
                # vectors in in_progress or pending (fully mastered)
                promoted_eid = v['entry_id']
                has_remaining = any(
                    x.get('entry_id') == promoted_eid
                    and not x.get('is_filler') and not x.get('already_in_srs')
                    for x in in_progress + pending
                )
                # Also check remaining_vectors for deferred grammar types
                has_deferred = any(
                    x.get('entry_id') == promoted_eid
                    for x in data.get('remaining_vectors', [])
                )
                if not has_remaining and not has_deferred and promoted_eid in active_entry_ids:
                    active_entry_ids.discard(promoted_eid)
                    data['active_entry_ids'] = list(active_entry_ids)
                
                remaining_info = data.get('remaining_info', [])
                if len(active_entry_ids) < MAX_ACTIVE_ENTRIES and remaining_info:
                    next_info = remaining_info.pop(0)
                    next_entry_id = next_info['entry_id']
                    active_entry_ids.add(next_entry_id)
                    data['active_entry_ids'] = list(active_entry_ids)
                    data['remaining_info'] = remaining_info
                    remaining_vectors = data.get('remaining_vectors', [])
                    new_vectors = [rv for rv in remaining_vectors if rv['entry_id'] == next_entry_id]
                    remaining_vectors = [rv for rv in remaining_vectors if rv['entry_id'] != next_entry_id]
                    data['remaining_vectors'] = remaining_vectors
                    # Interleave new vectors into pending so entries mix
                    for nv in new_vectors:
                        pos = random.randint(0, len(pending)) if pending else 0
                        pending.insert(pos, nv)
                    data['pending_info_card'] = next_info
                    promotion_unlocked = True
            
            # refill (skip pending entries scheduled for future redrill)
            replacement = None
            while pending:
                cand = pending.pop(0)
                ra = cand.get('redrill_after')
                if not ra or ra <= _now_iso():
                    replacement = cand
                    break
            if replacement:
                in_progress.append(replacement)
            else:
                # try to use next pending, otherwise fallback to filler
                if pending:
                    in_progress.append(pending.pop(0))
                else:
                    exclude = {x['id'] for x in in_progress}
                    filler = _get_pooled_filler(conn, data, v['kind'], exclude,
                                               data.get('meta', {}))
                    if filler:
                        in_progress.append(filler)
            # Ensure a real vector is next if one exists
            if in_progress and (in_progress[0].get('is_filler') or in_progress[0].get('already_in_srs')):
                for ri, rv in enumerate(in_progress):
                    if not rv.get('is_filler') and not rv.get('already_in_srs'):
                        in_progress.insert(0, in_progress.pop(ri))
                        break
            data['in_progress'] = in_progress
            data['pending'] = pending
            # record snapshot and save history
            res = {'status': 'promoted', 'entry_id': v['entry_id'], 'vector': v,
                   'show_next_info': promotion_unlocked}
            snapshot['result'] = res
            history.append(snapshot)
            if len(history) > 50:
                history = history[-50:]
            data['history'] = history
            dict_db.update_learning_session(conn, session_id, data)
            return res
        else:
            # rotate this vector randomly (but not at position 0 to avoid immediate repeat)
            in_progress.pop(idx)
            if len(in_progress) > 0:
                # Smart randomization: if there are many fillers, keep real vectors closer to front
                filler_count = sum(1 for vec in in_progress if vec.get('is_filler') or vec.get('already_in_srs'))
                real_count = len(in_progress) - filler_count
                
                if filler_count >= 4 and real_count <= 2:
                    # Many fillers, few real vectors - insert near front (positions 1-3)
                    max_pos = min(3, len(in_progress))
                    insert_pos = random.randint(1, max_pos)
                else:
                    # Normal case - full random range
                    insert_pos = random.randint(1, len(in_progress))
                in_progress.insert(insert_pos, v)
            else:
                in_progress.append(v)
            data['in_progress'] = in_progress
            data['pending'] = pending  # Save pending (may have new vectors from unlock)
            # record snapshot and save history
            res = {'status': 'streak_incremented', 'streak': v['streak'], 'show_next_info': show_next_info}
            snapshot['result'] = res
            history.append(snapshot)
            if len(history) > 50:
                history = history[-50:]
            data['history'] = history
            dict_db.update_learning_session(conn, session_id, data)
            return res
    else:
        # incorrect: reset streak and rotate this vector randomly (but not at position 0 to avoid immediate repeat)
        v['streak'] = 0
        in_progress.pop(idx)
        if len(in_progress) > 0:
            # Smart randomization: if there are many fillers, keep real vectors closer to front
            filler_count = sum(1 for vec in in_progress if vec.get('is_filler') or vec.get('already_in_srs'))
            real_count = len(in_progress) - filler_count
            
            if filler_count >= 4 and real_count <= 2:
                # Many fillers, few real vectors - insert near front (positions 1-3)
                max_pos = min(3, len(in_progress))
                insert_pos = random.randint(1, max_pos)
            else:
                # Normal case - full random range
                insert_pos = random.randint(1, len(in_progress))
            in_progress.insert(insert_pos, v)
        else:
            in_progress.append(v)
        data['in_progress'] = in_progress
        # record snapshot and save history
        res = {'status': 'reset'}
        snapshot['result'] = res
        history.append(snapshot)
        if len(history) > 50:
            history = history[-50:]
        data['history'] = history
        dict_db.update_learning_session(conn, session_id, data)
        return res


def undo_last_submission(conn, session_id: int) -> dict:
    """Undo the last submit_vector_result if possible.

    Returns:
      {'status': 'restored', 'vector_id': ..., 'prev_vector': {...}} on success
      {'status': 'no_history'} if nothing to undo
      {'status': 'cannot_undo_promotion'} if the last action promoted an item to SRS (not reversible)
    """
    s = get_session(conn, session_id)
    if not s:
        raise ValueError('session not found')
    data = s['data']
    history = data.get('history') or []
    if not history:
        return {'status': 'no_history'}
    last = history.pop()
    res = last.get('result') or {}
    if res.get('status') == 'promoted':
        # do not attempt to undo promotions to SRS
        # push it back and return an error
        history.append(last)
        return {'status': 'cannot_undo_promotion'}
    # restore prior state
    data['in_progress'] = last.get('prev_in_progress', [])
    data['pending'] = last.get('prev_pending', [])
    data['meta'] = last.get('prev_meta', data.get('meta', {}))
    data['history'] = history
    dict_db.update_learning_session(conn, session_id, data)
    return {'status': 'restored', 'vector_id': last.get('vector_id'), 'prev_vector': last.get('prev_vector')}
