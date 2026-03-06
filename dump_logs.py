import sqlite3
import json

try:
    conn = sqlite3.connect('oriexa.db')
    cursor = conn.cursor()
    cursor.execute("SELECT detail FROM orch_task_execution_steps WHERE component = 'deployment' ORDER BY id DESC LIMIT 5;")
    rows = [r[0] for r in cursor.fetchall()]
    with open('vercel_logs.json', 'w') as f:
        json.dump(rows, f, indent=2)
except Exception as e:
    with open('vercel_logs.json', 'w') as f:
        json.dump({"error": str(e)}, f)
finally:
    if 'conn' in locals():
        conn.close()
