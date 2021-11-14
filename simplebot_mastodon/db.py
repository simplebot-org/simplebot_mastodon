import sqlite3
from typing import List, Optional


class DBManager:
    def __init__(self, db_path: str) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS accounts
                (id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                api_url TEXT NOT NULL,
                accname TEXT NOT NULL,
                addr TEXT NOT NULL,
                home INTEGER NOT NULL,
                notif INTEGER NOT NULL,
                last_home TEXT,
                last_notif TEXT)"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS pchats
                (id INTEGER PRIMARY KEY,
                contact TEXT NOT NULL,
                account INTEGER NOT NULL REFERENCES accounts(id))"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS clients
                (api_url TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                secret TEXT NOT NULL)"""
            )

    # ==== client =====

    def get_client(self, api_url: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM clients WHERE api_url=?", (api_url,)
        ).fetchone()

    def add_client(self, api_url: str, client_id: str, client_secret: str) -> None:
        query = "INSERT INTO clients VALUES (?,?,?)"
        with self.db:
            self.db.execute(query, (api_url, client_id, client_secret))

    # ==== account =====

    def add_account(
        self,
        email: str,
        password: str,
        api_url: str,
        accname: str,
        addr: str,
        home: int,
        notif: int,
        last_home: str,
        last_notif: str,
    ) -> None:
        args = (
            None,
            email,
            password,
            api_url,
            accname,
            addr,
            home,
            notif,
            last_home,
            last_notif,
        )
        q = f"INSERT INTO accounts VALUES ({','.join('?' for i in range(len(args)))})"
        with self.db:
            self.db.execute(q, args)

    def remove_account(self, acc_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM pchats WHERE account=?", (acc_id,))
            self.db.execute("DELETE FROM accounts WHERE id=?", (acc_id,))

    def set_last_notif(self, acc_id: int, last_notif: str) -> None:
        q = "UPDATE accounts SET last_notif=? WHERE id=?"
        with self.db:
            self.db.execute(q, (last_notif, acc_id))

    def set_last_home(self, acc_id: int, last_home: str) -> None:
        q = "UPDATE accounts SET last_home=? WHERE id=?"
        with self.db:
            self.db.execute(q, (last_home, acc_id))

    def get_account(self, gid: int) -> Optional[sqlite3.Row]:
        q = "SELECT * FROM accounts WHERE home=? OR notif=?"
        acc = self.db.execute(q, (gid, gid)).fetchone()
        if not acc:
            pchat = self.get_pchat(gid)
            if pchat:
                acc = self.get_account_by_id(pchat["account"])
        return acc

    def get_account_by_id(self, acc_id: int) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM accounts WHERE id=?", (acc_id,)
        ).fetchone()

    def get_account_by_home(self, gid: int) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM accounts WHERE home=?", (gid,)).fetchone()

    def get_account_by_user(self, name: str, url: str) -> Optional[sqlite3.Row]:
        q = "SELECT * FROM accounts WHERE accname=? AND api_url=?"
        return self.db.execute(q, (name.lower(), url)).fetchone()

    def get_accounts(self, url: str = None, addr: str = None) -> List[sqlite3.Row]:
        if url:
            q = "SELECT * FROM accounts WHERE api_url=?"
            return self.db.execute(q, (url,)).fetchall()
        if addr:
            q = "SELECT * FROM accounts WHERE addr=?"
            return self.db.execute(q, (addr,)).fetchall()
        return self.db.execute("SELECT * FROM accounts").fetchall()

    # ==== pchat =====

    def add_pchat(self, gid: int, contact: str, acc_id: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO pchats VALUES (?,?,?)", (gid, contact.lower(), acc_id)
            )

    def remove_pchat(self, gid: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM pchats WHERE id=?", (gid,))

    def get_pchat(self, gid: int) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM pchats WHERE id=?", (gid,)).fetchone()

    def get_pchats(self, acc_id: int) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM pchats WHERE account=?", (acc_id,)
        ).fetchall()

    def get_pchat_by_contact(self, acc_id: int, contact: str) -> Optional[sqlite3.Row]:
        q = "SELECT * FROM pchats WHERE account=? AND contact=?"
        return self.db.execute(q, (acc_id, contact.lower())).fetchone()
