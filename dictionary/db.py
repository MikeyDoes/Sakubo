import sqlite3
import json
from typing import Optional, List, Tuple
from datetime import datetime, date, timedelta
from contextlib import contextmanager
from pathlib import Path
from kivy.logger import Logger
from dictionary.deinflect import deinflect as _deinflect


# Load JLPT kanji data for search priority
# N5 = easiest (highest priority), N1 = hardest (lowest priority)
_JLPT_KANJI_LOOKUP = {}

def _load_jlpt_kanji():
    """Load JLPT kanji data from JSON files and create lookup dictionary."""
    global _JLPT_KANJI_LOOKUP
    if _JLPT_KANJI_LOOKUP:  # Already loaded
        return
    
    base_path = Path(__file__).parent.parent / 'reports'
    jlpt_levels = {
        'n5': 0,  # Highest priority (easiest)
        'n4': 1,
        'n3': 2,
        'n2': 3,
        'n1': 4,  # Lowest priority (hardest)
    }
    
    for level, priority in jlpt_levels.items():
        json_path = base_path / f'jlpt_{level}_kanji_only.json'
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for kanji in data.get('kanji', []):
                        # Only assign if not already assigned (prefer earlier/easier level)
                        if kanji not in _JLPT_KANJI_LOOKUP:
                            _JLPT_KANJI_LOOKUP[kanji] = priority
            except Exception as e:
                Logger.warning(f'Failed to load JLPT data from {json_path}: {e}')
    
    Logger.info(f'Loaded JLPT kanji data: {len(_JLPT_KANJI_LOOKUP)} kanji')

# Load JLPT data at module initialization
_load_jlpt_kanji()


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (and create) the SQLite DB and ensure the `entries` table exists.

    Also does not create the FTS table here; use `ensure_fts(conn)` to create/populate FTS5.
    
    NOTE: Consider using get_db() context manager instead to ensure connections are closed.
    """
    conn = sqlite3.connect(db_path)
    # Enable foreign key constraints (required for CASCADE to work)
    conn.execute('PRAGMA foreign_keys = ON')
    # Performance optimizations for faster reads
    conn.execute('PRAGMA journal_mode = WAL')  # Write-Ahead Logging for better concurrency
    conn.execute('PRAGMA synchronous = NORMAL')  # Faster writes with reasonable safety
    conn.execute('PRAGMA cache_size = -64000')  # 64MB cache for better read performance
    conn.execute('PRAGMA temp_store = MEMORY')  # Store temp tables in memory
    cur = conn.cursor()
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY,
            kanji TEXT,
            kana TEXT,
            gloss TEXT,
            tokens TEXT,
            pos TEXT,
            tags TEXT
        )
        '''
    )
    conn.commit()
    return conn


@contextmanager
def get_db(db_path: str = 'dictionary/dictionary.db'):
    """Context manager for database connections that ensures proper cleanup.
    
    Usage:
        with get_db() as conn:
            results = search_entries(conn, 'cat')
    
    This automatically closes the connection even if an exception occurs.
    """
    conn = None
    try:
        conn = init_db(db_path)
        yield conn
    except Exception as e:
        Logger.error(f'Database error: {e}')
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                Logger.warning(f'Error closing database connection: {e}')


def ensure_learning_table(conn: sqlite3.Connection) -> None:
    """Create table to hold learning sessions."""
    cur = conn.cursor()
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS learning_sessions (
            id INTEGER PRIMARY KEY,
            jid TEXT,
            kind TEXT,
            data TEXT,
            created_at TEXT
        )
        '''
    )
    conn.commit()


def ensure_custom_lessons_tables(conn: sqlite3.Connection) -> None:
    """Create custom lessons tables if they don't exist."""
    cur = conn.cursor()
    
    # create custom_lessons table
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS custom_lessons (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            display_order INTEGER DEFAULT 0,
            is_collection INTEGER DEFAULT 0,
            parent_id INTEGER,
            FOREIGN KEY(parent_id) REFERENCES custom_lessons(id) ON DELETE CASCADE
        )
        '''
    )
    
    # Add new columns if they don't exist (for existing databases)
    try:
        cur.execute('ALTER TABLE custom_lessons ADD COLUMN is_collection INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute('ALTER TABLE custom_lessons ADD COLUMN parent_id INTEGER')
    except sqlite3.OperationalError:
        pass
    
    # create custom_lesson_items table
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS custom_lesson_items (
            id INTEGER PRIMARY KEY,
            lesson_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            reading_material_id INTEGER,
            FOREIGN KEY(lesson_id) REFERENCES custom_lessons(id) ON DELETE CASCADE
        )
        '''
    )
    
    # Migration: add reading_material_id column if missing
    try:
        cur.execute('SELECT reading_material_id FROM custom_lesson_items LIMIT 1')
    except sqlite3.OperationalError:
        cur.execute('ALTER TABLE custom_lesson_items ADD COLUMN reading_material_id INTEGER')

    # Migration: add graded_reading_path column if missing
    try:
        cur.execute('SELECT graded_reading_path FROM custom_lesson_items LIMIT 1')
    except sqlite3.OperationalError:
        cur.execute('ALTER TABLE custom_lesson_items ADD COLUMN graded_reading_path TEXT')

    conn.commit()


def create_learning_session(conn: sqlite3.Connection, kind: str, data: dict, jid: str | None = None) -> int:
    cur = conn.cursor()
    ensure_learning_table(conn)
    cur.execute('INSERT INTO learning_sessions (jid, kind, data, created_at) VALUES (?, ?, ?, ?)',
                (jid or '', kind, json.dumps(data or {}), datetime.utcnow().isoformat()))
    conn.commit()
    return cur.lastrowid


def get_learning_session(conn: sqlite3.Connection, session_id: int) -> dict | None:
    cur = conn.cursor()
    ensure_learning_table(conn)
    cur.execute('SELECT id, jid, kind, data, created_at FROM learning_sessions WHERE id = ?', (session_id,))
    row = cur.fetchone()
    if not row:
        return None
    sid, jid, kind, data, created_at = row
    try:
        data = json.loads(data or '{}')
    except Exception:
        data = {}
    return {'id': sid, 'jid': jid, 'kind': kind, 'data': data, 'created_at': created_at}


def update_learning_session(conn: sqlite3.Connection, session_id: int, data: dict) -> None:
    cur = conn.cursor()
    ensure_learning_table(conn)
    cur.execute('UPDATE learning_sessions SET data = ? WHERE id = ?', (json.dumps(data), session_id))
    conn.commit()


def delete_learning_session(conn: sqlite3.Connection, session_id: int) -> None:
    cur = conn.cursor()
    ensure_learning_table(conn)
    cur.execute('DELETE FROM learning_sessions WHERE id = ?', (session_id,))
    conn.commit()


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate schema safely:
    - add `jid` TEXT column if missing and a UNIQUE index on it
    - create `srs` table for user study data
    - add performance indexes for common queries
    """
    cur = conn.cursor()
    # add jid column if not present
    cur.execute("PRAGMA table_info(entries)")
    cols = [r[1] for r in cur.fetchall()]
    if 'jid' not in cols:
        cur.execute("ALTER TABLE entries ADD COLUMN jid TEXT")
    if 'tokens' not in cols:
        cur.execute("ALTER TABLE entries ADD COLUMN tokens TEXT")
    if 'kind' not in cols:
        # kind: vocab | kanji | grammar
        cur.execute("ALTER TABLE entries ADD COLUMN kind TEXT DEFAULT 'vocab'")
        # populate existing rows as 'vocab'
        cur.execute("UPDATE entries SET kind = 'vocab' WHERE kind IS NULL OR kind = ''")
    # create unique index on jid to enforce uniqueness (if sqlite supports it)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_jid ON entries(jid)")

    # create SRS table to store per-entry user study data
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS srs (
            id INTEGER PRIMARY KEY,
            entry_id INTEGER,
            jid TEXT,
            ease REAL DEFAULT 2.5,
            reps INTEGER DEFAULT 0,
            interval INTEGER DEFAULT 0,
            due_at TEXT,
            data TEXT,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE SET NULL
        )
        '''
    )
    
    # create custom_lessons table to store user-created lessons
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS custom_lessons (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            display_order INTEGER DEFAULT 0
        )
        '''
    )
    
    # create custom_lesson_items table to store entry_id list for each custom lesson
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS custom_lesson_items (
            id INTEGER PRIMARY KEY,
            lesson_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            FOREIGN KEY(lesson_id) REFERENCES custom_lessons(id) ON DELETE CASCADE,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
        '''
    )
    
    # create example_sentences table to store AI-generated example sentences
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS example_sentences (
            id INTEGER PRIMARY KEY,
            entry_id INTEGER NOT NULL,
            japanese TEXT NOT NULL,
            english TEXT,
            model_used TEXT,
            generated_at TEXT,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
        '''
    )
    
    # Add performance indexes for common queries
    ensure_performance_indexes(conn)

    # One-time fix: clear archaic kanji from kana-only vocab entries
    _KANA_VOCAB_IDS = (73760, 73712, 125040, 36458, 118053, 37817, 109285, 173774, 183134, 36449, 189286)
    cur.execute(
        f'UPDATE entries SET kanji = NULL WHERE id IN ({",".join("?" * len(_KANA_VOCAB_IDS))}) AND kanji IS NOT NULL',
        _KANA_VOCAB_IDS
    )

    conn.commit()


def ensure_performance_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes for better query performance on frequently searched columns."""
    cur = conn.cursor()
    try:
        # Index for gloss searches (LIKE queries)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_gloss ON entries(gloss)")
        # Index for kana searches
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_kana ON entries(kana)")
        # Index for kanji searches
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_kanji ON entries(kanji)")
        # Index for kind filtering
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind)")
        # Composite index for kind + gloss queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_kind_gloss ON entries(kind, gloss)")
        # Index for SRS due date queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_srs_due_at ON srs(due_at)")
        # Index for SRS entry_id lookups
        cur.execute("CREATE INDEX IF NOT EXISTS idx_srs_entry_id ON srs(entry_id)")
        conn.commit()
        Logger.info('Database: Performance indexes created successfully')
    except Exception as e:
        Logger.warning(f'Database: Error creating performance indexes: {e}')


def insert_entry(conn: sqlite3.Connection, kanji: Optional[str], kana: Optional[str], gloss: str, pos: Optional[str]=None, tags: Optional[dict]=None, jid: Optional[str]=None):
    cur = conn.cursor()
    # keep importer safe: if jid is provided, use INSERT OR IGNORE so we don't overwrite existing entries
    if jid:
        cur.execute(
            'INSERT OR IGNORE INTO entries (jid, kanji, kana, gloss, pos, tags) VALUES (?, ?, ?, ?, ?, ?)',
            (jid, kanji, kana, gloss, pos, json.dumps(tags or {}))
        )
        # if insert ignored, try to find existing id
        if cur.rowcount == 0:
            cur.execute('SELECT id FROM entries WHERE jid = ?', (jid,))
            row = cur.fetchone()
            return (row[0] if row else None, False)
        return (cur.lastrowid, True)
    else:
        cur.execute(
            'INSERT INTO entries (kanji, kana, gloss, pos, tags) VALUES (?, ?, ?, ?, ?)',
            (kanji, kana, gloss, pos, json.dumps(tags or {}))
        )
        return (cur.lastrowid, True)


def insert_entry_with_kind(conn: sqlite3.Connection, kanji: Optional[str], kana: Optional[str], gloss: str, pos: Optional[str]=None, tags: Optional[dict]=None, jid: Optional[str]=None, kind: Optional[str]='vocab'):
    """Compatibility wrapper: insert entry and set `kind` (vocab|kanji|grammar).

    Uses `jid` uniqueness when provided to avoid duplicates.
    """
    cur = conn.cursor()
    if jid:
        cur.execute(
            'INSERT OR IGNORE INTO entries (jid, kanji, kana, gloss, pos, tags, kind) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (jid, kanji, kana, gloss, pos, json.dumps(tags or {}), kind)
        )
        if cur.rowcount == 0:
            cur.execute('SELECT id FROM entries WHERE jid = ?', (jid,))
            row = cur.fetchone()
            return (row[0] if row else None, False)
        return (cur.lastrowid, True)
    else:
        cur.execute(
            'INSERT INTO entries (kanji, kana, gloss, pos, tags, kind) VALUES (?, ?, ?, ?, ?, ?)',
            (kanji, kana, gloss, pos, json.dumps(tags or {}), kind)
        )
        return (cur.lastrowid, True)


def count_entries(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM entries')
    return cur.fetchone()[0]


def fts_supported(conn: sqlite3.Connection) -> bool:
    """Return True if SQLite supports FTS5 in this python build."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT sqlite_version()")
        # Try to create an in-memory FTS5 table to test support
        cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts_test USING fts5(test)")
        cur.execute("DROP TABLE IF EXISTS __fts_test")
        conn.commit()
        return True
    except Exception:
        return False


def ensure_fts(conn: sqlite3.Connection) -> None:
    """Create and populate an FTS5 table `entries_fts` if not present.

    Creates table with an unindexed `entry_id` column to map back to `entries.id`.
    If the FTS table already exists but is empty, it will be populated from `entries`.
    """
    if not fts_supported(conn):
        raise RuntimeError('SQLite in this Python build does not support FTS5')

    cur = conn.cursor()
    # Create FTS5 table: include entry_id as UNINDEXED to retrieve original id
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
        USING fts5(tokens, kanji, kana, gloss, kind UNINDEXED, entry_id UNINDEXED, tokenize='unicode61')
        """
    )
    # If there's already data, skip population
    cur.execute("SELECT count(*) FROM entries_fts")
    try:
        fts_count = cur.fetchone()[0]
    except Exception:
        fts_count = 0

    if fts_count == 0:
        # Populate from existing entries table
        cur.execute("SELECT id, COALESCE(tokens,''), COALESCE(kanji,''), COALESCE(kana,''), COALESCE(gloss,''), COALESCE(kind,'vocab') FROM entries")
        rows = cur.fetchall()
        if rows:
            cur.executemany(
                'INSERT INTO entries_fts(rowid, tokens, kanji, kana, gloss, kind, entry_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                ((r[0], r[1], r[2], r[3], r[4], r[5], r[0]) for r in rows)
            )
            conn.commit()

    # create triggers to keep FTS table in sync on INSERT/UPDATE/DELETE
    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    existing_triggers = {r[0] for r in cur.fetchall()}

    if 'entries_ai' not in existing_triggers:
        cur.execute('''
        CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, tokens, kanji, kana, gloss, kind, entry_id)
            VALUES (new.id, COALESCE(new.tokens,''), COALESCE(new.kanji,''), COALESCE(new.kana,''), COALESCE(new.gloss,''), COALESCE(new.kind,'vocab'), new.id);
        END;
        ''')

    if 'entries_ad' not in existing_triggers:
        cur.execute('''
        CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
            DELETE FROM entries_fts WHERE rowid = old.id;
        END;
        ''')

    if 'entries_au' not in existing_triggers:
        cur.execute('''
        CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
            DELETE FROM entries_fts WHERE rowid = old.id;
            INSERT INTO entries_fts(rowid, tokens, kanji, kana, gloss, kind, entry_id)
            VALUES (new.id, COALESCE(new.tokens,''), COALESCE(new.kanji,''), COALESCE(new.kana,''), COALESCE(new.gloss,''), COALESCE(new.kind,'vocab'), new.id);
        END;
        ''')
    conn.commit()


