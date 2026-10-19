"""Comprehensive test suite for CatalogScannerService, weekly cron, and admin /sync.

Verifies:
- Discovery and insertion of brand new groups/teachers while leaving existing ones untouched.
- Zero MongoDB write overhead when no new items are found.
- Immediate searchability of new items in smart search.
- Network timeout/error resilience (partial failures don't crash the scanner).
- College group attributes parsing (id, name, language, age, studentCount).
- Teacher title cleaning and deduplication.
- Authentication flow.
- Admin /sync summary reporting.
- APScheduler weekly Monday 04:00 cron registration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
import zoneinfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.constants import TIMEZONE_NAME
from src.database.models.group import Group
from src.database.models.teacher import Teacher
from src.database.models.user import User
from src.services.catalog_scanner import CatalogScannerService, catalog_scanner_service
from src.services.smart_search import smart_search_service
from src.cron.daily_update import run_weekly_catalog_sync, setup_weekly_catalog_sync


# --- Sample Mock HTML Fixtures ---

HTML_FACULTIES = """
<html>
<body>
  <form>
    <select name="Login">
      <option value="">-- Выберите факультет --</option>
      <option value="Факультет математики и информационных технологий">Факультет математики и информационных технологий</option>
      <option value="Физико-технический факультет">Физико-технический факультет</option>
    </select>
  </form>
</body>
</html>
"""

HTML_PROGRAMS_FIT = """
<html>
<body>
  <div class="content">
    <a href="grupps1.php?id=101">6B06101 - Информационные системы</a>
    <a href="grupps1.php?id=102">6B06102 - Вычислительная техника</a>
  </div>
</body>
</html>
"""

HTML_GROUPS_PROG_101 = """
<html>
<body>
  <table border="1">
    <tr>
      <td><a href="view1.php?id=201&Otdel=рус&Kurs=1&Stud=25">ИС-25-1</a></td>
    </tr>
    <tr>
      <td><a href="view1.php?id=202&Otdel=рус&Kurs=1&Stud=24">ИС-25-2</a></td>
    </tr>
    <tr>
      <td><a href="view1.php?id=203&Otdel=каз&Kurs=1&Stud=20">ИС-26-1</a></td>
    </tr>
    <tr>
      <td><a href="view1.php?id=204&Otdel=рус&Kurs=2&Stud=18">ИТ-26-2</a></td>
    </tr>
  </table>
</body>
</html>
"""

HTML_KAFEDRA = """
<html>
<body>
  <table>
    <tr>
      <td><a href="report_prep.php?d=1&IdKaf=11">Кафедра прикладной математики</a></td>
      <td><a href="report_prep.php?d=1&IdKaf=12">Кафедра радиофизики</a></td>
    </tr>
  </table>
</body>
</html>
"""

HTML_TEACHERS_KAF_11 = """
<html>
<body>
  <table>
    <tr><td>Список кафедры</td></tr>
  </table>
  <table>
    <tr>
      <td><a href="report_prep1.php?IdPrep=501">к.т.н., доц. Касымов А. Б.</a></td>
    </tr>
    <tr>
      <td><a href="report_prep1.php?IdPrep=502">проф. Сериков Б. Т.</a></td>
    </tr>
    <tr>
      <td><a href="report_prep1.php?IdPrep=503">- </a></td>
    </tr>
  </table>
