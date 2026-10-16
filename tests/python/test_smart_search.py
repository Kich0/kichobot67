"""Unit tests for SmartSearchService, RapidFuzz matching, and query classification."""

import pytest
from src.services.smart_search import (
    SearchResultType,
    SmartSearchService,
    calculate_similarity,
    smart_search_service,
)


@pytest.fixture
def mock_teachers():
    return [
        {
            "id": 1,
            "name": "Аяған Е. С.",
            "fullName": "Аяған Еркін Сағынтайұлы",
            "firstName": "Еркін",
            "lastName": "Аяған",
            "patronymic": "Сағынтайұлы",
            "aliases": [],
            "isHead": False,
        },
        {
            "id": 2,
            "name": "Попова Н. В.",
            "fullName": "Попова Надежда Викторовна",
            "firstName": "Надежда",
            "lastName": "Попова",
            "patronymic": "Викторовна",
            "aliases": ["Попова Н.В."],
            "isHead": True,
        },
        {
            "id": 3,
            "name": "Нигай Е. В.",
            "fullName": "Нигай Евгения Валентиновна",
            "firstName": "Евгения",
            "lastName": "Нигай",
            "patronymic": "Валентиновна",
            "aliases": [],
            "isHead": False,
        },
        {
            "id": 4,
            "name": "Жунусова Б. К.",
            "fullName": "Жунусова Баян Кенесовна",
            "firstName": "Баян",
            "lastName": "Жунусова",
            "patronymic": "Кенесовна",
            "aliases": [],
            "isHead": False,
        },
    ]


@pytest.fixture
def mock_groups():
    return [
        {"id": 101, "name": "ИС-21-1", "language": "рус"},
        {"id": 102, "name": "ИС-21-2", "language": "рус"},
        {"id": 103, "name": "ВТиПО-21-1", "language": "рус"},
        {"id": 104, "name": "ФИЗ-22-1", "language": "каз"},
    ]


def test_calculate_similarity():
    """Verifies Levenshtein similarity calculation."""
    assert calculate_similarity("Попова", "Попова") == 1.0
    assert calculate_similarity("Попова", "Попово") >= 0.8
    assert calculate_similarity("", "Попова") == 0.0
    assert calculate_similarity("", "") == 0.0


def test_find_teachers_short_query(mock_teachers):
    """Verifies short query (<= 2 chars) strictly matches start of surname or first name."""
    res = smart_search_service.find_teachers("по", mock_teachers)
    assert len(res) == 1
    assert res[0]["lastName"] == "Попова"

    res_ni = smart_search_service.find_teachers("ни", mock_teachers)
    assert len(res_ni) == 1
    assert res_ni[0]["lastName"] == "Нигай"


def test_find_teachers_permutations_and_typos(mock_teachers):
    """Verifies full name search, word order permutations, and 1-letter typos."""
    # Permutation: "Надежда Попова"
    res1 = smart_search_service.find_teachers("Надежда Попова", mock_teachers)
    assert len(res1) > 0
    assert res1[0]["fullName"] == "Попова Надежда Викторовна"

    # Kazakh name: "Аяған Еркін"
    res2 = smart_search_service.find_teachers("Аяған Еркін", mock_teachers)
    assert len(res2) > 0
    assert res2[0]["fullName"] == "Аяған Еркін Сағынтайұлы"

    # 1-letter typo in surname: "Попово" -> "Попова"
    res_typo = smart_search_service.find_teachers("Попово", mock_teachers)
    assert len(res_typo) > 0
    assert res_typo[0]["lastName"] == "Попова"

    # 1-letter typo in Kazakh surname: "Аягон" -> "Аяған"
    res_kz_typo = smart_search_service.find_teachers("Аягон", mock_teachers)
    assert len(res_kz_typo) > 0
    assert res_kz_typo[0]["lastName"] == "Аяған"


def test_find_groups_prefixes_and_typos(mock_groups):
    """Verifies group search by prefix, compact name, and 1-letter typo."""
    # Abbreviation prefix: "ис"
    res_is = smart_search_service.find_groups("ис", mock_groups)
    assert len(res_is) == 2
    assert res_is[0]["name"] == "ИС-21-1"

    # Exact group name
    res_exact = smart_search_service.find_groups("ИС-21-1", mock_groups)
    assert len(res_exact) == 2
    assert res_exact[0]["name"] == "ИС-21-1"

    # 1-letter typo: "втпо" -> "ВТиПО-21-1"
    res_typo = smart_search_service.find_groups("втпо", mock_groups)
    assert len(res_typo) > 0
    assert res_typo[0]["name"] == "ВТиПО-21-1"


