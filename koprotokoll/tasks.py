"""Aufgaben als iCalendar oder gezielt in eine verbundene Aufgabenliste exportieren."""

from __future__ import annotations

import calendar
import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlsplit

from . import caldav_source as dav
from . import outlook


class TaskError(RuntimeError):
    pass


def resolve_due(value: str | None, anchor: date) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r"^bis\s+(?:zum\s+)?", "", value.strip(), flags=re.I).casefold()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
        numeric = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", text)
        if numeric:
            return date(int(numeric[3] or anchor.year), int(numeric[2]), int(numeric[1]))
    except ValueError:
        return None
    if text in ("heute", "morgen", "übermorgen"):
        return anchor + timedelta(days=("heute", "morgen", "übermorgen").index(text))
    if text == "nächste woche":
        return anchor + timedelta(days=7 - anchor.weekday())
    if text == "ende des monats":
        return anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    weekdays = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
    weekday = re.sub(r"^nächsten?\s+", "", text)
    if weekday in weekdays:
        days = (weekdays.index(weekday) - anchor.weekday()) % 7
        return anchor + timedelta(days=days or (7 if text.startswith("nächste") else 0))
    return None


def meeting_tasks(meeting: dict, segments: list[dict], public_url: str, namespace: str) -> list[dict]:
    safe_url(public_url)
    stamp = meeting.get("meta", {}).get("event_start") or meeting["created_at"]
    anchor = datetime.fromtimestamp(stamp).date()
    tasks, occurrences = [], {}
    for action in (meeting.get("summary") or {}).get("action_items", []):
        if not isinstance(action, dict) or not isinstance(action.get("task"), str):
            continue
        title = action["task"].strip()
        if not title:
            continue
        owner = str(action.get("owner") or "")
        identity = json.dumps([meeting["id"], owner.casefold(), " ".join(title.casefold().split()),
                               action.get("due"), action.get("time")], sort_keys=True, ensure_ascii=False)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        uid = str(uuid.uuid5(uuid.UUID(namespace), identity + f":{occurrence}")) + "@koprotokoll"
        clock = str(action.get("time") or "")
        seconds = None
        if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", clock):
            seconds = 0
            for part in clock.split(":"):
                seconds = seconds * 60 + int(part)
        link = f"{public_url}/#meeting={meeting['id']}" + (f"&t={seconds}" if seconds is not None else "")
        lines = [title]
        if owner:
            lines.append(f"Verantwortlich: {owner}")
        if action.get("due"):
            lines.append(f"Genannte Frist: {action['due']}")
        excerpt = next((s["text"] for s in segments if seconds is not None and s["start"] <= seconds <= s["end"]), None)
        if excerpt:
            lines.append(f"Transkript [{clock}]: {excerpt}")
        lines.append("Meeting: " + link)
        tasks.append({"uid": uid, "title": title, "description": "\n".join(lines), "link": link,
                      "due": resolve_due(action.get("due"), anchor)})
    return tasks


def tasks_ics(tasks: list[dict]) -> bytes:
    def escape(text):
        return str(text).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Koprotokoll//Aufgaben//DE", "CALSCALE:GREGORIAN"]
    for task in tasks:
        lines.extend(["BEGIN:VTODO", "UID:" + task["uid"],
                      "DTSTAMP:" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                      "SUMMARY:" + escape(task["title"]), "DESCRIPTION:" + escape(task["description"]),
                      "URL:" + task["link"], "STATUS:NEEDS-ACTION"])
        if task["due"]:
            lines.append("DUE;VALUE=DATE:" + task["due"].strftime("%Y%m%d"))
        lines.append("END:VTODO")
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        current = ""
        for char in line:
            if len((current + char).encode("utf-8")) > 75:
                folded.append(current)
                current = " "
            current += char
        folded.append(current)
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or any(c in url for c in "\r\n"):
        raise TaskError("Aufgabenlisten benötigen eine HTTP(S)-Adresse ohne Zugangsdaten in der URL.")
    return url


def caldav_task_lists(config: dict) -> list[dict]:
    auth = {"username": config.get("username", ""), "password": config.get("password", "")}
    if config.get("server_url"):
        return dav.discover(safe_url(config["server_url"]), **auth, component="VTODO")
    url = safe_url(config.get("url", ""))
    responses, final = dav._propfind(url, "<d:displayname/><c:supported-calendar-component-set/>", depth="0", **auth)
    for response in responses:
        props = dav._props(response)
        comps = props.get(f"{{{dav.CAL_NS}}}supported-calendar-component-set")
        if comps is not None and not any(c.get("name") == "VTODO" for c in comps):
            continue
        display = props.get(f"{{{dav.DAV_NS}}}displayname")
        return [{"url": safe_url(final), "name": display.text if display is not None else "Aufgaben"}]
    return []


