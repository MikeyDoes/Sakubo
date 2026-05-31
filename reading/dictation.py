"""
Dictation comparison — compares user-typed text against original text.

Uses pykakasi to tokenize the original into segments with known readings,
then aligns user input against those readings to handle kana-for-kanji gracefully.

Returns a list of color-coded spans for display:
  - green:  exact match (including kana↔kana variant)
  - yellow: correct reading typed in kana where kanji was expected
  - red:    wrong characters
  - grey:   punctuation (ignored in scoring)

Punctuation differences are ignored (not scored).
"""

import unicodedata

# Punctuation characters to ignore in scoring
_PUNCT = set('。、！？「」『』（）〜…・　 \t\n.!?,;:\'\"()-')


def _is_punct(ch):
    if ch == 'ー':  # Long vowel mark is part of words, not punctuation
        return False
    return ch in _PUNCT or unicodedata.category(ch).startswith('P') or unicodedata.category(ch).startswith('Z')


def _to_hiragana(text):
    """Convert katakana to hiragana for comparison."""
    result = []
    for ch in text:
        cp = ord(ch)
        if 0x30A1 <= cp <= 0x30F6:  # Katakana
            result.append(chr(cp - 0x60))
        else:
            result.append(ch)
    return ''.join(result)


def _is_kana(ch):
    cp = ord(ch)
    return (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF)


def _has_kanji(text):
    return any(0x4E00 <= ord(c) <= 0x9FFF for c in text)


# Character name reading overrides — names whose standard dictionary reading
# differs from the intended fictional reading.
CHARACTER_NAME_READINGS = {
    '桃花': 'ももか',
}


def tokenize_with_readings(text):
    """Tokenize text into segments with readings using pykakasi.
    Returns list of dicts: {'orig': str, 'reading': str, 'is_punct': bool}"""
    try:
        import pykakasi
        kakasi = pykakasi.kakasi()
        raw_segments = kakasi.convert(text)
    except Exception:
        # Fallback: DB-based tokenization with kanji→kana lookup
        return _tokenize_with_db_readings(text)

    result = []
    for seg in raw_segments:
        orig = seg.get('orig', '')
        hira = seg.get('hira', orig)
        # Split into individual characters for purely-punctuation segments
        if all(_is_punct(c) for c in orig):
            for c in orig:
                result.append({'orig': c, 'reading': c, 'is_punct': True})
        else:
            reading = CHARACTER_NAME_READINGS.get(orig, _to_hiragana(hira))
            result.append({'orig': orig, 'reading': reading, 'is_punct': False})
    # Merge adjacent segments when DB knows a better compound (e.g. ご+飯→ご飯)
    result = _merge_segments_with_db(result)
    # Correct single-char kanji readings: pykakasi may choose an on-reading
    # (e.g. 朝→ちょう) but students type the vocab/kun reading (朝→あさ).
    return _apply_db_single_char_readings(result)


def _apply_db_single_char_readings(segments):
    """Override pykakasi readings for single-char kanji using the app's vocab DB.
    pykakasi picks context-dependent readings (e.g. 朝→ちょう) that may differ
    from what the student is expected to type (朝→あさ from the vocab entry).
    Only overrides when the DB has a vocab-priority entry for that exact character."""
    kmap = _get_kanji_kana_map()
    if not kmap:
        return segments
    result = []
    for seg in segments:
        if (not seg['is_punct']
                and len(seg['orig']) == 1
                and any(0x4E00 <= ord(c) <= 0x9FFF for c in seg['orig'])
                and seg['orig'] not in CHARACTER_NAME_READINGS):
            db_reading = kmap.get(seg['orig'])
            if db_reading:
                seg = dict(seg)
                seg['reading'] = _to_hiragana(db_reading)
        result.append(seg)
    return result


_kanji_kana_map_cache = None


