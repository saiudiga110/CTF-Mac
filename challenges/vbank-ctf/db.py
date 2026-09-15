import sqlite3

conn = sqlite3.connect("vbank.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    username TEXT,
    password TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS accounts (
    user TEXT,
    balance TEXT
)
""")

c.execute("INSERT INTO users VALUES ('admin','admin123')")
c.execute("INSERT INTO accounts VALUES ('admin','1000000')")

conn.commit()
conn.close()