class TaskExporter:
    def __init__(self, db, microsoft):
        self.db, self.microsoft = db, microsoft
        self._lock = threading.Lock()

    def targets(self) -> dict:
        targets, errors = [], []
        for row in self.db.list_calendar_sources():
            if row["kind"] != "caldav" or not row["enabled"]:
                continue
            try:
                for collection in caldav_task_lists(row["config"]):
                    url = safe_url(collection["url"])
                    targets.append({"id": f"caldav:{row['id']}:{quote(url, safe='')}", "kind": "caldav",
                                    "label": f"{row['label']}: {collection['name']}", "source_id": row["id"], "url": url})
            except (dav.CalDavError, TaskError) as error:
                errors.append(f"{row['label']}: {error}")
        if outlook.outlook_available():
            targets.append({"id": "outlook", "kind": "outlook", "label": "Outlook: lokale Aufgaben"})
        if self.microsoft.connected() and self.microsoft.has_scope("Tasks.ReadWrite"):
            try:
                for item in self.microsoft.list_values("/me/todo/lists"):
                    targets.append({"id": "microsoft:" + item["id"], "kind": "microsoft",
                                    "label": "Microsoft To Do: " + item["displayName"], "list_id": item["id"]})
            except Exception:
                errors.append("Microsoft-Aufgabenlisten konnten nicht geladen werden. Anmeldung und Aufgabenrechte prüfen.")
        return {"targets": targets, "errors": errors}

    def export(self, target_id: str, tasks: list[dict]) -> dict:
        with self._lock:
            target = next((t for t in self.targets()["targets"] if t["id"] == target_id), None)
            if not target:
                raise TaskError("Aufgabenliste ist nicht verfügbar. Ziele neu laden und Verbindung prüfen.")
            created = skipped = 0
            if target["kind"] == "caldav":
                config = self.db.get_calendar_source(target["source_id"])["config"]
                for task in tasks:
                    url = urljoin(target["url"].rstrip("/") + "/", quote(task["uid"], safe="") + ".ics")
                    status, _, _ = dav._request("PUT", url, body=tasks_ics([task]),
                        username=config.get("username", ""), password=config.get("password", ""),
                        content_type="text/calendar; charset=utf-8", extra_headers={"If-None-Match": "*"},
                        accepted_errors=(412,), follow_redirects=False)
                    if status == 412:
                        skipped += 1
                    elif status in (200, 201, 204):
                        created += 1
                    else:
                        raise TaskError(f"Aufgabenserver meldet unerwarteten Status {status}.")
            elif target["kind"] == "microsoft":
                from .microsoft import GRAPH_BASE, _http
                path = "/me/todo/lists/" + quote(target["list_id"], safe="") + "/tasks"
                existing = self.microsoft.list_values(path + "?$expand=linkedResources")
                known = {link.get("externalId") for item in existing for link in item.get("linkedResources", [])
                         if link.get("applicationName") == "Koprotokoll"}
                for task in tasks:
                    if task["uid"] in known:
                        skipped += 1
                        continue
                    body = {"title": task["title"][:255], "body": {"contentType": "text", "content": task["description"]},
                            "linkedResources": [{"applicationName": "Koprotokoll", "displayName": "Meeting öffnen",
                                                 "externalId": task["uid"], "webUrl": task["link"]}]}
                    if task["due"]:
                        body["dueDateTime"] = {"dateTime": task["due"].isoformat() + "T00:00:00", "timeZone": "UTC"}
                    _http("POST", GRAPH_BASE + path, token=self.microsoft._access_token(), body=body)
                    known.add(task["uid"])
                    created += 1
            else:
                with outlook.outlook_session() as app:
                    folder = app.GetNamespace("MAPI").GetDefaultFolder(13)
                    known = set()
                    for item in folder.Items:
                        prop = item.UserProperties.Find("KoprotokollUID")
                        if prop:
                            known.add(prop.Value)
                    for task in tasks:
                        if task["uid"] in known:
                            skipped += 1
                            continue
                        item = app.CreateItem(3)
                        item.Subject, item.Body = task["title"][:255], task["description"]
                        if task["due"]:
                            item.DueDate = datetime.combine(task["due"], datetime.min.time()).replace(tzinfo=timezone.utc)
                        item.UserProperties.Add("KoprotokollUID", 1, True).Value = task["uid"]
                        item.Save()
                        known.add(task["uid"])
                        created += 1
            return {"created": created, "skipped": skipped}
