# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Journal ring buffer and JOURNAL command."""

import time
import pytest
from testcore.journal import Journal, JournalEntry, get_journal
from testcore.commands import handle_journal, dispatcher
from testcore.protocol import RESPParser


@pytest.fixture(autouse=True)
def reset_journal():
    """Reset global journal before each test."""
    import testcore.journal as jmod
    jmod._journal = None
    journal = get_journal(maxlen=100)
    journal.clear()
    yield journal


# ===== Journal module tests =====

class TestJournalModule:
    """Tests for the Journal class directly."""

    def test_record_basic(self, reset_journal):
        j = reset_journal
        j.record(1, "client1", ["KSET", "foo", "bar"], "ok")
        assert len(j) == 1

    def test_record_stores_fields(self, reset_journal):
        j = reset_journal
        j.record(42, "mytest", ["IREAD", "awg:CH1:FREQ"], "ok")
        entry = j.all()[0]
        assert entry.session_id == 42
        assert entry.client_name == "mytest"
        assert entry.command == "IREAD awg:CH1:FREQ"
        assert entry.status == "ok"
        assert entry.timestamp > 0

    def test_record_excludes_ping(self, reset_journal):
        j = reset_journal
        j.record(1, "", ["PING"], "ok")
        assert len(j) == 0

    def test_record_excludes_journal(self, reset_journal):
        j = reset_journal
        j.record(1, "", ["JOURNAL"], "ok")
        assert len(j) == 0

    def test_tail_default(self, reset_journal):
        j = reset_journal
        for i in range(20):
            j.record(1, "", [f"CMD{i}"], "ok")
        entries = j.tail(10)
        assert len(entries) == 10
        assert entries[0].command == "CMD10"
        assert entries[-1].command == "CMD19"

    def test_tail_more_than_available(self, reset_journal):
        j = reset_journal
        j.record(1, "", ["CMD1"], "ok")
        j.record(1, "", ["CMD2"], "ok")
        entries = j.tail(100)
        assert len(entries) == 2

    def test_head_from_start(self, reset_journal):
        j = reset_journal
        for i in range(20):
            j.record(1, "", [f"CMD{i}"], "ok")
        entries = j.head(0, 5)
        assert len(entries) == 5
        assert entries[0].command == "CMD0"
        assert entries[4].command == "CMD4"

    def test_head_with_offset(self, reset_journal):
        j = reset_journal
        for i in range(20):
            j.record(1, "", [f"CMD{i}"], "ok")
        entries = j.head(10, 3)
        assert len(entries) == 3
        assert entries[0].command == "CMD10"

    def test_head_offset_beyond_size(self, reset_journal):
        j = reset_journal
        j.record(1, "", ["CMD1"], "ok")
        entries = j.head(100)
        assert len(entries) == 0

    def test_head_no_count(self, reset_journal):
        j = reset_journal
        for i in range(5):
            j.record(1, "", [f"CMD{i}"], "ok")
        entries = j.head(2)
        assert len(entries) == 3  # from index 2 to end

    def test_ring_buffer_eviction(self):
        j = Journal(maxlen=5)
        for i in range(10):
            j.record(1, "", [f"CMD{i}"], "ok")
        assert len(j) == 5
        entries = j.all()
        assert entries[0].command == "CMD5"
        assert entries[-1].command == "CMD9"

    def test_clear(self, reset_journal):
        j = reset_journal
        for i in range(5):
            j.record(1, "", [f"CMD{i}"], "ok")
        count = j.clear()
        assert count == 5
        assert len(j) == 0

    def test_maxlen_property(self):
        j = Journal(maxlen=500)
        assert j.maxlen == 500

    def test_entry_to_str(self):
        e = JournalEntry(
            timestamp=1000000.123456,
            session_id=1,
            client_name="test",
            command="KSET foo bar",
            status="ok",
        )
        s = e.to_str()
        assert "1000000.123456" in s
        assert "[test]" in s
        assert "KSET foo bar" in s
        assert "ok" in s

    def test_entry_to_str_no_name(self):
        e = JournalEntry(
            timestamp=1000000.0,
            session_id=42,
            client_name="",
            command="PING",
            status="ok",
        )
        s = e.to_str()
        assert "session:42" in s


# ===== JOURNAL command handler tests =====

