"""Ansible Parser Module - Funktionen zum Parsen von Ansible-Dateien."""

import glob
import os
import yaml
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AnsibleData:
    """Container für geparste Ansible-Daten."""
    groups: dict[str, list[str]] = field(default_factory=dict)
    playbooks: dict[str, dict] = field(default_factory=dict)
    roles: set[str] = field(default_factory=set)
    role_tasks: dict[str, list[dict]] = field(default_factory=dict)


def find_role_tasks(repo_path: str, role_name: str) -> list[dict]:
    """Sucht die Tasks einer Rolle im roles/ Verzeichnis."""
    role_paths = [
        f"{repo_path}/**/roles/{role_name}/tasks/main.yml",
        f"{repo_path}/**/roles/{role_name}/tasks/main.yaml",
    ]
    for pattern in role_paths:
        for role_file in glob.glob(pattern, recursive=True):
            try:
                with open(role_file, encoding="utf-8") as f:
                    tasks = yaml.safe_load(f)
                    if tasks and isinstance(tasks, list):
                        return tasks
            except (yaml.YAMLError, IOError) as e:
                logger.warning(f"Konnte Role nicht laden: {role_file} - {e}")
    return []


def load_included_tasks(repo_path: str, task_file: str, base_path: str) -> list[dict]:
    """Lädt eingebundene Task-Dateien."""
    if not task_file:
        return []

    possible_paths = [
        os.path.join(os.path.dirname(base_path), task_file),
        os.path.join(repo_path, task_file),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    tasks = yaml.safe_load(f)
                    if tasks and isinstance(tasks, list):
                        return tasks
            except (yaml.YAMLError, IOError) as e:
                logger.warning(f"Konnte Task-Datei nicht laden: {path} - {e}")
    return []


def parse_inventory(inv_path: str) -> dict[str, list[str]]:
    """Parst ein Inventory und gibt Groups mit Hosts zurück."""
    groups = {}

    try:
        with open(inv_path, encoding="utf-8") as f:
            inv_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Ungültiges YAML in {inv_path}: {e}")

    if not inv_data or not isinstance(inv_data, dict):
        logger.warning(f"Leeres oder ungültiges Inventory: {inv_path}")
        return groups

    for group, content in inv_data.items():
        groups[group] = []

        if content is None or not isinstance(content, dict):
            continue

        hosts = content.get("hosts", {})
        if hosts:
            groups[group] = list(hosts.keys())

    return groups


def parse_playbook(pb_path: str, repo_path: str) -> dict:
    """Parst ein Playbook und extrahiert alle relevanten Informationen."""
    result = {
        "name": os.path.basename(pb_path),
        "path": pb_path,
        "plays": []
    }

    try:
        with open(pb_path, encoding="utf-8") as f:
            pb_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Ungültiges YAML in {pb_path}: {e}")

    if not pb_data or not isinstance(pb_data, list):
        logger.warning(f"Leeres oder ungültiges Playbook: {pb_path}")
        return result

    for play in pb_data:
        if not isinstance(play, dict):
            continue

        play_info = {
            "hosts": play.get("hosts"),
            "roles": [],
            "tasks": [],
            "handlers": []
        }

        # Roles extrahieren
        for role in play.get("roles", []) or []:
            if isinstance(role, dict):
                role_name = role.get("role") or role.get("name")
            else:
                role_name = str(role)
            if role_name:
                play_info["roles"].append(role_name)

        # Tasks extrahieren (pre_tasks, tasks, post_tasks)
        all_tasks = []
        for task_type in ["pre_tasks", "tasks", "post_tasks"]:
            for task in play.get(task_type, []) or []:
                if isinstance(task, dict):
                    task_info = extract_task_info(task, repo_path, pb_path)
                    all_tasks.append(task_info)
        play_info["tasks"] = all_tasks

        # Handlers extrahieren
        for handler in play.get("handlers", []) or []:
            if isinstance(handler, dict):
                play_info["handlers"].append(handler.get("name", "unnamed_handler"))

        result["plays"].append(play_info)

    return result


def extract_task_info(task: dict, repo_path: str, base_path: str) -> dict:
    """Extrahiert Informationen aus einem Task."""
    task_info = {
        "name": task.get("name", "unnamed_task"),
        "type": "task"
    }

    # include_role / import_role
    role_info = task.get("include_role") or task.get("import_role")
    if role_info:
        role_name = role_info.get("name") if isinstance(role_info, dict) else str(role_info)
        task_info["type"] = "role"
        task_info["role_name"] = role_name
        return task_info

    # include_tasks / import_tasks
    include_file = task.get("include_tasks") or task.get("import_tasks")
    if include_file:
        if isinstance(include_file, dict):
            include_file = include_file.get("file", "")
        task_info["type"] = "include"
        task_info["include_file"] = str(include_file)
        task_info["included_tasks"] = []

        # Rekursiv eingebundene Tasks laden
        included = load_included_tasks(repo_path, str(include_file), base_path)
        for t in included:
            if isinstance(t, dict):
                task_info["included_tasks"].append(extract_task_info(t, repo_path, base_path))

        return task_info

    return task_info


def parse_all(inventory_paths: list[str], playbook_paths: list[str], repo_path: str) -> AnsibleData:
    """Parst alle Inventories und Playbooks."""
    data = AnsibleData()

    # Inventories parsen
    for inv_path in inventory_paths:
        try:
            groups = parse_inventory(inv_path)
            data.groups.update(groups)
        except ValueError as e:
            logger.error(str(e))
            raise

    # Playbooks parsen
    for pb_path in playbook_paths:
        try:
            pb_data = parse_playbook(pb_path, repo_path)
            data.playbooks[pb_path] = pb_data

            # Roles sammeln
            for play in pb_data["plays"]:
                for role_name in play["roles"]:
                    data.roles.add(role_name)

                # Roles aus Tasks sammeln
                for task in play["tasks"]:
                    if task["type"] == "role":
                        data.roles.add(task["role_name"])

        except ValueError as e:
            logger.error(str(e))
            raise

    # Role-Tasks laden
    for role_name in data.roles:
        data.role_tasks[role_name] = find_role_tasks(repo_path, role_name)

    return data