# Helper functions for romaji/kana conversion
def _is_japanese(s: str) -> bool:
    """Detect whether the query is likely Japanese (contains kana/kanji)."""
    for ch in s:
        o = ord(ch)
        if (0x3040 <= o <= 0x30ff) or (0x4e00 <= o <= 0x9fff):
            return True
    return False

def _is_romaji(s: str) -> bool:
    """Detect whether the query looks like romaji (ASCII letters, hyphen, apostrophe)."""
    import re
    return bool(re.fullmatch(r"[A-Za-z\-']+", s))

def _romaji_to_hiragana_units(s: str) -> list[str] | None:
    """Simple greedy romaji->hiragana transliteration returning unit list."""
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
        'ha':'は','hi':'ひ','fu':'ふ','he':'へ','ho':'ほ',
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
    """Convert romaji string to hiragana."""
    units = _romaji_to_hiragana_units(s)
    if units is None:
        return None
    return ''.join(units)

def _hiragana_to_katakana(hira: str) -> str:
    """Convert hiragana string to katakana."""
    out_chars = []
    for ch in hira:
        o = ord(ch)
        if 0x3041 <= o <= 0x3096:
            out_chars.append(chr(o + 0x60))
        else:
            out_chars.append(ch)
    return ''.join(out_chars)

def _romaji_to_katakana_with_long(s: str) -> str | None:
    """Build katakana from romaji units, collapsing vowel-only units into chōon when appropriate."""
    hira_units = _romaji_to_hiragana_units(s)
    if hira_units is None:
        return None
    vowels = {'あ','い','う','え','お'}
    out: list[str] = []
    for u in hira_units:
        # if this unit is a hiragana vowel and previous output exists and previous isn't a chōon,
        # convert to chōon (long mark) after previous kana in katakana output
        if len(u) == 1 and u in vowels and out:
            # append chōon mark instead of vowel unit
            out.append('ー')
        else:
            out.append(_hiragana_to_katakana(u))
    return ''.join(out)


def _search_entries_like(conn: sqlite3.Connection, query: str, limit: int = 50, kind: str = None) -> List[Tuple[int, str, str, str, str]]:
    """LIKE-based fallback search used when FTS5 is unavailable (e.g. python-for-android builds).

    Returns list of (id, kanji, kana, gloss, kind) tuples.
    """
    cur = conn.cursor()
    q = query.strip()
    if not q:
        if kind == 'grammar':
            cur.execute(
                "SELECT id, kanji, kana, gloss, COALESCE(kind,'vocab') FROM entries WHERE kind=? ORDER BY kanji LIMIT ?",
                ('grammar', limit)
            )
            return cur.fetchall()
        return []

    like = '%' + q + '%'
    is_jp = _is_japanese(q)
    is_rom = _is_romaji(q) and not is_jp

    # Build search terms (original + romaji-converted variants)
    search_terms = [q]
    hira_q = kata_q = None
    if is_rom:
        hira_q = _romaji_to_hiragana(q)
        if hira_q is None and q.lower().endswith('n') and not q.lower().endswith('nn'):
            hira_q = _romaji_to_hiragana(q[:-1] + 'nn')
        if hira_q:
            kata_q = _hiragana_to_katakana(hira_q)
            # Validate: check if converted kana matches any dictionary entry.
            # Catches English words like "orange" being falsely parsed as romaji (おらんげ).
            kata_check = kata_q or ''
            if len(hira_q) <= 2:
                kana_hit = cur.execute(
                    "SELECT 1 FROM entries WHERE kana = ? OR kana = ? LIMIT 1",
                    (hira_q, kata_check)
                ).fetchone()
            else:
                kana_hit = cur.execute(
                    "SELECT 1 FROM entries WHERE kana LIKE ? OR kana LIKE ? LIMIT 1",
                    (hira_q + '%', kata_check + '%')
                ).fetchone()
            if not kana_hit:
                is_rom = False
                hira_q = kata_q = None
        if is_rom and hira_q:
            search_terms.append(hira_q)
            if kata_q:
                search_terms.append(kata_q)

    # Add "to " prefix variant for single-word verb searches
    if not is_jp and len(q.split()) == 1:
        search_terms.append('to ' + q.lower())

    # Generate word stem variations for better English matching (singular/plural)
    query_variants = [q.lower()]
    q_lower = q.lower()
    if len(q_lower) > 2 and not is_jp:
        if q_lower.endswith('s') and not q_lower.endswith('ss'):
            query_variants.append(q_lower[:-1])
            if not q_lower.endswith('ies'):
                query_variants.append(q_lower[:-1] + 'ies')
        else:
            query_variants.append(q_lower + 's')
            if q_lower.endswith('y') and len(q_lower) > 2:
                query_variants.append(q_lower[:-1] + 'ies')

    # Build LIKE patterns for all terms
    like_patterns = ['%' + t + '%' for t in search_terms]

    # Build kind filter
    kind_clause = ''
    kind_params = []
    if kind == 'kanji_kana':
        kind_clause = "AND kind = 'kanji'"
    elif kind:
        kind_clause = 'AND kind = ?'
        kind_params = [kind]

    # Build token match condition using all query variants (singular/plural forms)
    token_match_parts = []
    token_params = []
    for v in query_variants:
        token_match_parts.extend([
            "lower(gloss) LIKE ?",
            "lower(gloss) LIKE ?",
            "lower(gloss) LIKE ?",
            "instr('; ' || lower(gloss) || '; ', '; ' || ? || '; ') > 0",
            "instr('; ' || lower(gloss) || '; ', '; ' || ? || ',') > 0",
        ])
        token_params.extend([v + ';%', v + ',%', v + ' (%', v, v])
    token_match_condition = ' OR '.join(token_match_parts)

    # Build first_meaning_priority condition (query appears as first meaning in gloss)
    first_meaning_parts = []
    first_meaning_params = []
    for v in query_variants:
        first_meaning_parts.extend([
            "lower(gloss) LIKE ?",
            "lower(gloss) LIKE ?",
            "lower(gloss) LIKE ?",
        ])
        first_meaning_params.extend([v + ';%', v + ',%', v + ' (%'])
    first_meaning_condition = ' OR '.join(first_meaning_parts)

    # Ranking: 0=exact kanji/kana, 1=token match (semicolon-separated), 2=multi-word/starts-with,
    # 3=kanji/kana starts-with, 4=gloss contains, 5=rest
    exact_jp_params = []
    starts_jp_params = []

    # exact kanji/kana match
    exact_jp_parts = []
    for t in search_terms:
        exact_jp_parts.append(f"lower(kanji)=? OR lower(kana)=?")
        exact_jp_params.extend([t.lower(), t.lower()])
    exact_jp = ' OR '.join(exact_jp_parts)

    # starts-with on kanji/kana
    starts_jp_parts = []
    for t in search_terms:
        starts_jp_parts.append(f"lower(kanji) LIKE ? OR lower(kana) LIKE ?")
        starts_jp_params.extend([t.lower() + '%', t.lower() + '%'])
    starts_jp = ' OR '.join(starts_jp_parts)

    # WHERE: match any search term in kanji, kana, gloss, or tokens
    where_parts = []
    where_params = []
    for lp in like_patterns:
        where_parts.append('(kanji LIKE ? OR kana LIKE ? OR gloss LIKE ? OR tokens LIKE ?)')
        where_params.extend([lp, lp, lp, lp])
    where_clause = ' OR '.join(where_parts)

    sql = (
        f"SELECT id, kanji, kana, gloss, COALESCE(kind,'vocab') as kind, "
        f"CASE WHEN {exact_jp} THEN 0 "
        f"WHEN lower(gloss)=? THEN 1 "
        f"WHEN {token_match_condition} THEN 1 "
        f"WHEN instr(lower(gloss), ' ' || ?) > 0 OR instr(lower(gloss), ? || ' ') > 0 THEN 2 "
        f"WHEN lower(gloss) LIKE ? OR lower(gloss) LIKE ? THEN 3 "
        f"WHEN {starts_jp} THEN 4 "
        f"WHEN lower(gloss) LIKE ? THEN 5 ELSE 6 END as _rank, "
        f"CASE WHEN jlpt_level='N5' THEN 0 WHEN jlpt_level='N4' THEN 1 WHEN jlpt_level='N3' THEN 2 WHEN jlpt_level='N2' THEN 3 WHEN jlpt_level='N1' THEN 4 ELSE 5 END as _jlpt, "
        f"CASE WHEN kind='vocab' THEN 0 WHEN kind='kanji' THEN 1 ELSE 2 END as _kind_p, "
        f"CASE WHEN lower(gloss)=? THEN 0 ELSE 1 END as _exact_gloss, "
        f"CASE WHEN {first_meaning_condition} THEN 0 ELSE 1 END as _first_m "
        f"FROM entries WHERE ({where_clause}) {kind_clause} "
        f"ORDER BY _kind_p ASC, (_rank / 2) ASC, _jlpt ASC, _rank ASC, _exact_gloss ASC, _first_m ASC, length(kana) ASC, length(gloss) ASC LIMIT ?"
    )
    # Build params matching SQL ? order:
    # CASE: exact_jp → gloss=? → token_match → multi-word → starts-with-gloss → starts_jp → contains
    # Then: _exact_gloss → _first_m → WHERE → kind → LIMIT
    params = (
        tuple(exact_jp_params)    # exact kanji/kana match (rank 0)
        + (q.lower(),)            # lower(gloss)=? (exact gloss match, rank 1)
        + tuple(token_params)     # token match params (rank 1)
        + (q.lower(), q.lower())  # multi-word boundary params (rank 2)
        + (q.lower() + ' %', q.lower() + '(%')  # starts-with + space/parens (rank 3)
        + tuple(starts_jp_params) # kanji/kana starts-with (rank 4)
        + (like_patterns[0],)     # gloss LIKE ? (contains, rank 5)
        + (q.lower(),)            # _exact_gloss
        + tuple(first_meaning_params)  # _first_m
        + tuple(where_params) + tuple(kind_params)
        + (limit,)
    )
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    except Exception as e:
        Logger.error(f'LIKE search error: {e}')
        return []