</body>
</html>
"""


def create_mock_client(
    responses_map: dict[str, str] | None = None,
    status_map: dict[str, int] | None = None,
    raise_map: dict[str, Exception] | None = None,
) -> httpx.AsyncClient:
    """Creates an httpx.AsyncClient backed by MockTransport for reliable testing."""
    responses = responses_map or {}
    statuses = status_map or {}
    exceptions = raise_map or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        path_and_query = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
        full_url = str(request.url)

        for key, exc in sorted(exceptions.items(), key=lambda x: len(x[0]), reverse=True):
            if key == path_and_query or key == url_path or (key != "/" and key in full_url):
                raise exc

        for key, code in sorted(statuses.items(), key=lambda x: len(x[0]), reverse=True):
            if key == path_and_query or key == url_path or (key != "/" and key in full_url):
                return httpx.Response(code, text="Error Response")

        for key, body in sorted(responses.items(), key=lambda x: len(x[0]), reverse=True):
            if key == path_and_query or key == url_path or (key != "/" and key in full_url):
                return httpx.Response(200, text=body)

        if "/" in responses and url_path == "/":
            return httpx.Response(200, text=responses["/"])

        return httpx.Response(200, text="<html><body>Default OK</body></html>")

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://schedule.buketov.edu.kz")



# --- Unit Tests: Parsers ---


def test_college_group_attributes_parsing():
    """Verifies parsing of id, name, language, age, and studentCount from HTML."""
    scanner = CatalogScannerService()
    groups = scanner.parse_groups_html(HTML_GROUPS_PROG_101, program_id=101, faculty_id=1)

    assert len(groups) == 4

    g1 = groups[0]
    assert g1["id"] == 201
    assert g1["name"] == "ИС-25-1"
    assert g1["language"] == "рус"
    assert g1["age"] == 1
    assert g1["studentCount"] == 25
    assert g1["program"] == 101
    assert g1["facultyId"] == 1

    g3 = groups[2]
    assert g3["id"] == 203
    assert g3["name"] == "ИС-26-1"
    assert g3["language"] == "каз"
    assert g3["age"] == 1
    assert g3["studentCount"] == 20


def test_teacher_title_cleaning_and_deduplication():
    """Verifies stripping of academic degrees and ignoring placeholder names."""
    scanner = CatalogScannerService()
    teachers = scanner.parse_teachers_html(HTML_TEACHERS_KAF_11, department_id=11)

    assert len(teachers) == 2

    t1 = teachers[0]
    assert t1["id"] == 501
    assert t1["name"] == "Касымов А. Б."
    assert t1["fullName"] == "к.т.н., доц. Касымов А. Б."
    assert t1["department"] == 11

    t2 = teachers[1]
    assert t2["id"] == 502
    assert t2["name"] == "Сериков Б. Т."
    assert t2["fullName"] == "проф. Сериков Б. Т."


def test_faculties_and_departments_parsing():
    """Verifies extraction of faculties and departments."""
    scanner = CatalogScannerService()
    faculties = scanner.parse_faculties_html(HTML_FACULTIES)
    assert len(faculties) == 2
    assert faculties[0]["name"] == "Факультет математики и информационных технологий"
    assert faculties[1]["name"] == "Физико-технический факультет"

    programs = scanner.parse_programs_html(HTML_PROGRAMS_FIT, faculty_id=1)
    assert len(programs) == 2
    assert programs[0]["id"] == 101
    assert programs[0]["name"] == "6B06101 - Информационные системы"

    depts = scanner.parse_departments_html(HTML_KAFEDRA)
    assert len(depts) == 2
    assert depts[0]["id"] == 11
    assert depts[0]["name"] == "Кафедра прикладной математики"


# --- Integration Tests: Scan & Sync with In-Memory Mock DB ---


@pytest.mark.asyncio
async def test_scanner_discovers_only_unindexed_groups(mock_db):
    """Verifies that only new groups are inserted while existing documents remain untouched."""
    # Seed MongoDB with 2 existing groups
    await Group(id=201, name="ИС-25-1", language="рус", studentCount=25).insert()
    await Group(id=202, name="ИС-25-2", language="рус", studentCount=24).insert()

    initial_count = await Group.count()
    assert initial_count == 2

    # Mock server returns 4 groups (201, 202 already exist; 203, 204 are brand new)
    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": HTML_GROUPS_PROG_101,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 2
    assert summary["new_teachers"] == 0

    total_groups = await Group.count()
    assert total_groups == 4

    # Verify newly added groups are present in DB
    g203 = await Group.find_one(Group.group_id == 203)
    assert g203 is not None
    assert g203.name == "ИС-26-1"
    assert g203.language == "каз"

    g204 = await Group.find_one(Group.group_id == 204)
    assert g204 is not None
    assert g204.name == "ИТ-26-2"


@pytest.mark.asyncio
async def test_scanner_discovers_new_teachers(mock_db):
    """Verifies discovery of new teachers and deduplication against existing teachers."""
    # Seed DB with existing teacher (with cleaned name)
    await Teacher(id=501, name="Касымов А. Б.", fullName="Касымов А. Б.", department=11).insert()

    mock_client = create_mock_client({
        "/": "<html><body></body></html>",
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": HTML_TEACHERS_KAF_11,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_teachers"] == 1
    assert summary["new_groups"] == 0

    total_teachers = await Teacher.count()
    assert total_teachers == 2

    t502 = await Teacher.find_one(Teacher.teacher_id == 502)
    assert t502 is not None
    assert t502.name == "Сериков Б. Т."


@pytest.mark.asyncio
async def test_scanner_zero_db_overhead_when_no_changes(mock_db):
    """Verifies that no database write queries are executed when no new entities exist."""
    # Seed DB with all 4 groups from HTML
    await Group(id=201, name="ИС-25-1").insert()
    await Group(id=202, name="ИС-25-2").insert()
    await Group(id=203, name="ИС-26-1").insert()
    await Group(id=204, name="ИТ-26-2").insert()

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": HTML_GROUPS_PROG_101,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)

    with patch.object(Group, "insert_many", AsyncMock()) as mock_group_insert:
        with patch.object(Teacher, "insert_many", AsyncMock()) as mock_teacher_insert:
            summary = await scanner.scan_and_sync(client=mock_client)

            assert summary["new_groups"] == 0
            assert summary["new_teachers"] == 0
            assert mock_group_insert.await_count == 0
            assert mock_teacher_insert.await_count == 0


@pytest.mark.asyncio
async def test_new_group_immediately_searchable_in_smart_search(mock_db):
    """Verifies that newly discovered groups are immediately searchable in smart search."""
    smart_search_service.invalidate_caches()
    await smart_search_service.warmup()

    # Query before scanning: does not exist
    res_before = await smart_search_service.search("РОБОТ-26-1")
    assert res_before.exact_group is None

    # Mock server returns a new group "РОБОТ-26-1"
    html_robot_group = """
    <html><body><table>
      <tr><td><a href="view1.php?id=999&Otdel=рус&Kurs=1&Stud=20">РОБОТ-26-1</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": html_robot_group,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 1

    # Search immediately after scanning without restarting or waiting
    res_after = await smart_search_service.search("РОБОТ-26-1")
    assert res_after.exact_group is not None
    assert res_after.exact_group["name"] == "РОБОТ-26-1"
    assert res_after.exact_group["id"] == 999


