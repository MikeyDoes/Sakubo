"""
Fix: Move 51 katakana words from N5 back to N4.
N5 was already complete — these should be N4 vocab.
"""
import sqlite3, json, os

DB = os.path.join(os.path.dirname(__file__), "dictionary", "dictionary.db")
N5_JSON = os.path.join(os.path.dirname(__file__), "n5_study_order.json")
N4_JSON = os.path.join(os.path.dirname(__file__), "n4_study_order.json")

conn = sqlite3.connect(DB)
cur = conn.cursor()

# =====================================================================
# Step 1: Identify the 51 entries that were added to N5 (SO 690-740)
# =====================================================================
with open(N5_JSON, encoding='utf-8') as f:
    n5_data = json.load(f)

# The original N5 had 689 entries; entries 690+ are the additions
original_n5 = [v for v in n5_data['vocab'] if v['study_order'] <= 689]
added_to_n5 = [v for v in n5_data['vocab'] if v['study_order'] > 689]

print(f"Original N5 vocab: {len(original_n5)}")
print(f"Added to N5 (to move to N4): {len(added_to_n5)}")

# =====================================================================
# Step 2: Revert N5 JSON to original 689
# =====================================================================
n5_data['vocab'] = original_n5
with open(N5_JSON, 'w', encoding='utf-8') as f:
    json.dump(n5_data, f, ensure_ascii=False, indent=2)
print(f"Reverted N5 JSON: {len(n5_data['vocab'])} vocab")

# =====================================================================
# Step 3: Change these 51 entries from N5 to N4 in DB
# =====================================================================
move_ids = [v['entry_id'] for v in added_to_n5]
for eid in move_ids:
    cur.execute("UPDATE entries SET jlpt_level='N4' WHERE id=?", (eid,))

print(f"Relabeled {len(move_ids)} entries from N5 → N4 in DB")

# Revert their N5 study_order in DB (clear it, will be set by N4)
for v in added_to_n5:
    cur.execute("UPDATE entries SET study_order=0 WHERE id=?", (v['entry_id'],))

# =====================================================================
# Step 4: Add to N4 JSON
# =====================================================================
with open(N4_JSON, encoding='utf-8') as f:
    n4_data = json.load(f)

existing_n4_ids = {v['entry_id'] for v in n4_data['vocab']}
current_max_n4_so = max(v['study_order'] for v in n4_data['vocab'])
print(f"Current N4 vocab: {len(n4_data['vocab'])}, max SO: {current_max_n4_so}")

new_n4_entries = []
for v in added_to_n5:
    if v['entry_id'] not in existing_n4_ids:
        new_n4_entries.append({
            'entry_id': v['entry_id'],
            'kanji': v.get('kanji'),
            'kana': v['kana'],
            'gloss': v['gloss'],
        })

print(f"Adding {len(new_n4_entries)} entries to N4 JSON")

for i, entry in enumerate(new_n4_entries, 1):
    entry['study_order'] = current_max_n4_so + i

# Update DB study_order for new N4 entries
for entry in new_n4_entries:
    cur.execute("UPDATE entries SET study_order=? WHERE id=?", 
                (entry['study_order'], entry['entry_id']))

n4_data['vocab'].extend(new_n4_entries)
print(f"New N4 vocab total: {len(n4_data['vocab'])}")

with open(N4_JSON, 'w', encoding='utf-8') as f:
    json.dump(n4_data, f, ensure_ascii=False, indent=2)

# =====================================================================
# Verify counts
# =====================================================================
cur.execute("SELECT COUNT(*) FROM entries WHERE jlpt_level='N5' AND kind='vocab'")
n5_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM entries WHERE jlpt_level='N4' AND kind='vocab'")
n4_count = cur.fetchone()[0]

conn.commit()
conn.close()

print(f"\n=== FINAL STATE ===")
print(f"N5 DB vocab: {n5_count} (should be 689)")
print(f"N4 DB vocab: {n4_count}")
print(f"N5 JSON vocab: {len(n5_data['vocab'])}")
print(f"N4 JSON vocab: {len(n4_data['vocab'])}")
print(f"N4 vocab lessons: {len(n4_data['vocab'])} / 7 = {len(n4_data['vocab']) // 7} full + {len(n4_data['vocab']) % 7} remainder = {(len(n4_data['vocab']) + 6) // 7} total")
print(f"N4 grammar lessons: 105")
print(f"Vocab >= Grammar? {'YES' if (len(n4_data['vocab']) + 6) // 7 >= 105 else 'NO'}")
