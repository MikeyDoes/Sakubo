# Spoonfed Japanese — AI Instructions

## ⚠️ CRITICAL: App Icons — DO NOT TOUCH

**`android_resources/` is git-tracked and locked. Never modify, regenerate, or overwrite any file in it.**

The icons were pixel-perfectly tuned and extracted from the March 2026 Play Store AAB (`C:\Users\Michael\Desktop\sakubo-release.aab`). Every previous LLM session that tried to "fix" or "regenerate" icons made them worse. The build script verifies a SHA256 checksum and will fail loudly if the icons are wrong.

- ✅ Correct source: `android_resources/` (git-tracked, do not modify)
- ✅ Reference AAB: `C:\Users\Michael\Desktop\sakubo-release.aab` (March 2026 Play Store build)
- ❌ Never use: `sakubo_icon_512.png`, `sakubo_icon_foreground.png`, or any PIL/image script to regenerate icons
- ❌ Never regenerate `ic_launcher_foreground.png` from any source PNG

If icons look wrong on device, restore them from the March AAB — do not attempt to fix by regenerating.

---

## Quick Orientation

This is a Kivy-based Japanese learning app (Sakubo) with a structured lesson system covering JLPT N5–N1.
**Current state (March 2026):** N5 is COMPLETE (lessons, exercises, 121 graded readings, 689/689 vocab coverage). N4 is next — needs grammar/vocab audit against external sources before building.

**Read before making lesson/grammar/exercise/reading changes:**
- `LESSON_ARCHITECTURE.md` — the single source of truth for how the lesson system works (includes Graded Reading Rules)

**Read before working on N4 content:**
- `_N4_BUILD_BLUEPRINT.md` — plan for building N4 level (includes UI integration plan for readings)

**Read for general project context:**
- `_PROJECT_CONTEXT.md` — app overview, tech stack, folder structure

**Read for N5 reading status:**
- `_N5_READING_STATUS.md` — 121 readings (99 G-series + 22 S-series), 689/689 coverage

## The Golden Rule

> Every exercise sentence must ONLY use vocabulary and grammar the student has already learned.

This means: if a grammar point is at lesson N, its exercises can only reference vocab/grammar from lessons < N. Violating this creates an impossible learning experience.

## After Any Ordering or Exercise Change

Run the validation script:
```
python _validate_lesson_ordering.py
```

If you modified grammar exercises, reimport them:
```
python _import_grammar_exercises.py
```

If you changed lesson positions in index.json, regenerate the table:
```
python _print_lesson_table.py
```

## Key Files for Lesson Work

| File | Purpose |
|------|---------|
| `index.json` | Maps lesson numbers → lesson files |
| `n5_study_order.json` | Grammar study_order values |
| `grammar_exercises/*.json` | Exercise sentences per grammar point |
| `dictionary/dictionary.db` | SQLite DB (entries, grammar_exercises tables) |
| `vocab_grammar_availability.py` | AvailabilityChecker — what's taught at each lesson |
| `_validate_lesson_ordering.py` | 6-check validation of entire ordering system |

## Key Files for Graded Reading Work

| File | Purpose |
|------|---------|
| `readings/n5/G{NNN}_*.json` | 99 grammar-tied graded readings (G008–G106) |
| `readings/n5/S{NNN}_*.json` | 22 supplementary readings (S001–S022) |
| `_validate_readings.py` | Validates all readings (vocab availability, grammar use) |
| `_validate_reading_coverage.py` | Reports vocab coverage stats (covered vs uncovered) |
| `_N5_READING_STATUS.md` | Current status of N5 graded reading work |
| `vocab_grammar_availability.py` | AvailabilityChecker — determines what's allowed in each reading |

## Key Files for N4 Work

| File | Purpose |
|------|---------|
| `_N4_BUILD_BLUEPRINT.md` | Full plan including phases, rules, grammar list, UI plan |
| `vocab_order_maps/JLPT_N4_GRAMMAR_ORDER.csv` | 134-line grammar CSV (needs reconciliation with 90 DB entries) |
| `vocab_order_maps/JLPT_N4_VOCAB_ORDER.csv` | N4 vocab ordering reference |
| `dictionary/lessons/spoonfed_japanese/n4_vocab/` | 90 generated N4 vocab lesson files |
| `dictionary/lessons/spoonfed_japanese/n4_grammar/` | 90 generated N4 grammar lesson files |

## App UI Integration (Pending)

The 121 N5 graded readings exist as data files but are NOT yet visible in the app.
- `main.py:5136` — `show_sakubo_reading()` is a "Coming Soon!" placeholder
- Needs a reading list view + a dialogue/narrative viewer
- See `_N4_BUILD_BLUEPRINT.md` "Parallel Track: N5 Reading UI Integration" section

## After Any Reading Change

Run the reading validator:
```
python _validate_readings.py
```

Run the coverage report:
```
python _validate_reading_coverage.py
```

## Critical: How Availability Works

The `AvailabilityChecker` in `vocab_grammar_availability.py` determines word availability
by **lesson_number comparison** (NOT study_order):

```python
if vl_num < lesson_num:  # vocab lesson number < grammar lesson number
```

At grammar_position N, the grammar's lesson_number is looked up, and ALL vocab from
lessons with a smaller lesson_number are available. Study_order is irrelevant for
availability — only lesson numbers matter.

Example: Grammar position 8 (ます) is at lesson 69. Only vocab from lessons < 69
is available (~172 words), not the 662 that a study_order comparison would suggest.

## What NOT to Reference

The following docs were deleted because they described abandoned approaches:
- ~~GRAMMAR_INTEGRATION_GUIDE.md~~ (abandoned LLM-based generation)
- ~~SENTENCE_GENERATION_GUIDE.md~~ (abandoned LiquidAI approach)  
- ~~LLM_OPTIMIZATION_GUIDE.md~~ (abandoned LLM approach)
- ~~GRAMMAR_STUDY_VECTORS.md~~ (partially outdated)
- ~~dictionary/GRAMMAR_LEARNING_ORDER.md~~ (misleading raw dump)

If you encounter references to these files elsewhere, ignore them — they no longer exist and their methods are not used.