def search_entries(conn: sqlite3.Connection, query: str, limit: int = 50, kind: str = None) -> List[Tuple[int, str, str, str, str]]:
    """Search the FTS table and return a list of tuples: (entry_id, kanji, kana, gloss).

    Falls back to LIKE-based search if FTS5 isn't available (e.g. python-for-android builds).
    """
    if not fts_supported(conn):
        Logger.info('DictSearch: Using LIKE fallback (FTS5 not available)')
        return _search_entries_like(conn, query, limit, kind)
    Logger.info('DictSearch: Using FTS5')
    cur = conn.cursor()
    # sanitize and convert query into a prefix-match FTS5 query
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        # If grammar filter is active with no search query, show all grammar entries
        if kind == 'grammar':
            cur.execute('SELECT id, kanji, kana, gloss, kind FROM entries WHERE kind = ? ORDER BY kanji LIMIT ?', (kind, limit))
            return cur.fetchall()
        return []

    # Strip a leading "to " from English verb searches (e.g. "to run" → "run").
    # Single-word searches already add "to <word>" as a variant, so this normalises
    # "to run" into the same well-tested path as searching "run" directly.
    if not _is_japanese(query) and len(tokens) >= 2 and tokens[0].lower() == 'to':
        query = query.strip()[3:]   # drop "to "
        tokens = tokens[1:]

    # use prefix matching for each token, join with OR so results are broader and fast
    fts_query = ' OR '.join(f"{t}*" for t in tokens)

    # Ranking strategy:
    # 1) Exact kanji/kana equals the query (highest priority)
    # 2) Gloss contains the query (case-insensitive)
    # 3) FTS MATCH results (fallback ordering)
    like_q = '%' + query.lower() + '%'
    
    # Generate word stem variations for better English matching (singular/plural, etc.)
    query_variants = [query.lower()]
    q_lower = query.lower()
    fts_variants = [query.lower()]  # Variants to include in FTS query
    if len(q_lower) > 2 and not _is_japanese(query):
        # If ends with 's', try without it (boobs -> boob)
        if q_lower.endswith('s') and not q_lower.endswith('ss'):
            base = q_lower[:-1]
            query_variants.append(base)
            fts_variants.append(base)
            # Also try -ies variant (boobs -> boobies)
            if not q_lower.endswith('ies'):
                query_variants.append(base + 'ies')
                fts_variants.append(base + 'ies')
        # If doesn't end with 's', try with it (boob -> boobs)
        else:
            query_variants.append(q_lower + 's')
            fts_variants.append(q_lower + 's')
            # Also try -ies if ends with 'y' (baby -> babies)
            if q_lower.endswith('y') and len(q_lower) > 2:
                query_variants.append(q_lower[:-1] + 'ies')
                fts_variants.append(q_lower[:-1] + 'ies')
    
    # Add "to " prefix variant for single-word verb searches (e.g., "eat" → "to eat")
    # so token match picks up "to eat; ..." glosses at rank 1 instead of multi-word rank 2
    if len(tokens) == 1 and not _is_japanese(query):
        query_variants.append('to ' + q_lower)
    
    # Expand FTS query to include word variants for better recall
    if len(fts_variants) > 1 and len(tokens) == 1:
        fts_query = ' OR '.join(f"{v}*" for v in fts_variants)
    
    # Use module-level helper functions
    def _expand_hyphen_long_romaji(s: str) -> str:
        # Replace hyphens that indicate long-vowels by duplicating the previous
        # vowel (e.g. ke-ki -> keeki, no-to -> nooto). If no previous vowel,
        # just remove the hyphen.
        s = s.lower()
        vowels = set('aeiou')
        out_chars: list[str] = []
        for i, ch in enumerate(s):
            if ch == '-':
                if out_chars and out_chars[-1] in vowels:
                    out_chars.append(out_chars[-1])
                # otherwise drop the hyphen
            else:
                out_chars.append(ch)
        return ''.join(out_chars)

    is_japanese = _is_japanese(query)
    is_romaji = (_is_romaji(query) and not is_japanese)
    romaji_hira = romaji_kata = None
    if is_romaji:
        hira = _romaji_to_hiragana(query)
        # The standard converter returns None for romaji ending in bare 'n'
        # (e.g. "nomimasen", "tabemasen") because trailing 'n' is ambiguous.
        # Retry with 'nn' so the converter treats it as ん.
        if hira is None:
            q_lower_n = query.lower()
            if q_lower_n.endswith('n') and not q_lower_n.endswith('nn'):
                hira = _romaji_to_hiragana(q_lower_n[:-1] + 'nn')
        if hira:
            romaji_hira = hira
            romaji_kata = _romaji_to_katakana_with_long(query) or _hiragana_to_katakana(hira)
            # Validate: check if the converted kana matches any dictionary entry.
            # This catches English words like "orange" being falsely parsed as romaji (おらんげ).
            # For short kana (<=2 chars), require exact match to avoid false positives
            # from common English words like "run"→るん, "make"→まけ, etc.
            kata_check = romaji_kata or ''
            if len(romaji_hira) <= 2:
                kana_hit = cur.execute(
                    "SELECT 1 FROM entries WHERE kana = ? OR kana = ? LIMIT 1",
                    (romaji_hira, kata_check)
                ).fetchone()
            else:
                kana_hit = cur.execute(
                    "SELECT 1 FROM entries WHERE kana LIKE ? OR kana LIKE ? LIMIT 1",
                    (romaji_hira + '%', kata_check + '%')
                ).fetchone()
            if not kana_hit:
                is_romaji = False
                romaji_hira = romaji_kata = None
        if not is_romaji:
            pass  # fall through to English path below
        elif hira:
            # If the romaji contains hyphens, expand them into doubled-vowel ASCII
            # variants and use the expanded ASCII (not the raw hyphenated form)
            # when building the FTS query to avoid MATCH parsing/tokenization issues.
            if '-' in query:
                ascii_variant = _expand_hyphen_long_romaji(query)
            else:
                ascii_variant = query
            # expand FTS query to include ASCII and kana variants so the candidate set contains kana entries
            # Include both exact and prefix matches for kana to catch entries like ことば (exact) and ことば遊び (prefix)
            if romaji_hira and romaji_kata:
                # Also deinflect the converted hiragana so that romaji conjugations
                # like "tabemasu" → たべます → たべる are found in the dictionary.
                kana_fts_parts = [
                    f"{ascii_variant}*",
                    romaji_hira, f"{romaji_hira}*",
                    romaji_kata, f"{romaji_kata}*",
                ]
                for dcand in _deinflect(romaji_hira):
                    if dcand != romaji_hira:
                        kana_fts_parts.append(dcand)
                        kana_fts_parts.append(f"{dcand}*")
                        # katakana counterpart of the deinflected form
                        dcand_kata = ''.join(
                            chr(ord(ch) + 0x60) if 0x3041 <= ord(ch) <= 0x3096 else ch
                            for ch in dcand
                        )
                        if dcand_kata != dcand:
                            kana_fts_parts.append(dcand_kata)
                            kana_fts_parts.append(f"{dcand_kata}*")
                fts_query = ' OR '.join(kana_fts_parts)
        else:
            # not valid romaji sequence (treat as English)
            is_romaji = False
    # helper expression to match gloss as a semicolon-delimited token
    # we'll use SQL's instr('; ' || lower(gloss) || '; ', '; ' || ? || '; ')
    
    # For Japanese queries, also convert between hiragana and katakana for broader matching
    if is_japanese:
        # Build cross-kana FTS terms: if user typed katakana, also search hiragana and vice versa
        kana_variants = set()
        kana_variants.add(query)
        # Convert to hiragana
        hira_ver = []
        kata_ver = []
        for ch in query:
            o = ord(ch)
            if 0x30A1 <= o <= 0x30F6:  # katakana -> hiragana
                hira_ver.append(chr(o - 0x60))
                kata_ver.append(ch)
            elif 0x3041 <= o <= 0x3096:  # hiragana -> katakana
                hira_ver.append(ch)
                kata_ver.append(chr(o + 0x60))
            else:
                hira_ver.append(ch)
                kata_ver.append(ch)
        hira_str = ''.join(hira_ver)
        kata_str = ''.join(kata_ver)
        kana_variants.add(hira_str)
        kana_variants.add(kata_str)
        # Add deinflected (deconjugated) candidates so that polite/past/te/negative
        # forms (e.g. 食べます, 書きました) resolve to the dictionary form (食べる, 書く).
        # Each candidate is itself passed through the hiragana ↔ katakana expansion so
        # kana-only entries are also found.
        for dcand in _deinflect(query):
            if dcand != query:  # originals already added above
                kana_variants.add(dcand)
                # Cross-kana expansion for each deinflected candidate
                _hv, _kv = [], []
                for ch in dcand:
                    o = ord(ch)
                    if 0x30A1 <= o <= 0x30F6:
                        _hv.append(chr(o - 0x60)); _kv.append(ch)
                    elif 0x3041 <= o <= 0x3096:
                        _hv.append(ch); _kv.append(chr(o + 0x60))
                    else:
                        _hv.append(ch); _kv.append(ch)
                kana_variants.add(''.join(_hv))
                kana_variants.add(''.join(_kv))
        # Build FTS query with all kana variants (exact + prefix)
        fts_parts = []
        for kv in kana_variants:
            fts_parts.append(kv)
            fts_parts.append(f'{kv}*')
        fts_query = ' OR '.join(fts_parts)
    
    # Normalize the kind filter: 'kanji_kana' matches only 'kanji' kind (which includes single kana characters)
    kind_sql_filter = None
    kind_sql_params = []
    if kind == 'kanji_kana':
        kind_sql_filter = "e.kind = 'kanji'"
        fts_kind_filter = "kind = 'kanji'"
    elif kind:
        kind_sql_filter = "e.kind = ?"
        kind_sql_params = [kind]
        fts_kind_filter = "kind = ?"
    else:
        fts_kind_filter = None
    
    try:
        # Use a safe two-stage query: first get matching rowids from the FTS table,
        # then join back to the authoritative `entries` table so we can rely on its
        # `kind` column and compute ranking deterministically regardless of the
        # exact FTS schema.
        # When a kind filter is active, push it into the FTS CTE to ensure
        # we get enough candidates of the right kind (avoids the problem where
        # broad queries return only vocab entries in the first N results).
        cand_limit = max(limit * 10, 500)
        # Significantly increase candidate limit for kanji_kana filter 
        # (romaji prefix matches can return thousands of candidates before exact kana entries)
        if kind == 'kanji_kana':
            cand_limit = max(limit * 100, 10000)
        # fts_query is already prepared
        # Different ranking depending on query language:
        # Treat romaji queries like Japanese queries for ranking purposes
        if is_japanese or is_romaji:
            # Japanese/Romaji query: prefer exact kanji/kana, then gloss matches
            gloss_case = (
                "CASE WHEN lower(e.kanji)=? OR lower(e.kana)=? THEN 0 "
                "WHEN instr('; ' || lower(e.gloss) || '; ', '; ' || ? || '; ') > 0 THEN 1 "
                "WHEN lower(e.gloss)=? THEN 1 "
                "WHEN lower(e.gloss) LIKE ? THEN 2 ELSE 3 END as rank"
            )
            tie_break = "CASE WHEN lower(e.gloss)=? THEN 0 ELSE 1 END as exact_gloss_rank, length(e.gloss) as gloss_len"
            
            # Add JLPT priority for Japanese queries too
            jlpt_priority_case = (
                "CASE "
                "WHEN e.jlpt_level='N5' THEN 0 "
                "WHEN e.jlpt_level='N4' THEN 1 "
                "WHEN e.jlpt_level='N3' THEN 2 "
                "WHEN e.jlpt_level='N2' THEN 3 "
                "WHEN e.jlpt_level='N1' THEN 4 "
                "ELSE 5 END as jlpt_priority"
            )
            
            # For romaji queries, use converted kana for comparisons; otherwise use original query
            compare_str = romaji_hira if is_romaji else query.lower()
            compare_like = '%' + compare_str + '%'
            # For romaji, also check katakana version for exact matches
            compare_kata = romaji_kata.lower() if (is_romaji and romaji_kata) else None
            
            # Build the ranking case - check both hiragana and katakana for romaji queries
            if is_romaji and compare_kata:
                gloss_case = (
                    "CASE WHEN lower(e.kanji) IN (?, ?) OR lower(e.kana) IN (?, ?) THEN 0 "
                    "WHEN instr('; ' || lower(e.gloss) || '; ', '; ' || ? || '; ') > 0 THEN 1 "
                    "WHEN lower(e.gloss)=? THEN 1 "
                    "WHEN lower(e.gloss) LIKE ? THEN 2 ELSE 3 END as rank"
                )
            else:
                gloss_case = (
                    "CASE WHEN lower(e.kanji)=? OR lower(e.kana)=? THEN 0 "
                    "WHEN instr('; ' || lower(e.gloss) || '; ', '; ' || ? || '; ') > 0 THEN 1 "
                    "WHEN lower(e.gloss)=? THEN 1 "
                    "WHEN lower(e.gloss) LIKE ? THEN 2 ELSE 3 END as rank"
                )
            
            # Standard kind_priority for all searches
            kind_priority_case = "CASE WHEN e.kind='vocab' THEN 0 WHEN e.kind='kanji' THEN 1 ELSE 2 END as kind_priority"
            
            if kind:
                fts_where = f'entries_fts MATCH ? AND {fts_kind_filter}' if fts_kind_filter else 'entries_fts MATCH ?'
                fts_cte_params = [fts_query] + (kind_sql_params if kind != 'kanji_kana' else []) + [cand_limit]
                sql = (
                    f'WITH f AS (SELECT rowid AS rid FROM entries_fts WHERE {fts_where} LIMIT ?) '
                    "SELECT e.id, e.kanji, e.kana, e.gloss, COALESCE(e.kind, 'vocab') as kind, "
                    + gloss_case + ', ' + tie_break + ", CASE WHEN lower(e.gloss)=? AND e.kind='vocab' THEN 0 ELSE 1 END as exact_vocab_priority, "
                    + kind_priority_case + ", "
                    + jlpt_priority_case + ' '
                    'FROM f JOIN entries e ON e.id = f.rid '
                    'ORDER BY exact_vocab_priority ASC, rank ASC, jlpt_priority ASC, exact_gloss_rank ASC, kind_priority ASC, gloss_len ASC LIMIT ?'
                )
                # Build params based on whether we have katakana variant
                if is_romaji and compare_kata:
                    params = tuple(fts_cte_params) + (compare_str, compare_kata, compare_str, compare_kata, compare_str, compare_str, compare_like, compare_str, compare_str, limit)
                else:
                    params = tuple(fts_cte_params) + (compare_str, compare_str, compare_str, compare_str, compare_like, compare_str, compare_str, limit)
            else:
                sql = (
                    'WITH f AS (SELECT rowid AS rid FROM entries_fts WHERE entries_fts MATCH ? LIMIT ?) '
                    "SELECT e.id, e.kanji, e.kana, e.gloss, COALESCE(e.kind, 'vocab') as kind, "
                    + gloss_case + ', ' + tie_break + ", CASE WHEN lower(e.gloss)=? AND e.kind='vocab' THEN 0 ELSE 1 END as exact_vocab_priority, "
                    + kind_priority_case + ", "
                    + jlpt_priority_case + ' '
                    'FROM f JOIN entries e ON e.id = f.rid '
                    'ORDER BY exact_vocab_priority ASC, rank ASC, jlpt_priority ASC, exact_gloss_rank ASC, kind_priority ASC, gloss_len ASC LIMIT ?'
                )
                if is_romaji and compare_kata:
                    params = (fts_query, cand_limit, compare_str, compare_kata, compare_str, compare_kata, compare_str, compare_str, compare_like, compare_str, compare_str, limit)
                else:
                    params = (fts_query, cand_limit, compare_str, compare_str, compare_str, compare_str, compare_like, compare_str, compare_str, limit)
        else:
            # Latin/English query: prefer exact gloss equality or gloss token match first
            # Build token match conditions for query and its variants (singular/plural, etc.)
            variant_count = len(query_variants)
            
            # Build the WHEN clause for token matching that includes all variants
            token_match_parts = []
            for i in range(variant_count):
                token_match_parts.extend([
                    f"lower(e.gloss) LIKE ?",  # starts with variant + semicolon
                    f"lower(e.gloss) LIKE ?",  # starts with variant + comma
                    f"lower(e.gloss) LIKE ?",  # starts with variant + space + parenthesis (e.g., "cat (esp...")
                    f"instr('; ' || lower(e.gloss) || '; ', '; ' || ? || '; ') > 0",
                    f"instr('; ' || lower(e.gloss) || '; ', '; ' || ? || ',') > 0"
                ])
            token_match_condition = " OR ".join(token_match_parts)
            
            # Build condition to check if query matches the first meaning (before any ; or ,)
            first_meaning_parts = []
            for i in range(variant_count):
                first_meaning_parts.extend([
                    f"lower(e.gloss) LIKE ?",  # starts with variant + semicolon
                    f"lower(e.gloss) LIKE ?",  # starts with variant + comma  
                    f"lower(e.gloss) LIKE ?"   # starts with variant + space + parenthesis
                ])
            first_meaning_condition = " OR ".join(first_meaning_parts)
            
            gloss_case = (
                "CASE WHEN lower(e.gloss)=? THEN 0 "
                # Exact match on semicolon-separated token (any position including first)
                # Checks original query and all variants (singular/plural forms)
                f"WHEN {token_match_condition} THEN 1 "
                # Multi-word match (contains space in query term itself) - lower priority
                "WHEN instr(lower(e.gloss), ' ' || ?) > 0 OR instr(lower(e.gloss), ? || ' ') > 0 THEN 2 "
                "WHEN lower(e.gloss) LIKE ? OR lower(e.gloss) LIKE ? THEN 3 "
                "WHEN lower(e.kanji)=? OR lower(e.kana)=? THEN 4 "
                "WHEN lower(e.gloss) LIKE ? THEN 5 ELSE 6 END as match_rank"
            )
            # Add first_meaning_priority to prioritize entries where query matches first meaning
            tie_break = (
                "CASE WHEN lower(e.gloss)=? THEN 0 ELSE 1 END as exact_gloss_rank, "
                f"CASE WHEN {first_meaning_condition} THEN 0 ELSE 1 END as first_meaning_priority, "
                "length(e.gloss) as gloss_len, length(e.kana) as kana_len"
            )
            
            # Build JLPT priority CASE statement
            # Use jlpt_level column from database: N5 (highest priority), N4, N3, N2, N1, NULL (lowest)
            # Map to numeric priority: N5=0 (best), N4=1, N3=2, N2=3, N1=4, NULL=5 (worst)
            jlpt_priority_case = (
                "CASE "
                "WHEN e.jlpt_level='N5' THEN 0 "
                "WHEN e.jlpt_level='N4' THEN 1 "
                "WHEN e.jlpt_level='N3' THEN 2 "
                "WHEN e.jlpt_level='N2' THEN 3 "
                "WHEN e.jlpt_level='N1' THEN 4 "
                "ELSE 5 END as jlpt_priority"
            )
            
            if kind:
                fts_where = f'entries_fts MATCH ? AND {fts_kind_filter}' if fts_kind_filter else 'entries_fts MATCH ?'
                fts_cte_params = [fts_query] + (kind_sql_params if kind != 'kanji_kana' else []) + [cand_limit]
                sql = (
                    f'With f AS (SELECT rowid AS rid FROM entries_fts WHERE {fts_where} LIMIT ?) '
                    "SELECT e.id, e.kanji, e.kana, e.gloss, COALESCE(e.kind, 'vocab') as kind, "
                    + gloss_case + ', ' + tie_break + ", CASE WHEN e.kind='vocab' THEN 0 WHEN e.kind='kanji' THEN 1 ELSE 2 END as kind_priority, "
                    + jlpt_priority_case + ' '
                    'FROM f JOIN entries e ON e.id = f.rid '
                    'ORDER BY kind_priority ASC, (match_rank / 2) ASC, jlpt_priority ASC, match_rank ASC, exact_gloss_rank ASC, first_meaning_priority ASC, kana_len ASC, gloss_len ASC LIMIT ?'
                )
                params = list(fts_cte_params) + [
                          query.lower(),        # lower(e.gloss)=? (exact)
                ]
                # Add variant parameters for token matching (5 params per variant)
                for variant in query_variants:
                    params.extend([
                        variant + ';%',  # LIKE ? (starts-with + semicolon)
                        variant + ',%',  # LIKE ? (starts-with + comma)
                        variant + ' (%', # LIKE ? (starts-with + space + parenthesis)
                        variant,         # instr(... '; ' || ? || '; ')
                        variant,         # instr(... '; ' || ? || ',')
                    ])
                
                # Continue with remaining parameters
                variant_param_count = len(query_variants) * 5
                cte_len = len(fts_cte_params)
                kanji_kana_base_idx = cte_len + 1 + variant_param_count
                
                params.extend([
                          query.lower(),        # instr(... ' ' || ?) multi-word
                          query.lower(),        # instr(... ? || ' ') multi-word
                          query.lower() + ' %', # lower(e.gloss) LIKE ? (starts-with + space)
                          query.lower() + '(%', # lower(e.gloss) LIKE ? (starts-with + '(')
                          query.lower(),        # lower(e.kanji)=?
                          query.lower(),        # lower(e.kana)=?
                          like_q,               # lower(e.gloss) LIKE ? (contains)
                          query.lower(),        # tie_break exact_gloss_rank lower(e.gloss)=?
                ])
                # Add first_meaning_priority parameters (3 params per variant)
                for variant in query_variants:
                    params.extend([
                        variant + ';%',  # LIKE ? (starts-with + semicolon)
                        variant + ',%',  # LIKE ? (starts-with + comma)
                        variant + ' (%', # LIKE ? (starts-with + space + parenthesis)
                    ])
                # if romaji produced kana variants, use them for kanji/kana equality checks
                if 'is_romaji' in locals() and is_romaji and romaji_hira and romaji_kata:
                    params[kanji_kana_base_idx + 4] = romaji_kata.lower()  # kanji param
                    params[kanji_kana_base_idx + 5] = romaji_hira.lower()  # kana param
                params += [limit]
                params = tuple(params)
            else:
                sql = (
                    'WITH f AS (SELECT rowid AS rid FROM entries_fts WHERE entries_fts MATCH ? LIMIT ?) '
                    "SELECT e.id, e.kanji, e.kana, e.gloss, COALESCE(e.kind, 'vocab') as kind, "
                    + gloss_case + ', ' + tie_break + ", CASE WHEN e.kind='vocab' THEN 0 WHEN e.kind='kanji' THEN 1 ELSE 2 END as kind_priority, "
                    + jlpt_priority_case + ' '
                    'FROM f JOIN entries e ON e.id = f.rid '
                    'ORDER BY kind_priority ASC, (match_rank / 2) ASC, jlpt_priority ASC, match_rank ASC, exact_gloss_rank ASC, first_meaning_priority ASC, kana_len ASC, gloss_len ASC LIMIT ?'
                )
                params = [fts_query, cand_limit,
                          query.lower(),        # lower(e.gloss)=? (exact)
                ]
                # Add variant parameters for token matching (5 params per variant)
                for variant in query_variants:
                    params.extend([
                        variant + ';%',  # LIKE ? (starts-with + semicolon)
                        variant + ',%',  # LIKE ? (starts-with + comma)
                        variant + ' (%', # LIKE ? (starts-with + space + parenthesis)
                        variant,         # instr(... '; ' || ? || '; ')
                        variant,         # instr(... '; ' || ? || ',')
                    ])
                
                # Continue with remaining parameters
                variant_param_count = len(query_variants) * 5
                kanji_kana_base_idx = 3 + variant_param_count
                
                params.extend([
                          query.lower(),        # instr(... ' ' || ?) multi-word
                          query.lower(),        # instr(... ? || ' ') multi-word
                          query.lower() + ' %', # lower(e.gloss) LIKE ? (starts-with + space)
                          query.lower() + '(%', # lower(e.gloss) LIKE ? (starts-with + '(')
                          query.lower(),        # lower(e.kanji)=?
                          query.lower(),        # lower(e.kana)=?
                          like_q,               # lower(e.gloss) LIKE ? (contains)
                          query.lower(),        # tie_break exact_gloss_rank lower(e.gloss)=?
                ])
                # Add first_meaning_priority parameters (3 params per variant)
                for variant in query_variants:
                    params.extend([
                        variant + ';%',  # LIKE ? (starts-with + semicolon)
                        variant + ',%',  # LIKE ? (starts-with + comma)
                        variant + ' (%', # LIKE ? (starts-with + space + parenthesis)
                    ])
                if 'is_romaji' in locals() and is_romaji and romaji_hira and romaji_kata:
                    params[kanji_kana_base_idx + 4] = romaji_kata.lower()  # kanji param
                    params[kanji_kana_base_idx + 5] = romaji_hira.lower()  # kana param
                params += [limit]
                params = tuple(params)
        cur.execute(sql, params)
        rows = cur.fetchall()
    except Exception as search_err:
        # If FTS search fails (table missing or MATCH error), fall back to a safe LIKE search on `entries`.
        like_q_fallback = '%' + '%'.join(tokens) + '%'
        try:
            if kind == 'kanji_kana':
                cur.execute(
                    "SELECT id, kanji, kana, gloss, kind FROM entries WHERE (kanji LIKE ? OR kana LIKE ? OR gloss LIKE ?) AND kind = 'kanji' LIMIT ?",
                    (like_q_fallback, like_q_fallback, like_q_fallback, limit)
                )
            elif kind:
                cur.execute(
                    'SELECT id, kanji, kana, gloss, kind FROM entries WHERE (kanji LIKE ? OR kana LIKE ? OR gloss LIKE ?) AND kind = ? LIMIT ?',
                    (like_q_fallback, like_q_fallback, like_q_fallback, kind, limit)
                )
            else:
                cur.execute(
                    'SELECT id, kanji, kana, gloss, kind FROM entries WHERE kanji LIKE ? OR kana LIKE ? OR gloss LIKE ? LIMIT ?',
                    (like_q_fallback, like_q_fallback, like_q_fallback, limit)
                )
            rows = cur.fetchall()
        except Exception:
            return []
    # entry_id column is stored as JSON/text in some cases; ensure int
    out = []
    for row in rows:
        entry_id, kanji, kana, gloss, kind = row[:5]
        try:
            eid = int(entry_id)
        except Exception:
            eid = entry_id
        out.append((eid, kanji, kana, gloss, kind))
    return out


