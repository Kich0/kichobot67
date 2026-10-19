"""Unit tests for BuketovApiService, caching, Circuit Breaker, rate limiter, and MongoDB fallback."""

import time
import httpx
import pytest
from src.services.buketov_api import (
    BuketovApiError,
    BuketovApiService,
    CacheEntry,
    CircuitBreakerError,
)
from src.core.constants import SCHEDULE_API_RATE_LIMIT
from src.database.models import Group, Schedule, ScheduleDay, SubjectItem, Teacher, TeacherSchedule, TeacherScheduleDay, TeacherLessonItem


def test_buketov_api_initial_capacities():
    """Verifies partitioned cache capacities and default parameters."""
    service = BuketovApiService()
    assert service.STUDENT_CAPACITY == 3000
    assert service.TEACHER_CAPACITY == 1500
    assert service.circuit_state == "CLOSED"
    assert service.MAX_REQUESTS_PER_MINUTE == SCHEDULE_API_RATE_LIMIT
    assert service.REQUEST_TIMEOUT_SECONDS == 4.5


def test_buketov_api_lru_cache_isolation():
    """Verifies student cache eviction does not touch teacher cache."""
    service = BuketovApiService()
    service.student_cache.clear()
    service.teacher_cache.clear()

    # Add entry to teacher cache
    service._set_cache(
        service.teacher_cache,
        service.TEACHER_CAPACITY,
        "teacher_1",
        CacheEntry(data={"records": ["t1"]}, timestamp=time.time()),
    )
    assert "teacher_1" in service.teacher_cache
    assert len(service.teacher_cache) == 1

    # Overfill student cache with small capacity
    test_cap = 3
    for i in range(1, 5):
        service._set_cache(
            service.student_cache,
            test_cap,
            f"student_{i}",
            CacheEntry(data={"records": [f"s{i}"]}, timestamp=time.time()),
        )

    # student_1 must be evicted, student_4 must exist, size must be 3
    assert "student_1" not in service.student_cache
    assert "student_4" in service.student_cache
    assert len(service.student_cache) == test_cap

    # Teacher cache must remain completely unaffected
    assert "teacher_1" in service.teacher_cache
    assert len(service.teacher_cache) == 1


def test_circuit_breaker_transitions():
    """Verifies state machine: CLOSED -> API_DOWN -> CLOSED and HALF_OPEN probe failure."""
    service = BuketovApiService()
    assert service.circuit_state == "CLOSED"
    assert service.consecutive_failures == 0

    # 1. First failure keeps state CLOSED
    service._record_failure(Exception("Network timeout 1"))
    assert service.consecutive_failures == 1
    assert service.circuit_state == "CLOSED"

    # 2. Second failure trips to API_DOWN
    service._record_failure(Exception("Network timeout 2"))
    assert service.consecutive_failures == 2
    assert service.circuit_state == "API_DOWN"
    assert service.circuit_tripped_at > 0

    # 3. Recovery resets to CLOSED
    service._record_success()
    assert service.circuit_state == "CLOSED"
    assert service.consecutive_failures == 0

    # 4. Probe failure in HALF_OPEN immediately trips to API_DOWN
    service.circuit_state = "HALF_OPEN"
    service._record_failure(Exception("Probe error"))
    assert service.circuit_state == "API_DOWN"
    assert service.consecutive_failures == service.FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_circuit_breaker_raises_503_or_returns_cache():
    """Verifies 503 exception when circuit is OPEN, or cached data if available."""
    service = BuketovApiService()
    service.circuit_state = "API_DOWN"
    service.circuit_tripped_at = time.time()
    service.student_cache.clear()

    # Without cache: raises CircuitBreakerError
    with pytest.raises(CircuitBreakerError) as exc_info:
        await service.fetch_schedule({"group": "TEST-1"}, cache_type="student")
    assert exc_info.value.status_code == 503
    assert exc_info.value.is_circuit_breaker is True

    # With stale cache: returns cached data with warning
    service._set_cache(
        service.student_cache,
        service.STUDENT_CAPACITY,
        f"{service.api_url}?group=TEST-1",
        CacheEntry(data={"records": [{"subject": "Cached Math"}]}, timestamp=time.time() - 1000),
    )
    result = await service.fetch_schedule({"group": "TEST-1"}, cache_type="student")
    assert result == {"records": [{"subject": "Cached Math"}]}


