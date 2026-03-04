import sqlite3

def run():
    print("Opening SQLite DB...")
    conn = sqlite3.connect("oriexa.db")
    c = conn.cursor()
    c.execute("UPDATE task_claims SET status='accepted' WHERE task_id=1961")
    c.execute("UPDATE tasks SET status='in_progress' WHERE id=1961")
    conn.commit()
    print("Task 1961 has been forcefully accepted and set to in_progress!")
    conn.close()

if __name__ == "__main__":
    run()