@pytest.mark.asyncio
async def test_scanner_network_resilience_and_timeout(mock_db):
    """Verifies that network timeouts or HTTP errors on one page do not abort the scanner."""
    # Faculty 1 fails with 500, but Faculty 2 and Kafedra succeed
    html_two_faculties = """
    <html><body><select name="Login">
      <option value="Fac1">Failing Faculty</option>
      <option value="Fac2">Healthy Faculty</option>
    </select></body></html>
    """
    html_prog_healthy = """
    <html><body><a href="grupps1.php?id=888">Healthy Program</a></body></html>
    """
    html_group_healthy = """
    <html><body><table>
      <tr><td><a href="view1.php?id=777&Kurs=1">HEALTHY-26-1</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client(
        responses_map={
            "/": html_two_faculties,
            "/stud.php": html_prog_healthy,
            "/grupps1.php?id=888": html_group_healthy,
            "/kafedra.php": "<html><body></body></html>",
        },
        raise_map={
            "index.php?x": httpx.ConnectTimeout("Connection timed out to KarU gateway"),
        },
    )

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    # Scanner completes smoothly without raising exceptions
    assert isinstance(summary, dict)
    assert "elapsed_seconds" in summary
    assert "new_groups" in summary


@pytest.mark.asyncio
async def test_scanner_authentication_flow():
    """Verifies authentication request sequence and credentials validation."""
    scanner = CatalogScannerService()

    # 1. When credentials are not set -> returns False gracefully
    with patch("src.services.catalog_scanner.settings.KSU_LOGIN", None):
        with patch("src.services.catalog_scanner.settings.KSU_PASSWORD", None):
            client = create_mock_client()
            res = await scanner.authenticate(client)
            assert res is False

    # 2. When credentials are set and server responds 200/302 -> returns True
    with patch("src.services.catalog_scanner.settings.KSU_LOGIN", "TestUser"):
        with patch("src.services.catalog_scanner.settings.KSU_PASSWORD", "TestPass"):
            client = create_mock_client(
                responses_map={
                    "/login.php": "<html><body>Login Form</body></html>",
                }
            )
            res = await scanner.authenticate(client)
            assert res is True


@pytest.mark.asyncio
async def test_admin_router_sync_reporting(mock_db):
    """Verifies that /sync admin command reports newly discovered entities summary."""
    from src.bot.handlers.admin_router import handle_sync_new_data
    from tests.python.test_handlers import make_message

    admin_id = 887766
    await User(userId=admin_id, username="admin_tester", isAdmin=True).insert()

    msg = make_message(user_id=admin_id, text="/sync")

    with patch.object(
        catalog_scanner_service,
        "scan_and_sync",
        AsyncMock(return_value={"elapsed_seconds": 2.5, "new_groups": 4, "new_teachers": 3}),
    ):
        await handle_sync_new_data(msg)

        # Allow background task to execute
        await asyncio.sleep(0.05)

        assert msg.answer.call_count >= 1
        answers = [call[0][0] for call in msg.answer.call_args_list]

        # Initial launch message
        assert any("Синхронизация запущена" in a for a in answers)
        # Completion summary message
        summary_msg = [a for a in answers if "Синхронизация каталога завершена" in a]
        assert len(summary_msg) == 1
        assert "Новых групп: <b>4</b>" in summary_msg[0]
        assert "Новых преподавателей: <b>3</b>" in summary_msg[0]


@pytest.mark.asyncio
async def test_cron_weekly_catalog_sync_registration():
    """Verifies that APScheduler schedules weekly catalog sync at Mon 04:00 Asia/Almaty."""
    sched = AsyncIOScheduler(timezone=zoneinfo.ZoneInfo(TIMEZONE_NAME))
    setup_weekly_catalog_sync(sched)

    job = sched.get_job("weekly_catalog_sync")
    assert job is not None
    assert job.name == "Weekly University Catalog Sync (Mon 04:00 Asia/Almaty)"
    assert str(job.trigger) == "cron[day_of_week='mon', hour='4', minute='0']"

    # Verify execution of the registered function
    with patch.object(
        catalog_scanner_service,
        "scan_and_sync",
        AsyncMock(return_value={"elapsed_seconds": 1.0, "new_groups": 0, "new_teachers": 0}),
    ) as mock_scan:
        res = await run_weekly_catalog_sync()
        assert res["elapsed_seconds"] == 1.0
        assert mock_scan.await_count == 1


def test_parse_faculties_without_value_attribute():
    """Verifies that <option> tags without explicit value attributes are parsed correctly."""
    scanner = CatalogScannerService()
    html_sample = """
    <html><body>
      <select name="Login">
        <option>-- Выберите факультет --</option>
        <option>Факультет компьютерных наук</option>
        <option value="Физический">Физический факультет</option>
      </select>
    </body></html>
    """
    faculties = scanner.parse_faculties_html(html_sample)
    assert len(faculties) == 2
    assert faculties[0]["name"] == "Факультет компьютерных наук"
    assert faculties[0]["value"] == "Факультет компьютерных наук"
    assert faculties[1]["name"] == "Физический факультет"
    assert faculties[1]["value"] == "Физический"


def test_parse_groups_ignores_navigation_links():
    """Verifies that navigation links ('Назад', 'Главная', 'stud.php') are not parsed as student groups."""
    scanner = CatalogScannerService()
    html_with_nav = """
    <html><body>
      <div class="nav">
        <a href="stud.php?id=10">Назад к списку специальностей</a>
        <a href="index.php?id=1">Главная</a>
      </div>
      <table>
        <tr>
          <td><a href="view1.php?id=305&Otdel=рус&Kurs=2&Stud=19">ВТиПО-24-1</a></td>
        </tr>
      </table>
    </body></html>
    """
    groups = scanner.parse_groups_html(html_with_nav, program_id=101, faculty_id=4)
    assert len(groups) == 1
    assert groups[0]["id"] == 305
    assert groups[0]["name"] == "ВТиПО-24-1"


@pytest.mark.asyncio
async def test_groups_faculty_id_linked_to_real_mongodb_faculty(mock_db):
    """Verifies that groups inherit the genuine faculty_id from MongoDB (e.g. ID=4), not arbitrary 1-based index."""
    from src.database.models.faculty import Faculty

    # Seed MongoDB with Faculty ID 4
    await Faculty(id=4, name="Факультет математики и информационных технологий").insert()

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,  # Returns 2 faculties; first is Math (option 1)
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": HTML_GROUPS_PROG_101,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 4

    # Verify group in DB has facultyId == 4 (matching MongoDB Faculty), NOT 1
    g201 = await Group.find_one(Group.group_id == 201)
    assert g201 is not None
    assert g201.faculty == 4


@pytest.mark.asyncio
async def test_scanner_authentication_fails_on_login_form_returned():
    """Verifies authentication returns False when server rejects credentials and returns login form."""
    scanner = CatalogScannerService()
    with patch("src.services.catalog_scanner.settings.KSU_LOGIN", "InvalidUser"):
        with patch("src.services.catalog_scanner.settings.KSU_PASSWORD", "WrongPass"):
            # Mock server returns 200 with password input on failed POST
            client = create_mock_client(
                responses_map={
                    "/login.php": '<form action="login.php"><input type="password" name="password"></form>',
                }
            )
            res = await scanner.authenticate(client)
            assert res is False


def test_scanner_base_url_defaults_to_official_karnu_portal():
    """Verifies scanner strictly uses official portal URL https://schedule.karnu-buketov.edu.kz."""
    scanner = CatalogScannerService()
    assert scanner.base_url == "https://schedule.karnu-buketov.edu.kz"

    legacy_scanner = CatalogScannerService(base_url="https://schedule.buketov.edu.kz")
    assert legacy_scanner.base_url == "https://schedule.karnu-buketov.edu.kz"