def get_entry_by_id(conn: sqlite3.Connection, entry_id: int):
    cur = conn.cursor()
    cur.execute('SELECT id, kanji, kana, gloss, pos, tags, kind, stroke_order_available, jlpt_level FROM entries WHERE id = ?', (entry_id,))
    row = cur.fetchone()
    if not row:
        return None
    id_, kanji, kana, gloss, pos, tags, kind, stroke_order_available, jlpt_level = row
    try:
        tags = json.loads(tags or '{}')
    except Exception:
        tags = {}
    return {
        'id': id_, 
        'kanji': kanji, 
        'kana': kana, 
        'gloss': gloss, 
        'pos': pos, 
        'tags': tags, 
        'kind': kind,
        'stroke_order_available': stroke_order_available,
        'jlpt_level': jlpt_level
    }


def get_entries_by_ids(conn: sqlite3.Connection, entry_ids: List[int]) -> dict:
    """Batch fetch multiple entries by ID in a single query.
    
    Returns a dict mapping entry_id -> entry dict for efficient lookup.
    This is much faster than calling get_entry_by_id in a loop.
    """
    if not entry_ids:
        return {}
    
    cur = conn.cursor()
    # Use parameterized query with placeholders for each ID
    placeholders = ','.join('?' * len(entry_ids))
    cur.execute(f'SELECT id, kanji, kana, gloss, pos, tags, kind, stroke_order_available, jlpt_level FROM entries WHERE id IN ({placeholders})', entry_ids)
    rows = cur.fetchall()
    
    result = {}
    for row in rows:
        id_, kanji, kana, gloss, pos, tags, kind, stroke_order_available, jlpt_level = row
        try:
            tags = json.loads(tags or '{}')
        except Exception:
            tags = {}
        result[id_] = {
            'id': id_, 
            'kanji': kanji, 
            'kana': kana, 
            'gloss': gloss, 
            'pos': pos, 
            'tags': tags, 
            'kind': kind,
            'stroke_order_available': stroke_order_available,
            'jlpt_level': jlpt_level
        }
    
    return result


# ---------------- SRS per-kind helpers ----------------

def get_srs_row(conn: sqlite3.Connection, entry_id: int):
    cur = conn.cursor()
    cur.execute('SELECT id, entry_id, jid, ease, reps, interval, due_at, data FROM srs WHERE entry_id = ?', (entry_id,))
    row = cur.fetchone()
    if not row:
        return None
    id_, entry_id, jid, ease, reps, interval, due_at, data = row
    try:
        data = json.loads(data or '{}')
    except Exception:
        data = {}
    return {'id': id_, 'entry_id': entry_id, 'jid': jid, 'ease': ease, 'reps': reps, 'interval': interval, 'due_at': due_at, 'data': data}


