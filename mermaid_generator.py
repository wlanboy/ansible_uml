"""Mermaid Generator Module - Erzeugt Mermaid-Diagramme aus Ansible-Daten."""

import re
import os
import logging
from dataclasses import dataclass, field
from ansible_parser import AnsibleData, find_role_tasks

logger = logging.getLogger(__name__)


# Styles für verschiedene Node-Typen
STYLES = [
    "classDef groupClass fill:#e1f5fe,stroke:#01579b,stroke-width:2px",
    "classDef hostClass fill:#fff3e0,stroke:#e65100,stroke-width:1px",
    "classDef playbookClass fill:#e8f5e9,stroke:#1b5e20,stroke-width:3px",
    "classDef roleClass fill:#f3e5f5,stroke:#4a148c,stroke-width:2px",
    "classDef taskClass fill:#fafafa,stroke:#616161,stroke-width:1px",
    "classDef handlerClass fill:#fff8e1,stroke:#ff6f00,stroke-width:1px,stroke-dasharray: 5 5",
    "classDef includeClass fill:#e0f2f1,stroke:#00695c,stroke-width:1px",
]


@dataclass
class DiagramNodes:
    """Container für gesammelte Diagram-Nodes."""
    groups: set = field(default_factory=set)
    hosts: set = field(default_factory=set)
    playbooks: list = field(default_factory=list)
    roles: set = field(default_factory=set)
    tasks: list = field(default_factory=list)
    handlers: set = field(default_factory=set)
    includes: set = field(default_factory=set)


def sanitize(text: str) -> str:
    """Bereinigt Text für Mermaid-Node-IDs."""
    text = text.strip()
    text = re.sub(r'[^\w\-]', '_', text)
    text = re.sub(r'__+', '_', text)
    if re.match(r'^\d', text):
        text = f"id_{text}"
    return text


def escape_label(text: str) -> str:
    """Escaped Anführungszeichen in Labels."""
    return str(text).replace('"', "'")


def generate_diagram(data: AnsibleData, layout: str = "LR", repo_path: str = "") -> str:
    """Generiert ein Mermaid-Diagramm aus AnsibleData."""
    lines = [f"graph {layout}"]
    nodes = DiagramNodes()
    connections = []
    task_counter = 0

    # === SPALTE 1: Inventory ===
    lines.append('    subgraph inventory["Inventory"]')
    lines.append('    direction TB')

    for group, hosts in data.groups.items():
        group_id = sanitize(group)
        nodes.groups.add(group_id)
        lines.append(f'        {group_id}[["fa:fa-layer-group {group}"]]')

        for host in hosts:
            host_id = sanitize(host)
            nodes.hosts.add(host_id)
            lines.append(f'        {host_id}(("fa:fa-server {host}"))')
            lines.append(f'        {group_id} --- {host_id}')

    lines.append('    end')

    # === SPALTE 2: Playbooks ===
    lines.append('    subgraph playbooks_section["Playbooks"]')
    lines.append('    direction TB')

    for pb_path, pb_data in data.playbooks.items():
        pb_name = pb_data["name"]
        pb_id = sanitize(pb_name)
        nodes.playbooks.append(pb_id)
        lines.append(f'        {pb_id}["fa:fa-book {pb_name}"]')

        for play in pb_data["plays"]:
            hosts_target = play.get("hosts")
            if hosts_target:
                group_id = sanitize(str(hosts_target))
                connections.append(f'    {group_id} -->|"runs"| {pb_id}')

            # Roles verbinden
            for role_name in play["roles"]:
                role_id = sanitize(f"role_{role_name}")
                nodes.roles.add(role_id)
                connections.append(f'    {pb_id} ==>|"uses"| {role_id}')

            # Tasks verarbeiten
            for task in play["tasks"]:
                task_counter = _process_task(
                    task, pb_id, lines, nodes, connections, task_counter
                )

            # Handlers (nur Node-Erstellung, Verbindungen kommen über notify)
            for handler_name in play["handlers"]:
                handler_id = sanitize(f"handler_{handler_name}")
                if handler_id not in nodes.handlers:
                    nodes.handlers.add(handler_id)
                    label = escape_label(handler_name)
                    lines.append(f'        {handler_id}(["fa:fa-bell {label}"])')

        # import_playbook-Verbindungen
        for imp_path in pb_data.get("imported_playbooks", []):
            imp_name = os.path.basename(imp_path)
            imp_id = sanitize(imp_name)
            connections.append(f'    {pb_id} -->|"imports"| {imp_id}')

    lines.append('    end')

    # === SPALTE 3: Roles ===
    lines.append('    subgraph roles_section["Roles"]')
    lines.append('    direction TB')

    for role_name in data.roles:
        role_id = sanitize(f"role_{role_name}")
        nodes.roles.add(role_id)
        lines.append(f'        {role_id}{{"fa:fa-cube {role_name}"}}')

        # Role-Tasks
        role_tasks = data.role_tasks.get(role_name, [])
        for rt in role_tasks:
            if not isinstance(rt, dict):
                continue
            task_name = rt.get("name", f"task_{task_counter}")
            rt_task_id = f"{role_id}_task_{task_counter}"
            label = escape_label(task_name)
            nodes.tasks.append(rt_task_id)
            lines.append(f'        {rt_task_id}["{label}"]')
            lines.append(f'        {role_id} --> {rt_task_id}')
            task_counter += 1

    # Role-Dependencies
    for role_name, deps in data.role_dependencies.items():
        role_id = sanitize(f"role_{role_name}")
        for dep in deps:
            dep_id = sanitize(f"role_{dep}")
            connections.append(f'    {role_id} -->|"depends"| {dep_id}')

    lines.append('    end')

    # Verbindungen hinzufügen
    lines.extend(connections)

    # Styling
    lines.extend(STYLES)

    # Klassen zuweisen
    _apply_classes(lines, nodes)

    return "\n".join(lines)