@pytest.mark.asyncio
async def test_scanner_deduplicates_groups_by_normalized_name(mock_db):
    """Verifies that existing group КО-24-2к is not duplicated even if incoming group has different ID or formatting."""
    # Seed MongoDB with existing group КО-24-2к (ID: 6337)
    await Group(id=6337, name="КО-24-2к", language="каз", studentCount=18).insert()

    initial_count = await Group.count()
    assert initial_count == 1

    # Incoming HTML from portal contains same group with latin/spaces variation and different ID (e.g. 9999)
    html_latin_group = """
    <html><body><table>
      <tr><td><a href="view1.php?id=9999&Otdel=каз&Kurs=2&Stud=18">KO-24-2k</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": html_latin_group,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    # Must be recognized as already existing, so new_groups is 0
    assert summary["new_groups"] == 0
    total_groups = await Group.count()
    assert total_groups == 1


@pytest.mark.asyncio
async def test_scanner_deduplicates_groups_by_id(mock_db):
    """Verifies that existing group ID 6337 is not duplicated even if name text differs slightly."""
    await Group(id=6337, name="КО-24-2к").insert()

    html_diff_name = """
    <html><body><table>
      <tr><td><a href="view1.php?id=6337&Kurs=2">КО-24-2к (новое)</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": html_diff_name,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 0
    assert await Group.count() == 1


