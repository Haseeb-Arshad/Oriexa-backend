import sqlite3
import json

try:
    conn = sqlite3.connect('oriexa.db')
    cursor = conn.cursor()
    cursor.execute("SELECT detail FROM task_progress_step WHERE component = 'deployment' ORDER BY id DESC LIMIT 5;")
    rows = [r[0] for r in cursor.fetchall()]
    with open('vercel_logs.json', 'w') as f:
        json.dump(rows, f, indent=2)
    print("Done")
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'conn' in locals():
        conn.close()
