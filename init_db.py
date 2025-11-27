# init_db.py
import sqlite3

def init_db():
    conn = sqlite3.connect('downloads.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            id TEXT PRIMARY KEY,
            original_url TEXT NOT NULL,
            renamed_filename TEXT NOT NULL,
            generated_link TEXT NOT NULL,
            downloads INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized.")

if __name__ == '__main__':
    init_db()