@pytest.mark.asyncio
async def test_scanner_deduplicates_teachers_by_normalized_name_and_titles(mock_db):
    """Verifies that existing teacher Додонова А. Ш. is not duplicated if incoming has academic title prefix."""
    await Teacher(id=8, name="Додонова А. Ш.", fullName="Додонова А. Ш.", department=11).insert()

    html_dodonova = """
    <html><body><table>
      <tr><td><a href="report_prep1.php?IdPrep=8888">к.п.н., доц. Додонова А. Ш.</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": "<html><body></body></html>",
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": html_dodonova,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_teachers"] == 0
    assert await Teacher.count() == 1


@pytest.mark.asyncio
async def test_scanner_teacher_enrichment_without_name_error(mock_db):
    """Verifies that new teacher insertion enriches profile cleanly without NameError."""
    html_new_teacher = """
    <html><body><table>
      <tr><td><a href="report_prep1.php?IdPrep=7777">проф. Исабеков К. М.</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": "<html><body></body></html>",
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": html_new_teacher,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_teachers"] == 1
    t = await Teacher.find_one(Teacher.teacher_id == 7777)
    assert t is not None
    assert t.name == "Исабеков К. М."


@pytest.mark.asyncio
async def test_bot_viewing_schedule_never_creates_catalog_groups_or_teachers(mock_db):
    """Verifies that viewing a schedule in the bot strictly caches Schedule/TeacherSchedule and NEVER creates Group/Teacher."""
    from src.bot.handlers.callbacks_router import _bg_persist_student_schedule, _bg_persist_teacher_schedule
    from src.database.models.schedule import Schedule
    from src.database.models.teacher_schedule import TeacherSchedule

    # Seed 1 catalog group
    await Group(id=6337, name="КО-24-2к").insert()
    assert await Group.count() == 1
    assert await Teacher.count() == 0

    sample_student_sched = [
        {"day": "Понедельник", "subjects": [{"time": "8.30-9.20", "subject": "Высшая математика"}]}
    ]
    sample_teacher_sched = [
        {"day": "Понедельник", "groups": [{"time": "8.30-9.20", "group": "КО-24-2к", "subject": "Высшая математика", "lessonType": "Лекция"}]}
    ]

    # User in Telegram views group schedule and teacher schedule
    await _bg_persist_student_schedule(6337, sample_student_sched)
    await _bg_persist_teacher_schedule(8, sample_teacher_sched)

    # 1. Schedule collections have the cached data
    s_doc = await Schedule.find_one(Schedule.group_id == 6337)
    assert s_doc is not None
    assert s_doc.has_schedule is True

    ts_doc = await TeacherSchedule.find_one(TeacherSchedule.teacher_id == 8)
    assert ts_doc is not None

    # 2. But the catalog collections (Group and Teacher) NEVER had new groups or teachers created
    assert await Group.count() == 1
    assert await Teacher.count() == 0


