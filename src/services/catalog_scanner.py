"""High-speed university catalog crawler for Buketov KarU.

Scans faculties, programs, student groups, and teacher rosters.
Discovers unindexed entities via in-memory O(1) set-difference against MongoDB.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any
from urllib.parse import unquote

import httpx

from src.core.config import settings
from src.core.logging import get_logger
from src.database.models.department import Department
from src.database.models.faculty import Faculty
from src.database.models.group import Group
from src.database.models.program import Program
from src.database.models.teacher import Teacher
from src.services.smart_search import smart_search_service
from src.services.teacher_directory import clean_titles, normalize_cyrillic

logger = get_logger("catalog_scanner")


class CatalogScannerService:
    """Discovers unindexed groups and teachers without headless browsers."""

    def __init__(
        self,
        base_url: str | None = None,
        pacing_delay: float = 0.08,
        client_timeout: float = 8.0,
    ) -> None:
        self.base_url = (base_url or settings.KSU_DOMAIN).rstrip("/")
        self.pacing_delay = pacing_delay
        self.client_timeout = client_timeout

    async def authenticate(self, client: httpx.AsyncClient) -> bool:
        """Authenticates session on schedule.buketov.edu.kz if credentials configured."""
        login = settings.KSU_LOGIN
        password = settings.KSU_PASSWORD
        if not login or not password:
            logger.debug("[CatalogScanner] KSU_LOGIN/KSU_PASSWORD не заданы, пропуск аутентификации")
            return False

        try:
            # Step 1: GET login.php to initialize session cookies
            login_page_res = await client.get(f"{self.base_url}/login.php")
            if login_page_res.status_code >= 400:
                logger.warning(f"[CatalogScanner] Ошибка загрузки login.php: {login_page_res.status_code}")
                return False

            # Step 2: POST login.php with credentials
            post_data = {
                "login": login,
                "password": password,
                "submit": "Вход",
            }
            login_res = await client.post(
                f"{self.base_url}/login.php",
                data=post_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{self.base_url}/login.php",
                },
            )

            if login_res.status_code in (200, 302):
                loc = login_res.headers.get("location", "").lower()
                body_lower = login_res.text.lower()
                if "login.php" in loc or 'name="password"' in body_lower or 'type="password"' in body_lower:
                    logger.warning("[CatalogScanner] Авторизация не удалась (неверный логин или пароль)")
                    return False
                logger.info(f"[CatalogScanner] Успешная авторизация пользователя {login}")
                return True
            logger.warning(f"[CatalogScanner] Авторизация вернула статус {login_res.status_code}")
            return False
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка авторизации: {e}")
            return False

    @staticmethod
    def parse_faculties_html(html_text: str) -> list[dict[str, Any]]:
        """Extracts faculties from <select name='Login'> or similar faculty selector."""
        faculties: list[dict[str, Any]] = []
        select_match = re.search(
            r"<select[^>]*name=[\"']?(?:Login|fak|facult|faculty)[\"']?[^>]*>(.*?)</select>",
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not select_match:
            select_match = re.search(r"<select[^>]*>(.*?)</select>", html_text, re.DOTALL | re.IGNORECASE)

        options_block = select_match.group(1) if select_match else html_text

        # Matches both <option value="...">text</option> and <option>text</option>
        option_matches = re.findall(
            r"<option(?:\s+[^>]*?value=[\"']?([^\"'>]*)[\"']?)?[^>]*>(.*?)</option>",
            options_block,
            re.DOTALL | re.IGNORECASE,
        )
        for idx, (val, text) in enumerate(option_matches, start=1):
            clean_text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ").strip()
            clean_val = val.strip() if val else ""
            if not clean_text or clean_text in ("--", "-", "0") or "выберите" in clean_text.lower():
                continue
            faculties.append({
                "id": idx,
                "name": clean_text,
                "value": clean_val or clean_text,
            })
        return faculties

    async def _scan_faculties(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Parses faculty list from main page dropdown."""
        try:
            res = await client.get(f"{self.base_url}/")
            if res.status_code >= 400:
                logger.warning(f"[CatalogScanner] GET / вернул {res.status_code}")
                return []
            return self.parse_faculties_html(res.text)
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка получения факультетов: {e}")
            return []

    @staticmethod
    def parse_programs_html(
        html_text: str,
        faculty_id: Any = None,
        faculty_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extracts educational programs from stud.php links."""
        programs: list[dict[str, Any]] = []
        matches = re.finditer(
            r"<a[^>]*href=[\"']([^\"']*grupps[^\"']*)[\"'][^>]*>(.*?)</a>",
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        for m in matches:
            href = m.group(1)
            raw_text = m.group(2)
            clean_text = html.unescape(re.sub(r"<[^>]+>", "", raw_text)).replace("\xa0", " ").strip()
            id_m = re.search(r"[?&]id=(\d+)", href)
            if id_m and clean_text:
                p_id = int(id_m.group(1))
                programs.append({
                    "id": p_id,
                    "name": clean_text,
                    "href": href,
                    "facultyId": faculty_id,
                    "facultyName": faculty_name,
                })
        return programs

    async def _scan_programs(
        self,
        client: httpx.AsyncClient,
        faculty_name: str,
        faculty_id: Any = None,
    ) -> list[dict[str, Any]]:
        """Switches active faculty via POST index.php?x and retrieves stud.php programs."""
        try:
            await client.post(
                f"{self.base_url}/index.php?x",
                data={"Login": faculty_name, "pw": ""},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{self.base_url}/",
                },
            )
            res = await client.get(f"{self.base_url}/stud.php")
            if res.status_code >= 400:
                return []
            return self.parse_programs_html(res.text, faculty_id=faculty_id, faculty_name=faculty_name)
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка получения программ факультета '{faculty_name}': {e}")
            return []

    @staticmethod
    def parse_groups_html(
        html_text: str,
        program_id: int | None = None,
        faculty_id: Any = None,
    ) -> list[dict[str, Any]]:
        """Extracts student groups from grupps1.php HTML table."""
        groups: list[dict[str, Any]] = []
        matches = list(re.finditer(
            r"<a[^>]*href=[\"']([^\"']*(?:view\d*|report_stud|grupp)[^\"']*[?&]id=(\d+)[^\"']*)[\"'][^>]*>(.*?)</a>",
            html_text,
            re.DOTALL | re.IGNORECASE,
        ))
        if not matches:
            matches = list(re.finditer(
                r"<a[^>]*href=[\"']([^\"']*[?&]id=(\d+)[^\"']*)[\"'][^>]*>(.*?)</a>",
                html_text,
                re.DOTALL | re.IGNORECASE,
            ))

        navigation_stop_words = {
            "назад", "главная", "меню", "выход", "специальности", "факультеты",
            "бөлім", "мамандықтар", "факультеттер", "артқа", "басты", "тізім"
        }

        for m in matches:
            href = m.group(1)
            raw_id = m.group(2)
            raw_text = m.group(3) if len(m.groups()) >= 3 else m.group(2)
            name = html.unescape(re.sub(r"<[^>]+>", "", raw_text)).replace("\xa0", " ").strip()
            if not raw_id or not name:
                continue

            name_lower = name.lower()
            if name_lower in navigation_stop_words or "назад" in name_lower or "артқа" in name_lower:
                continue

            if re.search(r"(?:stud|index|login)\.php", href, re.IGNORECASE):
                continue

            g_id = int(raw_id)
            otdel_m = re.search(r"[?&]Otdel=([^&#\s]+)", href)
            kurs_m = re.search(r"[?&]Kurs=(\d+)", href)
            stud_m = re.search(r"[?&]Stud=(\d+)", href)

            language = unquote(otdel_m.group(1)).strip() if otdel_m else None
            age = int(kurs_m.group(1)) if kurs_m else 1
            student_count = int(stud_m.group(1)) if stud_m else 0

            groups.append({
                "id": g_id,
                "name": name,
                "href": href,
                "language": language,
                "age": age,
                "studentCount": student_count,
                "program": program_id,
                "facultyId": faculty_id,
            })
        return groups

    async def _scan_groups_for_program(
        self,
        client: httpx.AsyncClient,
        program_id: int,
        faculty_id: Any = None,
        href: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetches and parses grupps1.php?id={program_id} or given href."""
        try:
            if href:
                if href.startswith("http://") or href.startswith("https://"):
                    url = href
                elif href.startswith("/"):
                    url = f"{self.base_url}{href}"
                else:
                    url = f"{self.base_url}/{href}"
            else:
                url = f"{self.base_url}/grupps1.php?id={program_id}"

            res = await client.get(url)
            if res.status_code >= 400:
                return []
            return self.parse_groups_html(res.text, program_id=program_id, faculty_id=faculty_id)
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка получения групп программы {program_id}: {e}")
            return []

    async def _scan_all_groups(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Walks faculties -> programs -> groups tree."""
        db_facs = []
        try:
            db_facs = await Faculty.find().to_list()
        except Exception as e:
            logger.debug(f"[CatalogScanner] DB faculties load skipped: {e}")

        fac_id_by_name = {f.name.strip().lower(): f.faculty_id for f in db_facs}

        faculties = await self._scan_faculties(client)
        if not faculties and db_facs:
            faculties = [{"id": f.faculty_id, "name": f.name, "value": f.name} for f in db_facs]

        # Match each parsed faculty against MongoDB faculty by name
        for fac in faculties:
            fac_name = fac.get("name", "")
            norm_name = fac_name.strip().lower()
            if norm_name in fac_id_by_name:
                fac["id"] = fac_id_by_name[norm_name]

        all_groups: list[dict[str, Any]] = []
        for fac in faculties:
            fac_name = fac.get("name", "")
            fac_id = fac.get("id")
            try:
                programs = await self._scan_programs(client, faculty_name=fac_name, faculty_id=fac_id)
                if self.pacing_delay > 0:
                    await asyncio.sleep(self.pacing_delay)

                for prog in programs:
                    prog_id = prog["id"]
                    prog_href = prog.get("href")
                    try:
                        groups = await self._scan_groups_for_program(
                            client, program_id=prog_id, faculty_id=fac_id, href=prog_href
                        )
                        all_groups.extend(groups)
                        if self.pacing_delay > 0:
                            await asyncio.sleep(self.pacing_delay)
                    except Exception as e:
                        logger.warning(f"[CatalogScanner] Ошибка сканирования программы {prog_id}: {e}")
            except Exception as e:
                logger.warning(f"[CatalogScanner] Ошибка сканирования факультета {fac_name}: {e}")
        return all_groups

    @staticmethod
    def parse_departments_html(html_text: str) -> list[dict[str, Any]]:
        """Extracts departments from kafedra.php links."""
        departments: list[dict[str, Any]] = []
        matches = re.finditer(
            r"<a[^>]*href=[\"']([^\"']*IdKaf=(\d+)[^\"']*)[\"'][^>]*>(.*?)</a>",
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        for m in matches:
            href = m.group(1)
            k_id = int(m.group(2))
            raw_text = m.group(3)
            clean_text = html.unescape(re.sub(r"<[^>]+>", "", raw_text)).replace("\xa0", " ").strip()
            if clean_text:
                departments.append({
                    "id": k_id,
                    "name": clean_text,
                    "href": href,
                })
        return departments

    async def _scan_departments(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Parses university departments from kafedra.php."""
        try:
            res = await client.get(f"{self.base_url}/kafedra.php")
            if res.status_code >= 400:
                return []
            return self.parse_departments_html(res.text)
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка получения кафедр: {e}")
            return []

    @staticmethod
    def parse_teachers_html(
        html_text: str,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Extracts teacher profiles from report_prep.php HTML table."""
        teachers: list[dict[str, Any]] = []
        matches = re.finditer(
            r"<a[^>]*href=[\"']([^\"']*IdPrep=(\d+)[^\"']*)[\"'][^>]*>(.*?)</a>",
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        for m in matches:
            href = m.group(1)
            t_id = int(m.group(2))
            raw_name = html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).replace("\xa0", " ").strip()
            if not raw_name or raw_name in ("-", "- ", "—", "— "):
                continue

            cleaned = clean_titles(raw_name)
            if not cleaned:
                continue

            teachers.append({
                "id": t_id,
                "name": cleaned,
                "fullName": raw_name,
                "href": href,
                "department": department_id,
            })
        return teachers

    async def _scan_teachers_for_department(
        self,
        client: httpx.AsyncClient,
        department_id: int,
        href: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parses teachers roster from report_prep.php?d=1&IdKaf={department_id} or given href."""
        try:
            if href:
                if href.startswith("http://") or href.startswith("https://"):
                    url = href
                elif href.startswith("/"):
                    url = f"{self.base_url}{href}"
                else:
                    url = f"{self.base_url}/{href}"
            else:
                url = f"{self.base_url}/report_prep.php?d=1&IdKaf={department_id}"

            res = await client.get(url)
            if res.status_code >= 400:
                return []
            return self.parse_teachers_html(res.text, department_id=department_id)
        except Exception as e:
            logger.warning(f"[CatalogScanner] Ошибка получения преподавателей кафедры {department_id}: {e}")
            return []

    async def _scan_all_teachers(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Walks departments -> teachers roster."""
        departments = await self._scan_departments(client)
        if not departments:
            try:
                db_depts = await Department.find().to_list()
                departments = [{"id": d.department_id, "name": d.name} for d in db_depts]
            except Exception as e:
                logger.debug(f"[CatalogScanner] DB departments fallback skipped: {e}")

        all_teachers: list[dict[str, Any]] = []
        for dept in departments:
            dept_id = dept.get("id")
            dept_href = dept.get("href")
            if not dept_id:
                continue
            try:
                teachers = await self._scan_teachers_for_department(
                    client, department_id=dept_id, href=dept_href
                )
                all_teachers.extend(teachers)
                if self.pacing_delay > 0:
                    await asyncio.sleep(self.pacing_delay)
            except Exception as e:
                logger.warning(f"[CatalogScanner] Ошибка сканирования кафедры {dept_id}: {e}")
        return all_teachers

    async def scan_and_sync(self, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        """Runs full catalog scan, persists unindexed items, and refreshes search."""
        t0 = time.time()
        logger.info("[CatalogScanner] Запуск сканирования каталога КарУ...")

        # 1. Быстро собираем известные ID и нормализованные имена в ОЗУ
        raw_group_ids = await Group.distinct("id")
        known_group_ids: set[int] = {
            int(x) for x in raw_group_ids if x is not None and str(x).isdigit()
        }

        raw_teacher_ids = await Teacher.distinct("id")
        known_teacher_ids: set[int] = {
            int(x) for x in raw_teacher_ids if x is not None and str(x).isdigit()
        }

        raw_teacher_names = await Teacher.distinct("name")
        raw_teacher_full_names = await Teacher.distinct("fullName")
        known_teacher_names: set[str] = {
            normalize_cyrillic(clean_titles(n))
            for n in (raw_teacher_names + raw_teacher_full_names)
            if n
        }

        new_groups: list[dict[str, Any]] = []
        new_teachers: list[dict[str, Any]] = []

        if client is not None:
            await self._run_scan(
                client,
                known_group_ids,
                known_teacher_ids,
                known_teacher_names,
                new_groups,
                new_teachers,
            )
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.client_timeout),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KichoBot/2.0"},
                follow_redirects=True,
            ) as session_client:
                await self.authenticate(session_client)
                await self._run_scan(
                    session_client,
                    known_group_ids,
                    known_teacher_ids,
                    known_teacher_names,
                    new_groups,
                    new_teachers,
                )

        # 4. Пакетное сохранение только реальных новинок в MongoDB
        inserted_groups_count = 0
        inserted_teachers_count = 0

        if new_groups:
            docs = [Group(**g) for g in new_groups]
            try:
                await Group.insert_many(docs, ordered=False)
            except Exception as e:
                logger.warning(f"[CatalogScanner] Замечание при Group.insert_many: {e}")
            inserted_groups_count = len(docs)
            logger.info(f"[CatalogScanner] Добавлено новых групп: {inserted_groups_count}")

        if new_teachers:
            enriched_teachers = []
            for t in new_teachers:
                try:
                    enr = teacher_directory_service.enrich(t)
                    enriched_teachers.append(enr or t)
                except Exception:
                    enriched_teachers.append(t)
            docs = [Teacher(**t) for t in enriched_teachers]
            try:
                await Teacher.insert_many(docs, ordered=False)
            except Exception as e:
                logger.warning(f"[CatalogScanner] Замечание при Teacher.insert_many: {e}")
            inserted_teachers_count = len(docs)
            logger.info(f"[CatalogScanner] Добавлено новых преподавателей: {inserted_teachers_count}")

        # 5. Если найдены новинки — мгновенно обновляем умный поиск
        if inserted_groups_count > 0 or inserted_teachers_count > 0:
            smart_search_service.invalidate_caches()
            await smart_search_service.warmup()

        # 6. Освобождаем память для Render (Free Plan, 512 MB)
        try:
            from src.cron.memory_trim import trim_process_memory

            trim_process_memory()
        except Exception as e:
            logger.debug(f"[CatalogScanner] memory_trim пропущен: {e}")

        elapsed = round(time.time() - t0, 2)
        summary = {
            "elapsed_seconds": elapsed,
            "new_groups": inserted_groups_count,
            "new_teachers": inserted_teachers_count,
        }
        logger.info(f"[CatalogScanner] Сканирование завершено за {elapsed}с: {summary}")
        return summary

    async def _run_scan(
        self,
        client: httpx.AsyncClient,
        known_group_ids: set[int],
        known_teacher_ids: set[int],
        known_teacher_names: set[str],
        new_groups: list[dict[str, Any]],
        new_teachers: list[dict[str, Any]],
    ) -> None:
        """Executes crawl over groups and teachers, filtering out existing entities."""
        # Сканируем студенческие группы
        try:
            scanned_groups = await self._scan_all_groups(client)
            for g in scanned_groups:
                g_id = g.get("id")
                if g_id and g_id not in known_group_ids:
                    new_groups.append(g)
                    known_group_ids.add(g_id)
        except Exception as e:
            logger.error(f"[CatalogScanner] Ошибка сканирования групп: {e}")

        # Сканируем преподавателей
        try:
            scanned_teachers = await self._scan_all_teachers(client)
            for t in scanned_teachers:
                t_id = t.get("id")
                raw_name = t.get("name") or ""
                norm_t = normalize_cyrillic(clean_titles(raw_name))
                if t_id and t_id not in known_teacher_ids and norm_t and norm_t not in known_teacher_names:
                    new_teachers.append(t)
                    known_teacher_ids.add(t_id)
                    known_teacher_names.add(norm_t)
        except Exception as e:
            logger.error(f"[CatalogScanner] Ошибка сканирования преподавателей: {e}")


catalog_scanner_service = CatalogScannerService()