def _get_kanji_kana_map():
    """Load kanji→kana lookup map from DB (cached).
    Also generates stem forms by stripping matching trailing okurigana,
    so conjugated forms like 食べ (from 食べる/たべる) are found."""
    global _kanji_kana_map_cache
    if _kanji_kana_map_cache is not None:
        return _kanji_kana_map_cache
    _kanji_kana_map_cache = {}
    try:
        import sqlite3
        from dictionary.paths import get_db_path
        conn = sqlite3.connect(get_db_path())
        cur = conn.cursor()
        # Load kanji entries first (lower priority)
        cur.execute('SELECT kanji, kana FROM entries WHERE kanji IS NOT NULL AND kanji != "" AND kind = "kanji"')
        for kanji, kana in cur.fetchall():
            if 'kun:' in kana:
                parts = kana.split('kun:')[1].split('|')[0].strip()
                first_reading = parts.split()[0].replace('.', '') if parts else kana
                _kanji_kana_map_cache[kanji] = first_reading
            else:
                _kanji_kana_map_cache[kanji] = kana
        # Overwrite with vocab entries (higher priority)
        cur.execute(
            'SELECT kanji, kana FROM entries WHERE kanji IS NOT NULL AND kanji != "" AND kind = "vocab"'
            ' ORDER BY CASE WHEN study_order > 0 THEN 0 ELSE 1 END, study_order'
        )
        vocab_seen = set()
        stems = []  # collect (stem_kanji, stem_kana) to add after
        for kanji, kana in cur.fetchall():
            if kanji not in vocab_seen:
                _kanji_kana_map_cache[kanji] = kana
                vocab_seen.add(kanji)
                # Generate stem forms by stripping matching trailing kana
                # e.g. 食べる/たべる → 食べ/たべ, 飲む/のむ → 飲/の
                if not kana:
                    continue
                k, r = kanji, kana
                while (len(k) > 1 and len(r) > 1
                       and _is_kana(k[-1]) and _to_hiragana(k[-1]) == _to_hiragana(r[-1])):
                    k = k[:-1]
                    r = r[:-1]
                    # Only add if the stem contains kanji and is different from original
                    if k != kanji and any(0x4E00 <= ord(c) <= 0x9FFF for c in k):
                        stems.append((k, r))
        conn.close()
        # Add stems with lower priority (don't overwrite existing entries)
        for k, r in stems:
            if k not in _kanji_kana_map_cache:
                _kanji_kana_map_cache[k] = r
    except Exception:
        pass
    return _kanji_kana_map_cache


def _tokenize_with_db_readings(text):
    """Fallback tokenizer using DB kanji→kana map when pykakasi is unavailable."""
    if not text:
        return []
    kmap = _get_kanji_kana_map()
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if _is_punct(ch):
            result.append({'orig': ch, 'reading': ch, 'is_punct': True})
            i += 1
            continue
        # Try greedy match for compound words (longest first, up to 10 chars)
        best_len = 0
        best_kana = None
        for length in range(min(10, len(text) - i), 1, -1):
            substr = text[i:i + length]
            if substr in kmap:
                best_len = length
                best_kana = kmap[substr]
                break
        if best_len > 1:
            reading = CHARACTER_NAME_READINGS.get(text[i:i + best_len], _to_hiragana(best_kana))
            result.append({
                'orig': text[i:i + best_len],
                'reading': reading,
                'is_punct': False,
            })
            i += best_len
            continue
        # Single character — look up in kmap so single-kanji like 朝 get their
        # vocab reading (あさ) rather than falling back to the raw character.
        kana_reading = kmap.get(ch)
        if kana_reading:
            result.append({'orig': ch, 'reading': _to_hiragana(kana_reading), 'is_punct': False})
        else:
            result.append({'orig': ch, 'reading': _to_hiragana(ch), 'is_punct': False})
        i += 1
    return result


def _merge_segments_with_db(segments):
    """Post-process segments by merging adjacent ones when the DB knows a better
    compound word.  E.g. pykakasi may split ご飯→[ご, 飯(めし)] but the DB has
    ご飯→ごはん, which is the correct reading in context."""
    kmap = _get_kanji_kana_map()
    if not kmap or not segments:
        return segments
    # Work with non-punct segments only, preserving punct positions
    result = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg['is_punct']:
            result.append(seg)
            i += 1
            continue
        # Try merging 2–5 consecutive non-punct segments
        best_merge = 0
        best_kana = None
        j_start = i
        merged_orig = ''
        for k in range(5):
            j = j_start + k
            if j >= len(segments):
                break
            # Skip punct segments in the middle
            if segments[j]['is_punct']:
                j_start += 1  # shift to skip this punct
                continue
            merged_orig += segments[j]['orig']
            if merged_orig in kmap and k > 0:
                best_merge = k + 1
                best_kana = kmap[merged_orig]
        if best_merge > 1:
            # Collect the original segments being merged (skip punct between)
            orig_text = ''
            consumed = 0
            merge_end = i
            while consumed < best_merge and merge_end < len(segments):
                if segments[merge_end]['is_punct']:
                    merge_end += 1
                    continue
                orig_text += segments[merge_end]['orig']
                consumed += 1
                merge_end += 1
            result.append({
                'orig': orig_text,
                'reading': _to_hiragana(best_kana),
                'is_punct': False,
            })
            i = merge_end
        else:
            result.append(seg)
            i += 1
    return result