def test_safe_int_helper():
    """Verifies that _safe_int correctly parses various representations and rejects non-numerics."""
    from src.services.catalog_scanner import _safe_int

    assert _safe_int(123) == 123
    assert _safe_int("123") == 123
    assert _safe_int(" 123 ") == 123
    assert _safe_int(123.0) == 123
    assert _safe_int("123.0") == 123
    assert _safe_int(None) is None
    assert _safe_int("") is None
    assert _safe_int("abc") is None


def test_teacher_name_variants_generation():
    """Verifies generation of initials, compact variants, and title stripping."""
    from src.services.catalog_scanner import _get_teacher_name_variants

    variants = _get_teacher_name_variants("д.э.н., проф. Додонова Асия Шамильевна")
    assert "додонова асия шамильевна" in variants
    assert "додоноваасияшамильевна" in variants
    assert "додонова а ш" in variants
    assert "додоновааш" in variants

    # Empty or None
    assert _get_teacher_name_variants(None) == set()
    assert _get_teacher_name_variants("") == set()


@pytest.mark.asyncio
async def test_teacher_deduplication_fullname_in_db_initials_on_site(mock_db):
    """Verifies that if DB has full name, incoming initials on site are matched even with different ID."""
    await Teacher(id=8, name="Додонова Асия Шамильевна", fullName="Додонова Асия Шамильевна").insert()

    html_site = """
    <html><body><table>
      <tr><td><a href="report_prep1.php?IdPrep=99999">к.э.н., доц. Додонова А. Ш.</a></td></tr>
    </table></body></html>
    """
    mock_client = create_mock_client({
        "/": "<html><body></body></html>",
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": html_site,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_teachers"] == 0
    assert await Teacher.count() == 1


@pytest.mark.asyncio
async def test_all_five_user_groups_deduplication(mock_db):
    """Verifies that all 5 groups from user complaint (6337, 6346, 6553, 7096, 7500) are never re-added."""
    user_groups = [
        (6337, "КО-24-2к"),
        (6346, "КИ-24-1к"),
        (6553, "ТЭ-24-1к"),
        (7096, "М2-НН-25-1к"),
        (7500, "М2-ХЕ-26-1к"),
    ]
    for g_id, g_name in user_groups:
        await Group(id=g_id, name=g_name).insert()

    assert await Group.count() == 5

    # Portal HTML contains all 5 groups with spacing and Latin homoglyphs variations
    html_five_groups = """
    <html><body><table>
      <tr><td><a href="view1.php?id=6337&Kurs=2">KO-24-2k</a></td></tr>
      <tr><td><a href="view1.php?id=6346&Kurs=2">КИ - 24 - 1к</a></td></tr>
      <tr><td><a href="view1.php?id=6553&Kurs=2">ТЭ-24-1к</a></td></tr>
      <tr><td><a href="view1.php?id=7096&Kurs=1">M2-HH-25-1k</a></td></tr>
      <tr><td><a href="view1.php?id=7500&Kurs=1">М2-ХЕ-26-1к</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": html_five_groups,
        "/kafedra.php": "<html><body></body></html>",
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 0
    assert await Group.count() == 5


@pytest.mark.asyncio
async def test_all_five_user_teachers_deduplication(mock_db):
    """Verifies that all 5 teachers from user complaint (8, 4414, 4381, 3191, 3375) are never re-added."""
    user_teachers = [
        (8, "Додонова А. Ш."),
        (4414, "Народхан Дашжан"),
        (4381, "Сулейменов Н.М."),
        (3191, "Раисова А.А."),
        (3375, "Сагадатова А.Т."),
    ]
    for t_id, t_name in user_teachers:
        await Teacher(id=t_id, name=t_name, fullName=t_name).insert()

    assert await Teacher.count() == 5

    html_five_teachers = """
    <html><body><table>
      <tr><td><a href="report_prep1.php?IdPrep=8">к.э.н. Додонова А.Ш.</a></td></tr>
      <tr><td><a href="report_prep1.php?IdPrep=4414">аға оқытушы Народхан Дашжан</a></td></tr>
      <tr><td><a href="report_prep1.php?IdPrep=4381">доц. Сулейменов Н. М.</a></td></tr>
      <tr><td><a href="report_prep1.php?IdPrep=3191">пр. Раисова А. А.</a></td></tr>
      <tr><td><a href="report_prep1.php?IdPrep=3375">PhD Сагадатова А. Т.</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": "<html><body></body></html>",
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": html_five_teachers,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_teachers"] == 0
    assert await Teacher.count() == 5