def test_rate_limiter_window():
    """Verifies sliding window rate limiter prevents exceeding 90 req/min."""
    service = BuketovApiService()
    now = time.time()
    service.outbound_request_timestamps = [now - 10.0] * service.MAX_REQUESTS_PER_MINUTE

    assert service.check_rate_limit() is False
    assert service.can_make_api_call() is False

    # Old timestamps outside 60s window are pruned
    service.outbound_request_timestamps = [now - 70.0] * service.MAX_REQUESTS_PER_MINUTE
    assert service.check_rate_limit() is True
    assert service.can_make_api_call() is True


@pytest.mark.asyncio
async def test_http_etag_304_handling():
    """Verifies that HTTP 304 Not Modified updates timestamp and returns cached response."""
    service = BuketovApiService()
    service.student_cache.clear()

    cached_data = {"records": [{"subject": "ETag Subject"}]}
    cache_key = f"{service.api_url}?group=ETAG-1"
    old_timestamp = time.time() - 800  # Stale (>10 min)
    service._set_cache(
        service.student_cache,
        service.STUDENT_CAPACITY,
        cache_key,
        CacheEntry(data=cached_data, etag='"abc123etag"', timestamp=old_timestamp),
    )

    # Mock HTTP transport returning 304
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("if-none-match") == '"abc123etag"'
        return httpx.Response(304)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._client = mock_client

    try:
        data = await service.fetch_schedule({"group": "ETAG-1"}, cache_type="student")
        assert data == cached_data
        updated_entry = service.student_cache[cache_key]
        assert updated_entry.timestamp > old_timestamp
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_http_success_and_cache_store():
    """Verifies standard 200 OK response stores entry with ETag."""
    service = BuketovApiService()
    service.student_cache.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"records": [{"subject": "Algorithms"}]},
            headers={"etag": '"etag-new-999"'},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._client = mock_client

    try:
        data = await service.fetch_schedule({"group": "ALG-1"}, cache_type="student")
        assert data == {"records": [{"subject": "Algorithms"}]}
        cache_key = f"{service.api_url}?group=ALG-1"
        assert cache_key in service.student_cache
        assert service.student_cache[cache_key].etag == '"etag-new-999"'
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_mongodb_fallback_group_schedule(mock_db):
    """Verifies MongoDB fallback for student group schedule."""
    service = BuketovApiService()

    # Insert Group and Schedule into mock_db
    group = Group(group_id=202, name="ИС-21-1")
    await group.insert()

    sched = Schedule(
        group_id=202,
        data=[
            ScheduleDay(
                day="Понедельник",
                subjects=[
                    SubjectItem(time="08:30-09:20", subject="Базы данных /Лекция/\nПопова Н. В.\nАуд. 204/3")
                ],
            )
        ],
    )
    await sched.insert()

    result = await service.fetch_schedule_from_db({"group": "ИС-21-1"}, cache_type="student")
    assert result.get("_fromDb") is True
    assert result.get("total") == 1
    rec = result["records"][0]
    assert rec["day"] == "Понедельник"
    assert rec["dayIndex"] == 1
    assert rec["slot"] == 1
    assert "Базы данных" in rec["subject"]
    assert rec["type"] == "Лекция"
    assert rec["teacher"] == "Попова Н. В."
    assert rec["room"] == "204"
    assert rec["building"] == "3"