def _ensure_srs_row(conn: sqlite3.Connection, entry_id: int, jid: str | None = None):
    cur = conn.cursor()
    row = get_srs_row(conn, entry_id)
    if row:
        return row
    # create a new srs row with defaults
    cur.execute('INSERT INTO srs (entry_id, jid, ease, reps, interval, due_at, data, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (entry_id, jid or '', 2.5, 0, 0, None, json.dumps({}), datetime.utcnow().isoformat()))
    conn.commit()
    return get_srs_row(conn, entry_id)


def get_srs_vector(conn: sqlite3.Connection, entry_id: int, kind: str = 'default', vector_type: str = None) -> dict:
    """Return the SRS vector for an entry, kind, and optional vector_type.

    If vector_type is provided, look in srs.data.per_vector[vector_type].
    Otherwise fall back to per_kind[kind], then top-level columns.
    If no row exists, return a default fresh vector.
    """
    row = get_srs_row(conn, entry_id)
    if not row:
        return {'ease': 2.5, 'reps': 0, 'interval': 0, 'due_at': None, 'last_seen': None, 'accuracy': None, 'streak': 0}
    data = row.get('data') or {}
    
    # Try per_vector first if vector_type provided
    if vector_type:
        pv = (data.get('per_vector') or {}).get(vector_type)
        if pv:
            vec = pv.copy()
            vec.setdefault('ease', 2.5)
            vec.setdefault('reps', 0)
            vec.setdefault('interval', 0)
            vec.setdefault('due_at', None)
            vec.setdefault('last_seen', None)
            vec.setdefault('accuracy', None)
            vec.setdefault('streak', 0)
            return vec
    
    # Fall back to per_kind
    pk = (data.get('per_kind') or {}).get(kind)
    if pk:
        vec = pk.copy()
        vec.setdefault('ease', 2.5)
        vec.setdefault('reps', 0)
        vec.setdefault('interval', 0)
        vec.setdefault('due_at', None)
        vec.setdefault('last_seen', None)
        vec.setdefault('accuracy', None)
        vec.setdefault('streak', 0)
        return vec
    # fallback to top-level
    return {
        'ease': row.get('ease') if row.get('ease') is not None else 2.5,
        'reps': row.get('reps') or 0,
        'interval': row.get('interval') or 0,
        'due_at': row.get('due_at'),
        'last_seen': (data.get('last_seen') if isinstance(data, dict) else None),
        'accuracy': (data.get('accuracy') if isinstance(data, dict) else None),
        'streak': (data.get('streak') if isinstance(data, dict) else 0),
    }


def update_srs_vector(conn: sqlite3.Connection, entry_id: int, kind: str, updates: dict, vector_type: str = None):
    """Merge updates into srs.data.per_vector[vector_type] (or per_kind[kind]) and persist.

    If vector_type is provided, updates go to per_vector[vector_type].
    Otherwise updates go to per_kind[kind] (legacy behavior).
    If the srs row doesn't exist it will be created. Returns the new vector.
    """
    row = _ensure_srs_row(conn, entry_id)
    cur = conn.cursor()
    data = row.get('data') or {}
    
    if vector_type:
        # Store in per_vector
        per_vector = data.get('per_vector') or {}
        vec = per_vector.get(vector_type) or {}
        vec.update(updates)
        vec.setdefault('ease', vec.get('ease', 2.5))
        vec.setdefault('reps', vec.get('reps', 0))
        vec.setdefault('interval', vec.get('interval', 0))
        vec.setdefault('due_at', vec.get('due_at', None))
        vec.setdefault('last_seen', vec.get('last_seen', None))
        vec.setdefault('accuracy', vec.get('accuracy', None))
        vec.setdefault('streak', vec.get('streak', 0))
        per_vector[vector_type] = vec
        data['per_vector'] = per_vector
    else:
        # Store in per_kind (legacy)
        per_kind = data.get('per_kind') or {}
        vec = per_kind.get(kind) or {}
        vec.update(updates)
        vec.setdefault('ease', vec.get('ease', 2.5))
        vec.setdefault('reps', vec.get('reps', 0))
        vec.setdefault('interval', vec.get('interval', 0))
        vec.setdefault('due_at', vec.get('due_at', None))
        vec.setdefault('last_seen', vec.get('last_seen', None))
        vec.setdefault('accuracy', vec.get('accuracy', None))
        vec.setdefault('streak', vec.get('streak', 0))
        per_kind[kind] = vec
        data['per_kind'] = per_kind
    
    cur.execute('UPDATE srs SET data = ?, updated_at = ? WHERE id = ?',
                (json.dumps(data), datetime.utcnow().isoformat(), row['id']))
    conn.commit()
    return vec


def migrate_srs_rows_to_per_kind(conn: sqlite3.Connection) -> int:
    """Migrate existing top-level columns (ease/reps/interval/due_at) into
    `data.per_kind[entry_kind]` so we have per-kind vectors. Returns number of
    rows modified."""
    cur = conn.cursor()
    cur.execute('SELECT id, entry_id, jid, ease, reps, interval, due_at, data FROM srs')
    rows = cur.fetchall()
    modified = 0
    for r in rows:
        sid, entry_id, jid, ease, reps, interval, due_at, data = r
        try:
            data_json = json.loads(data or '{}')
        except Exception:
            data_json = {}
        per_kind = data_json.get('per_kind') or {}
        # find entry kind so we can place vector under the right kind
        cur2 = conn.cursor()
        cur2.execute('SELECT kind FROM entries WHERE id = ?', (entry_id,))
        row = cur2.fetchone()
        entry_kind = (row[0] if row else 'vocab') or 'vocab'
        if entry_kind in per_kind:
            continue
        per_kind[entry_kind] = {
            'ease': (ease if ease is not None else 2.5),
            'reps': (reps or 0),
            'interval': (interval or 0),
            'due_at': due_at,
            'last_seen': data_json.get('last_seen'),
            'accuracy': data_json.get('accuracy'),
            'streak': data_json.get('streak', 0),
        }
        data_json['per_kind'] = per_kind
        cur.execute('UPDATE srs SET data = ? WHERE id = ?', (json.dumps(data_json), sid))
        modified += 1
    conn.commit()
    return modified


def count_low_stability_vectors(conn: sqlite3.Connection, threshold: int = 3) -> int:
    """Return the number of per-kind SRS vectors with streak < threshold."""
    cur = conn.cursor()
    cur.execute('SELECT data FROM srs')
    rows = cur.fetchall()
    c = 0
    for (data,) in rows:
        try:
            data_json = json.loads(data or '{}')
        except Exception:
            data_json = {}
        per_kind = data_json.get('per_kind') or {}
        for v in per_kind.values():
            try:
                streak = int(v.get('streak', 0) or 0)
            except Exception:
                streak = 0
            if streak < threshold:
                c += 1
    return c


# -------------- FSRS review application ----------------
from dictionary import fsrs as _fsrs
from dictionary import learning as _learning


def apply_review(conn: sqlite3.Connection, entry_id: int, quality: int, kind: str | None = None, vector_type: str | None = None) -> dict:
    """Apply a review (quality 0..5) for `entry_id` and `kind` (if omitted uses entry.kind).

    If vector_type is provided, reads/writes per_vector[vector_type] instead of per_kind[kind],
    keeping SRS state independent per vector type.

    This computes the new vector via FSRS rules, updates review failure counters
    and sets a `flag_forced_drill` when thresholds are reached (3 wrongs or 3 consecutive wrong days).
    Returns the updated vector.
    """
    cur = conn.cursor()
    # resolve kind if not provided
    if kind is None:
        cur.execute('SELECT kind FROM entries WHERE id = ?', (entry_id,))
        row = cur.fetchone()
        kind = (row[0] if row else 'vocab') or 'vocab'

    prev = get_srs_vector(conn, entry_id, kind, vector_type)
    new = _fsrs.apply_fsrs(prev, quality)

    # Determine if this review was a failure (treat quality <= 2 as incorrect)
    was_wrong = (quality <= 2)

    # prepare per-kind failure tracking fields
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rev_wrongs = prev.get('consecutive_review_wrongs', 0)
    consec_days = prev.get('consecutive_wrong_days', 0)
    last_wrong_date = prev.get('last_wrong_date')

    if was_wrong:
        rev_wrongs = (prev.get('consecutive_review_wrongs', 0) or 0) + 1
        # handle consecutive days tracking
        if last_wrong_date == today:
            # multiple wrongs same day don't change consecutive days
            pass
        elif last_wrong_date == yesterday:
            consec_days = (prev.get('consecutive_wrong_days', 0) or 0) + 1
        else:
            consec_days = 1
        last_wrong_date = today
    else:
        # successful review: reset consecutive wrong counters
        rev_wrongs = 0
        consec_days = 0
        last_wrong_date = None

    # check thresholds
    flag_forced = prev.get('flag_forced_drill', False)
    if (rev_wrongs >= 3) or (consec_days >= 3):
        if not flag_forced:
            flag_forced = True
            forced_at = datetime.utcnow().isoformat()
        else:
            forced_at = prev.get('forced_drill_added_at')
    else:
        forced_at = prev.get('forced_drill_added_at') if prev.get('flag_forced_drill') else None

    # Merge FSRS changes with our tracking fields and persist per-kind vector
    updates = dict(new)
    updates.update({
        'consecutive_review_wrongs': rev_wrongs,
        'consecutive_wrong_days': consec_days,
        'last_wrong_date': last_wrong_date,
        'flag_forced_drill': flag_forced,
        'forced_drill_added_at': forced_at,
    })

    # persist vector (per_vector if vector_type given, otherwise per_kind)
    update_srs_vector(conn, entry_id, kind, updates, vector_type)

    # also update top-level columns for backward compatibility if this is the default kind
    # (only when not using per-vector storage, to avoid overwriting with one vector's data)
    if not vector_type:
        try:
            cur.execute('SELECT kind FROM entries WHERE id = ?', (entry_id,))
            row = cur.fetchone()
            entry_kind = (row[0] if row else 'vocab') or 'vocab'
            if entry_kind == kind:
                # update ease/reps/interval/due_at columns too
                cur.execute('UPDATE srs SET ease = ?, reps = ?, interval = ?, due_at = ? WHERE entry_id = ?',
                            (updates.get('ease'), updates.get('reps'), updates.get('interval'), updates.get('due_at'), entry_id))
                conn.commit()
        except Exception:
            pass

    return updates


# ------------ Forced drill helpers -------------

def get_forced_drill_entries(conn: sqlite3.Connection, kind: str | None = None) -> List[int]:
    """Return entry IDs that have flag_forced_drill set (optionally filtered by kind)."""
    cur = conn.cursor()
    cur.execute('SELECT entry_id, data FROM srs')
    rows = cur.fetchall()
    out = []
    for entry_id, data in rows:
        try:
            data_json = json.loads(data or '{}')
        except Exception:
            data_json = {}
        per_kind = data_json.get('per_kind') or {}
        # Determine forced-drill condition by computed thresholds (more robust than relying on the flag field)
        def _is_forced(v: dict) -> bool:
            if not v:
                return False
            if (v.get('consecutive_review_wrongs') or 0) >= 3:
                return True
            if (v.get('consecutive_wrong_days') or 0) >= 3:
                return True
            if v.get('flag_forced_drill'):
                return True
            return False

        if kind:
            v = per_kind.get(kind)
            if _is_forced(v):
                out.append(entry_id)
        else:
            # any kind
            for v in per_kind.values():
                if _is_forced(v):
                    out.append(entry_id)
                    break
    return out


def create_forced_drill_session(conn: sqlite3.Connection, kind: str | None = None, created_by: str = 'system', clear_flags_on_create: bool = False) -> int | None:
    """Create a learning session that contains all entries flagged for forced drill.

    Returns the session id, or None if no entries need forced drill.
    If `clear_flags_on_create` is True, clear the `flag_forced_drill` for included entries after creating the session.
    """
    entries = get_forced_drill_entries(conn, kind)
    if not entries:
        return None
    # ensure learning table exists
    ensure_learning_table(conn)
    sid = _learning.create_session(conn, entries, kind or 'vocab', created_by=created_by)
    # mark this session as a forced drill in its meta
    s = get_learning_session(conn, sid)
    data = s['data']
    data['meta']['forced_drill'] = True
    data['meta']['forced_entries'] = entries
    update_learning_session(conn, sid, data)
    if clear_flags_on_create:
        clear_forced_drill_flags(conn, entries=entries, kind=kind)
    return sid


def clear_forced_drill_flags(conn: sqlite3.Connection, entries: List[int] | None = None, kind: str | None = None) -> int:
    """Clear the `flag_forced_drill` and related tracking fields for given entries or all if entries is None.

    Returns the number of rows updated.
    """
    cur = conn.cursor()
    cur.execute('SELECT id, entry_id, data FROM srs')
    rows = cur.fetchall()
    modified = 0
    for sid, entry_id, data in rows:
        if entries is not None and entry_id not in entries:
            continue
        try:
            data_json = json.loads(data or '{}')
        except Exception:
            data_json = {}
        per_kind = data_json.get('per_kind') or {}
        changed = False
        for k, v in list(per_kind.items()):
            if kind and k != kind:
                continue
            if v and v.get('flag_forced_drill'):
                v.pop('flag_forced_drill', None)
                v.pop('forced_drill_added_at', None)
                v.pop('consecutive_review_wrongs', None)
                v.pop('consecutive_wrong_days', None)
                v.pop('last_wrong_date', None)
                per_kind[k] = v
                changed = True
        if changed:
            data_json['per_kind'] = per_kind
            cur.execute('UPDATE srs SET data = ? WHERE id = ?', (json.dumps(data_json), sid))
            modified += 1
    conn.commit()
    return modified


def maybe_create_forced_drill(conn: sqlite3.Connection, kind: str | None = None, created_by: str = 'system') -> int | None:
    """Convenience: create a forced drill session when any entries are flagged; returns sid or None."""
    return create_forced_drill_session(conn, kind=kind, created_by=created_by, clear_flags_on_create=False)


def get_due_review_entries(conn: sqlite3.Connection, kind: str | None = None) -> List[int]:
    """Return entry ids that have SRS due now (due_at <= utcnow).

    If `kind` is provided, only entries of that kind are considered.
    """
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    if kind:
        cur.execute("SELECT e.id FROM entries e JOIN srs s ON s.entry_id = e.id WHERE s.due_at IS NOT NULL AND s.due_at <= ? AND e.kind = ?", (now, kind))
    else:
        cur.execute("SELECT entry_id FROM srs WHERE due_at IS NOT NULL AND due_at <= ?", (now,))
    rows = cur.fetchall()
    return [r[0] for r in rows]


def get_due_review_vectors(conn: sqlite3.Connection, kind: str | None = None) -> List[dict]:
    """Return vector dicts that have SRS due now (due_at <= utcnow).
    
    Checks per_vector data in srs.data JSON for each entry. Returns list of:
    {'entry_id': int, 'kind': str, 'vector_type': str, 'prompt': str, 'answer': str}
    
    If `kind` is provided, only entries of that kind are considered.
    """
    from dictionary import learning as _learning
    
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    # Single JOIN query: fetch SRS data + entry columns together (avoids N+1)
    if kind:
        cur.execute("""
            SELECT e.id, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors, s.data
            FROM entries e JOIN srs s ON s.entry_id = e.id WHERE e.kind = ?
        """, (kind,))
    else:
        cur.execute("""
            SELECT e.id, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors, s.data
            FROM entries e JOIN srs s ON s.entry_id = e.id
        """)
    
    entry_rows = cur.fetchall()
    due_vectors = []
    
    for entry_id, entry_kind, kanji, kana, gloss, study_vectors_str, srs_data_str in entry_rows:
        # Parse SRS data once
        try:
            srs_data = json.loads(srs_data_str or '{}')
        except Exception:
            srs_data = {}
        
        # Generate vectors from pre-fetched data (no extra DB query)
        vectors = _learning.make_vectors_from_row(entry_id, entry_kind, kanji, kana, gloss, study_vectors_str)
        
        for vec in vectors:
            vector_type = vec.get('vector_type')
            # Get SRS data for this specific vector from the already-parsed JSON
            pv = (srs_data.get('per_vector') or {}).get(vector_type)
            if pv:
                due_at = pv.get('due_at')
                interval = pv.get('interval', 0)
            else:
                pk = (srs_data.get('per_kind') or {}).get(entry_kind, {})
                due_at = pk.get('due_at')
                interval = pk.get('interval', 0)
            
            # Skip "Known" vectors (interval >= 180 days)
            if interval >= 180:
                continue
            
            if due_at and due_at <= now:
                due_vectors.append({
                    'entry_id': entry_id,
                    'kind': entry_kind,
                    'vector_type': vector_type,
                    'prompt': vec.get('prompt', ''),
                    'answer': vec.get('answer', '')
                })
    
    return due_vectors


def create_review_session(conn: sqlite3.Connection, kind: str | None = None, created_by: str = 'system') -> int | None:
    """Create a learning session containing due review entries and mark meta.review = True.

    Returns session id or None if no due reviews.
    """
    entries = get_due_review_entries(conn, kind)
    if not entries:
        return None
    ensure_learning_table(conn)
    sid = _learning.create_session(conn, entries, kind or 'vocab', created_by=created_by)
    s = get_learning_session(conn, sid)
    data = s['data']
    data['meta']['review'] = True
    data['meta']['review_count'] = len(entries)
    update_learning_session(conn, sid, data)
    return sid


# -------------- Weak Words Drill Functions ----------------

def get_weak_vectors_for_drill(conn: sqlite3.Connection, threshold: int = 3, limit: int = 14, exclude_vector_ids: set = None) -> List[dict]:
    """Get weakest vectors for drilling (no SRS updates).
    
    Only returns vectors that have actually been promoted to SRS (have per_kind data with stability > 0).
    
    Logic:
    1. Get vectors with stability < threshold AND stability > 0 (already in SRS), excluding already drilled
    2. ALSO include vectors with flag_forced_drill = True AND stability >= threshold (leeches)
    3. Sort by stability (lowest first)
    4. If < 7 found, fill rest with next weakest vectors (any stability > 0)
    5. Return up to 'limit' vectors
    
    Returns list of vector dicts with: entry_id, kind, vector_type, prompt, answer, stability
    """
    if exclude_vector_ids is None:
        exclude_vector_ids = set()
    
    cur = conn.cursor()
    # Single JOIN query: fetch SRS data + entry columns together (avoids N+1)
    cur.execute('''
        SELECT s.entry_id, s.data, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors
        FROM srs s JOIN entries e ON e.id = s.entry_id
    ''')
    rows = cur.fetchall()
    
    weak_vectors = []  # stability < threshold (but > 0, in SRS)
    all_vectors = []   # all vectors with stability > 0
    
    for entry_id, data_str, entry_kind, kanji, kana, gloss, study_vectors_str in rows:
        try:
            data_json = json.loads(data_str or '{}')
        except Exception:
            data_json = {}
        
        per_kind = data_json.get('per_kind') or {}
        
        # Skip entries without per_kind data (not yet in SRS)
        if not per_kind:
            continue
        
        for kind, kind_data in per_kind.items():
            per_vector = kind_data.get('per_vector', {})
            
            # Generate vectors from pre-fetched data (no extra DB query)
            vectors = _learning.make_vectors_from_row(entry_id, entry_kind, kanji, kana, gloss, study_vectors_str)
            
            for vec in vectors:
                vector_type = vec.get('vector_type')
                
                # Check exclusion per vector_type, not per kind
                vector_id = f"{entry_id}:{vector_type}"
                if vector_id in exclude_vector_ids:
                    continue
                
                # Get stability from per_vector if available, otherwise use per_kind
                vector_srs = per_vector.get(vector_type, {})
                stability = vector_srs.get('stability', None)
                if stability is None:
                    # Fall back to per_kind stability
                    stability = float(kind_data.get('stability', 0) or 0)
                else:
                    stability = float(stability or 0)
                
                # Skip vectors not in SRS yet (stability == 0)
                if stability == 0:
                    continue
                
                # Check if this is a forced drill item (leech)
                is_forced = kind_data.get('flag_forced_drill', False)
                
                # Use the full vector structure from make_vectors_for_entries
                # but update stability from SRS data and reset streak to 0 for drill
                vector_info = vec.copy()
                vector_info['stability'] = stability
                vector_info['streak'] = 0  # Always start at 0 for drill sessions
                
                # Add to appropriate list
                # Include if: stability < threshold OR (forced flag AND stability >= threshold)
                if stability < threshold:
                    weak_vectors.append(vector_info)
                elif is_forced and stability >= threshold:
                    # Leech: high stability but repeatedly failing
                    weak_vectors.append(vector_info)
                    Logger.info(f'WeakVectors: Including forced drill leech - entry {entry_id}, stability {stability:.2f}')
                all_vectors.append(vector_info)
    
    # Log final vector lists for debugging
    Logger.info(f'WeakVectors: weak_vectors ({len(weak_vectors)}): {[(v["entry_id"], v["kind"], v["vector_type"], v["stability"]) for v in weak_vectors]}')
    Logger.info(f'WeakVectors: all_vectors ({len(all_vectors)}): {[(v["entry_id"], v["kind"], v["vector_type"], v["stability"]) for v in all_vectors]}')
    Logger.info(f'WeakVectors: threshold={threshold}, exclude_vector_ids count={len(exclude_vector_ids)}')    
    # Sort by stability (lowest first)
    weak_vectors.sort(key=lambda v: v['stability'])
    all_vectors.sort(key=lambda v: v['stability'])
    
    # Selection logic: prioritize weak, fill with next weakest if < 7
    selected = weak_vectors[:limit]
    
    if len(selected) < 7:
        # Need to fill with next weakest
        needed = limit - len(selected)
        # Get vectors not already in selected
        selected_ids = {f"{v['entry_id']}:{v['vector_type']}" for v in selected}
        remaining = [v for v in all_vectors if f"{v['entry_id']}:{v['vector_type']}" not in selected_ids]
        selected.extend(remaining[:needed])
    
    return selected[:limit]


def count_weak_vectors_available(conn: sqlite3.Connection, threshold: int = 3, exclude_vector_ids: set = None) -> int:
    """Count how many weak vectors are available for drilling.
    
    Returns count of vectors with:
    - stability < threshold (normally weak), OR
    - flag_forced_drill = True AND stability >= threshold (leeches)
    Excludes already drilled vectors.
    Does NOT include filler logic - just counts actual weak vectors.
    """
    if exclude_vector_ids is None:
        exclude_vector_ids = set()
    
    cur = conn.cursor()
    # Single JOIN query: fetch SRS data + entry columns together (avoids N+1)
    cur.execute('''
        SELECT s.entry_id, s.data, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors
        FROM srs s JOIN entries e ON e.id = s.entry_id
    ''')
    rows = cur.fetchall()
    
    weak_count = 0
    
    for entry_id, data_str, entry_kind, kanji, kana, gloss, study_vectors_str in rows:
        try:
            data_json = json.loads(data_str or '{}')
        except Exception:
            data_json = {}
        
        per_kind = data_json.get('per_kind') or {}
        
        if not per_kind:
            continue
        
        for kind, kind_data in per_kind.items():
            per_vector = kind_data.get('per_vector', {})
            
            # Generate vectors from pre-fetched data (no extra DB query)
            vectors = _learning.make_vectors_from_row(entry_id, entry_kind, kanji, kana, gloss, study_vectors_str)
            
            for vec in vectors:
                vector_type = vec.get('vector_type')
                
                # Check exclusion
                vector_id = f"{entry_id}:{vector_type}"
                if vector_id in exclude_vector_ids:
                    continue
                
                # Get stability from per_vector if available, otherwise use per_kind
                vector_srs = per_vector.get(vector_type, {})
                stability = vector_srs.get('stability', None)
                if stability is None:
                    stability = float(kind_data.get('stability', 0) or 0)
                else:
                    stability = float(stability or 0)
                
                # Check if this is a forced drill item (leech)
                is_forced = kind_data.get('flag_forced_drill', False)
                
                # Count if: stability > 0 AND (stability < threshold OR forced leech)
                if 0 < stability < threshold:
                    weak_count += 1
                elif stability > 0 and is_forced and stability >= threshold:
                    weak_count += 1
    
    return weak_count


def get_tools_drill_weak_vectors(conn: sqlite3.Connection, limit: int = 14) -> list:
    """Get random weak vectors for Tools Drill Mode.
    
    This is separate from study screen weak words:
    - Threshold: stability < 4 (instead of < 3)
    - ALSO includes flag_forced_drill items regardless of stability
    - Random selection (not ordered by weakest)
    - No exclusion tracking (no daily limits)
    - Used for extra practice mode in Tools
    
    Args:
        conn: Database connection
        limit: Max number of vectors to return
    
    Returns:
        List of random weak vector dicts
    """
    import random
    
    cursor = conn.cursor()
    # Single JOIN query: fetch SRS data + entry columns together (avoids N+1)
    cursor.execute('''
        SELECT s.entry_id, s.data, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors
        FROM srs s JOIN entries e ON e.id = s.entry_id
    ''')
    srs_rows = cursor.fetchall()
    
    weak_vectors = []
    
    for entry_id, data_str, entry_kind, kanji, kana, gloss, study_vectors_str in srs_rows:
        try:
            srs_data = json.loads(data_str or '{}')
        except Exception:
            srs_data = {}
        
        per_kind = srs_data.get('per_kind', {})
        
        if not per_kind:
            continue
        
        for kind, kind_data in per_kind.items():
            per_vector = kind_data.get('per_vector', {})
            
            # Generate vectors from pre-fetched data (no extra DB query)
            vectors = _learning.make_vectors_from_row(entry_id, entry_kind, kanji, kana, gloss, study_vectors_str)
            
            for vec in vectors:
                vector_type = vec.get('vector_type')
                vector_srs = per_vector.get(vector_type, {})
                stability = vector_srs.get('stability', 0)
                
                # Check if this is a forced drill item
                is_forced = kind_data.get('flag_forced_drill', False)
                
                # Include if: (stability < 4 and > 0) OR forced flag
                if 0 < stability < 4:
                    vector_info = vec.copy()
                    vector_info['stability'] = stability
                    vector_info['streak'] = 0
                    weak_vectors.append(vector_info)
                elif stability > 0 and is_forced:
                    # Forced leech - include regardless of stability
                    vector_info = vec.copy()
                    vector_info['stability'] = stability
                    vector_info['streak'] = 0
                    weak_vectors.append(vector_info)
    
    # Shuffle for random selection
    random.shuffle(weak_vectors)
    
    return weak_vectors[:limit]


def count_tools_drill_weak_vectors(conn: sqlite3.Connection) -> int:
    """Count total weak vectors available for Tools Drill Mode.
    
    Includes:
    - Vectors with stability < 4, OR
    - Vectors with flag_forced_drill = True (regardless of stability)
    
    Optimized: counts directly from SRS JSON without building full vector objects.
    """
    cur = conn.cursor()
    cur.execute('''
        SELECT s.entry_id, s.data, e.kind, e.kanji, e.kana, e.gloss, e.study_vectors
        FROM srs s JOIN entries e ON e.id = s.entry_id
    ''')
    rows = cur.fetchall()
    
    count = 0
    for entry_id, data_str, entry_kind, kanji, kana, gloss, study_vectors_str in rows:
        try:
            srs_data = json.loads(data_str or '{}')
        except Exception:
            continue
        
        per_kind = srs_data.get('per_kind', {})
        if not per_kind:
            continue
        
        for kind, kind_data in per_kind.items():
            per_vector = kind_data.get('per_vector', {})
            is_forced = kind_data.get('flag_forced_drill', False)
            
            # Generate vector types from pre-fetched data
            vectors = _learning.make_vectors_from_row(entry_id, entry_kind, kanji, kana, gloss, study_vectors_str)
            
            for vec in vectors:
                vector_type = vec.get('vector_type')
                vector_srs = per_vector.get(vector_type, {})
                stability = vector_srs.get('stability', 0)
                
                if 0 < stability < 4:
                    count += 1
                elif stability > 0 and is_forced:
                    count += 1
    
    return count


# ============================================================================
# Custom Lessons CRUD Functions
# ============================================================================

def create_custom_lesson(conn: sqlite3.Connection, name: str, is_collection: bool = False, parent_id: int = None) -> int:
    """Create a new custom lesson or collection and return its ID."""
    ensure_custom_lessons_tables(conn)
    cur = conn.cursor()
    
    # Get max display_order to put new lesson at end
    cur.execute('SELECT MAX(display_order) FROM custom_lessons')
    max_order = cur.fetchone()[0]
    next_order = (max_order or 0) + 1
    
    cur.execute(
        'INSERT INTO custom_lessons (name, created_at, display_order, is_collection, parent_id) VALUES (?, ?, ?, ?, ?)',
        (name, datetime.utcnow().isoformat(), next_order, 1 if is_collection else 0, parent_id)
    )
    conn.commit()
    return cur.lastrowid


def get_custom_lesson(conn: sqlite3.Connection, lesson_id: int) -> dict | None:
    """Get a custom lesson by ID with its items."""
    ensure_custom_lessons_tables(conn)
    cur = conn.cursor()
    cur.execute('SELECT id, name, created_at, display_order FROM custom_lessons WHERE id = ?', (lesson_id,))
    row = cur.fetchone()
    
    if not row:
        return None
    
    lesson_id, name, created_at, display_order = row
    
    # Get items for this lesson
    cur.execute(
        '''
        SELECT entry_id, position, reading_material_id FROM custom_lesson_items 
        WHERE lesson_id = ? 
        ORDER BY position
        ''',
        (lesson_id,)
    )
    items = [{'entry_id': r[0], 'position': r[1], 'reading_material_id': r[2]} for r in cur.fetchall()]
    
    return {
        'id': lesson_id,
        'name': name,
        'created_at': created_at,
        'display_order': display_order,
        'items': items
    }


def get_all_custom_lessons(conn: sqlite3.Connection) -> list[dict]:
    """Get all custom lessons ordered by display_order."""
    ensure_custom_lessons_tables(conn)
    cur = conn.cursor()
    cur.execute('SELECT id, name, created_at, display_order, is_collection, parent_id FROM custom_lessons ORDER BY display_order')
    
    lessons = []
    for row in cur.fetchall():
        lesson_id, name, created_at, display_order, is_collection, parent_id = row
        
        # Count items
        cur.execute('SELECT COUNT(*) FROM custom_lesson_items WHERE lesson_id = ?', (lesson_id,))
        item_count = cur.fetchone()[0]
        
        lessons.append({
            'id': lesson_id,
            'name': name,
            'created_at': created_at,
            'display_order': display_order,
            'item_count': item_count,
            'is_collection': bool(is_collection),
            'parent_id': parent_id
        })
    
    return lessons


def update_custom_lesson_name(conn: sqlite3.Connection, lesson_id: int, name: str) -> None:
    """Update a custom lesson's name."""
    cur = conn.cursor()
    cur.execute('UPDATE custom_lessons SET name = ? WHERE id = ?', (name, lesson_id))
    conn.commit()


def _mark_custom_lesson_deleted_for_sync(conn: sqlite3.Connection, lesson_id: int) -> None:
    """Record a tombstone so sync can delete the remote custom lesson row."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sync_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute(
        'INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)',
        (f'deleted_custom_lesson:{int(lesson_id)}', datetime.utcnow().isoformat())
    )


def delete_custom_lesson(conn: sqlite3.Connection, lesson_id: int) -> None:
    """Delete a custom lesson and all its items (CASCADE handles items)."""
    _mark_custom_lesson_deleted_for_sync(conn, lesson_id)
    cur = conn.cursor()
    cur.execute('DELETE FROM custom_lessons WHERE id = ?', (lesson_id,))
    conn.commit()


def delete_all_custom_lessons(conn: sqlite3.Connection) -> int:
    """Delete ALL custom lessons and return count of deleted lessons."""
    cur = conn.cursor()
    try:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cur.execute(
            'INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)',
            ('custom_lessons_erased', datetime.utcnow().isoformat())
        )
    except Exception:
        pass
    cur.execute('SELECT id FROM custom_lessons')
    lesson_ids = [row[0] for row in cur.fetchall()]
    count = len(lesson_ids)
    for lesson_id in lesson_ids:
        _mark_custom_lesson_deleted_for_sync(conn, lesson_id)
    cur.execute('DELETE FROM custom_lessons')
    conn.commit()
    return count


def add_item_to_custom_lesson(conn: sqlite3.Connection, lesson_id: int, entry_id: int) -> None:
    """Add an entry to a custom lesson (at the end)."""
    ensure_custom_lessons_tables(conn)
    cur = conn.cursor()
    
    # Check if already exists
    cur.execute(
        'SELECT id FROM custom_lesson_items WHERE lesson_id = ? AND entry_id = ?',
        (lesson_id, entry_id)
    )
    if cur.fetchone():
        return  # Already in lesson
    
    # Get max position to add at end
    cur.execute('SELECT MAX(position) FROM custom_lesson_items WHERE lesson_id = ?', (lesson_id,))
    max_pos = cur.fetchone()[0]
    next_pos = (max_pos or 0) + 1
    
    cur.execute(
        'INSERT INTO custom_lesson_items (lesson_id, entry_id, position) VALUES (?, ?, ?)',
        (lesson_id, entry_id, next_pos)
    )
    conn.commit()


def remove_item_from_custom_lesson(conn: sqlite3.Connection, lesson_id: int, entry_id: int) -> None:
    """Remove an entry from a custom lesson."""
    cur = conn.cursor()
    cur.execute(
        'DELETE FROM custom_lesson_items WHERE lesson_id = ? AND entry_id = ?',
        (lesson_id, entry_id)
    )
    conn.commit()


def get_custom_lesson_entry_ids(conn: sqlite3.Connection, lesson_id: int) -> list[int]:
    """Get list of entry IDs for a custom lesson, ordered by position."""
    cur = conn.cursor()
    cur.execute(
        'SELECT entry_id FROM custom_lesson_items WHERE lesson_id = ? ORDER BY position',
        (lesson_id,)
    )
    return [r[0] for r in cur.fetchall()]


# ===== Time Attack High Scores =====

def ensure_time_attack_table(conn: sqlite3.Connection) -> None:
    """Create time_attack_high_scores table if it doesn't exist."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS time_attack_high_scores (
            id INTEGER PRIMARY KEY,
            attack_type TEXT NOT NULL,
            lesson_id INTEGER,
            mode TEXT NOT NULL,
            score INTEGER NOT NULL,
            accuracy REAL,
            timestamp TEXT NOT NULL,
            UNIQUE(attack_type, lesson_id, mode)
        )
    ''')
    conn.commit()


def get_time_attack_high_score(conn: sqlite3.Connection, attack_type: str, lesson_id: int | None, mode: str) -> int:
    """Get high score for a specific time attack configuration."""
    ensure_time_attack_table(conn)
    cur = conn.cursor()
    cur.execute(
        'SELECT score FROM time_attack_high_scores WHERE attack_type = ? AND lesson_id IS ? AND mode = ?',
        (attack_type, lesson_id, mode)
    )
    result = cur.fetchone()
    return result[0] if result else 0


def update_time_attack_high_score(conn: sqlite3.Connection, attack_type: str, lesson_id: int | None, mode: str, score: int, accuracy: float) -> None:
    """Update high score if new score is higher."""
    from datetime import datetime
    ensure_time_attack_table(conn)
    cur = conn.cursor()
    
    # Get current high score
    current = get_time_attack_high_score(conn, attack_type, lesson_id, mode)
    
    if score > current:
        cur.execute('''
            INSERT OR REPLACE INTO time_attack_high_scores 
            (attack_type, lesson_id, mode, score, accuracy, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (attack_type, lesson_id, mode, score, accuracy, datetime.utcnow().isoformat()))
        conn.commit()


