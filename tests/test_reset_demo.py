import reset_demo


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)

    def transaction(self):
        return FakeCursor(self.log)


def test_reset_clears_every_console_table_with_delete():
    # DELETE, not TRUNCATE: the deployer's role has DML grants on the App's tables, not TRUNCATE.
    conn = FakeConn()
    reset_demo.reset(conn)
    assert conn.log == [f"DELETE FROM {t}" for t in
                        ("safety.device_commands", "safety.incidents", "safety.telemetry", "safety.worker_status")]