def _process_task(
    task: dict,
    parent_id: str,
    lines: list,
    nodes: DiagramNodes,
    connections: list,
    task_counter: int
) -> int:
    """Verarbeitet einen Task und fügt ihn zum Diagramm hinzu."""
    task_name = task.get("name", f"task_{task_counter}")
    task_id = f"{parent_id}_task_{task_counter}"
    label = escape_label(task_name)

    task_type = task.get("type", "task")

    if task_type == "role":
        role_name = task.get("role_name")
        if role_name:
            role_id = sanitize(f"role_{role_name}")
            nodes.roles.add(role_id)
            connections.append(f'    {parent_id} ==> {role_id}')
        return task_counter + 1

    if task_type == "include":
        include_file = task.get("include_file", "")
        include_id = sanitize(f"include_{os.path.basename(include_file)}")
        nodes.includes.add(include_id)
        lines.append(f'        {include_id}[/"{os.path.basename(include_file)}"/]')
        lines.append(f'        {parent_id} --> {include_id}')

        # Eingebundene Tasks verarbeiten
        for included_task in task.get("included_tasks", []):
            task_counter = _process_task(
                included_task, include_id, lines, nodes, connections, task_counter + 1
            )
        return task_counter + 1

    if task_type == "block":
        block_id = f"{parent_id}_block_{task_counter}"
        nodes.tasks.append(block_id)
        lines.append(f'        {block_id}["{label}"]')
        lines.append(f'        {parent_id} --> {block_id}')
        task_counter += 1
        for bt in task.get("block_tasks", []):
            task_counter = _process_task(
                bt, block_id, lines, nodes, connections, task_counter
            )
        return task_counter

    # Normaler Task
    nodes.tasks.append(task_id)
    lines.append(f'        {task_id}["{label}"]')
    lines.append(f'        {parent_id} --> {task_id}')

    # Notify-Verbindungen
    for handler_name in task.get("notify", []):
        handler_id = sanitize(f"handler_{handler_name}")
        if handler_id not in nodes.handlers:
            nodes.handlers.add(handler_id)
            lines.append(f'        {handler_id}(["fa:fa-bell {escape_label(handler_name)}"])')
        connections.append(f'    {task_id} -.->|"notifies"| {handler_id}')

    return task_counter + 1


def _apply_classes(lines: list, nodes: DiagramNodes) -> None:
    """Wendet CSS-Klassen auf die Nodes an."""
    if nodes.groups:
        lines.append(f'    class {",".join(nodes.groups)} groupClass')
    if nodes.hosts:
        lines.append(f'    class {",".join(nodes.hosts)} hostClass')
    if nodes.playbooks:
        lines.append(f'    class {",".join(nodes.playbooks)} playbookClass')
    if nodes.roles:
        lines.append(f'    class {",".join(nodes.roles)} roleClass')
    if nodes.tasks:
        lines.append(f'    class {",".join(nodes.tasks)} taskClass')
    if nodes.handlers:
        lines.append(f'    class {",".join(nodes.handlers)} handlerClass')
    if nodes.includes:
        lines.append(f'    class {",".join(nodes.includes)} includeClass')
