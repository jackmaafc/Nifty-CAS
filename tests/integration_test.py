import subprocess
import time
import requests
import sys
import os

def run_integration_test():
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    print("Running Task 1: Synthetic Data Generator...")
    subprocess.run(["python3", "src/utils/synthetic_data.py"], env=env, check=True)
    
    print("Running Task 4: Pass 1 Detection...")
    subprocess.run(["python3", "src/scanner/pass1_detection.py"], env=env, check=True)
    
    print("Running Task 5: Pass 2 Outcomes...")
    subprocess.run(["python3", "src/scanner/pass2_outcomes.py"], env=env, check=True)
    
    import sqlite3
    from pathlib import Path
    db_path = Path('data/db/research.db').resolve()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM candidate_events")
    c_count = cur.fetchone()[0]
    print(f"Candidates detected: {c_count}")
    
    cur.execute("SELECT outcome_category, COUNT(*) as c FROM forward_outcomes GROUP BY outcome_category")
    for r in cur.fetchall():
        print(f"Outcome {r['outcome_category']}: {r['c']}")
        
    cur.execute("SELECT COUNT(*) FROM raw_options_data WHERE data_quality != 'SYNTHETIC_TEST_DATA'")
    bad_quality = cur.fetchone()[0]
    print(f"Rows without SYNTHETIC_TEST_DATA label: {bad_quality}")
    
    # Test DB trigger by manually inserting with detection_pass_complete = 0
    cur.execute("UPDATE candidate_events SET detection_pass_complete = 0")
    conn.commit()
    
    cur.execute("SELECT id FROM candidate_events LIMIT 1")
    row = cur.fetchone()
    if row:
        c_id = row[0]
        trigger_worked = False
        try:
            cur.execute("INSERT INTO forward_outcomes (candidate_id, outcome_category, data_quality) VALUES (?, 'EXTENSION', 'SYNTHETIC_TEST_DATA')", (c_id,))
            conn.commit()
        except sqlite3.IntegrityError:
            trigger_worked = True
        print(f"DB Trigger enforcement worked: {'YES' if trigger_worked else 'NO'}")
    else:
        print("DB Trigger enforcement worked: N/A (no candidates)")
    
    conn.close()

if __name__ == "__main__":
    run_integration_test()
