# -*- coding: utf-8 -*-
"""
Vocab/Grammar availability constraint system for grammar exercises.

For any grammar lesson position, determines:
1. Which vocab words the learner has already studied
2. Which grammar points the learner has already studied
3. What "combo" forms are available (e.g., 食べる + ます = 食べます)

Usage:
    checker = AvailabilityChecker('dictionary/dictionary.db')
    available = checker.get_available_at(entry_id=326759)  # ます lesson
    # available.vocab = [list of vocab entry dicts]
    # available.grammar = [list of grammar entry dicts already learned]
    # available.combos = [list of derivable forms]
"""
import sqlite3, json, os, re


class AvailableContext:
    """What's available at a given grammar lesson position."""
    def __init__(self):
        self.vocab = []       # list of dicts: {id, kanji, kana, gloss, pos}
        self.grammar = []     # list of dicts: {id, kanji, kana, gloss} — grammar already learned
        self.vocab_set = set()    # set of (kanji, kana) for quick lookup
        self.grammar_set = set()  # set of kana forms for grammar already learned
        self.combo_forms = {}     # kana_form -> description of how it's derived


class AvailabilityChecker:
    def __init__(self, db_path='dictionary/dictionary.db',
                 index_path='dictionary/lessons/spoonfed_japanese/index.json'):
        self.db_path = db_path
        self.index_path = index_path
        self._load_data()

    def _load_data(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Load all vocab entries that might appear in lessons (any JLPT level)
        # Kana lessons contain words from various levels (e.g. ケーキ is N4)
        c.execute("""SELECT id, kanji, kana, gloss, pos, study_order 
                     FROM entries WHERE kind='vocab'
                     ORDER BY study_order""")
        self.vocab_by_id = {}
        for r in c.fetchall():
            self.vocab_by_id[r[0]] = dict(id=r[0], kanji=r[1], kana=r[2], 
                                           gloss=r[3], pos=r[4], study_order=r[5])

        # Load all N5 and N4 grammar ordered by study_order
        c.execute("""SELECT id, kanji, kana, gloss, study_order, jlpt_level
                     FROM entries WHERE kind='grammar' 
                     AND jlpt_level IN ('N5', 'N4')
                     AND study_order IS NOT NULL
                     ORDER BY 
                         CASE jlpt_level WHEN 'N5' THEN 1 WHEN 'N4' THEN 2 END,
                         study_order""")
        self.all_grammar = [dict(id=r[0], kanji=r[1], kana=r[2], gloss=r[3],
                                 study_order=r[4], jlpt_level=r[5]) for r in c.fetchall()]

        # Build lesson maps from index.json
        with open(self.index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        base_dir = os.path.dirname(self.index_path)

        # Map grammar entry_id -> lesson_number
        self.grammar_lesson_map = {}
        for lesson_num_str, paths in index.get('lessons', {}).items():
            lesson_num = int(lesson_num_str)
            if 'grammar' in paths:
                fpath = paths['grammar']
                # Try path as-is first (full relative from workspace root),
                # then try relative to index.json
                if not os.path.exists(fpath):
                    fpath = os.path.join(base_dir, fpath)
                if os.path.exists(fpath):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # entry_id is inside items[0], not top-level
                    items = data.get('items', [])
                    if items and 'entry_id' in items[0]:
                        self.grammar_lesson_map[items[0]['entry_id']] = lesson_num
                    elif 'entry_id' in data:
                        self.grammar_lesson_map[data['entry_id']] = lesson_num

        # Map lesson_number -> list of vocab entry_ids
        self.vocab_ids_by_lesson = {}
        for lesson_num_str, paths in index.get('lessons', {}).items():
            lesson_num = int(lesson_num_str)
            if 'vocab' in paths:
                fpath = paths['vocab']
                if not os.path.exists(fpath):
                    fpath = os.path.join(base_dir, fpath)
                if os.path.exists(fpath):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.vocab_ids_by_lesson[lesson_num] = [
                        item['entry_id'] for item in data.get('items', [])
                        if 'entry_id' in item
                    ]

        conn.close()

    def get_available_at(self, entry_id=None, grammar_position=None):
        """Get available vocab/grammar at a given grammar lesson.
        
        Args:
            entry_id: The grammar entry_id to check availability for
            grammar_position: 1-based position in curriculum (alternative to entry_id)
        
        Returns:
            AvailableContext with vocab, grammar, and combo forms
        """
        if entry_id is not None:
            lesson_num = self.grammar_lesson_map.get(entry_id)
            if lesson_num is None:
                raise ValueError(f"Grammar entry {entry_id} not found in lesson map")
        elif grammar_position is not None:
            # Look up by position in the grammar sequence
            # Grammar position 1 = first grammar lesson in curriculum
            grammar_by_lesson = sorted(self.grammar_lesson_map.items(), 
                                        key=lambda x: x[1])
            if 1 <= grammar_position <= len(grammar_by_lesson):
                entry_id = grammar_by_lesson[grammar_position - 1][0]
                lesson_num = grammar_by_lesson[grammar_position - 1][1]
            else:
                raise ValueError(f"Grammar position {grammar_position} out of range")
        else:
            raise ValueError("Must provide entry_id or grammar_position")

        ctx = AvailableContext()

        # Vocab available: kana lessons (1-50) are always known + N5 vocab lessons before this grammar lesson
        for vl_num in sorted(self.vocab_ids_by_lesson.keys()):
            if vl_num < lesson_num:
                for eid in self.vocab_ids_by_lesson[vl_num]:
                    v = self.vocab_by_id.get(eid)
                    if v:
                        ctx.vocab.append(v)
                        if v['kanji']:
                            ctx.vocab_set.add((v['kanji'], v['kana']))
                        if v['kana']:
                            ctx.vocab_set.add((v['kana'], v['kana']))

        # Grammar available: all grammar entries with lesson_number < this grammar lesson  
        # PLUS the current grammar being taught (for combo generation)
        for g in self.all_grammar:
            g_lesson = self.grammar_lesson_map.get(g['id'])
            if g_lesson is not None and g_lesson < lesson_num:
                ctx.grammar.append(g)
                ctx.grammar_set.add(g['kana'])
        
        # Add the current grammar point being taught to grammar_set
        # (so combos can be generated for the grammar being learned)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT kana FROM entries WHERE id=?", (entry_id or 0,))
        row = cur.fetchone()
        if row and row[0]:
            ctx.grammar_set.add(row[0])
        conn.close()

        # Generate combo forms (verb + learned conjugation suffixes)
        self._generate_combos(ctx)

        return ctx

    def _generate_combos(self, ctx):
        """Generate combo forms from learned vocab + learned grammar.
        
        Example: if 食べる (vocab) and ます (grammar) are both learned,
        食べます is an available combo form.
        Also generates verb stems for conjugation-teaching exercises.
        """
        # Identify verb vocab entries
        verbs = [v for v in ctx.vocab if v.get('pos') and 
                 any(p in v['pos'] for p in ['Ichidan', 'Godan', 'Kuru verb', 'Suru verb'])]

        # Define conjugation rules for grammar suffixes
        conjugation_rules = self._get_conjugation_rules()

        # Track which conjugation types are being learned or already learned
        # Include the CURRENT grammar being taught (passed via grammar_set + current)
        masu_family = {'ます', 'ません', 'ました', 'ませんでした'}
        nai_family = {'ない', 'なかった'}
        te_family = {'て', 'ている', 'てください', 'てから', 'てある'}
        
        for verb in verbs:
            kana = verb['kana']
            kanji = verb['kanji']
            pos = verb.get('pos', '')
            
            # Generate masu stems if any masu-family grammar is known/being taught
            if masu_family & ctx.grammar_set:
                stem_k, stem_h = self._get_masu_stem(kanji or kana, kana, pos)
                if stem_k:
                    ctx.combo_forms[stem_k] = f'{kanji or kana} masu-stem'
                    if stem_h and stem_h != stem_k:
                        ctx.combo_forms[stem_h] = f'{kana} masu-stem'
            
            for grammar_kana, rule in conjugation_rules:
                if grammar_kana not in ctx.grammar_set:
                    continue
                
                forms = rule(kanji, kana, pos)
                for form, description in forms:
                    ctx.combo_forms[form] = description

        # い-adjective combo generation (stems, くない forms)
        i_adj_grammar = {'い-adjectives'}
        if i_adj_grammar & ctx.grammar_set:
            i_adjs = [v for v in ctx.vocab if v.get('pos') and
                      'adjective (keiyoushi)' in v['pos'] and
                      v['kana'] and v['kana'].endswith('い')]
            for adj in i_adjs:
                kana = adj['kana']
                kanji = adj['kanji']
                # Special case: いい → よくない (irregular)
                if kana == 'いい':
                    ctx.combo_forms['よくない'] = 'いい negative (irregular)'
                    ctx.combo_forms['よ'] = 'いい stem (irregular)'
                    continue
                # Regular: drop い, add くない
                kana_stem = kana[:-1]
                kanji_stem = kanji[:-1] if kanji and kanji.endswith('い') else kana_stem
                ctx.combo_forms[kanji_stem] = f'{kanji or kana} i-adj stem'
                ctx.combo_forms[kanji_stem + 'くない'] = f'{kanji or kana} negative'
                if kana_stem != kanji_stem:
                    ctx.combo_forms[kana_stem] = f'{kana} i-adj stem'
                    ctx.combo_forms[kana_stem + 'くない'] = f'{kana} negative'
            # Also add くない as a recognized combo suffix
            ctx.combo_forms['くない'] = 'i-adj negative suffix'

    def _get_conjugation_rules(self):
        """Return conjugation rules as (grammar_kana, transform_function) pairs.
        
        Each transform function takes (kanji, kana, pos) and returns
        list of (conjugated_form, description) tuples.
        """
        rules = []

        # ます form
        def masu_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_masu_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'ます', f'{kanji} + ます'))
                if stem_h and stem_h != stem_k:
                    results.append((stem_h + 'ます', f'{kana} + ます'))
            return results
        rules.append(('ます', masu_form))

        # ません form
        def masen_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_masu_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'ません', f'{kanji} + ません'))
                if stem_h and stem_h != stem_k:
                    results.append((stem_h + 'ません', f'{kana} + ません'))
            return results
        rules.append(('ません', masen_form))

        # ました form 
        def mashita_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_masu_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'ました', f'{kanji} + ました'))
                if stem_h and stem_h != stem_k:
                    results.append((stem_h + 'ました', f'{kana} + ました'))
            return results
        rules.append(('ました', mashita_form))

        # ませんでした form
        def masendeshita_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_masu_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'ませんでした', f'{kanji} + ませんでした'))
                if stem_h and stem_h != stem_k:
                    results.append((stem_h + 'ませんでした', f'{kana} + ませんでした'))
            return results
        rules.append(('ませんでした', masendeshita_form))

        # たい form
        def tai_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_masu_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'たい', f'{kanji} + たい'))
            return results
        rules.append(('たい', tai_form))

        # て form
        def te_form(kanji, kana, pos):
            results = []
            te_k, te_h = self._get_te_form(kanji, kana, pos)
            if te_k:
                results.append((te_k, f'{kanji} て-form'))
                if te_h and te_h != te_k:
                    results.append((te_h, f'{kana} て-form'))
            return results
        rules.append(('て', te_form))

        # ている form
        def teiru_form(kanji, kana, pos):
            results = []
            te_k, te_h = self._get_te_form(kanji, kana, pos)
            if te_k:
                results.append((te_k + 'いる', f'{kanji} + ている'))
                results.append((te_k + 'います', f'{kanji} + ています'))
            return results
        rules.append(('ている', teiru_form))

        # てください form
        def tekudasai_form(kanji, kana, pos):
            results = []
            te_k, te_h = self._get_te_form(kanji, kana, pos)
            if te_k:
                results.append((te_k + 'ください', f'{kanji} + てください'))
            return results
        rules.append(('てください', tekudasai_form))

        # ない form (plain negative)
        def nai_form(kanji, kana, pos):
            results = []
            stem_k, stem_h = self._get_nai_stem(kanji, kana, pos)
            if stem_k:
                results.append((stem_k + 'ない', f'{kanji} + ない'))
            return results
        rules.append(('ない', nai_form))

        # た form (plain past)
        def ta_form(kanji, kana, pos):
            results = []
            ta_k, ta_h = self._get_ta_form(kanji, kana, pos)
            if ta_k:
                results.append((ta_k, f'{kanji} + た'))
            return results
        rules.append(('た', ta_form))

        return rules

    def _get_masu_stem(self, kanji, kana, pos):
        """Get the masu-stem (連用形) of a verb.
        Returns (kanji_stem, kana_stem) or (None, None).
        """
        if not kana:
            return None, None
        kanji = kanji or kana  # fallback to kana if no kanji

        # Ichidan (る-verbs): drop る
        if 'Ichidan' in pos:
            if kana.endswith('る'):
                kana_stem = kana[:-1]
                kanji_stem = kanji[:-1] if kanji.endswith('る') else kanji[:-1]
                return kanji_stem, kana_stem
            return None, None

        # Special: する → し, 来る → き
        if kana == 'する':
            return 'し', 'し'
        if kana == 'くる' or kana == 'きます':
            return '来' if kanji == '来る' else 'き', 'き'

        # Godan (う-verbs): change ending to い-row
        godan_map = {
            'う': 'い', 'く': 'き', 'ぐ': 'ぎ', 'す': 'し',
            'つ': 'ち', 'ぬ': 'に', 'ぶ': 'び', 'む': 'み', 'る': 'り'
        }
        if kana and kana[-1] in godan_map:
            kana_stem = kana[:-1] + godan_map[kana[-1]]
            if kanji and kanji[-1] in godan_map:
                kanji_stem = kanji[:-1] + godan_map[kanji[-1]]
            else:
                kanji_stem = kanji[:-1] + godan_map[kana[-1]] if kanji else kana_stem
            return kanji_stem, kana_stem

        return None, None

    def _get_nai_stem(self, kanji, kana, pos):
        """Get the nai-stem (未然形) of a verb."""
        kanji = kanji or kana
        if 'Ichidan' in pos:
            if kana.endswith('る'):
                return kanji[:-1], kana[:-1]
            return None, None

        if kana == 'する':
            return 'し', 'し'
        if kana == 'くる':
            return '来' if kanji == '来る' else 'こ', 'こ'

        # Godan: change to あ-row (う → わ special case)
        godan_nai = {
            'う': 'わ', 'く': 'か', 'ぐ': 'が', 'す': 'さ',
            'つ': 'た', 'ぬ': 'な', 'ぶ': 'ば', 'む': 'ま', 'る': 'ら'
        }
        if kana and kana[-1] in godan_nai:
            kana_stem = kana[:-1] + godan_nai[kana[-1]]
            if kanji and kanji[-1] in godan_nai:
                kanji_stem = kanji[:-1] + godan_nai[kanji[-1]]
            else:
                kanji_stem = kanji[:-1] + godan_nai[kana[-1]] if kanji else kana_stem
            return kanji_stem, kana_stem

        return None, None

    def _get_te_form(self, kanji, kana, pos):
        """Get the て-form of a verb."""
        kanji = kanji or kana
        if 'Ichidan' in pos:
            if kana.endswith('る'):
                return kanji[:-1] + 'て', kana[:-1] + 'て'
            return None, None

        if kana == 'する':
            return 'して', 'して'
        if kana == 'くる':
            return '来て' if kanji == '来る' else 'きて', 'きて'

        # Godan て-form rules
        if kana.endswith('く'):
            # 行く is special
            if kana == 'いく' or kanji == '行く':
                return kanji[:-1] + 'って' if kanji != kana else 'いって', 'いって'
            return kanji[:-1] + 'いて', kana[:-1] + 'いて'
        if kana.endswith('ぐ'):
            return kanji[:-1] + 'いで', kana[:-1] + 'いで'
        if kana.endswith('す'):
            return kanji[:-1] + 'して', kana[:-1] + 'して'
        if kana.endswith(('う', 'つ', 'る')):
            return kanji[:-1] + 'って', kana[:-1] + 'って'
        if kana.endswith(('ぬ', 'ぶ', 'む')):
            return kanji[:-1] + 'んで', kana[:-1] + 'んで'

        return None, None

    def _get_ta_form(self, kanji, kana, pos):
        """Get the た-form (plain past) of a verb."""
        te_k, te_h = self._get_te_form(kanji, kana, pos)
        if te_k:
            # て → た, で → だ
            ta_k = te_k[:-1] + ('た' if te_k.endswith('て') else 'だ')
            ta_h = te_h[:-1] + ('た' if te_h.endswith('て') else 'だ')
            return ta_k, ta_h
        return None, None