class TestJournalCommand:
    """Tests for the JOURNAL command handler."""

    @pytest.mark.asyncio
    async def test_journal_default_last_10(self, reset_journal):
        j = reset_journal
        for i in range(20):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal([], {})
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]
        assert len(result) == 10
        assert "CMD10" in result[0]
        assert "CMD19" in result[-1]

    @pytest.mark.asyncio
    async def test_journal_count(self, reset_journal):
        j = reset_journal
        for i in range(20):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["5"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 5
        assert "CMD15" in result[0]

    @pytest.mark.asyncio
    async def test_journal_plus_offset(self, reset_journal):
        """JOURNAL +1 → from first entry (tail -n +1 style)."""
        j = reset_journal
        for i in range(5):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["+1"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 5
        assert "CMD0" in result[0]

    @pytest.mark.asyncio
    async def test_journal_plus_offset_with_count(self, reset_journal):
        """JOURNAL +1 3 → first 3 entries."""
        j = reset_journal
        for i in range(10):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["+1", "3"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 3
        assert "CMD0" in result[0]
        assert "CMD2" in result[2]

    @pytest.mark.asyncio
    async def test_journal_plus_offset_mid(self, reset_journal):
        """JOURNAL +5 → from 5th entry to end."""
        j = reset_journal
        for i in range(10):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["+5"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 6  # entries 4..9 (0-based)
        assert "CMD4" in result[0]

    @pytest.mark.asyncio
    async def test_journal_all(self, reset_journal):
        j = reset_journal
        for i in range(15):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["ALL"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 15

    @pytest.mark.asyncio
    async def test_journal_clear(self, reset_journal):
        j = reset_journal
        for i in range(5):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["CLEAR"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert result == 5
        assert len(j) == 0

    @pytest.mark.asyncio
    async def test_journal_empty(self, reset_journal):
        response = await handle_journal([], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert result == []

    @pytest.mark.asyncio
    async def test_journal_invalid_count(self, reset_journal):
        response = await handle_journal(["abc"], {})
        assert b"-" in response  # error

    @pytest.mark.asyncio
    async def test_journal_negative_count(self, reset_journal):
        response = await handle_journal(["-5"], {})
        assert b"-" in response  # error

    @pytest.mark.asyncio
    async def test_journal_error_status(self, reset_journal):
        j = reset_journal
        j.record(1, "", ["KSET"], "error")
        entries = j.all()
        assert entries[0].status == "error"

    @pytest.mark.asyncio
    async def test_journal_rel_format(self, reset_journal):
        """JOURNAL N REL returns relative timestamps."""
        j = reset_journal
        j.record(1, "", ["CMD1"], "ok")
        j.record(1, "", ["CMD2"], "ok")
        j.record(1, "", ["CMD3"], "ok")
        response = await handle_journal(["3", "REL"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 3
        # First entry shows +0.000000
        assert result[0].startswith("+0.000000")
        # Other entries show positive deltas
        assert result[1].startswith("+")
        assert result[2].startswith("+")

    @pytest.mark.asyncio
    async def test_journal_all_rel(self, reset_journal):
        """JOURNAL ALL REL works."""
        j = reset_journal
        j.record(1, "", ["CMD1"], "ok")
        j.record(1, "", ["CMD2"], "ok")
        response = await handle_journal(["ALL", "REL"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 2
        assert result[0].startswith("+0.000000")

    @pytest.mark.asyncio
    async def test_journal_rel_without_count(self, reset_journal):
        """JOURNAL REL (no count) returns last 10 with relative timestamps."""
        j = reset_journal
        for i in range(3):
            j.record(1, "", [f"CMD{i}"], "ok")
        response = await handle_journal(["REL"], {})
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert len(result) == 3
        assert result[0].startswith("+0.000000")

    @pytest.mark.asyncio
    async def test_journal_registered(self):
        assert "JOURNAL" in dispatcher._handlers


# ===== Integration: dispatch records to journal =====

class TestJournalDispatchIntegration:
    """Test that the dispatcher records commands in the journal."""

    @pytest.mark.asyncio
    async def test_dispatch_records_command(self, reset_journal):
        j = reset_journal
        await dispatcher.dispatch(["KSET", "testkey", "testval"],
                                  {"session_id": 1})
        assert len(j) >= 1
        entry = j.all()[-1]
        assert "KSET" in entry.command
        assert entry.status == "ok"

    @pytest.mark.asyncio
    async def test_dispatch_records_error(self, reset_journal):
        j = reset_journal
        await dispatcher.dispatch(["KSET"],  # missing args → error
                                  {"session_id": 1})
        entries = j.all()
        assert len(entries) >= 1
        assert entries[-1].status == "error"

    @pytest.mark.asyncio
    async def test_dispatch_skips_ping(self, reset_journal):
        j = reset_journal
        j.clear()
        await dispatcher.dispatch(["PING"], {"session_id": 1})
        assert len(j) == 0