def _strip_punct_from_segments(segments):
    """Return only non-punctuation segments."""
    return [s for s in segments if not s['is_punct']]


def _convert_romaji_in_text(text):
    """Convert ASCII romaji runs in mixed text to hiragana.
    Handles trailing 'n' → ん for incomplete IME input."""
    import re
    if not any(c.isascii() and c.isalpha() for c in text):
        return text
    from dictionary.learning import convert_greedy_romaji
    def _replace(m):
        r = m.group(0).lower()
        converted, remainder = convert_greedy_romaji(r)
        if remainder == 'n':
            converted += '\u3093'
            remainder = ''
        return converted + remainder
    return re.sub(r'[a-zA-Z]+', _replace, text)


def compare_dictation(original, typed):
    """
    Compare typed text against original using token-aware alignment.

    The original is tokenized with readings so that kana-for-kanji is detected.
    The typed text is consumed left-to-right, matching against each original
    segment by either its surface form or its hiragana reading.

    Returns list of dicts: [{'text': str, 'color': 'green'|'yellow'|'red'|'grey'}, ...]
    Also returns a score dict: {'correct': int, 'close': int, 'wrong': int, 'total': int}
    """
    if not typed or not typed.strip():
        orig_count = sum(1 for c in original if not _is_punct(c))
        return [], {'correct': 0, 'close': 0, 'wrong': 0, 'total': orig_count}

    # Convert any remaining ASCII romaji to hiragana
    typed = _convert_romaji_in_text(typed)

    # Tokenize the original to get segments with readings
    orig_segments = tokenize_with_readings(original)
    content_segments = _strip_punct_from_segments(orig_segments)

    # Strip punctuation from typed text for alignment
    typed_chars = list(typed)
    typed_content = [(i, ch) for i, ch in enumerate(typed_chars) if not _is_punct(ch)]

    score = {'correct': 0, 'close': 0, 'wrong': 0, 'total': 0}
    match_results = {}  # typed_pos -> 'green' | 'yellow' | 'red'

    t_ptr = 0  # pointer into typed_content

    for seg in content_segments:
        orig_text = seg['orig']
        reading = seg['reading']
        score['total'] += len(reading)  # score by reading length (kana units)

        if t_ptr >= len(typed_content):
            # Ran out of typed text — remaining original is missed
            continue

        # Try to match this segment against typed text
        # Option 1: exact surface match
        seg_len = len(orig_text)
        typed_slice_text = ''.join(ch for _, ch in typed_content[t_ptr:t_ptr + seg_len])

        if _to_hiragana(typed_slice_text) == _to_hiragana(orig_text):
            # Exact match (or kana variant match like katakana↔hiragana)
            for j in range(seg_len):
                if t_ptr + j < len(typed_content):
                    match_results[typed_content[t_ptr + j][0]] = 'green'
            score['correct'] += len(reading)
            t_ptr += seg_len
            continue

        # Option 2: reading match (user typed kana for kanji)
        reading_len = len(reading)
        typed_reading_slice = ''.join(ch for _, ch in typed_content[t_ptr:t_ptr + reading_len])

        if _to_hiragana(typed_reading_slice) == reading:
            # Correct reading in kana
            color = 'yellow' if _has_kanji(orig_text) else 'green'
            for j in range(reading_len):
                if t_ptr + j < len(typed_content):
                    match_results[typed_content[t_ptr + j][0]] = color
            if color == 'yellow':
                score['close'] += len(reading)
            else:
                score['correct'] += len(reading)
            t_ptr += reading_len
            continue

        # Option 3: partial/wrong — try reading-length chunk, mark red
        # Consume reading_len chars from typed (or surface len, whichever typed has)
        consume = min(reading_len, len(typed_content) - t_ptr)
        for j in range(consume):
            match_results[typed_content[t_ptr + j][0]] = 'red'
        score['wrong'] += len(reading)
        t_ptr += consume

    # Any remaining typed characters beyond original segments
    while t_ptr < len(typed_content):
        match_results[typed_content[t_ptr][0]] = 'red'
        score['wrong'] += 1
        score['total'] += 1
        t_ptr += 1

    # Build spans from typed text (preserving punctuation as grey)
    spans = []
    current_color = None
    current_text = []

    for i, ch in enumerate(typed_chars):
        if _is_punct(ch):
            color = 'grey'
        elif i in match_results:
            color = match_results[i]
        else:
            color = 'red'

        if color != current_color and current_text:
            spans.append({'text': ''.join(current_text), 'color': current_color})
            current_text = []
        current_color = color
        current_text.append(ch)

    if current_text:
        spans.append({'text': ''.join(current_text), 'color': current_color})

    return spans, score
