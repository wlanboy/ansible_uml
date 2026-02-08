"""Ansible Parser Module - Funktionen zum Parsen von Ansible-Dateien."""

import glob
import os
import re
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
    role_dependencies: dict[str, list[str]] = field(default_factory=dict)


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


def find_role_dependencies(repo_path: str, role_name: str) -> list[str]:
    """Sucht die Abhängigkeiten einer Rolle in meta/main.yml."""
    meta_paths = [
        f"{repo_path}/**/roles/{role_name}/meta/main.yml",
        f"{repo_path}/**/roles/{role_name}/meta/main.yaml",
    ]
    for pattern in meta_paths:
        for meta_file in glob.glob(pattern, recursive=True):
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = yaml.safe_load(f)
                    if meta and isinstance(meta, dict):
                        deps = meta.get("dependencies", [])
                        result = []
                        for dep in deps or []:
                            if isinstance(dep, dict):
                                name = dep.get("role") or dep.get("name")
                            else:
                                name = str(dep)
                            if name:
                                result.append(name)
                        return result
            except (yaml.YAMLError, IOError) as e:
                logger.warning(f"Konnte Meta nicht laden: {meta_file} - {e}")
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


def _parse_yaml_group(groups: dict, group_name: str, content: dict) -> None:
    """Rekursiv YAML-Inventory-Gruppen parsen, inkl. children."""
    if content is None or not isinstance(content, dict):
        groups[group_name] = []
        return

    hosts = content.get("hosts", {})
    groups[group_name] = list(hosts.keys()) if hosts and isinstance(hosts, dict) else []

    children = content.get("children", {})
    if children and isinstance(children, dict):
        for child_name, child_content in children.items():
            _parse_yaml_group(groups, child_name, child_content)


def parse_ini_inventory(inv_path: str) -> dict[str, list[str]]:
    """Parst ein INI-Format Inventory."""
    groups = {}
    current_group = None
    current_section = "hosts"

    with open(inv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith(';'):
                continue

            match = re.match(r'^\[([^:\]]+)(?::(\w+))?\]$', line)
            if match:
                current_group = match.group(1)
                current_section = match.group(2) or "hosts"
                if current_group not in groups:
                    groups[current_group] = []
                continue

            if current_group is None:
                continue

            if current_section == "hosts":
                host = line.split()[0]
                if host:
                    groups[current_group].append(host)
            elif current_section == "children":
                child_group = line.split()[0]
                if child_group not in groups:
                    groups[child_group] = []

    return groups


def parse_inventory(inv_path: str) -> dict[str, list[str]]:
    """Parst ein Inventory (YAML oder INI) und gibt Groups mit Hosts zurück."""
    # Versuche YAML zu parsen
    try:
        with open(inv_path, encoding="utf-8") as f:
            inv_data = yaml.safe_load(f)

        if inv_data and isinstance(inv_data, dict):
            groups = {}
            for group, content in inv_data.items():
                _parse_yaml_group(
                    groups, group,
                    content if isinstance(content, dict) else {}
                )
            return groups
    except yaml.YAMLError:
        pass

    # Fallback: INI-Format
    try:
        return parse_ini_inventory(inv_path)
    except IOError as e:
        raise ValueError(f"Konnte Inventory nicht laden: {inv_path}: {e}")


def parse_playbook(pb_path: str, repo_path: str) -> dict:
    """Parst ein Playbook und extrahiert alle relevanten Informationen."""
    result = {
        "name": os.path.basename(pb_path),
        "path": pb_path,
        "plays": [],
        "imported_playbooks": []
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

        # import_playbook erkennen
        import_pb = play.get("import_playbook")
        if import_pb:
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(pb_path), import_pb)
            )
            result["imported_playbooks"].append(resolved)
            continue

        play_info = {
            "hosts": play.get("hosts"),
            "roles": [],
            "tasks": [],
            "handlers": []
        }

        # Play-Level become/become_user
        if play.get("become"):
            play_info["become"] = True
            become_user = play.get("become_user")
            if become_user:
                play_info["become_user"] = str(become_user)

        # Play-Level tags
        play_tags = play.get("tags")
        if play_tags is not None:
            if isinstance(play_tags, list):
                play_info["tags"] = play_tags
            else:
                play_info["tags"] = [str(play_tags)]

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

    # when extrahieren
    when = task.get("when")
    if when is not None:
        if isinstance(when, list):
            task_info["when"] = when
        else:
            task_info["when"] = [str(when)]

    # tags extrahieren
    tags = task.get("tags")
    if tags is not None:
        if isinstance(tags, list):
            task_info["tags"] = tags
        else:
            task_info["tags"] = [str(tags)]

    # become extrahieren
    if task.get("become"):
        task_info["become"] = True
        become_user = task.get("become_user")
        if become_user:
            task_info["become_user"] = str(become_user)

    # notify extrahieren
    notify = task.get("notify")
    if notify:
        if isinstance(notify, str):
            task_info["notify"] = [notify]
        elif isinstance(notify, list):
            task_info["notify"] = notify

    # block / rescue / always
    if "block" in task:
        task_info["type"] = "block"
        task_info["block_tasks"] = []
        for section in ["block", "rescue", "always"]:
            for t in task.get(section, []) or []:
                if isinstance(t, dict):
                    task_info["block_tasks"].append(
                        extract_task_info(t, repo_path, base_path)
                    )
        return task_info

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


def _collect_roles_from_tasks(tasks: list[dict], roles: set[str]) -> None:
    """Sammelt Rollen aus Tasks rekursiv (inkl. Blocks)."""
    for task in tasks:
        if task["type"] == "role":
            roles.add(task["role_name"])
        elif task["type"] == "block":
            _collect_roles_from_tasks(task.get("block_tasks", []), roles)
        elif task["type"] == "include":
            _collect_roles_from_tasks(task.get("included_tasks", []), roles)


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

    # Playbooks parsen (inkl. import_playbook-Auflösung)
    parsed_paths = set()
    pending = list(playbook_paths)

    while pending:
        pb_path = pending.pop(0)
        if pb_path in parsed_paths:
            continue
        parsed_paths.add(pb_path)

        try:
            pb_data = parse_playbook(pb_path, repo_path)
            data.playbooks[pb_path] = pb_data

            # Roles sammeln
            for play in pb_data["plays"]:
                for role_name in play["roles"]:
                    data.roles.add(role_name)
                _collect_roles_from_tasks(play["tasks"], data.roles)

            # Importierte Playbooks zur Queue hinzufügen
            for imp_path in pb_data.get("imported_playbooks", []):
                if os.path.exists(imp_path) and imp_path not in parsed_paths:
                    pending.append(imp_path)

        except ValueError as e:
            logger.error(str(e))
            raise

    # Role-Tasks und Dependencies laden (transitiv)
    all_roles = set(data.roles)
    processed_roles = set()

    while all_roles - processed_roles:
        for role_name in list(all_roles - processed_roles):
            processed_roles.add(role_name)
            data.role_tasks[role_name] = find_role_tasks(repo_path, role_name)
            deps = find_role_dependencies(repo_path, role_name)
            if deps:
                data.role_dependencies[role_name] = deps
                for dep in deps:
                    all_roles.add(dep)
                    data.roles.add(dep)

    return data