@pytest.mark.asyncio
async def test_mongodb_fallback_teacher_schedule(mock_db):
    """Verifies MongoDB fallback for teacher schedule."""
    service = BuketovApiService()

    teacher = Teacher(teacher_id=305, name="Попова Н. В.", full_name="Попова Надежда Викторовна")
    await teacher.insert()

    teacher_sched = TeacherSchedule(
        teacher_id=305,
        data=[
            TeacherScheduleDay(
                day="Вторник",
                groups=[
                    TeacherLessonItem(
                        time="10:00-11:20",
                        group="ИС-21-1 (Ауд. 305/1)",
                        subject="Архитектура ЭВМ",
                        lesson_type="Прак.зан.",
                    )
                ],
            )
        ],
    )
    await teacher_sched.insert()

    result = await service.fetch_schedule_from_db({"teacher": "Попова Н. В."}, cache_type="teacher")
    assert result.get("_fromDb") is True
    assert result.get("total") == 1
    rec = result["records"][0]
    assert rec["day"] == "Вторник"
    assert rec["dayIndex"] == 2
    assert rec["subject"] == "Архитектура ЭВМ"
    assert rec["type"] == "Прак.зан."
    assert rec["teacher"] == "Попова Н. В."
    assert rec["room"] == "305"
    assert rec["building"] == "1"