@pytest.mark.asyncio
async def test_smart_search_exact_teacher(mock_teachers, mock_groups):
    """Verifies classification to EXACT_TEACHER when full name matches."""
    result = await smart_search_service.search(
        "Попова Надежда Викторовна",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.EXACT_TEACHER
    assert result.exact_teacher is not None
    assert result.exact_teacher["name"] == "Попова Н. В."


@pytest.mark.asyncio
async def test_smart_search_exact_group(mock_teachers, mock_groups):
    """Verifies classification to EXACT_GROUP when exact group matches."""
    result = await smart_search_service.search(
        "ВТиПО-21-1",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.EXACT_GROUP
    assert result.exact_group is not None
    assert result.exact_group["name"] == "ВТиПО-21-1"


@pytest.mark.asyncio
async def test_smart_search_groups_list(mock_teachers, mock_groups):
    """Verifies classification to GROUPS_LIST when multiple groups and 0 teachers match."""
    result = await smart_search_service.search(
        "ИС",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.GROUPS_LIST
    assert len(result.groups) == 2


@pytest.mark.asyncio
async def test_smart_search_teachers_list(mock_teachers, mock_groups):
    """Verifies classification to TEACHERS_LIST when multiple teachers match and 0 groups."""
    # Let's add another teacher with same prefix
    teachers = list(mock_teachers) + [
        {
            "id": 5,
            "name": "Попов А. В.",
            "fullName": "Попов Александр Викторович",
            "firstName": "Александр",
            "lastName": "Попов",
            "patronymic": "Викторович",
            "aliases": [],
            "isHead": False,
        }
    ]
    result = await smart_search_service.search(
        "Попов",
        teachers=teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.TEACHERS_LIST
    assert len(result.teachers) >= 2


@pytest.mark.asyncio
async def test_smart_search_combined_scenario(mock_teachers, mock_groups):
    """Verifies classification to COMBINED when both teachers and groups match."""
    teachers = list(mock_teachers) + [
        {
            "id": 6,
            "name": "Физ А. А.",
            "fullName": "Физ Алихан Азаматович",
            "firstName": "Алихан",
            "lastName": "Физ",
            "patronymic": "Азаматович",
            "aliases": [],
            "isHead": False,
        }
    ]
    result = await smart_search_service.search(
        "Физ",
        teachers=teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.COMBINED
    assert len(result.teachers) > 0
    assert len(result.groups) > 0


@pytest.mark.asyncio
async def test_smart_search_fuzzy_suggestions(mock_teachers, mock_groups):
    """Verifies fuzzy suggestions for misspelled queries with strict assertion."""
    # 2-letter typo on teacher "Попова" -> "Попаваа" (similarity 5/7 = 0.714 >= 0.60)
    result_teacher_typo = await smart_search_service.search(
        "Попаваа",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result_teacher_typo.result_type == SearchResultType.FUZZY_SUGGESTIONS
    assert len(result_teacher_typo.fuzzy_suggestions) > 0
    assert result_teacher_typo.fuzzy_suggestions[0].type == "teacher"
    assert result_teacher_typo.fuzzy_suggestions[0].item["lastName"] == "Попова"

    # Misspelled group "ФИЗЗ-22-1" -> "ФИЗ-22-1" (similarity 8/9 = 0.889 >= 0.60)
    result_group_typo = await smart_search_service.search(
        "ФИЗЗ-22-1",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result_group_typo.result_type in (SearchResultType.FUZZY_SUGGESTIONS, SearchResultType.EXACT_GROUP)
    if result_group_typo.result_type == SearchResultType.FUZZY_SUGGESTIONS:
        assert len(result_group_typo.fuzzy_suggestions) > 0
        assert result_group_typo.fuzzy_suggestions[0].type == "group"
        assert result_group_typo.fuzzy_suggestions[0].item["name"] == "ФИЗ-22-1"


@pytest.mark.asyncio
async def test_smart_search_not_found(mock_teachers, mock_groups):
    """Verifies NOT_FOUND when gibberish query has no matching or similar items."""
    result = await smart_search_service.search(
        "zzzzzzqqqqqqqq9999",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result.result_type == SearchResultType.NOT_FOUND


@pytest.mark.asyncio
async def test_smart_search_welcome_fallback():
    """Verifies WELCOME_FALLBACK for non-alphanumeric queries."""
    result = await smart_search_service.search("!")
    assert result.result_type == SearchResultType.WELCOME_FALLBACK

    result2 = await smart_search_service.search("   ")
    assert result2.result_type == SearchResultType.WELCOME_FALLBACK

    result3 = await smart_search_service.search("---")
    assert result3.result_type == SearchResultType.WELCOME_FALLBACK


@pytest.mark.asyncio
async def test_smart_search_single_letter_group_and_teacher():
    """Verifies that single-letter queries like 'Ю' find matching groups and teachers."""
    groups = [
        {"id": 1, "name": "Ю-26-1к"},
        {"id": 2, "name": "Ю-25-2р"},
        {"id": 3, "name": "Ю-24-1к"},
        {"id": 4, "name": "ИС-25-3р"},
    ]
    teachers = [
        {"id": 10, "name": "Юсупова А.", "fullName": "Юсупова Асель", "lastName": "Юсупова", "firstName": "Асель", "aliases": []},
        {"id": 11, "name": "Попова Н.", "fullName": "Попова Надежда", "lastName": "Попова", "firstName": "Надежда", "aliases": []},
    ]

    # 1. Direct find_groups with single letter 'Ю'
    matched_g = smart_search_service.find_groups("Ю", groups)
    assert len(matched_g) == 3
    # Check natural sorting: 26 before 25 before 24
    assert matched_g[0]["name"] == "Ю-26-1к"
    assert matched_g[1]["name"] == "Ю-25-2р"
    assert matched_g[2]["name"] == "Ю-24-1к"

    # 2. Direct find_teachers with single letter 'Ю'
    matched_t = smart_search_service.find_teachers("Ю", teachers)
    assert len(matched_t) == 1
    assert matched_t[0]["lastName"] == "Юсупова"

    # 3. Full search workflow with 'Ю'
    res = await smart_search_service.search("Ю", groups=groups, teachers=teachers)
    assert res.result_type == SearchResultType.COMBINED
    assert len(res.groups) == 3
    assert len(res.teachers) == 1
    assert res.prefer_groups_first is True



@pytest.mark.asyncio
async def test_smart_search_kazakh_language_detection(mock_teachers, mock_groups):
    """Verifies query containing Kazakh characters automatically switches language to kz."""
    result = await smart_search_service.search(
        "Аяған",
        user_language="ru",
        teachers=mock_teachers,
        groups=mock_groups,
    )
    assert result.user_language == "kz"


@pytest.mark.asyncio
async def test_smart_search_real_directory_teachers():
    """Verifies that search works on real teacher profiles from teachers_full_directory.json."""
    from src.services.teacher_directory import teacher_directory_service
    # Search for an actual teacher from data/teachers_full_directory.json
    found = smart_search_service.find_teachers("Тлеукенова", teacher_directory_service.teachers)
    assert len(found) > 0
    assert any("Тлеукенова" in t.get("fullName", "") for t in found)

    # Full search workflow with directory fallback
    result = await smart_search_service.search("Тлеукенова", groups=[])
    assert result.result_type in (SearchResultType.EXACT_TEACHER, SearchResultType.TEACHERS_LIST)
    if result.result_type == SearchResultType.EXACT_TEACHER:
        assert "Тлеукенова" in result.exact_teacher["fullName"]
    else:
        assert any("Тлеукенова" in t["fullName"] for t in result.teachers)


@pytest.mark.asyncio
async def test_smart_search_combined_prefer_groups_first(mock_teachers, mock_groups):
    """Verifies prefer_groups_first is True when query matches group pattern or contains digits."""
    teachers = list(mock_teachers) + [
        {
            "id": 10,
            "name": "Исаев И. И.",
            "fullName": "Исаев Ислам Исаевич",
            "firstName": "Ислам",
            "lastName": "Исаев",
            "patronymic": "Исаевич",
            "aliases": ["ИС 21"],
            "isHead": False,
        },
        {
            "id": 11,
            "name": "Исаев С. С.",
            "fullName": "Исаев Серик Сейтович",
            "firstName": "Серик",
            "lastName": "Исаев",
            "patronymic": "Сейтович",
            "aliases": ["ИС 21"],
            "isHead": False,
        },
    ]
    # "ИС" is a group pattern and matches group names -> prefer_groups_first = True
    res_is = await smart_search_service.search("ИС", teachers=teachers, groups=mock_groups)
    assert res_is.result_type == SearchResultType.COMBINED
    assert res_is.prefer_groups_first is True

    # "ИС 21" contains digits and matches both teachers and groups -> prefer_groups_first = True
    res_digits = await smart_search_service.search("ИС 21", teachers=teachers, groups=mock_groups)
    assert res_digits.result_type == SearchResultType.COMBINED
    assert res_digits.prefer_groups_first is True


@pytest.mark.asyncio
async def test_find_teachers_and_groups_completeness(mock_groups):
    """Verifies that short queries like 'ис' return all matching teachers and all matching groups."""
    teachers = [
        {"id": 21, "name": "Исабеков К.", "fullName": "Исабеков Кайрат", "lastName": "Исабеков", "firstName": "Кайрат", "aliases": []},
        {"id": 22, "name": "Искакова А.", "fullName": "Искакова Айнур", "lastName": "Искакова", "firstName": "Айнур", "aliases": []},
        {"id": 23, "name": "Асанов И.", "fullName": "Асанов Ислам", "lastName": "Асанов", "firstName": "Ислам", "aliases": []},
        {"id": 24, "name": "Смирнов В.", "fullName": "Смирнов В.", "lastName": "Смирнов", "firstName": "В.", "aliases": []},
    ]
    matched_t = smart_search_service.find_teachers("ис", teachers)
    # Исабеков, Искакова, Асанов Ислам should all be matched
    matched_ids = [t["id"] for t in matched_t]
    assert 21 in matched_ids
    assert 22 in matched_ids
    assert 23 in matched_ids
    assert 24 not in matched_ids

    # Groups: ИС-21-1, ИС-21-2 should be matched
    matched_g = smart_search_service.find_groups("ис", mock_groups)
    assert len(matched_g) == 2
    assert matched_g[0]["name"] == "ИС-21-1"
    assert matched_g[1]["name"] == "ИС-21-2"