@pytest.mark.asyncio
async def test_genuinely_new_entities_inserted_and_warmed_up(mock_db):
    """Verifies that 100% genuine new group and teacher are inserted to DB and warm up search."""
    # Existing DB has 1 group and 1 teacher
    await Group(id=100, name="ИС-20-1").insert()
    await Teacher(id=200, name="Асанов Б. К.").insert()

    # Portal returns existing + 1 truly new group and 1 truly new teacher
    html_groups = """
    <html><body><table>
      <tr><td><a href="view1.php?id=100&Kurs=4">ИС-20-1</a></td></tr>
      <tr><td><a href="view1.php?id=9999&Kurs=1&Otdel=рус&Stud=20">НОВ-26-1</a></td></tr>
    </table></body></html>
    """
    html_teachers = """
    <html><body><table>
      <tr><td><a href="report_prep1.php?IdPrep=200">Асанов Б. К.</a></td></tr>
      <tr><td><a href="report_prep1.php?IdPrep=8888">магистр Ахметов С. Т.</a></td></tr>
    </table></body></html>
    """

    mock_client = create_mock_client({
        "/": HTML_FACULTIES,
        "/stud.php": HTML_PROGRAMS_FIT,
        "/grupps1.php?id=101": html_groups,
        "/kafedra.php": HTML_KAFEDRA,
        "/report_prep.php?d=1&IdKaf=11": html_teachers,
    })

    scanner = CatalogScannerService(pacing_delay=0.0)
    summary = await scanner.scan_and_sync(client=mock_client)

    assert summary["new_groups"] == 1
    assert summary["new_teachers"] == 1

    assert await Group.count() == 2
    assert await Teacher.count() == 2

    new_g = await Group.find_one(Group.group_id == 9999)
    assert new_g is not None
    assert new_g.name == "НОВ-26-1"

    new_t = await Teacher.find_one(Teacher.teacher_id == 8888)
    assert new_t is not None
    assert new_t.name == "Ахметов С. Т."


