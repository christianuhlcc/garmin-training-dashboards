"""List exercise names in the strength log that have no muscle mapping, plus every raw spelling per canonical name
and lines the notes parser flagged. Fix mappings in src/exercise_map.yaml; fix parsing in ../notes_import.py.

    make review-exercises
"""

from src.adapters import source as src
from src.metrics import strength as st


def main():
    emap = st.load_exercise_map()
    sets = src.load_strength_sets()
    manual = sets[sets["source"] == "manual"]
    names = manual.groupby("exercise").agg(sets=("reps", "size"), raw=("exercise_raw", lambda s: sorted(set(s))))
    unmapped = [e for e in names.index if e not in emap["exercises"]]
    print("Unmapped exercises:" if unmapped else "All logged exercises are mapped.")
    for e in unmapped:
        print(f"  ! {e} ({names.loc[e, 'sets']} sets) - add it to src/exercise_map.yaml")
    unused = [e for e in emap["exercises"] if e not in names.index]
    if unused:
        print("Mapped but never logged:", ", ".join(unused))
    print("\nCanonical name <- raw spellings in the note:")
    for e, row in names.sort_values("sets", ascending=False).iterrows():
        print(f"  {e:28} {row['sets']:4} sets  <- {', '.join(row['raw'])}")
    with src.connect() as con:
        flagged = con.execute("SELECT DISTINCT workout_date, parse_flag, line_raw FROM manual_sets "
                              "WHERE parse_flag IS NOT NULL AND parse_flag != 'skipped' ORDER BY 1").fetchall()
    if flagged:
        print("\nLines with parser interpretations to double-check:")
        for d, f, line in flagged:
            print(f"  {d}  [{f}]  {line}")


if __name__ == "__main__":
    main()