# ============================================================
# DRILL STATS
# ============================================================

def ensure_drill_stats_table(conn: sqlite3.Connection) -> None:
    """Create drill_stats table to track completed drill/time-attack sessions."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS drill_stats (
            id INTEGER PRIMARY KEY,
            session_type TEXT NOT NULL,
            source_type TEXT,
            vectors_completed INTEGER DEFAULT 0,
            total_vectors INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            total_attempts INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            accuracy REAL DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()


def save_drill_stat(conn: sqlite3.Connection, session_type: str, source_type: str,
                    vectors_completed: int, total_vectors: int,
                    correct_count: int, total_attempts: int,
                    duration_seconds: float, accuracy: float) -> None:
    """Insert one completed drill stat row."""
    from datetime import datetime
    ensure_drill_stats_table(conn)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO drill_stats
        (session_type, source_type, vectors_completed, total_vectors,
         correct_count, total_attempts, duration_seconds, accuracy, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_type, source_type, vectors_completed, total_vectors,
          correct_count, total_attempts, duration_seconds, accuracy,
          datetime.utcnow().isoformat()))
    conn.commit()


def get_drill_stats_summary(conn: sqlite3.Connection) -> dict:
    """Return aggregate drill statistics."""
    ensure_drill_stats_table(conn)
    cur = conn.cursor()
    summary = {}

    def _stats_for(session_type: str, prefix: str):
        cur.execute(f'''
            SELECT COUNT(*),
                   COALESCE(SUM(vectors_completed), 0),
                   COALESCE(AVG(vectors_completed), 0),
                   COALESCE(SUM(correct_count), 0),
                   COALESCE(AVG(correct_count), 0),
                   COALESCE(SUM(total_attempts), 0),
                   COALESCE(AVG(total_attempts), 0),
                   COALESCE(AVG(duration_seconds), 0),
                   COALESCE(AVG(accuracy), 0)
            FROM drill_stats WHERE session_type = ?
        ''', (session_type,))
        r = cur.fetchone()
        summary[f'{prefix}_sessions'] = r[0]
        summary[f'{prefix}_total_cards'] = r[1]
        summary[f'{prefix}_avg_cards'] = round(r[2], 1)
        summary[f'{prefix}_total_correct'] = r[3]
        summary[f'{prefix}_avg_correct'] = round(r[4], 1)
        summary[f'{prefix}_total_attempts'] = r[5]
        summary[f'{prefix}_avg_attempts'] = round(r[6], 1)
        summary[f'{prefix}_avg_duration'] = round(r[7], 1)
        summary[f'{prefix}_avg_accuracy'] = round(r[8], 1)

    _stats_for('drill', 'drill')
    _stats_for('time_attack', 'ta')

    # Overall
    cur.execute('''
        SELECT COALESCE(AVG(accuracy), 0), COUNT(*)
        FROM drill_stats
    ''')
    r = cur.fetchone()
    summary['overall_accuracy'] = round(r[0], 1)
    summary['total_sessions'] = r[1]

    return summary


# ============================================================
# TATOEBA EXAMPLE SENTENCES
# ============================================================

def get_tatoeba_sentences(conn: sqlite3.Connection, entry_id: int = None, 
                          jid: int = None, word: str = None, 
                          reading: str = None, limit: int = 10) -> List[dict]:
    """Get example sentences from Tatoeba for an entry.
    
    Can look up by:
    - entry_id: Our internal entry ID (will look up jid from entries table)
    - jid: Direct JMDict sequence number
    - word/reading: Text-based lookup
    
    Returns list of dicts with: sentence_id, japanese, english, word, reading
    """
    cur = conn.cursor()
    
    # First check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tatoeba_sentences'")
    if not cur.fetchone():
        return []
    
    # Determine jmdict_id to search for
    jmdict_id = jid
    search_word = word
    search_reading = reading
    
    if entry_id and not jmdict_id:
        # Look up jid from entries table
        cur.execute('SELECT jid, kanji, kana FROM entries WHERE rowid = ?', (entry_id,))
        row = cur.fetchone()
        if row:
            jmdict_id = row[0]
            if not search_word:
                search_word = row[1] or row[2]
            if not search_reading:
                search_reading = row[2]
    
    results = []
    
    # Try jmdict_id lookup first (most accurate)
    if jmdict_id:
        cur.execute('''
            SELECT sentence_id, japanese, english, word, reading
            FROM tatoeba_sentences
            WHERE jmdict_id = ?
            GROUP BY sentence_id
            ORDER BY CASE WHEN english IS NOT NULL THEN 0 ELSE 1 END, 
                     LENGTH(japanese), sentence_id
            LIMIT ?
        ''', (jmdict_id, limit))
        results = cur.fetchall()
    
    # Fall back to word lookup if no results
    if not results and search_word:
        if search_reading:
            cur.execute('''
                SELECT sentence_id, japanese, english, word, reading
                FROM tatoeba_sentences
                WHERE word = ? AND (reading = ? OR reading = '')
                GROUP BY sentence_id
                ORDER BY CASE WHEN english IS NOT NULL THEN 0 ELSE 1 END,
                         LENGTH(japanese), sentence_id
                LIMIT ?
            ''', (search_word, search_reading, limit))
        else:
            cur.execute('''
                SELECT sentence_id, japanese, english, word, reading
                FROM tatoeba_sentences
                WHERE word = ?
                GROUP BY sentence_id
                ORDER BY CASE WHEN english IS NOT NULL THEN 0 ELSE 1 END,
                         LENGTH(japanese), sentence_id
                LIMIT ?
            ''', (search_word, limit))
        results = cur.fetchall()
    
    return [
        {
            'sentence_id': r[0],
            'japanese': r[1],
            'english': r[2],
            'word': r[3],
            'reading': r[4]
        }
        for r in results
    ]


def get_user_stats_summary(conn: sqlite3.Connection) -> dict:
    """Return a comprehensive summary of the user's study progress across all sources."""
    import json
    cur = conn.cursor()
    stats = {}

    # ── SRS overview ─────────────────────────────────────────
    cur.execute('SELECT COUNT(*) FROM srs')
    stats['srs_total'] = cur.fetchone()[0]

    cur.execute('SELECT COUNT(*) FROM srs WHERE reps > 0')
    stats['srs_studied'] = cur.fetchone()[0]

    # Known = interval >= 21 days (solidly in long-term memory)
    cur.execute('SELECT COUNT(*) FROM srs WHERE interval >= 21')
    stats['srs_known'] = cur.fetchone()[0]

    # Good = interval >= 7 days
    cur.execute('SELECT COUNT(*) FROM srs WHERE interval >= 7')
    stats['srs_good'] = cur.fetchone()[0]

    # Total review cards (sum of top-level reps = proxy for total reviews done)
    cur.execute('SELECT COALESCE(SUM(reps), 0) FROM srs')
    stats['srs_total_review_reps'] = cur.fetchone()[0]

    # Per-kind breakdown (join srs with entries)
    cur.execute('''
        SELECT e.kind, COUNT(*) as cnt, SUM(s.reps) as total_reps,
               SUM(CASE WHEN s.reps > 0 THEN 1 ELSE 0 END) as studied,
               SUM(CASE WHEN s.interval >= 21 THEN 1 ELSE 0 END) as known
        FROM srs s
        JOIN entries e ON s.entry_id = e.id
        GROUP BY e.kind
    ''')
    per_kind = {}
    for row in cur.fetchall():
        kind = row[0] or 'vocab'
        per_kind[kind] = {
            'total': row[1], 'reps': row[2] or 0,
            'studied': row[3], 'known': row[4]
        }
    stats['per_kind'] = per_kind

    # Per-vector totals from JSON data (handwriting, reading, meaning, etc.)
    vector_reps = {}
    cur.execute('SELECT data FROM srs WHERE data IS NOT NULL')
    for (data_json,) in cur.fetchall():
        try:
            d = json.loads(data_json)
            for vtype, vdata in d.get('per_vector', {}).items():
                r = int(vdata.get('reps', 0) or 0)
                vector_reps[vtype] = vector_reps.get(vtype, 0) + r
        except Exception:
            pass
    stats['per_vector_reps'] = vector_reps

    # ── Reading stats ─────────────────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reading_stats'")
    if cur.fetchone():
        cur.execute('SELECT COALESCE(SUM(words_read), 0) FROM reading_stats')
        stats['reading_words'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(DISTINCT date) FROM reading_stats WHERE words_read > 0')
        stats['reading_days'] = cur.fetchone()[0]
        # New listening/dictation columns (may not exist yet)
        for col, key in [('sections_listened', 'sections_listened'),
                         ('words_dictated', 'words_dictated'),
                         ('listening_time', 'listening_time'),
                         ('sections_dictated', 'sections_dictated')]:
            try:
                cur.execute(f'SELECT COALESCE(SUM({col}), 0) FROM reading_stats')
                stats[key] = cur.fetchone()[0]
            except Exception:
                stats[key] = 0
    else:
        stats['reading_words'] = 0
        stats['reading_days'] = 0
        stats['sections_listened'] = 0
        stats['words_dictated'] = 0
        stats['listening_time'] = 0
        stats['sections_dictated'] = 0

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='custom_reading_materials'")
    if cur.fetchone():
        cur.execute('SELECT COUNT(*), COALESCE(SUM(total_read_time), 0), COALESCE(SUM(times_read), 0) FROM custom_reading_materials')
        row = cur.fetchone()
        stats['reading_texts'] = row[0]
        stats['reading_seconds'] = row[1]
        stats['reading_times_read'] = row[2]
    else:
        stats['reading_texts'] = 0
        stats['reading_seconds'] = 0
        stats['reading_times_read'] = 0

    # ── Drill stats ───────────────────────────────────────────
    ensure_drill_stats_table(conn)
    cur.execute('''
        SELECT session_type,
               COUNT(*) as sessions,
               COALESCE(SUM(vectors_completed), 0) as cards,
               COALESCE(SUM(correct_count), 0) as correct,
               COALESCE(SUM(duration_seconds), 0) as secs,
               COALESCE(AVG(accuracy), 0) as avg_acc
        FROM drill_stats
        GROUP BY session_type
    ''')
    for row in cur.fetchall():
        stype = row[0]
        stats[f'{stype}_sessions'] = row[1]
        stats[f'{stype}_cards'] = row[2]
        stats[f'{stype}_correct'] = row[3]
        stats[f'{stype}_seconds'] = row[4]
        stats[f'{stype}_avg_accuracy'] = round(row[5], 1)

    # ── Time Attack high scores ───────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='time_attack_high_scores'")
    if cur.fetchone():
        cur.execute('SELECT COUNT(*), COALESCE(MAX(score), 0) FROM time_attack_high_scores')
        row = cur.fetchone()
        stats['ta_play_count'] = stats.get('time_attack_sessions', row[0])
        stats['ta_best_score'] = row[1]
    else:
        stats['ta_play_count'] = 0
        stats['ta_best_score'] = 0

    # Longest time attack session (seconds)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='drill_stats'")
    if cur.fetchone():
        cur.execute(
            "SELECT COALESCE(MAX(duration_seconds), 0) FROM drill_stats WHERE session_type = 'time_attack'"
        )
        stats['ta_longest_session'] = cur.fetchone()[0]
    else:
        stats['ta_longest_session'] = 0

    return stats


def count_tatoeba_sentences(conn: sqlite3.Connection, entry_id: int = None,
                            jid: int = None, word: str = None) -> int:
    """Count how many Tatoeba sentences exist for an entry."""
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tatoeba_sentences'")
    if not cur.fetchone():
        return 0
    
    jmdict_id = jid
    search_word = word
    
    if entry_id and not jmdict_id:
        cur.execute('SELECT jid, kanji, kana FROM entries WHERE rowid = ?', (entry_id,))
        row = cur.fetchone()
        if row:
            jmdict_id = row[0]
            if not search_word:
                search_word = row[1] or row[2]
    
    if jmdict_id:
        cur.execute('SELECT COUNT(DISTINCT sentence_id) FROM tatoeba_sentences WHERE jmdict_id = ?', (jmdict_id,))
        count = cur.fetchone()[0]
        if count > 0:
            return count
    
    if search_word:
        cur.execute('SELECT COUNT(DISTINCT sentence_id) FROM tatoeba_sentences WHERE word = ?', (search_word,))
        return cur.fetchone()[0]
    
    return 0


# ============================================================
# DAILY ACTIVITY LOG
# ============================================================

def ensure_daily_activity_log_table(conn: sqlite3.Connection) -> None:
    """Create daily_activity_log table if it doesn't exist."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_activity_log (
            date TEXT PRIMARY KEY,
            items_reviewed INTEGER DEFAULT 0,
            items_learned INTEGER DEFAULT 0,
            words_read INTEGER DEFAULT 0
        )
    ''')
    conn.commit()


def record_daily_activity(conn: sqlite3.Connection, reviewed: int = 0, learned: int = 0) -> None:
    """Increment (or decrement, for undo) today's reviewed/learned counters.
    Uses local time to match reading_stats. Values are clamped to >= 0."""
    from datetime import datetime
    ensure_daily_activity_log_table(conn)
    today = datetime.now().strftime('%Y-%m-%d')
    cur = conn.cursor()
    cur.execute(
        '''
        INSERT INTO daily_activity_log (date, items_reviewed, items_learned, words_read)
        VALUES (?, MAX(0, ?), MAX(0, ?), 0)
        ON CONFLICT(date) DO UPDATE SET
            items_reviewed = MAX(0, items_reviewed + excluded.items_reviewed),
            items_learned  = MAX(0, items_learned  + excluded.items_learned)
        ''',
        (today, reviewed, learned)
    )
    conn.commit()


def get_daily_activity(conn: sqlite3.Connection, year: int, month: int) -> dict:
    """Return a dict mapping 'YYYY-MM-DD' -> {reviewed, learned, words_read}
    for all days in the given year/month that have any activity.
    Also merges in reading_stats words_read for the same month."""
    ensure_daily_activity_log_table(conn)
    cur = conn.cursor()
    prefix = f'{year:04d}-{month:02d}'
    cur.execute(
        '''
        SELECT date, items_reviewed, items_learned, words_read
        FROM daily_activity_log
        WHERE date LIKE ?
        ''',
        (f'{prefix}-%',)
    )
    result = {}
    for date, reviewed, learned, words in cur.fetchall():
        result[date] = {
            'reviewed': reviewed or 0,
            'learned': learned or 0,
            'words_read': words or 0,
        }

    # Merge reading_stats words for the month
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reading_stats'")
    if cur.fetchone():
        # Build column list (some may not exist yet)
        extra_cols = []
        for col in ('sections_listened', 'words_dictated', 'sections_dictated'):
            try:
                cur.execute(f'SELECT {col} FROM reading_stats LIMIT 0')
                extra_cols.append(col)
            except Exception:
                pass
        
        select_cols = 'date, words_read' + (', ' + ', '.join(extra_cols) if extra_cols else '')
        cur.execute(
            f'SELECT {select_cols} FROM reading_stats WHERE date LIKE ?',
            (f'{prefix}-%',)
        )
        for row in cur.fetchall():
            date = row[0]
            words = row[1] or 0
            entry = result.get(date, {'reviewed': 0, 'learned': 0, 'words_read': 0})
            entry['words_read'] = max(entry.get('words_read', 0), words)
            # Merge extra columns
            for i, col in enumerate(extra_cols):
                entry[col] = (entry.get(col, 0) or 0) + (row[2 + i] or 0)
            result[date] = entry

    return result


# ─── Glossary Table ──────────────────────────────────────────────────────────

_GLOSSARY_DEFAULTS = [
    # (term_en, term_ja, definition, see_also, category)
    ("hiragana", "ひらがな", "One of the two Japanese phonetic writing systems. Used for native Japanese words, grammar, and function words. Has 46 basic characters, each representing a syllable.", None, "writing"),
    ("katakana", "カタカナ", "The second Japanese phonetic writing system. Primarily used for foreign loanwords, onomatopoeia, emphasis, and scientific terms. Has 46 basic characters.", None, "writing"),
    ("kanji", "漢字", "Chinese characters used in Japanese writing. Each kanji can have multiple readings and meanings. There are ~2,136 jōyō (常用) kanji in everyday use.", "jōyō kanji, onyomi, kunyomi", "writing"),
    ("kana", "かな", "A collective term for hiragana and katakana — the two phonetic writing systems of Japanese.", "hiragana, katakana", "writing"),
    ("furigana", "ふりがな", "Small kana written above or beside kanji to show their pronunciation. Also called 'ruby text'.", "kanji", "writing"),
    ("romaji", "ローマ字", "The representation of Japanese sounds using the Latin alphabet. Used for input on keyboards and in some signage.", None, "writing"),
    ("okurigana", "送り仮名", "The hiragana characters that follow a kanji stem, showing the inflectional ending of a word (e.g. 食べる — べる is okurigana).", "kanji", "writing"),
    ("dakuten", "濁点", "The two-dot diacritical mark (゛) added to certain kana to voice the consonant sound (e.g. か→が, た→だ, は→ば).", "handakuten", "phonetics"),
    ("handakuten", "半濁点", "The small circle diacritical mark (゜) added to は-row kana to create a 'p' sound (は→ぱ, ひ→ぴ, etc.).", "dakuten", "phonetics"),
    ("sokuon", "促音", "A small っ/ッ that indicates a double (geminate) consonant — a brief pause before the next consonant sound (e.g. きっぷ kippu, がっこう gakkō).", None, "phonetics"),
    ("yōon", "拗音", "Combination sounds formed by pairing an i-column kana with a small ゃ, ゅ, or ょ (e.g. きゃ kya, しゅ shu, ちょ cho). Each combination counts as one syllable.", None, "phonetics"),
    ("chōon", "長音", "A long vowel sound. In hiragana, indicated by adding a vowel kana; in katakana, indicated by a dash (ー).", None, "phonetics"),
    ("mora", "モーラ", "The basic unit of sound timing in Japanese. Each kana character represents one mora, including ん and small っ. Unlike syllables, every mora takes roughly equal time.", None, "phonetics"),
    ("pitch accent", "高低アクセント", "The pattern of high and low pitch across the morae of a Japanese word. Unlike English stress accent, Japanese uses pitch to distinguish otherwise identical words.", None, "phonetics"),
    ("onyomi", "音読み", "The Sino-Japanese (Chinese-derived) reading of a kanji. Often used in compound words (熟語). A single kanji may have multiple onyomi.", "kunyomi", "phonetics"),
    ("kunyomi", "訓読み", "The native Japanese reading of a kanji. Often used when the kanji appears alone or with okurigana.", "onyomi", "phonetics"),
    ("rendaku", "連濁", "Sequential voicing — when the first consonant of the second element in a compound word becomes voiced (e.g. ひと + ひと → ひとびと).", None, "phonetics"),
    ("particle", "助詞", "A function word that follows nouns, verbs, or phrases to mark grammatical roles like subject (が), object (を), topic (は), or direction (に/へ).", None, "grammar"),
    ("copula", "繋辞", "A linking word that connects a subject to a description or identity. In Japanese: です (polite) and だ (plain).", None, "grammar"),
    ("predicate", "述語", "The part of a sentence that states something about the subject — typically the verb, adjective, or noun+copula at the end of the clause.", None, "grammar"),
    ("conjugation", "活用", "The systematic changing of a verb or adjective's ending to express tense, mood, negation, or politeness (e.g. 食べる → 食べます → 食べない → 食べた).", None, "grammar"),
    ("inflection", "活用", "A change in the form of a word (usually the ending) to express a grammatical function. Japanese verbs and adjectives inflect; nouns do not.", "conjugation", "grammar"),
    ("transitive verb", "他動詞", "A verb that takes a direct object — the action is done TO something (e.g. ドアを開ける 'open the door').", "intransitive verb", "grammar"),
    ("intransitive verb", "自動詞", "A verb that does not take a direct object — the action happens on its own (e.g. ドアが開く 'the door opens').", "transitive verb", "grammar"),
    ("godan verb", "五段動詞", "A verb whose stem ends in a consonant sound. The final kana changes across five vowel rows when conjugated (e.g. 書く → 書か/書き/書く/書け/書こ). Also called u-verbs or Group I verbs.", "ichidan verb", "grammar"),
    ("ichidan verb", "一段動詞", "A verb whose stem ends in an 'i' or 'e' sound before る. Conjugated by simply dropping る and adding the ending (e.g. 食べる → 食べ + ます). Also called ru-verbs or Group II verbs.", "godan verb", "grammar"),
    ("i-adjective", "い形容詞", "An adjective that ends in い and conjugates directly (e.g. 大きい → 大きくない, 大きかった). Also called true adjectives.", "na-adjective", "grammar"),
    ("na-adjective", "な形容詞", "An adjective that requires な when modifying a noun (e.g. 静かな部屋). Conjugates like a noun with です/だ. Also called adjectival nouns.", "i-adjective", "grammar"),
    ("counter", "助数詞", "A suffix used when counting objects in Japanese. Different counters are used for different types of things (e.g. ～人 for people, ～匹 for small animals, ～本 for long objects).", None, "grammar"),
    ("honorific", "敬語", "Polite or respectful language forms. Japanese has three main levels: sonkeigo (尊敬語, respectful), kenjōgo (謙譲語, humble), and teineigo (丁寧語, polite).", "keigo", "grammar"),
    ("keigo", "敬語", "The Japanese system of honorific speech with three levels: sonkeigo (尊敬語) elevates the listener's actions, kenjōgo (謙譲語) humbles the speaker's actions, and teineigo (丁寧語) is general politeness (です/ます).", "honorific", "grammar"),
    ("topic", "主題", "What the sentence is about, marked by は. Different from the grammatical subject (が). In 'As for me, I am a student,' 'as for me' is the topic.", "subject", "grammar"),
    ("subject", "主語", "The grammatical doer of the action, marked by が. Different from the topic (は). Often omitted in Japanese when clear from context.", "topic", "grammar"),
    ("stem", "語幹", "The unchanging base part of a verb or adjective before its inflectional ending. For 食べる, the stem is 食べ. For 大きい, the stem is 大き.", None, "grammar"),
    ("te-form", "て形", "A verb form ending in て/で, used to connect actions, make requests, describe ongoing states, and form many grammar patterns (e.g. 食べて, 書いて, 飲んで).", None, "grammar"),
    ("plain form", "普通形", "The casual, dictionary-style form of verbs and adjectives used in informal speech, subordinate clauses, and before many grammar patterns. Contrasts with the polite ます/です forms.", "polite form", "grammar"),
    ("polite form", "丁寧形", "The です/ます style of speech used in polite conversation, with strangers, or in formal settings. Contrasts with the plain/casual form.", "plain form", "grammar"),
    ("conditional", "条件形", "A verb/adjective form expressing 'if' or 'when.' Japanese has several: ～ば, ～たら, ～と, and ～なら, each with different nuances.", None, "grammar"),
    ("passive", "受身形", "A verb form where the subject receives the action rather than performing it (e.g. 食べられる 'is eaten'). In Japanese, also used for the 'suffering passive' (迷惑の受身).", None, "grammar"),
    ("causative", "使役形", "A verb form meaning 'to make/let someone do' (e.g. 食べさせる 'make someone eat / let someone eat').", None, "grammar"),
    ("potential", "可能形", "A verb form meaning 'can do' (e.g. 食べられる 'can eat', 書ける 'can write').", None, "grammar"),
    ("volitional", "意志形", "A verb form expressing intention or suggestion — 'let's' or 'I shall' (e.g. 食べよう 'let's eat', 行こう 'let's go').", None, "grammar"),
    ("jōyō kanji", "常用漢字", "The 2,136 'regular-use kanji' designated by the Japanese government as the standard set for everyday reading and writing.", "kanji", "study"),
    ("JLPT", None, "Japanese Language Proficiency Test — the standardized test of Japanese ability. Has 5 levels: N5 (beginner) through N1 (advanced). Sakubo's content is organized by JLPT level.", None, "study"),
    ("SRS", None, "Spaced Repetition System — a study method that shows items at increasing intervals based on how well you know them. Items you struggle with appear more often.", None, "study"),
    ("compound word", "熟語", "A word formed by combining two or more kanji, where the meaning comes from the combination (e.g. 電話 = 電 'electricity' + 話 'talk' = 'telephone').", None, "study"),
    ("loanword", "外来語", "A word borrowed from another language, usually written in katakana (e.g. コーヒー from 'coffee', パン from Portuguese 'pão').", "katakana", "study"),
    ("onomatopoeia", "擬音語", "Words that imitate sounds (e.g. ワンワン 'woof woof', ザーザー 'heavy rain sound'). Japanese also has gitaigo (擬態語) — mimetic words for states and feelings (e.g. キラキラ 'sparkly').", None, "study"),
    ("radical", "部首", "A component of a kanji character used for classification and lookup in dictionaries. Kanji are organized under 214 traditional radicals (e.g. 氵water, 木 tree, 人 person).", "kanji", "study"),
    ("stroke order", "筆順", "The standardized sequence in which the strokes of a kanji or kana character should be written. Following proper stroke order helps with legibility and recognition.", None, "study"),
    ("SOV", None, "Subject-Object-Verb — the basic Japanese word order. Unlike English (SVO), the verb comes at the end: 猫が魚を食べる (Cat-fish-eats).", None, "grammar"),
    ("clause", "節", "A group of words containing a subject and predicate that forms part of a sentence. Japanese subordinate clauses precede the main clause.", None, "grammar"),
    ("modifier", "修飾語", "A word or phrase that describes or qualifies another word. In Japanese, modifiers always come BEFORE what they modify.", None, "grammar"),
]


def ensure_glossary_table(conn: sqlite3.Connection) -> None:
    """Create the glossary table and seed default terms if empty."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS glossary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term_en TEXT NOT NULL UNIQUE,
            term_ja TEXT,
            definition TEXT NOT NULL,
            see_also TEXT,
            category TEXT DEFAULT 'general'
        )
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_glossary_term_en
        ON glossary(term_en COLLATE NOCASE)
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_glossary_category
        ON glossary(category)
    ''')
    # Seed defaults if the table is empty
    cur.execute('SELECT COUNT(*) FROM glossary')
    if cur.fetchone()[0] == 0:
        cur.executemany(
            'INSERT OR IGNORE INTO glossary (term_en, term_ja, definition, see_also, category) VALUES (?, ?, ?, ?, ?)',
            _GLOSSARY_DEFAULTS
        )
    conn.commit()


def get_all_glossary_terms(conn: sqlite3.Connection) -> list:
    """Return all glossary terms sorted alphabetically by term_en."""
    cur = conn.cursor()
    cur.execute('SELECT id, term_en, term_ja, definition, see_also, category FROM glossary ORDER BY term_en COLLATE NOCASE')
    return [
        {'id': r[0], 'term_en': r[1], 'term_ja': r[2], 'definition': r[3],
         'see_also': r[4], 'category': r[5]}
        for r in cur.fetchall()
    ]


def search_glossary(conn: sqlite3.Connection, query: str) -> list:
    """Search glossary by English term, Japanese term, or definition text."""
    q = f'%{query}%'
    cur = conn.cursor()
    cur.execute('''
        SELECT id, term_en, term_ja, definition, see_also, category
        FROM glossary
        WHERE term_en LIKE ? OR term_ja LIKE ? OR definition LIKE ?
        ORDER BY term_en COLLATE NOCASE
    ''', (q, q, q))
    return [
        {'id': r[0], 'term_en': r[1], 'term_ja': r[2], 'definition': r[3],
         'see_also': r[4], 'category': r[5]}
        for r in cur.fetchall()
    ]


def get_glossary_term(conn: sqlite3.Connection, term_en: str) -> dict | None:
    """Look up a single glossary term by exact English key (case-insensitive)."""
    cur = conn.cursor()
    cur.execute('''
        SELECT id, term_en, term_ja, definition, see_also, category
        FROM glossary WHERE term_en = ? COLLATE NOCASE
    ''', (term_en,))
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'term_en': r[1], 'term_ja': r[2], 'definition': r[3],
            'see_also': r[4], 'category': r[5]}