@pytest.mark.asyncio
async def test_get_group_schedule_pagination():
    """Verifies get_group_schedule auto-pagination concatenation."""
    service = BuketovApiService()

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        offset = request.url.params.get("offset")
        if offset == "200":
            return httpx.Response(
                200,
                json={
                    "records": [{"subject": "Page 2 Lesson"}],
                    "pagination": {"hasMore": False},
                },
            )
        return httpx.Response(
            200,
            json={
                "records": [{"subject": "Page 1 Lesson"}],
                "pagination": {"hasMore": True, "nextOffset": 200},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._client = mock_client

    try:
        res = await service.get_group_schedule("TEST-PAGINATED")
        assert len(res["records"]) == 2
        assert res["records"][0]["subject"] == "Page 1 Lesson"
        assert res["records"][1]["subject"] == "Page 2 Lesson"
        assert calls == 2
    finally:
        await mock_client.aclose()


def test_buketov_api_purge_expired():
    """Verifies purge_expired removes stale items and retains fresh ones."""
    service = BuketovApiService()
    now = time.time()
    service.student_cache.clear()
    service.teacher_cache.clear()

    # Fresh entries
    service.student_cache["fresh_student"] = CacheEntry(data={"s": 1}, timestamp=now)
    service.teacher_cache["fresh_teacher"] = CacheEntry(data={"t": 1}, timestamp=now)

    # Expired entries (older than 10 mins = 600s)
    service.student_cache["expired_student"] = CacheEntry(data={"s": 2}, timestamp=now - 700)
    service.teacher_cache["expired_teacher"] = CacheEntry(data={"t": 2}, timestamp=now - 800)

    purged = service.purge_expired()
    assert purged == 2
    assert "fresh_student" in service.student_cache
    assert "fresh_teacher" in service.teacher_cache
    assert "expired_student" not in service.student_cache
    assert "expired_teacher" not in service.teacher_cache


def test_match_teacher_record_symmetric():
    """Verifies match_teacher_record works symmetrically and handles inverted initials."""
    service = BuketovApiService()
    # Direct surname-first and initials-first combinations
    assert service.match_teacher_record("Попова Н. В.", "Попова Надежда Викторовна") is True
    assert service.match_teacher_record("Попова Надежда Викторовна", "Попова Н. В.") is True
    assert service.match_teacher_record("Н. В. Попова", "Попова Надежда Викторовна") is True
    assert service.match_teacher_record("Попова Надежда Викторовна", "Н. В. Попова") is True
    assert service.match_teacher_record("Н. В. Попова", "Попова Н. В.") is True
    assert service.match_teacher_record("Попова Н. В.", "Н. В. Попова") is True

    # Negative matches
    assert service.match_teacher_record("Попова Н. В.", "Иванова Н. В.") is False
    assert service.match_teacher_record("Попова А. В.", "Попова Н. В.") is False


@pytest.mark.asyncio
async def test_extract_surname_and_query_param():
    """Verifies that get_teacher_schedule extracts the true surname even if initials are first."""
    service = BuketovApiService()

    assert service._extract_surname("Н. В. Попова") == "Попова"
    assert service._extract_surname("Попова Н. В.") == "Попова"
    assert service._extract_surname("Н.В.Попова") == "Попова"
    assert service._extract_surname("Попова") == "Попова"

    captured_q = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_q
        captured_q = request.url.params.get("q")
        return httpx.Response(
            200,
            json={"records": [{"teacher": "Попова Н. В.", "subject": "Math"}]},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._client = mock_client

    try:
        res = await service.get_teacher_schedule("Н. В. Попова")
        assert captured_q == "Попова"
        assert len(res["records"]) == 1
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_fetch_schedule_from_db_teacher_id(mock_db):
    """Verifies that fetch_schedule_from_db properly recognizes teacherId parameter."""
    service = BuketovApiService()

    teacher = Teacher(teacher_id=555, name="Смагулов А. Б.", full_name="Смагулов Арман Болатович")
    await teacher.insert()

    teacher_sched = TeacherSchedule(
        teacher_id=555,
        data=[
            TeacherScheduleDay(
                day="Среда",
                groups=[
                    TeacherLessonItem(
                        time="12:00-12:50",
                        group="ВТ-22-1",
                        subject="Сети",
                        lesson_type="Лекция",
                    )
                ],
            )
        ],
    )
    await teacher_sched.insert()

    result = await service.fetch_schedule_from_db({"teacherId": 555})
    assert result.get("_fromDb") is True
    assert result.get("total") == 1
    assert result["records"][0]["teacher"] == "Смагулов А. Б."
    assert result["records"][0]["subject"] == "Сети"


@pytest.mark.asyncio
async def test_circuit_breaker_falls_back_to_mongodb_when_available(mock_db):
    """Verifies that when Circuit Breaker is OPEN, MongoDB fallback is returned if data exists."""
    service = BuketovApiService()
    service.circuit_state = "API_DOWN"
    service.circuit_tripped_at = time.time()
    service.student_cache.clear()

    group = Group(group_id=901, name="ИС-22-1")
    await group.insert()

    sched = Schedule(
        group_id=901,
        data=[
            ScheduleDay(
                day="Понедельник",
                subjects=[
                    SubjectItem(time="08:30-09:20", subject="Компьютерные сети /Лекция/\nИванов С. П.\nАуд. 201/1")
                ],
            )
        ],
    )
    await sched.insert()

    result = await service.fetch_schedule({"group": "ИС-22-1"}, cache_type="student")
    assert result.get("_fromDb") is True
    assert result.get("total") == 1
    assert "Компьютерные сети" in result["records"][0]["subject"]


@pytest.mark.asyncio
async def test_network_error_falls_back_to_mongodb_when_available(mock_db):
    """Verifies that when network error occurs without cache, MongoDB fallback is returned."""
    service = BuketovApiService()
    service.student_cache.clear()

    group = Group(group_id=902, name="ВТ-23-1")
    await group.insert()

    sched = Schedule(
        group_id=902,
        data=[
            ScheduleDay(
                day="Среда",
                subjects=[
                    SubjectItem(time="10:00-11:20", subject="Философия /Лекция/\nПетров А. А.\nАуд. 105/2")
                ],
            )
        ],
    )
    await sched.insert()

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    service._client = mock_client

    try:
        result = await service.fetch_schedule({"group": "ВТ-23-1"}, cache_type="student")
        assert result.get("_fromDb") is True
        assert result.get("total") == 1
        assert "Философия" in result["records"][0]["subject"]
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_teacher_namesakes_db_fallback(mock_db):
    """Verifies that teachers sharing the same surname match their specific schedule in DB fallback."""
    service = BuketovApiService()

    # Teacher 1: Иванов А. Б.
    teacher1 = Teacher(teacher_id=701, name="Иванов А. Б.", full_name="Иванов Алексей Борисович")
    await teacher1.insert()
    sched1 = TeacherSchedule(
        teacher_id=701,
        data=[
            TeacherScheduleDay(
                day="Вторник",
                groups=[
                    TeacherLessonItem(time="08:30-09:20", group="ИТ-20", subject="История", lesson_type="Лекция")
                ],
            )
        ],
    )
    await sched1.insert()

    # Teacher 2: Иванов С. П. (Namesake)
    teacher2 = Teacher(teacher_id=702, name="Иванов С. П.", full_name="Иванов Серик Полатович")
    await teacher2.insert()
    sched2 = TeacherSchedule(
        teacher_id=702,
        data=[
            TeacherScheduleDay(
                day="Четверг",
                groups=[
                    TeacherLessonItem(time="10:00-11:20", group="ИС-21", subject="Физика", lesson_type="Прак.зан.")
                ],
            )
        ],
    )
    await sched2.insert()

    # Buketov API throws error so it falls back to DB
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    service._client = mock_client

    try:
        res2 = await service.get_teacher_schedule("Иванов С. П.")
        assert res2.get("_fromDb") is True
        assert len(res2["records"]) == 1
        assert res2["records"][0]["subject"] == "Физика"
        assert res2["records"][0]["teacher"] == "Иванов С. П."

        res1 = await service.get_teacher_schedule("Иванов А. Б.")
        assert res1.get("_fromDb") is True
        assert len(res1["records"]) == 1
        assert res1["records"][0]["subject"] == "История"
        assert res1["records"][0]["teacher"] == "Иванов А. Б."
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_pagination_stops_when_rate_limit_headroom_exhausted():
    """Verifies that pagination stops if rate limit headroom (2 req) is exhausted."""
    service = BuketovApiService()
    now = time.time()
    # Fill timestamps so only 1 request is available before reaching rate limit
    # Headroom = 2 requires < (MAX_REQUESTS_PER_MINUTE - 2) requests, so can_make_api_call(headroom=2) is False
    service.outbound_request_timestamps = [now] * (service.MAX_REQUESTS_PER_MINUTE - 1)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "records": [{"subject": f"Page {calls}"}],
                "pagination": {"hasMore": True, "nextOffset": calls * 200},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._client = mock_client

    try:
        # Page 1 passes via check_rate_limit, but page 2 is blocked by can_make_api_call(headroom=2)
        res = await service.get_group_schedule("TEST-HEADROOM")
        assert calls == 1
        assert len(res["records"]) == 1
        assert res["records"][0]["subject"] == "Page 1"
    finally:
        await mock_client.aclose()


def test_match_teacher_record_different_second_initial():
    """Verifies that teachers with same surname and same first initial but different second initial do NOT match."""
    service = BuketovApiService()
    assert service.match_teacher_record("Попова Н. С.", "Попова Н. В.") is False
    assert service.match_teacher_record("Попова Н. В.", "Попова Н. С.") is False
    assert service.match_teacher_record("Н. С. Попова", "Попова Н. В.") is False
    assert service.match_teacher_record("Попова Н. В.", "Попова Надежда Викторовна") is True
    assert service.match_teacher_record("Н. В. Попова", "Попова Н. В.") is True


@pytest.mark.asyncio
async def test_teacher_fallback_resolves_via_directory_when_db_has_conflicting_namesake(mock_db):
    """Verifies that DB fallback does not take a single namesake with conflicting initials, and falls back to directory."""
    service = BuketovApiService()

    # In DB: only Иванов А. Б. (id=701) exists
    teacher1 = Teacher(teacher_id=701, name="Иванов А. Б.", full_name="Иванов Алексей Борисович")
    await teacher1.insert()
    sched1 = TeacherSchedule(
        teacher_id=701,
        data=[
            TeacherScheduleDay(
                day="Вторник",
                groups=[TeacherLessonItem(time="08:30-09:20", group="ИТ-20", subject="История", lesson_type="Лекция")],
            )
        ],
    )
    await sched1.insert()

    # Schedule for Иванов С. П. (id=702) exists in TeacherSchedule
    sched2 = TeacherSchedule(
        teacher_id=702,
        data=[
            TeacherScheduleDay(
                day="Четверг",
                groups=[TeacherLessonItem(time="10:00-11:20", group="ИС-21", subject="Физика", lesson_type="Прак.зан.")],
            )
        ],
    )
    await sched2.insert()

    # Mock teacher_directory_service.find_match to resolve "Иванов С. П." to id 702
    from unittest.mock import patch
    with patch("src.services.teacher_directory.teacher_directory_service.find_match", return_value={"id": 702, "name": "Иванов С. П."}):
        result = await service.fetch_schedule_from_db({"_teacher_full": "Иванов С. П."}, cache_type="teacher")
        assert result.get("_fromDb") is True
        assert len(result["records"]) == 1
        assert result["records"][0]["subject"] == "Физика"
        assert result["records"][0]["teacher"] == "Иванов С. П."


