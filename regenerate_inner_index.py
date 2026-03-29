"""
Regenerate lessons with 1 grammar point per lesson and variable spacing per level.
Structure:
  Kana (1-50): 30 kana character lessons + 20 kana vocab (interleaved)
  JLPT (51+): N5, N4, N3, N2, N1
"""
import sqlite3
import json
from pathlib import Path

DB_PATH = 'dictionary/dictionary.db'
LESSONS_ROOT = Path('dictionary/lessons/spoonfed_japanese')
INNER_INDEX = LESSONS_ROOT / 'index.json'
VOCAB_PER_LESSON = 7

LEVELS = [
    {'level': 'N5', 'dir': 'n5_vocab', 'prefix': 'n5_vocab_lesson', 'grammar_dir': 'n5_grammar', 'grammar_prefix': 'n5_grammar_lesson'},
    {'level': 'N4', 'dir': 'n4_vocab', 'prefix': 'n4_vocab_lesson', 'grammar_dir': 'n4_grammar', 'grammar_prefix': 'n4_grammar_lesson'},
    {'level': 'N3', 'dir': 'n3_vocab', 'prefix': 'n3_vocab_lesson', 'grammar_dir': 'n3_grammar', 'grammar_prefix': 'n3_grammar_lesson'},
    {'level': 'N2', 'dir': 'n2_vocab', 'prefix': 'n2_vocab_lesson', 'grammar_dir': 'n2_grammar', 'grammar_prefix': 'n2_grammar_lesson'},
    {'level': 'N1', 'dir': 'n1_vocab', 'prefix': 'n1_vocab_lesson', 'grammar_dir': 'n1_grammar', 'grammar_prefix': 'n1_grammar_lesson'},
]

# Interleaving order for 50 kana lessons (30 char + 20 vocab)
# Each entry is ('char', index) or ('vocab', index)
KANA_SEQUENCE = [
    # First 4 pure character lessons
    ('char', 0), ('char', 1), ('char', 2), ('char', 3),
    ('vocab', 0),   # V1: after 4 char
    ('char', 4), ('char', 5),
    ('vocab', 1),   # V2: after 6 char
    ('char', 6), ('char', 7),
    ('vocab', 2),   # V3: after 8 char
    ('char', 8), ('char', 9),
    ('vocab', 3),   # V4: after 10 char
    ('char', 10), ('char', 11),
    ('vocab', 4),   # V5: after 12 char
    ('char', 12), ('char', 13),
    ('vocab', 5),   # V6: after 14 char
    ('char', 14),   # Last hiragana (わをん)
    ('vocab', 6),   # V7: after 15 char (all hiragana)
    ('char', 15),   # First katakana (アイウエオ)
    ('vocab', 7),   # V8: after 16 char
    ('char', 16), ('char', 17),
    ('vocab', 8),   # V9: after 18 char
    ('char', 18), ('char', 19),
    ('vocab', 9),   # V10: after 20 char
    ('char', 20), ('char', 21),
    ('vocab', 10),  # V11: after 22 char
    ('char', 22), ('char', 23),
    ('vocab', 11),  # V12: after 24 char
    ('char', 24), ('char', 25),
    ('vocab', 12),  # V13: after 26 char
    ('char', 26), ('char', 27),
    ('vocab', 13),  # V14: after 28 char
    ('char', 28), ('char', 29),  # Last two katakana
    ('vocab', 14),  # V15: after 30 char (all done)
    ('vocab', 15),  # V16
    ('vocab', 16),  # V17
    ('vocab', 17),  # V18
    ('vocab', 18),  # V19
    ('vocab', 19),  # V20
]

