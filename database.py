import sqlite3
from datetime import datetime

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('ivasms_bot.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT UNIQUE,
                country TEXT DEFAULT 'Unknown',
                service TEXT DEFAULT 'Unknown',
                status TEXT DEFAULT 'unused',
                assigned_to TEXT,
                assigned_time TIMESTAMP,
                otp TEXT,
                otp_time TIMESTAMP,
                added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id TEXT PRIMARY KEY,
                last_request TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS seen_sms (
                sms_hash TEXT PRIMARY KEY,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        defaults = [
            ('numbers_per_request', '3'),
            ('cooldown_seconds', '5'),
            ('admin_ids', ''),
            ('otp_group_id', ''),
            ('fetch_interval', '10'),
        ]
        for key, value in defaults:
            self.cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        
        self.conn.commit()
    
    def add_numbers(self, numbers_list):
        count = 0
        for item in numbers_list:
            item = item.strip()
            if not item:
                continue
            parts = [p.strip() for p in item.split(',')]
            phone = parts[0] if len(parts) > 0 else ''
            country = parts[1] if len(parts) > 1 else 'Unknown'
            service = parts[2] if len(parts) > 2 else 'Unknown'
            if phone:
                try:
                    self.cursor.execute('INSERT OR IGNORE INTO numbers (phone_number, country, service, status) VALUES (?, ?, ?, ?)',
                        (phone, country, service, 'unused'))
                    if self.cursor.rowcount > 0:
                        count += 1
                except:
                    pass
        self.conn.commit()
        return count
    
    def get_unused_numbers(self, limit=3):
        self.cursor.execute('SELECT phone_number, country, service FROM numbers WHERE status=? LIMIT ?', ('unused', limit))
        return self.cursor.fetchall()
    
    def assign_numbers(self, user_id, limit=3):
        numbers = self.get_unused_numbers(limit)
        assigned = []
        for phone, country, service in numbers:
            self.cursor.execute('UPDATE numbers SET status=?, assigned_to=?, assigned_time=? WHERE phone_number=?',
                ('assigned', user_id, datetime.now(), phone))
            assigned.append((phone, country, service))
        self.conn.commit()
        return assigned
    
    def mark_used_with_otp(self, phone_number, otp):
        self.cursor.execute('UPDATE numbers SET status=?, otp=?, otp_time=? WHERE phone_number=?',
            ('used', otp, datetime.now(), phone_number))
        self.conn.commit()
    
    def get_assigned_numbers(self):
        self.cursor.execute("SELECT phone_number, assigned_to FROM numbers WHERE status='assigned'")
        return self.cursor.fetchall()
    
    def check_cooldown(self, user_id, cooldown_seconds):
        self.cursor.execute('SELECT last_request FROM cooldowns WHERE user_id=?', (user_id,))
        row = self.cursor.fetchone()
        if row and row[0]:
            last_time = datetime.fromisoformat(row[0])
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < cooldown_seconds:
                return True, cooldown_seconds - int(elapsed)
        return False, 0
    
    def update_cooldown(self, user_id):
        self.cursor.execute('INSERT OR REPLACE INTO cooldowns (user_id, last_request) VALUES (?, ?)',
            (user_id, datetime.now().isoformat()))
        self.conn.commit()
    
    def get_setting(self, key):
        self.cursor.execute('SELECT value FROM settings WHERE key=?', (key,))
        row = self.cursor.fetchone()
        return row[0] if row else None
    
    def update_setting(self, key, value):
        self.cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
        self.conn.commit()
    
    def get_stats(self):
        stats = {}
        for key in ['total', 'unused', 'used', 'assigned']:
            status = key if key != 'total' else None
            if status:
                self.cursor.execute(f'SELECT COUNT(*) FROM numbers WHERE status=?', (status,))
            else:
                self.cursor.execute('SELECT COUNT(*) FROM numbers')
            stats[key] = self.cursor.fetchone()[0]
        return stats
    
    def get_recent_used(self, limit=10):
        self.cursor.execute('SELECT phone_number, otp, otp_time FROM numbers WHERE status="used" ORDER BY otp_time DESC LIMIT ?', (limit,))
        return self.cursor.fetchall()
    
    def is_sms_seen(self, sms_hash):
        self.cursor.execute('SELECT 1 FROM seen_sms WHERE sms_hash=?', (sms_hash,))
        return self.cursor.fetchone() is not None
    
    def mark_sms_seen(self, sms_hash):
        self.cursor.execute('INSERT OR IGNORE INTO seen_sms (sms_hash) VALUES (?)', (sms_hash,))
        self.conn.commit()
    
    def close(self):
        self.conn.close()