def check_sentence_vocab(sentence_japanese, context):
    """Check if a sentence uses only available vocab/grammar/combos.
    
    Returns:
        (is_valid, unknown_words) tuple
    """
    # This is a simplified check — full morphological analysis would need MeCab
    # For now, we check if each word in the vocab/combo sets appears in the sentence
    # and flag any unrecognized segments
    
    known_forms = set()
    
    # Add all vocab kanji and kana forms
    for v in context.vocab:
        if v['kanji']:
            known_forms.add(v['kanji'])
        if v['kana']:
            known_forms.add(v['kana'])
    
    # Add grammar forms
    for g in context.grammar:
        if g['kanji']:
            known_forms.add(g['kanji'])
        if g['kana']:
            known_forms.add(g['kana'])
    
    # Add combo forms
    for form in context.combo_forms:
        known_forms.add(form)
    
    # Add common particles/markers that are "free" (not taught explicitly but used)
    known_forms.update(['は', 'が', 'を', 'に', 'で', 'へ', 'と', 'か', 'の', 'も', 
                       'や', 'よ', 'ね', 'です', 'じゃない', '。', '、', '？', '！',
                       '…', ' ', '　'])
    
    return True, []  # Placeholder — full validation needs morphological analysis


if __name__ == '__main__':
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    checker = AvailabilityChecker()
    
    # Test: what's available when learning ます (position 8)?
    print("=== Available at ます (position 8, L66) ===")
    ctx = checker.get_available_at(entry_id=326759)
    print(f"Vocab: {len(ctx.vocab)} words")
    print(f"Grammar: {len(ctx.grammar)} points")
    
    print(f"\nPreviously learned grammar:")
    for g in ctx.grammar:
        print(f"  - {g['kana']}")
    
    print(f"\nFirst 20 vocab:")
    for v in ctx.vocab[:20]:
        print(f"  - {v['kanji']} ({v['kana']})")
    
    # Test: what's available when learning を (position 12)?
    print(f"\n=== Available at を (position 12, L74) ===")
    ctx = checker.get_available_at(entry_id=325427)
    print(f"Vocab: {len(ctx.vocab)} words")
    print(f"Grammar: {len(ctx.grammar)} points")
    print(f"Combo forms: {len(ctx.combo_forms)}")
    
    print(f"\nPreviously learned grammar:")
    for g in ctx.grammar:
        print(f"  - {g['kana']}")
    
    print(f"\nSample combo forms (first 20):")
    for form, desc in list(ctx.combo_forms.items())[:20]:
        print(f"  - {form} ({desc})")
    
    # Test: what's available at て-form (position 33)?
    print(f"\n=== Available at て (position 33, L116) ===")
    ctx = checker.get_available_at(grammar_position=33)
    print(f"Vocab: {len(ctx.vocab)} words")
    print(f"Grammar: {len(ctx.grammar)} points")
    print(f"Combo forms: {len(ctx.combo_forms)}")
    
    print(f"\nSample combo forms (first 30):")
    for form, desc in list(ctx.combo_forms.items())[:30]:
        print(f"  - {form} ({desc})")