assert len(KANA_SEQUENCE) == 50, f"Expected 50 kana lessons, got {len(KANA_SEQUENCE)}"
assert sum(1 for t, _ in KANA_SEQUENCE if t == 'char') == 30
assert sum(1 for t, _ in KANA_SEQUENCE if t == 'vocab') == 20


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Scan for kana character lesson files (30)
    kana_char_files = sorted(LESSONS_ROOT.glob('lesson_*_kana.json'),
                             key=lambda p: int(p.stem.split('_')[1]))
    print(f"Found {len(kana_char_files)} kana character lesson files")
    assert len(kana_char_files) == 30, f"Expected 30 kana char files, got {len(kana_char_files)}"

    # Scan for new kana vocab files (20)
    kana_vocab_files = sorted(LESSONS_ROOT.glob('kana_vocab_*.json'),
                              key=lambda p: int(p.stem.split('_')[-1]))
    print(f"Found {len(kana_vocab_files)} kana vocab lesson files")
    assert len(kana_vocab_files) == 20, f"Expected 20 kana vocab files, got {len(kana_vocab_files)}"

    # Load N5 from standalone file (single source of truth)
    N5_ORDER_FILE = Path('n5_study_order.json')
    with open(N5_ORDER_FILE, 'r', encoding='utf-8') as f:
        n5_order = json.load(f)
    n5_vocab_from_file = n5_order['vocab']   # already in correct order
    n5_grammar_from_file = n5_order['grammar']  # already in correct order
    print(f"Loaded N5 order from {N5_ORDER_FILE}: {len(n5_vocab_from_file)} vocab, {len(n5_grammar_from_file)} grammar")

    # Load N4 from standalone file (single source of truth)
    N4_ORDER_FILE = Path('n4_study_order.json')
    if N4_ORDER_FILE.exists():
        with open(N4_ORDER_FILE, 'r', encoding='utf-8') as f:
            n4_order = json.load(f)
        n4_vocab_from_file = n4_order['vocab']
        n4_grammar_from_file = n4_order['grammar']
        print(f"Loaded N4 order from {N4_ORDER_FILE}: {len(n4_vocab_from_file)} vocab, {len(n4_grammar_from_file)} grammar")
    else:
        n4_vocab_from_file = None
        n4_grammar_from_file = None
        print(f"N4 order file not found, will use DB ordering")

    # Get grammar for other levels from DB, ordered by study_order
    db_grammar_exclude = ['N5']
    if n4_grammar_from_file:
        db_grammar_exclude.append('N4')
    placeholders = ','.join('?' * len(db_grammar_exclude))
    cur.execute(f"""
        SELECT id, jlpt_level, gloss FROM entries
        WHERE kind = 'grammar' AND jlpt_level IS NOT NULL
              AND jlpt_level NOT IN ({placeholders})
        ORDER BY
            CASE jlpt_level
                WHEN 'N4' THEN 2 WHEN 'N3' THEN 3
                WHEN 'N2' THEN 4 WHEN 'N1' THEN 5
            END,
            study_order
    """, db_grammar_exclude)
    all_grammar = [dict(r) for r in cur.fetchall()]
    grammar_by_level = {level['level']: [] for level in LEVELS}
    # N5 grammar from the standalone file
    grammar_by_level['N5'] = [{'id': g['entry_id'], 'jlpt_level': 'N5', 'gloss': g['gloss']} for g in n5_grammar_from_file]
    # N4 grammar from the standalone file (if available)
    if n4_grammar_from_file:
        grammar_by_level['N4'] = [{'id': g['entry_id'], 'jlpt_level': 'N4', 'gloss': g['gloss']} for g in n4_grammar_from_file]
    for g in all_grammar:
        if g['jlpt_level'] in grammar_by_level:
            grammar_by_level[g['jlpt_level']].append(g)

    lesson_num = 1
    level_ranges = {}
    new_lessons = {}

    # ========== KANA LESSONS (1-50, interleaved) ==========
    kana_start = lesson_num
    kana_char_count = 0
    kana_vocab_count = 0

    for entry_type, idx in KANA_SEQUENCE:
        if entry_type == 'char':
            kana_file = kana_char_files[idx]
            kana_path = f"dictionary/lessons/spoonfed_japanese/{kana_file.name}"

            with open(kana_file, 'r', encoding='utf-8') as f:
                kana_data = json.load(f)

            old_title = kana_data.get('title', '')
            chars_part = old_title.split(' - ', 1)[1] if ' - ' in old_title else old_title
            kana_data['title'] = f"Lesson {lesson_num}: (Kana) - {chars_part}"
            kana_data['lesson_number'] = lesson_num

            with open(kana_file, 'w', encoding='utf-8') as f:
                json.dump(kana_data, f, ensure_ascii=False, indent=2)

            new_lessons[str(lesson_num)] = {'kana': kana_path}
            kana_char_count += 1

        else:  # vocab
            kv_file = kana_vocab_files[idx]

            with open(kv_file, 'r', encoding='utf-8') as f:
                kv_data = json.load(f)

            items = kv_data.get('items', [])
            preview_words = [item.get('kana', '') for item in items[:3]]
            preview = ', '.join(w for w in preview_words if w)
            if len(items) > 3:
                preview += '...'

            kv_data['title'] = f"Lesson {lesson_num}: (Vocab) - {preview}"
            kv_data['lesson_number'] = lesson_num

            with open(kv_file, 'w', encoding='utf-8') as f:
                json.dump(kv_data, f, ensure_ascii=False, indent=2)

            rel_path = f"dictionary/lessons/spoonfed_japanese/{kv_file.name}"
            new_lessons[str(lesson_num)] = {"vocab": rel_path}
            kana_vocab_count += 1

        lesson_num += 1

    kana_end = lesson_num - 1
    level_ranges['kana'] = (kana_start, kana_end)
    print(f"  Kana: {kana_char_count} char + {kana_vocab_count} vocab = {kana_char_count + kana_vocab_count} lessons (range {kana_start}-{kana_end})")

    # ========== JLPT LEVELS ==========
    for cfg in LEVELS:
        level = cfg['level']
        level_dir = cfg['dir']
        level_prefix = cfg['prefix']
        grammar_dir = cfg['grammar_dir']
        grammar_prefix = cfg['grammar_prefix']
        level_start = lesson_num

        # N5 vocab comes from the standalone file; N4 from its file if available; other levels from DB
        if level == 'N5':
            vocab_entries = [{'id': v['entry_id'], 'kanji': v['kanji'], 'kana': v['kana'], 'gloss': v['gloss']} for v in n5_vocab_from_file]
        elif level == 'N4' and n4_vocab_from_file:
            vocab_entries = [{'id': v['entry_id'], 'kanji': v['kanji'], 'kana': v['kana'], 'gloss': v['gloss']} for v in n4_vocab_from_file]
        else:
            cur.execute("""
                SELECT id, kanji, kana, gloss FROM entries
                WHERE kind = 'vocab' AND jlpt_level = ? AND study_order > 0
                ORDER BY study_order
            """, (level,))
            vocab_entries = [dict(r) for r in cur.fetchall()]

        if not vocab_entries:
            print(f"  {level}: No vocab entries found!")
            continue

        grammar_entries = grammar_by_level[level]
        num_vocab_lessons = (len(vocab_entries) + VOCAB_PER_LESSON - 1) // VOCAB_PER_LESSON

        # Create output directories
        vocab_out_dir = LESSONS_ROOT / level_dir
        vocab_out_dir.mkdir(parents=True, exist_ok=True)
        grammar_out_dir = LESSONS_ROOT / grammar_dir
        grammar_out_dir.mkdir(parents=True, exist_ok=True)

        # Clear old files
        for f in vocab_out_dir.glob(f'{level_prefix}_*.json'):
            f.unlink()
        for f in grammar_out_dir.glob(f'{grammar_prefix}_*.json'):
            f.unlink()

        vocab_counter = 0
        grammar_counter = 0
        level_vocab_count = 0
        level_grammar_count = 0
        grammar_idx = 0

        # For N5: load kana bonus files to interleave with grammar tail
        bonus_files = []
        if level == 'N5':
            bonus_files = sorted(LESSONS_ROOT.glob('n5_kana_bonus_*.json'),
                                 key=lambda p: int(p.stem.split('_')[-1]))

        def _emit_grammar_entry(grammar_entry):
            """Emit a specific grammar lesson."""
            nonlocal grammar_counter, level_grammar_count, lesson_num
            grammar_counter += 1
            level_grammar_count += 1

            gloss = grammar_entry['gloss']
            title_gloss = gloss[:50] + "..." if len(gloss) > 50 else gloss

            grammar_data = {
                "title": f"Lesson {lesson_num}: (Grammar) - {title_gloss}",
                "lesson_number": lesson_num,
                "lesson_type": "grammar",
                "jlpt_level": level,
                "items": [{"entry_id": grammar_entry['id']}]
            }

            grammar_filename = f"{grammar_prefix}_{grammar_counter}.json"
            grammar_filepath = grammar_out_dir / grammar_filename
            with open(grammar_filepath, 'w', encoding='utf-8') as f:
                json.dump(grammar_data, f, ensure_ascii=False, indent=2)

            grammar_rel = f"dictionary/lessons/spoonfed_japanese/{grammar_dir}/{grammar_filename}"
            new_lessons[str(lesson_num)] = {"grammar": grammar_rel}
            lesson_num += 1

        def _emit_bonus(bf):
            """Emit a kana bonus vocab lesson."""
            nonlocal lesson_num, level_vocab_count
            with open(bf, 'r', encoding='utf-8') as fh:
                bonus_data = json.load(fh)

            items = bonus_data.get('items', [])
            preview_words = []
            for item in items[:3]:
                cur.execute("SELECT kana FROM entries WHERE id=?", (item['entry_id'],))
                row = cur.fetchone()
                if row:
                    preview_words.append(row['kana'])
            preview = ', '.join(preview_words)
            if len(items) > 3:
                preview += '...'

            bonus_data['title'] = f"Lesson {lesson_num}: (Vocab) - {preview}"
            bonus_data['lesson_number'] = lesson_num

            with open(bf, 'w', encoding='utf-8') as fh:
                json.dump(bonus_data, fh, ensure_ascii=False, indent=2)

            rel_path = f"dictionary/lessons/spoonfed_japanese/{bf.name}"
            new_lessons[str(lesson_num)] = {"vocab": rel_path}
            lesson_num += 1
            level_vocab_count += 1

        def _emit_vocab_batch(batch, vc):
            """Emit a vocab lesson and return the filename."""
            nonlocal lesson_num, level_vocab_count

            preview_words = []
            for entry in batch[:3]:
                word = entry['kanji'] if entry['kanji'] else entry['kana']
                preview_words.append(word)
            preview = ', '.join(preview_words)
            if len(batch) > 3:
                preview += '...'

            vocab_data = {
                "title": f"Lesson {lesson_num}: (Vocab) - {preview}",
                "lesson_number": lesson_num,
                "jlpt_level": level,
                "items": [{"entry_id": e['id']} for e in batch]
            }

            vocab_filename = f"{level_prefix}_{vc}.json"
            vocab_filepath = vocab_out_dir / vocab_filename
            with open(vocab_filepath, 'w', encoding='utf-8') as f:
                json.dump(vocab_data, f, ensure_ascii=False, indent=2)

            vocab_rel = f"dictionary/lessons/spoonfed_japanese/{level_dir}/{vocab_filename}"
            new_lessons[str(lesson_num)] = {"vocab": vocab_rel}
            lesson_num += 1
            level_vocab_count += 1

        # ===== N5: Curriculum-driven interleaving =====
        # Keep grammar in strict canonical order and use the original
        # pedagogical pacing: front-load early vocab and place the two
        # intentional grammar-grammar pairs.
        if level == 'N5':
            N5_FRONTLOAD_VOCAB = 5
            # Intentional consecutive grammar pair positions in canonical order:
            # 4 = か right after は, 7 = が right after な-adjectives.
            N5_GG_PAIR_POSITIONS = {4, 7}

            total_vocab_lessons = (len(vocab_entries) + VOCAB_PER_LESSON - 1) // VOCAB_PER_LESSON
            next_vocab_lesson = 1
            next_grammar_idx = 0
            next_bonus_idx = 0

            def emit_next_vocab_lesson():
                nonlocal next_vocab_lesson, vocab_counter
                start = (next_vocab_lesson - 1) * VOCAB_PER_LESSON
                batch = vocab_entries[start:start + VOCAB_PER_LESSON]
                vocab_counter += 1
                _emit_vocab_batch(batch, next_vocab_lesson)
                next_vocab_lesson += 1

            def emit_next_bonus():
                """Emit a kana bonus file as a vocab spacer."""
                nonlocal next_bonus_idx
                if next_bonus_idx < len(bonus_files):
                    _emit_bonus(bonus_files[next_bonus_idx])
                    next_bonus_idx += 1

            # Phase 1: front-load early vocab before first grammar.
            while next_vocab_lesson <= total_vocab_lessons and next_vocab_lesson <= N5_FRONTLOAD_VOCAB:
                emit_next_vocab_lesson()

            # Phase 2: interleave in canonical grammar order.
            # When regular vocab is exhausted, use kana bonus files as
            # spacers to maintain V-G alternation (no consecutive grammar
            # except the intentional GG pairs).
            while next_vocab_lesson <= total_vocab_lessons or next_grammar_idx < len(grammar_entries):
                grammar_pos = next_grammar_idx + 1

                # Handle intentional G-G pair positions.
                if next_grammar_idx < len(grammar_entries) and grammar_pos in N5_GG_PAIR_POSITIONS:
                    _emit_grammar_entry(grammar_entries[next_grammar_idx])
                    next_grammar_idx += 1
                    continue

                if next_vocab_lesson <= total_vocab_lessons:
                    emit_next_vocab_lesson()
                elif next_bonus_idx < len(bonus_files) and next_grammar_idx < len(grammar_entries):
                    # Regular vocab exhausted but grammar remains — use kana bonus as spacer
                    emit_next_bonus()

                if next_grammar_idx < len(grammar_entries):
                    _emit_grammar_entry(grammar_entries[next_grammar_idx])
                    next_grammar_idx += 1

            # Phase 3: emit remaining kana bonus vocab at the tail.
            while next_bonus_idx < len(bonus_files):
                _emit_bonus(bonus_files[next_bonus_idx])
                next_bonus_idx += 1

        # ===== N4: Curriculum-driven interleaving =====
        # GG pairs: positions where grammar follows immediately without vocab
        # Exactly 15 GG positions => 105 grammar - 15 = 90 non-GG, matching 90 vocab lessons
        elif level == 'N4':
            # GG pair positions (2nd/3rd entries in each group — no vocab before these)
            N4_GG_PAIR_POSITIONS = {
                8, 9,      # favor trio: てくれる, てもらう (after てあげる)
                11,        # てくる (after ていく)
                13,        # ちゃう (after てしまう)
                18,        # たところ (after たばかり)
                40, 41,    # そうな, そうに (after そうだ)
                43, 44,    # ような, ように (after ようだ)
                49, 50,    # みたいな, みたいに (after みたいだ)
                53, 54,    # 続ける, おわる (after 始める)
                57,        # にくい (after やすい)
                92,        # ならない (after いけない)
            }

            total_vocab_lessons = (len(vocab_entries) + VOCAB_PER_LESSON - 1) // VOCAB_PER_LESSON
            next_vocab_lesson = 1
            next_grammar_idx = 0

            def emit_next_n4_vocab():
                nonlocal next_vocab_lesson, vocab_counter
                start = (next_vocab_lesson - 1) * VOCAB_PER_LESSON
                batch = vocab_entries[start:start + VOCAB_PER_LESSON]
                vocab_counter += 1
                _emit_vocab_batch(batch, next_vocab_lesson)
                next_vocab_lesson += 1

            # Interleave: V-G for non-GG positions, G-only for GG positions
            while next_grammar_idx < len(grammar_entries):
                grammar_pos = next_grammar_idx + 1

                # GG pair: emit grammar directly without preceding vocab
                if grammar_pos in N4_GG_PAIR_POSITIONS:
                    _emit_grammar_entry(grammar_entries[next_grammar_idx])
                    next_grammar_idx += 1
                    continue

                # Non-GG: emit vocab then grammar
                if next_vocab_lesson <= total_vocab_lessons:
                    emit_next_n4_vocab()

                _emit_grammar_entry(grammar_entries[next_grammar_idx])
                next_grammar_idx += 1

            # Emit any remaining vocab lessons (shouldn't happen with balanced counts)
            while next_vocab_lesson <= total_vocab_lessons:
                emit_next_n4_vocab()

        # ===== Other levels: even distribution =====
        else:
            for i in range(0, len(vocab_entries), VOCAB_PER_LESSON):
                batch = vocab_entries[i:i + VOCAB_PER_LESSON]
                vocab_counter += 1
                _emit_vocab_batch(batch, vocab_counter)

                # Insert grammar based on even distribution
                if grammar_idx < len(grammar_entries) and len(grammar_entries) > 0:
                    should_insert = False
                    if level_vocab_count < num_vocab_lessons:
                        grammar_before = (level_vocab_count * len(grammar_entries)) // num_vocab_lessons
                        grammar_prev = ((level_vocab_count - 1) * len(grammar_entries)) // num_vocab_lessons
                        should_insert = grammar_before > grammar_prev
                    elif grammar_idx < len(grammar_entries):
                        should_insert = True

                    if should_insert:
                        _emit_grammar_entry(grammar_entries[grammar_idx])
                        grammar_idx += 1

            # Emit remaining grammar for non-N5
            while grammar_idx < len(grammar_entries):
                _emit_grammar_entry(grammar_entries[grammar_idx])
                grammar_idx += 1

        level_end = lesson_num - 1
        level_ranges[level] = (level_start, level_end)
        total_for_level = level_vocab_count + level_grammar_count
        print(f"  {level}: {level_vocab_count} vocab + {level_grammar_count} grammar = "
              f"{total_for_level} lessons (range {level_start}-{level_end})")

    # ========== BUILD FINAL INDEX ==========
    final_index = {
        "source_file": "Spoonfed Japanese JLPT Lessons",
        "lessons": dict(sorted(new_lessons.items(), key=lambda x: int(x[0])))
    }

    with open(INNER_INDEX, 'w', encoding='utf-8') as f:
        json.dump(final_index, f, ensure_ascii=False, indent=2)

    conn.close()

    # ========== SUMMARY ==========
    total = len(new_lessons)
    kana_total_count = kana_char_count + kana_vocab_count
    jlpt_total = total - kana_total_count

    print(f"\nUpdated inner index: {INNER_INDEX}")
    print(f"  Total lessons: {total}")
    print(f"  Kana section: {kana_total_count} (1-{kana_end})")
    print(f"  JLPT section: {jlpt_total} ({kana_end + 1}-{total})")

    print(f"\n  === MAIN.PY RANGE UPDATES ===")
    if 'kana' in level_ranges:
        s, e = level_ranges['kana']
        print(f"  Kana: {s} <= int(k) <= {e}")
    for level in ['N5', 'N4', 'N3', 'N2', 'N1']:
        if level in level_ranges:
            s, e = level_ranges[level]
            print(f"  {level}: {s} <= int(k) <= {e}")


if __name__ == '__main__':
    main()
