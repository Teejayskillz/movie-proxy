# migrate_add_size.py
import sqlite3

conn = sqlite3.connect('downloads.db')
# Add file_size column if not exists
try:
    conn.execute("ALTER TABLE downloads ADD COLUMN file_size TEXT")
    print("Added file_size column")
except sqlite3.OperationalError as e:
    if "duplicate column" in str(e):
        print("file_size column already exists")
    else:
        raise
conn.commit()
conn.close()